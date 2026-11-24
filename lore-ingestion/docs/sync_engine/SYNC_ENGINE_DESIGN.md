> **Historical (as of 2026-09-25)** — superseded by the L0 ledger (`builders/l0_ledger.py`, `runners/l0_sync.py`). Kept for context; do not treat as current.

# Sync Engine — Design (LOCKED v1)

Status: **LOCKED v1** (2026-06-02) · Owner: ingestion · Last updated: 2026-06-02
Scope: continuous, production-grade ingestion for **Gmail, Slack, Jira** (GitLab/chronograph out of scope here).

This document is the single authoritative design for three capabilities:

1. **Timeline reversal** — on onboarding, start at *now* and walk **backwards** in time (bounded), so the org's knowledge brain is *current-first*, not clogged with 10-year-old noise.
2. **Edit/delete batch sync** — keep the graph in step with the source when records change. **v1 ships EDIT reconciliation; DELETE reconciliation is designed here but deferred** (it needs a Neo4j tombstone path + backend/RBAC coordination).
3. **Production rate limiting** — adaptive, per-source rate limiting + retry across all connectors.

Locked decisions (2026-06-02):
- **Edits in v1, deletes deferred** to a dedicated later phase.
- **Backfill is quarter-batched** (one calendar-quarter window per scope per step) with a day-mapped coverage ledger.
- **Time floor: 12 months by default, per-org configurable** in org config JSON.
- **Deployment: on-prem / EC2 + systemd timers**, JSON state on local disk / bind-mount.
- Discovery (scopes/ACL) continues to run **first**, before any data load — unchanged.
- **LLM extraction runs on AWS Bedrock** (API-key auth), model **DeepSeek V3.2**, region **ap-south-1 (Mumbai)** — same model family as today, so prompts are unchanged; Mumbai keeps data in-region for the Indian customer. The extraction budget is a **dual requests/min + tokens/min** budget, config-driven (start conservative, raise via Service Quotas) — not the old fixed 40-RPM NVIDIA cap.
- **Embeddings stay on NVIDIA NeMo** (2048-dim). Bedrock governs extraction only; no re-embed, no dimension change.

---

## 1. The core model — two edges, three loops

Every `(source, scope)` pair (a Gmail user, a Slack channel, a Jira project) has a small sync state with **two time edges**:

```
   floor                         backward_cursor        forward_cursor = NOW
     │                                  │                        │
     ▼                                  ▼                        ▼
  ───●──────────────────────────────────●════════════════════════●──────────▶ time
     │            (not yet                │   (already ingested)   │
     │             ingested)              │                        │
   now − N months              backfill has reached here     caught up to here
```

- **`forward_cursor`** — high-water mark; everything newer has been ingested. Advanced by the **delta loop**.
- **`backward_cursor`** — low-water mark; how deep into history we've backfilled. Advanced (downward) by the **backfill loop**.
- **`floor`** — `now − N months` (default 12). Backfill stops here.
- **`last_reconciled`** — watermark for the **reconcile loop** (edits in v1).

Three loops operate over this state:

| Loop | Cadence (systemd) | Direction | Cost | Purpose |
|------|-------------------|-----------|------|---------|
| **Delta** (hot) | every 6 h | `forward_cursor → now` | light | new messages/issues |
| **Backfill** (cold) | nightly, 1 quarter-step | `backward_cursor → floor`, newest-first | heavy, **bounded per step** | historical brain, current-first |
| **Reconcile** (warm) | daily | rescan recent window | medium | catch **edits** (v1); deletes later |

On **onboarding**: discovery (ACL) → set `forward_cursor = anchor = now` → backfill begins walking backward one quarter per nightly tick. The delta loop simultaneously keeps the leading edge fresh.

---

## 2. What already exists (build-on, don't rebuild)

Previous work scaffolded most of this but left it unwired. The build is mostly **wiring + unifying + hardening**, not greenfield.

