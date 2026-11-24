# Parallel Fetch — near-rate-limit Gmail replication

**Status:** Built 2026-07-22 · tests green (`pipeline/tests/test_parallel_fetch.py`)
**Applies to:** `infra/gmail/fetcher.py`, `infra/gmail/throttle.py`, `runners/ledger_sync.py`, `builders/l0_ledger.py`
**Context:** the sync front-end of `docs/revised_ingestion_pipeline/05_SYNC_AND_LEDGER.md`

## Why this exists

The serial fetcher moved ~6.7 msg/s — not because of Gmail's rate limit, but
because it never *reached* the rate limit. One batch in flight means the worker
spends its life waiting on a ~1.4s round trip, then sleeping a fixed pause.
Meanwhile Gmail's quota is **per mailbox** (250 units/user/sec, `messages.get`
= 5 units): fetching one mailbox at a time leaves every other mailbox's quota
bucket idle. For a 62-mailbox org that's >95% of available throughput unused.

First-principles conclusion: the job is I/O-bound and quota-bound, never
CPU-bound — so the fix is concurrency, not a faster language. (A Go fetcher
would wait on the same Google round trips at the same pace, and add a second
runtime + deploy + data handoff for zero gain. If per-core JSON parsing ever
matters at fleet scale, shard by org across processes — the control API
already launches one process per org run.)

## The three layers of parallelism

```
Layer 3   org × org          separate processes (control API — already existed)
Layer 2   mailbox × mailbox  runners/ledger_sync.py --parallel N   ← new
Layer 1   batch × batch      pipeline_depth in-flight per mailbox  ← new
          + QuotaThrottle    token bucket @ 80% of quota, AIMD     ← new
```

### Layer 1 — pipelined batches inside one mailbox

`fetch_user_messages` submits batches to a small worker pool
(`gmail_pipeline_depth`, default 3) so round trips overlap. Rules:

- **One Gmail service per worker thread** (`threading.local`). httplib2
  connections are not thread-safe; two batches on one socket corrupt responses.
- Services rebuild every 200 batches per thread (stale-connection hygiene).
  A failed rebuild keeps the old service; a failed *first* build retries once.
  One blip must not abandon a mailbox thousands of messages in.
- Batch results are handed to `on_batch` from the main thread (via
  `as_completed`), so the runner's per-mailbox write path stays single-threaded.
- Any batch failure fails the mailbox (pool drains, queued batches cancelled)
  → runner leaves the cursor put → next run resumes via `skip_local_ids`.
  Completion order is not arrival order, which is fine: payload upserts are
  idempotent and the cursor is a max over `internalDate`, order-independent.

### The throttle — `infra/gmail/throttle.py`

Token bucket, one per mailbox (quota buckets are per-user):

- Budget refills at `gmail_units_per_sec` (default **200 = 80% of 250**);
  bucket caps at 250. Each batch `acquire()`s `5 × batch_size` units before
  sending; when the bucket is dry the worker sleeps.
- **AIMD** (TCP's shape): any 429 → halve the pace immediately (floor 25 u/s
  so progress never stops); every clean batch → +5 u/s back toward target.
  So the fetcher self-tunes to the tenant's *actual* ceiling — Workspace
  tiers and concurrent-user load vary — instead of trusting the published
  number. This replaces the old fixed `gmail_batch_pause` sleep.

### Layer 2 — parallel mailboxes

`runners/ledger_sync.py --parallel N` fans mailboxes over N workers (**default
8 — parallel IS the path**; `--parallel 1` runs the same pool with one worker
for debugging a mailbox in order). Per-mailbox workers
don't compete for quota at all — the only shared ceiling is the per-project
daily budget (~1B units/day; a full 62-mailbox backfill is ~1M).

What stays exactly as it was, per mailbox: per-batch durable writes, cursor
advanced only on a clean finish, resume skipping already-held messages, one
failed mailbox never ending the run. Shared run totals sit behind a lock.

**Concurrency safety in the ledger:**
- The cross-mailbox sighting merge was already atomic in SQL
  (`ON CONFLICT … sightings || excluded.sightings`) — no read-modify-write.
- New: upsert rows are **sorted by payload_id** so two workers upserting an
  overlapping thread lock rows in the same order (deadlock guard).
- Engine pool (5 + 10 overflow) covers the default worker counts; raise
  `pool_size` if `--parallel` ever goes past ~12.

## Quota math

| | units/burst | % of one mailbox's 250/s |
|---|---|---|
| batch of 20 (`messages.get` ×20 ×5u) | 100 | 40% |
| throttle target | 200/s | 80% |
| N parallel mailboxes | N × ≤200/s | 80% **each** — independent buckets |

Expected: ~6.7 → ~15–20 msg/s per mailbox (depth 3 ≈ 2 batches/s sustained),
× N mailboxes org-wide.

**Measured (2026-07-22):**

- **Live probe, one real mailbox** (amol@ezobooks.in, 510 messages, depth 3,
  200 u/s target): **19.8 msg/s vs the 6.7–7.3 msg/s serial baseline — 2.8×
  per mailbox.** 17 of 510 gets (3%) 429'd; AIMD halved the pace, recovered,
  net throughput above. That is the throttle working: it *probes* the
  ceiling rather than guessing under it.
- **Cross-mailbox fan-out** ran in production over the backfill's 8-mailbox
  tail: 8 workers concurrent, totals aggregated correctly, zero failures.
  All 8 happened to be empty (unused accounts), so fan-out × per-mailbox
  throughput at once is still unmeasured — expect ≈ workers × ~20 msg/s;
  record the real number on the next org backfill.

## How to run

```bash
# Tail run: only mailboxes not yet replicated, 8 at a time
../.venv/bin/python -m runners.ledger_sync \
    --org ezobooks --domain ezobooks.in \
    --admin-email gk@ezobooks.in --key key.json \
    --users-file output/config/remaining_users.json \
    --parallel 8

# Monitor (DB truth + live log, read-only)
../.venv/bin/python scripts/fetch_progress.py
```

Knobs (`common/config.py`): `gmail_units_per_sec` (200), `gmail_pipeline_depth`
(3), `gmail_batch_size` (20 — see the quota-burst comment there before raising).

## Deliberate non-choices

- **No Go / separate fetcher service** — I/O-bound + quota-bound; concurrency
  in-process reaches the same ceiling with one runtime. Revisit only if
  per-core JSON parsing measurably saturates at fleet scale (then: more
  processes, not another language).
- **No asyncio rewrite** — google-api-python-client is synchronous; threads
  get full overlap on network waits with a fraction of the churn.
- **No shared multi-mailbox scheduler** — per-mailbox buckets are independent;
  a global scheduler would add coordination for zero throughput.
- `gmail_batch_pause` is now unused by the batch loop (the throttle paces it)
  but kept as a setting for the legacy `infra/gmail/run.py` path.
