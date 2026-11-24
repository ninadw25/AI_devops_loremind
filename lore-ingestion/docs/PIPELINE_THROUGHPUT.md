# Pipeline Throughput — Adaptive Concurrency & Continuous Flow

**Date:** 2026-07-23 · **Status:** built, running in production on ezobooks

Two named mechanisms, plus the observability fixes that made them measurable.
Together they took claim extraction from **1.8 → ~125 conversations/min**
(ETA on the ezobooks corpus: ~6 days → ~2 hours) and turned the substrate from
a stop-and-wait batch chain into a continuously-refreshing flow.

---

## 1. AIMD Adaptive Concurrency Limiter

**Where:** `runners/extract_claims.py` — `class AdaptiveLimiter`
**Replaces:** a fixed `asyncio.Semaphore(6)`

### The problem

A hardcoded concurrency number is wrong in both directions and there is no
value that is right. Too low (what we had — 6) and the provider's real
capacity sits idle; too high and every call starts eating `ThrottlingException`
retries, which is *slower* than not sending them. The correct number is a
property of the provider, the region, the model, the time of day, and how many
other runs share the account — none of which we know at launch, and all of
which change during a multi-hour run.

### The mechanism

The same control loop TCP uses for congestion, and the same one already proven
in `infra/gmail/throttle.py` for Gmail's per-mailbox quota — **AIMD**
(Additive Increase, Multiplicative Decrease):

| Signal | Action | Rationale |
|---|---|---|
| A clean streak of N successful calls (default 8) | limit **+1** | probe upward gently; a success streak is evidence there's headroom |
| Any 429 / `ThrottlingException` / `TooManyRequests` | limit **÷2** | back off hard and immediately — congestion collapse is far more expensive than lost throughput |

Bounded by a floor (2, never stalls completely) and a cap (48, a safety rail
against a provider that never pushes back).

```python
limiter = AdaptiveLimiter(start=12, cap=48)
await limiter.acquire()          # blocks while at the limit
try:
    resp = await client.chat.completions.create(...)
    await limiter.reward()       # clean call → streak++
except Exception as exc:
    if _is_throttle(exc):
        await limiter.penalize() # 429 → halve
finally:
    await limiter.release()
```

Implemented on `asyncio.Condition`, so a limit *increase* wakes waiters
immediately rather than making them wait for the next release — otherwise the
additive increase wouldn't actually take effect until traffic drained.

### Why this shape rather than a fixed higher number

We never learn the provider's ceiling; we converge on it, continuously, and
re-converge when it moves. A run that starts while another org's run is
saturating the account settles lower; when that run finishes, this one climbs
into the freed capacity on its own. Setting `--concurrency 40` instead would
have been faster than 6 and worse than this — it would spend the whole run
either under or over the real limit, and it would need re-tuning per client.

### Flags

```bash
--concurrency 12        # STARTING in-flight limit (not a fixed value)
--max-concurrency 48    # hard cap the limiter may climb to
--batch 400             # conversations loaded per DB batch (was 200)
```

### Observed behaviour

Started at 12, climbed to 15+ within the first 25 conversations, sustained
~125 conv/min in-process. No throttle penalties observed at that level in
`ap-south-1` — the previous limit of 6 was leaving roughly 20x on the table.

---

## 2. Substrate Flow Daemon

**Where:** `runners/substrate_flow.py`
**Replaces:** running each builder by hand, once, after the stage above it
finished completely.

### The problem

Every derived layer (L1 registries, L2 edges, dedup, Neo4j projection) was a
manual command you ran *after* extraction finished. So the graph sat frozen
and empty-ish for the entire multi-hour extraction, and any new mail meant
remembering to re-run four things in the right order.

### The mechanism

Every layer in this substrate is already a **cheap, idempotent, re-runnable
projection**, and **data presence is the work queue** — a conversation with no
digest row is pending extraction; a claim not yet in Neo4j gets MERGEd on the
next pass. Given that, orchestration reduces to: *notice what changed, re-run
what depends on it.*

```
events count changed (a fetch landed)
    → L1 registries → L2 edges → dedup → FULL graph projection

claim count changed (extraction produced more)
    → dedup → graph projection (claims-only)

nothing changed
    → sleep
```

Default interval 300s. Each cycle is wrapped so a DB blip logs a warning and
retries next tick rather than killing the daemon.