| Piece | File | State | Action |
|-------|------|-------|--------|
| Reverse-chron backfill engine (anchor, `oldest_fetched`, `last_cursor`, `max_history_days` floor, source-agnostic) | `common/checkpoint.py` | written, **orphaned** | adopt as backfill half of `sync_state` |
| Forward watermark (Gmail) | `common/watermark.py` | works, **Gmail-only** | generalize into `sync_state` |
| Slack reverse fetch (`fetch_channel_messages_reverse`, `iter_channel_events_reverse`, floor-aware, resumable) | `infra/slack/fetcher.py` | written, **dead** | wire into run.py + quarter windows |
| Jira reverse fetch (`fetch_issues_reverse`, `updated<=anchor ORDER BY updated DESC`, floor + token resume) | `infra/jira/fetcher.py` | written, **dead** | wire into run.py + quarter windows |
| Gmail backward fetch | `infra/gmail/fetcher.py` | **missing** (`after:` only) | add `before:` + backward page walk |
| Adaptive rate limit (token bucket, sliding window, 429/Retry-After, per-source tracker) | `scheduler/rate_limit.py` | complete, **unwired** | wire into all 3 connectors |
| Retry/backoff + circuit breaker | `scheduler/retry.py` | complete, **unwired** | wire into all 3 connectors |
| Weighted-fair queue across sources | `scheduler/priority_queue.py` | complete, **unwired** | drive the shared LLM-extract queue |
| `scheduler` package | `scheduler/__init__.py` | **BROKEN** — imports nonexistent `scheduler.py` | fix first (P0) |
| Delta orchestrator | `runners/sync.py` | works, **Gmail-only** | generalize to all sources |
| Idempotency: stable `id = SHA(source+source_id)` + Neo4j MERGE | `common/hashing.py`, builders | works | re-fetch is free & lossless (except deletes) |

---

## 3. Unified sync state — `common/sync_state.py`

Supersedes `watermark.py` (forward) and absorbs `checkpoint.py` (backward) into one atomic, source-and-scope-keyed file per org.

Location: `${LORE_STATE_DIR}/checkpoints/{org}_sync.json` (same path/atomic-write discipline as today).

```jsonc
{
  "version": 2,
  "org": "ezobooks",
  "floor_months": 12,                      // per-org, from org config
  "sources": {
    "slack": {
      "scopes": {
        "C0123#eng": {
          "forward_cursor":  "1717000000.000200",   // newest ingested (source-native cursor: slack ts)
          "backward_cursor": "1709251200.000000",    // oldest ingested
          "floor_ts":        "1685577600.000000",     // now − 12mo, frozen at onboarding
          "last_reconciled": "2026-06-01T00:00:00Z",
          "windows": [                                 // day-mapped coverage ledger
            {"q": "2026-Q2", "from": "2026-04-01", "to": "2026-06-02",
             "status": "built",     "events": 812, "updated_at": "..."},
            {"q": "2026-Q1", "from": "2026-01-01", "to": "2026-03-31",
             "status": "extracted", "events": 1340, "updated_at": "..."},
            {"q": "2025-Q4", "from": "2025-10-01", "to": "2025-12-31",
             "status": "pending"}
          ]
        }
      }
    },
    "jira":  { "scopes": { "PROJ": { ... } } },
    "gmail": { "scopes": { "alice@ezobooks.in": { ... } } }
  },
  "last_run": { "delta": {...}, "backfill": {...}, "reconcile": {...} }
}
```

- **Cursor values are source-native** (Slack `ts`, Gmail `internalDate` ms, Jira `updated` ISO). The state layer is dumb storage; each connector interprets its own cursor — keeps `sync_state` source-agnostic.
- **`windows[]` is the day-mapped ledger** you asked for: every quarter window records its day boundaries, status (`pending → fetched → extracted → built`), event counts, and timestamps. This is the resume map and the audit log.
- **Atomicity rule (unchanged):** a window's status only advances after that stage fully succeeds; a crash leaves it where it was; re-running re-processes the same window, and dedup + MERGE make that free.

---

## 4. Quarter-stepped backfill — the load-management mechanism

**Problem:** backfilling 12 months for Gmail + Slack + Jira at once is a thundering herd — and three uncoordinated LLM extractors would blow the single global 40-RPM budget.

**Mechanism: quarter-stepped, fetch-parallel, extract-serialized.**

```
nightly tick ──▶ for each source (concurrent, I/O-bound, own rate limiter):
                    pick next pending window = newest quarter below backward_cursor
                    FETCH that quarter  → raw JSONL          [status: pending→fetched]
                 ──▶ enqueue threads into ONE shared extract queue
                 ──▶ EXTRACT drains queue under global Bedrock budget (RPM+TPM, WFQ fair across sources)
                                                              [status: fetched→extracted]
                 ──▶ BUILD (validate→embed→dedup→Neo4j MERGE) [status: extracted→built]
                 ──▶ advance backward_cursor to window.from; stop (one step per tick)
```

Properties:
- **Bounded work per tick** — one quarter per scope, not the whole year. Memory and load are capped by construction.
- **Newest-first** — Q-current built before Q-1 → brain usable immediately, deepens nightly.
- **One global Bedrock budget** — fetch is parallel, extraction is a single fair queue governed by a **token-aware** limiter (RPM *and* TPM). Three sources can't collectively blow the account's tokens/min quota.
- **Spread over time** — ~3–4 nightly ticks to backfill a year; never competes with the 6-hour delta loop or business hours.
- **Jira 10k guard** — a quarter occasionally exceeds Jira's 10k-result search window on a very busy project → sub-split that window to **month** automatically (the ledger just gains finer windows). Slack/Gmail quarters are safe.

Configurable knobs (org config / env): `floor_months` (12), `backfill_window` (`quarter`|`month`), `backfill_steps_per_tick` (1), and the Bedrock extraction budget `bedrock_rpm` / `bedrock_tpm` / `bedrock_concurrency` (see §6b).

---

## 5. Connector contract (uniform across sources)

Each connector's fetcher exposes three capabilities; `run.py` selects mode. Normalizer & discovery are untouched.

```python
fetch_forward(scope, since_cursor)              -> events, new_forward_cursor   # delta
fetch_backward(scope, anchor, floor, cursor)    -> events, new_backward_cursor  # backfill (quarter window)
list_live_ids(scope, window)                    -> set[source_id]               # reconcile (P4/P5)
```

- **Slack** — `fetch_backward` ≈ existing `iter_channel_events_reverse` (reconcile 3-tuple/2-tuple signature with `process_channel`); `list_live_ids` = history scan over window returning `ts`s.
- **Jira** — `fetch_backward` ≈ existing `fetch_issues_reverse` bounded to one quarter; `list_live_ids` = cheap `fields=key` JQL over `updated` window.
- **Gmail** — `fetch_backward` = **new**: `build_query` gains `before:`; page `messages.list` newest→oldest, stop at window floor; `list_live_ids` = ID-only `messages.list` (already prototyped in `dry_run_counts`).

---

## 6. Rate limiting & retry

Two **independent** rate-limit domains. Don't conflate them.

### 6a. Source-API limits — connectors (P0–P1)

The Gmail/Slack/Jira REST APIs. Wire the existing, complete `scheduler/` primitives into each connector's **single HTTP chokepoint**:

- Gmail `_fetch_batch` · Slack `_get` · Jira `_request`.
- One shared `RateLimitTracker` per source (adaptive: halves RPM on 429, honors `Retry-After`, recovers on success).
- One shared `RetryPolicy` (exponential + decorrelated jitter, circuit breaker) wrapping each chokepoint.
- **Jira is urgent**: today a 429 *crashes the run* (no handling at all).
- **First fix (P0):** `scheduler/__init__.py` imports a nonexistent `scheduler/scheduler.py` → the package won't import. Trim `__init__` to what exists (or write the orchestrator). Nothing in `scheduler/` can be used until this is fixed.

Per-source config (env, via `common/config.py`): RPM caps, burst, base delay, max retries. No more hard-coded `time.sleep` constants scattered in fetchers. **Bedrock does not change this domain.**

### 6b. LLM extraction budget — AWS Bedrock (P0–P1)

The triple-extraction LLM now runs on **AWS Bedrock with API-key auth**. This replaces the NVIDIA flat-40-RPM model and needs three changes:

**(1) Provider integration — mechanical, fits the existing OpenAI-based stack.**
- New `infra/llm/bedrock.py` `ChatProvider`; one line in `infra/llm/registry.py`; `"bedrock"` added to `llm_provider` Literal in `common/config.py`.
- Use Bedrock's **OpenAI-compatible Chat Completions endpoint** (`https://bedrock-runtime.{region}.amazonaws.com/openai/v1`) with the **Bedrock API key as the bearer token** → drops straight into the existing `AsyncOpenAI` `make_async_client()`; no boto3/SigV4 rewrite. (boto3 `bedrock-runtime` + SigV4 is the fallback if the OpenAI-compat path is unavailable for the chosen model.)
- New config: `bedrock_api_key`, `bedrock_region` (`ap-south-1`), `bedrock_base_url`, `extraction_model` set to the Bedrock DeepSeek V3.2 model/inference-profile id. **P0 verify:** confirm the exact id — DeepSeek in ap-south-1 is likely served via an **APAC cross-region inference profile** (`apac.deepseek...`) rather than a plain regional id; and confirm the **OpenAI-compat endpoint supports DeepSeek** — if not, fall back to boto3 `bedrock-runtime` **Converse** API in `infra/llm/bedrock.py`.