### The claims-only projection

`builders/project_claims.build(org, claims_only=True)` skips the static L1
nodes (persons / organizations / containers / artifacts) and the entire L2 edge
pass. Those only change when new **events** land — not when new **claims**
land. During extraction only claims are moving, so the frequent refresh cycle
costs seconds instead of the multi-minute full rebuild, which is what makes a
5-minute cadence affordable at all.

### The barriers that stay sequential (deliberately)

Parallelism was applied where it is safe, not everywhere:

| Barrier | Why it must hold |
|---|---|
| A conversation must be fully fetched before extraction reads it | Fetch writes events atomically per batch; extraction only ever selects complete rows. Reading mid-fetch would extract a truncated thread and cache that as the claim. |
| L1 registry must exist before extraction builds prompts | Prompts embed canonical participant names from the registry; without it, entity resolution silently degrades. It's a ~10s rebuild, refreshed by the daemon whenever events change. |

Everything downstream of extraction runs simultaneously with it.

### Running it

```bash
setsid nohup ../.venv/bin/python -u -m runners.substrate_flow \
    --org ezobooks --interval 300 >> output/logs/substrate_flow.log 2>&1 &

# one cycle and exit (useful in cron / CI)
../.venv/bin/python -m runners.substrate_flow --org ezobooks --once
```

---

## 3. Cost & Token Accounting

**Where:** `runners/extract_claims.py` — `MODEL_PRICING`, `estimate_cost()`

`extract_one` now returns the API's own `usage` block alongside the parsed
extraction, accumulated into run totals. Every 25th conversation logs:

```
[claims] 25/15470 convs · 40 claims (35 grounded) · 2.08 conv/s · inflight limit 15 · ~$0.08
```

and the run summary logs exact prompt/completion token counts plus cost.

Pricing table is explicit and small; an **unpriced model logs token counts and
omits the dollar figure** rather than guessing:

```python
MODEL_PRICING = {
    "moonshotai.kimi-k2.5": (0.6, 3.0),   # ($/M input, $/M output)
}
```

Before this, cost could only be *reconstructed* after the fact by re-tokenizing
stored prompts and outputs — an estimate. It is now measured.

---

## 4. Observability Fixes (why none of the above was visible before)

Two bugs were independently hiding all progress output, which is why the run
appeared dead for hours while the database was in fact filling normally.

**`setup_logging()` was never called.** `common/logger.get_logger()` returns a
bare `logging.Logger`; the file and console handlers are attached by
`setup_logging()`. Runners that skipped it emitted `log.info(...)` calls that
went nowhere — silently, with no error. Fixed in `runners/extract_claims.py`
and `runners/substrate_flow.py`.

**Python block-buffers stdout when it isn't a TTY.** Redirected to a log file,
output arrives in ~8KB chunks, so a live log can sit stale for a very long
time. Every detached driver now runs `python -u`.

### `scripts/claims_progress.py`

A live terminal view of the extraction run that reads **the database, not the
log** — `claim_digest.created_at` is the ground truth for both progress and
rate, and it can't go stale the way a buffered log can.

```bash
../.venv/bin/python scripts/claims_progress.py          # live, redraws in place
../.venv/bin/python scripts/claims_progress.py --once   # single snapshot
```

Shows: conversations done / total with %, claims and evidence-grounded rate,
kind distribution, conversations/min over the last 5 minutes, ETA, and the most
recent conversations processed with their digests. Read-only — safe to start
and stop at any time during a run.

---

## Tests

`pipeline/tests/test_adaptive_limiter.py` — pins the control loop:
multiplicative decrease floors correctly, additive increase caps correctly,
`acquire` blocks at the limit and unblocks on release (no lost wakeups), and
throttle-detection matches real Bedrock error text without false-positiving on
ordinary connection errors.

Suite: **422 passing.**

---

## Restart reference

```bash
cd lore-ingestion

# extraction (auto-restarting driver, adaptive concurrency)
setsid nohup bash scripts/extract_claims_overnight.sh 2 &

# continuous downstream refresh
setsid nohup ../.venv/bin/python -u -m runners.substrate_flow --org ezobooks --interval 300 \
    >> output/logs/substrate_flow.log 2>&1 &

# watch it
../.venv/bin/python scripts/claims_progress.py
```