**(2) Token-aware throttling — the real work.** Bedrock enforces **both** requests/min (RPM) **and** tokens/min (TPM), per model per region; for large thread prompts **TPM is usually the binding constraint**. The current limiter (`pipeline/extraction/rate_limit.py` `AsyncRateLimiter`) and the scheduler's `RateLimitTracker` are **RPM-only** → extend to a dual sliding window:
- Before each call, **estimate tokens** (prompt + expected completion) and block if either the RPM *or* TPM window would be exceeded.
- After each call, **reconcile** the estimate with the response's actual `usage` tokens so the window self-corrects.
- Add a `"bedrock"` entry to `_PROVIDER_LIMITS` in `pipeline/extraction/extract.py`, but make the numbers **config-driven** (`bedrock_rpm`, `bedrock_tpm`, `bedrock_concurrency`) since Bedrock quotas are account/model/region-specific and adjustable via **Service Quotas**.

**(3) ThrottlingException backoff.** Bedrock throttling = `ThrottlingException` (HTTP 429). Route extraction calls through `scheduler/retry.py` (`RateLimitTracker.record_429` honors `Retry-After`, exponential backoff) instead of crashing or naively retrying. On sustained throttling the adaptive tracker shrinks effective RPM/TPM and recovers on success.

This single token-aware budget **is** the "one global budget" the quarter-stepped backfill (§4) drains through — so backfill + delta + reconcile across all three sources collectively respect the Bedrock account quota.

> **Note — embeddings (Stage 6.5):** currently NVIDIA NeMo (2048-dim), *separate* from extraction. If embeddings also move to Bedrock (Titan/Cohere), that's a second Bedrock-quota consumer with its own TPM and must share/segment the budget. Open question in §12.

---

## 7. Edit reconciliation (v1) — `reconcile` loop, edits only

Daily warm loop re-scans a recent window (default 30 days) per scope and upserts changed records. Because `id = SHA(source+source_id)` is stable and writes are MERGE, an edit re-emits the same node id with new content → clean upsert, no duplicates.

- **Jira** — nearly free: re-query `updated >= last_reconciled`. Changed issues flow through normally. Changelog already captured (`expand=changelog`).
- **Slack** — capture `edited.ts` + a `body_hash` in `slack_meta` (today only a boolean `is_edited`); on rescan, if `edited.ts`/hash differs from stored → re-emit. Also handle socket `message_changed` subtype (currently dropped as noise).
- **Gmail** — messages are immutable; only labels/trash change. Capture `historyId`; use `users.history.list(startHistoryId=…)` to catch label changes. (Trash = a delete signal → feeds the deferred delete phase.)

State: add a lightweight `{id: {edited_ts|body_hash}}` side-store per scope, or compare against the graph. Edits do **not** need the Neo4j delete path, which is why they ship in v1.

---

## 8. Delete reconciliation (DEFERRED — design sketch only)

Recorded so v1 doesn't paint us into a corner. **Not built in v1.**

Requires three new pieces:
1. **Live-ID manifest** per `(org, source, scope)` — the authoritative set of currently-live upstream ids, refreshed each reconcile (`list_live_ids`). Distinct from the existing monotonic `seen_ids` dedup caches (which never prune).
2. **Diff** — `manifest_prev − manifest_now` (Jira/Gmail) or absence-from-history (Slack) → delete candidates. Note the ambiguity: "deleted" vs "moved out of scope" vs "permission lost" can look identical (esp. Jira) → tombstone, don't hard-delete, until disambiguated.
3. **Neo4j tombstone path** — *new*: the graph is MERGE/upsert-only today and the ACL design treats nodes as immutable. A tombstone pass (set `:Deleted`/`deleted_at`, exclude from retrieval) is a builders change **and** a backend/RBAC change → cross-team, hence deferred.

When we pick this up: it becomes a 4th loop or an extension of `reconcile`, reusing the same `list_live_ids` contract already added in v1's reconcile work.

---

## 9. Idempotency, atomicity & safety

- **Re-fetch is free**: stable ids + Neo4j MERGE → overlapping windows collapse losslessly.
- **Per-window atomicity**: ledger status advances only after a stage fully succeeds; crash → re-run same window.
- **Backfill ≠ delta state**: the two cursors are independent; a stalled backfill never blocks delta freshness and vice-versa.
- **Discovery-first preserved**: ACL/scope crawl runs before data in every mode. Backfill that runs in multiple passes must **merge** (not overwrite) dynamic per-issue/per-message ACL grants across passes (Jira `append_dynamic_issue_scope_rows` is already dedup-safe).

---

## 10. Deployment (on-prem / EC2 + systemd)

Three timers, one unit each, all firing `python -m runners.<loop>`:

| Timer | Schedule | Unit |
|-------|----------|------|
| `lore-delta.timer` | `*-*-* 00,06,12,18:00` | `runners.sync` (delta, all sources) |
| `lore-backfill.timer` | nightly `02:30` | `runners.backfill` (1 quarter-step) |
| `lore-reconcile.timer` | daily `04:00` | `runners.reconcile` (edits) |

- State = JSON on local disk / bind-mount (`LORE_STATE_DIR`). No S3/EFS needed for on-prem.
- `Persistent=true` (catch up missed runs), `RandomizedDelaySec` (avoid herd), `TimeoutStartSec` guard.
- Observability: one greppable `STAGE_SUMMARY` line per window (reuse `common/stage_summary.py`); "minutes since last successful delta/backfill/reconcile" metric; alert on `last_run.status == failed`.

---

## 11. Phased build plan

Each phase is independently shippable and testable (`--dry-run`, `--limit`, mock fetchers).

| Phase | Deliverable | Files (primary) | Risk |
|-------|-------------|-----------------|------|
| **P0 ✅ DONE** | Fixed `scheduler` package; wired adaptive rate-limit+retry into **Jira** (429/Retry-After + 5xx + transport backoff — no more crashes); **added Bedrock provider** (DeepSeek V3.2, ap-south-1, bedrock-mantle OpenAI-compat) so extraction runs on Bedrock | `scheduler/__init__.py`, `infra/jira/fetcher.py`, `infra/llm/bedrock.py`, `infra/llm/registry.py`, `common/config.py`, `.env.example` | Low |
| **P1 ✅ DONE** | **Token-aware (RPM+TPM) Bedrock limiter** (`TokenAwareRateLimiter`, estimate→acquire→reconcile) + `ThrottlingException`/Retry-After backoff in the extract retry loop; **Gmail** batch size/pause/cooldown/retries config-driven + Retry-After honored; **Slack** non-Marketplace `restricted` tier preset (~1 req/min, 15-msg history cap) | `pipeline/extraction/rate_limit.py`, `pipeline/extraction/extract.py`, `infra/gmail/fetcher.py`, `infra/slack/fetcher.py`, `common/config.py` | Low–Med |
| **P2** | `common/sync_state.py` (unify watermark+checkpoint, day-mapped ledger); quarter-window helper | `common/sync_state.py`, `common/org_config.py` | Med |
| **P3** | **Backfill loop**: wire Slack/Jira reverse fetchers + add Gmail `before:`; `runners/backfill.py`; quarter-stepped, fetch-parallel, extract-serialized via WFQ | `infra/*/run.py`, `infra/gmail/fetcher.py`, `runners/backfill.py`, `scheduler/priority_queue.py` | Med |
| **P4** | **Delta loop generalized** to all sources on `sync_state` | `runners/sync.py`, `infra/*/run.py` | Med |
| **P5** | **Edit reconciliation** loop (Jira/Slack/Gmail) | `runners/reconcile.py`, `infra/*/{fetcher,normalizer}.py` | Med |
| **P6** | systemd units + observability + real-world validation on live Slack + Jira | `docs/sync_engine/*`, ops | — |
| **(later)** | **Delete reconciliation** + Neo4j tombstone path (cross-team) | builders, backend/RBAC | High |

Build order: **P0 → P1 → P2 → P3 → P4 → P5 → P6**. P0/P1 are pure hardening and can land immediately with no behavior change to existing runs.

---

## 12. Locked defaults & remaining P0 verifications

**Locked (defaults; overridable in org config):**
- Backfill window = **calendar quarters** (cleaner ledger, fiscal alignment).
- Reconcile window = **30 days**.
- Floor = **12 months, per-org** (per-source override is a small later extension if needed).
- Extraction = **Bedrock DeepSeek V3.2, ap-south-1**; **conservative** `bedrock_rpm`/`bedrock_tpm` defaults + adaptive backoff, raise via Service Quotas.
- Embeddings = **NVIDIA NeMo** (unchanged).

**P0 verifications (implementation-time, not design blockers):**
- Confirm DeepSeek V3.2 model-id + ap-south-1 availability (likely an `apac.deepseek...` cross-region inference profile).
- Confirm Bedrock OpenAI-compat endpoint supports DeepSeek; else use boto3 `bedrock-runtime` Converse in `infra/llm/bedrock.py`.
- Set conservative starting `bedrock_rpm` / `bedrock_tpm` and validate against live throttling behavior.
