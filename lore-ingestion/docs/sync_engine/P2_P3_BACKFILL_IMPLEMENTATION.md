> **Historical (as of 2026-09-25)** — superseded by the L0 ledger (`builders/l0_ledger.py`, `runners/l0_sync.py`); `runners/backfill.py` was never built. Kept for context; do not treat as current.

# P2 + P3 — Timeline-Reversal Backfill: Implementation Spec

Status: **proposed (pre-execution)** · Depends on: P0 ✅, P1 ✅ · Parent: [SYNC_ENGINE_DESIGN.md](./SYNC_ENGINE_DESIGN.md)

This is the build plan for the **newest-first, quarter-stepped backward backfill** (the "current knowledge brain first" requirement). P2 builds the unified state layer; P3 builds the backfill loop on top of it. Each sub-task is independently shippable and testable with `--dry-run` / mock fetchers.

**Guiding constraints (locked):** calendar-quarter windows · 12-month floor (per-org configurable) · discovery/ACL first · fetch-parallel + extract-serialized under one Bedrock budget · on-prem/EC2 + systemd · idempotent re-runs.

---

## Part 1 — P2: Unified sync state (`common/sync_state.py`)

### 1.1 Why a new module (and what it supersedes)

Today there are two half-states:
- `common/watermark.py` — **forward**, Gmail-only (`last_internal_date_ms` per user). Used by `runners/sync.py`.
- `common/checkpoint.py` — **backward**, source-agnostic, reverse-chronological with a `max_history_days` floor. **Orphaned** (no importers).

`common/sync_state.py` unifies both into one atomic per-org file holding, per `(source, scope)`, the **two edges + ledger** from the parent design (§3). It reuses checkpoint.py's reverse-chron concepts and watermark.py's atomic-write discipline.

> **Coexistence rule (important):** P2/P3 do **not** touch `runners/sync.py` or `watermark.py`. The Gmail delta loop keeps running on `watermark.py` unchanged. `sync_state` powers **backfill only** for now; **P4** migrates the delta loop onto `sync_state` (seeding `forward_cursor` from the existing watermark) and retires `watermark.py`. This keeps the working hot path stable while we build the cold path.

### 1.2 State file

Path: `${LORE_STATE_DIR}/checkpoints/{org}_sync_state.json` (distinct filename from watermark's `{org}_sync.json` so the two coexist during P2–P3). Atomic temp-write + `os.replace`, identical to `watermark._atomic_write`.

Schema = the JSON in parent design §3 (`version: 2`, `sources.{source}.scopes.{scope_key}` → `{forward_cursor, backward_cursor, floor_ts, last_reconciled, windows[]}`). Cursors are **source-native opaque strings** (Slack `ts`, Gmail `internalDate` ms, Jira `updated` ISO) — `sync_state` never interprets them.

### 1.3 Public API

```python
# ── load / save (atomic) ─────────────────────────────────────────────
def load(org: str) -> dict                      # empty skeleton on first run
def save(org: str, state: dict) -> None         # atomic write

# ── scope lifecycle ──────────────────────────────────────────────────
def ensure_scope(state, source, scope_key, *, floor_months, now_iso) -> dict
    # creates the scope entry if absent; freezes floor_ts = now - floor_months;
    # plans the window ledger (pending) from now back to floor. Idempotent.

def get_scope(state, source, scope_key) -> dict | None

# ── cursors ──────────────────────────────────────────────────────────
def set_forward_cursor(state, source, scope_key, cursor) -> None   # delta edge (P4)
def set_backward_cursor(state, source, scope_key, cursor) -> None  # backfill edge
def set_last_reconciled(state, source, scope_key, iso) -> None     # reconcile (P5)

# ── window ledger ────────────────────────────────────────────────────
def next_pending_window(state, source, scope_key) -> dict | None
    # newest window whose status != 'built' (drives newest-first walk)
def mark_window(state, source, scope_key, q, status, *, events=None) -> None
    # status transitions: pending → fetched → extracted → built (+ timestamp)
def coverage(state, source, scope_key) -> dict   # {built: n, pending: m, oldest_built_day}
```

### 1.4 Quarter-window helpers (same module or `common/timewindows.py`)

```python
def quarter_label(d: date) -> str                  # date → "2026-Q2"
def quarter_bounds(year, q) -> tuple[date, date]   # → (2026-04-01, 2026-06-30)
def plan_windows(anchor: datetime, floor: datetime,
                 granularity="quarter") -> list[dict]
    # → [{q, from, to, status:'pending'}, ...] newest-first, anchor→floor
def month_subsplit(window: dict) -> list[dict]     # Jira 10k overflow → 3 monthly windows
```

`floor` = `anchor - relativedelta(months=floor_months)`. The most-recent window is clamped to `to = anchor` (today), so backfill starts at "now" and walks down.

### 1.5 Migration / safety

- First `load()` returns an empty v2 skeleton; nothing to migrate (separate file).
- A `version` mismatch raises (same guard as `watermark.load`), forcing an explicit decision rather than silent reset.
- No dependency on `infra/`, `pipeline/`, `builders/` — stays a Layer-0 leaf (enforced by `test_layering`).

### 1.6 P2 sub-tasks

| ID | Deliverable | Test |
|----|-------------|------|
| **P2.1** | `common/sync_state.py`: load/save (atomic), scope lifecycle, cursor setters | unit: round-trip, atomicity, version guard |
| **P2.2** | Window ledger: `plan_windows`, `next_pending_window`, `mark_window`, quarter helpers | unit: quarter math across year boundary; newest-first ordering; transitions |
| **P2.3** | `ensure_scope` + per-org `floor_months` from `common/org_config.py` | unit: floor freezing; idempotent re-ensure |
| **P2.4** | `org_config` knobs: `floor_months` (12), `backfill_window` (`quarter`), `backfill_steps_per_tick` (1) | unit: defaults + override |

---

## Part 2 — P3: The backfill loop

### 2.1 Connector backward contract

Each connector exposes one uniform method (most already exist — wiring, not greenfield):

```python
fetch_backward(scope_key, window_from_ts, window_to_ts, cursor=None)
    -> (events: list[dict], new_oldest_cursor: str, exhausted: bool)
```

- **Slack** (`infra/slack/fetcher.py`): wrap the **existing dead** `iter_channel_events_reverse(channel, anchor_ts=window_to, since=window_from, checkpoint={cursor})`. Reconcile its 3-tuple `(msg, replies, progress)` yield with a small collector. `conversations.replies` ignores the floor (pulls full thread) — accepted; logged. Uses `HISTORY_PAGE_LIMIT` from P1.
- **Jira** (`infra/jira/fetcher.py`): wrap the **existing dead** `fetch_issues_reverse(project_key, anchor_updated=window_to, since_updated=window_from, checkpoint={next_page_token, oldest_updated})`. **10k guard:** if a quarter returns ≥10k (the `JIRA_OVERFLOWS` metric exists), `month_subsplit` the window in the ledger and re-run as 3 monthly windows. Per-project (the helper is `project = {key}`).
- **Gmail** (`infra/gmail/fetcher.py`): **new** — add `before_epoch` to `build_query` (currently only emits `after:`), then a backward page walk: `messages.list` is newest-first; page until the window floor is crossed or `nextPageToken` runs out. `list_live_ids` ID-only listing already prototyped in `dry_run_counts`.

Normalizers and discovery are **untouched**.

### 2.2 One global Bedrock budget — shared limiter singleton

**Problem:** if backfill runs per-source extraction concurrently, each `extract_all` builds its own `TokenAwareRateLimiter` → three limiters → blows the account TPM (exactly the failure P1 prevents within a single run).

**Solution:** make the Bedrock limiter a **process-global singleton** in `pipeline/extraction/rate_limit.py`:

```python
def get_shared_bedrock_limiter(max_rpm, max_tpm) -> TokenAwareRateLimiter
    # module-level singleton keyed by (max_rpm, max_tpm); all extract_all
    # invocations in the process share ONE limiter → one budget, regardless
    # of how many sources/loops run concurrently.
```

`extract_all` uses the singleton when `provider.name == "bedrock"`. This makes "fetch-parallel, extract-serialized, one budget" hold even if delta + backfill run in the same process, and is simpler than wiring `WeightedFairQueue` (kept as a later fairness optimization, not required for correctness).

### 2.3 Orchestrator — `runners/backfill.py`

Per tick (one systemd firing), per source, advance **one** window-step (config `backfill_steps_per_tick`):

```
load sync_state(org)
for source in enabled_sources(org):                      # Gmail, Slack, Jira
    ensure discovery/ACL artifact is current (run if stale)   # discovery FIRST
    scopes = list_scopes(source)                              # users / channels / projects

# ── FETCH phase (sources concurrent; each behind its own P0/P1 limiter) ──
fetched = await gather over (source, scope):
    ensure_scope(state, source, scope, floor_months, now)
    w = next_pending_window(state, source, scope)
    if not w: skip
    events, oldest, exhausted = fetch_backward(source, scope, w.from, w.to, cursor)
    write raw JSONL  → output/raw/{org}_{source}_backfill_{w.q}.jsonl
    mark_window(state, source, scope, w.q, 'fetched', events=len(events))
    save(state)                                              # checkpoint after each fetch

# ── EXTRACT phase (single shared Bedrock budget) ──
run the standard pipeline stages on this tick's fetched windows:
    filter → person_registry → comm_graph → triple_extractor (shared limiter)
mark each window 'extracted'; save(state)

# ── BUILD phase ──
validator → embed_knowledge → deduplicator → unified_builder (Neo4j MERGE)
for each window: mark 'built'; set_backward_cursor(oldest); save(state)
```

- **Newest-first:** `next_pending_window` always returns the most-recent non-built window → Q-current builds before Q-1. Brain is usable immediately, deepens each tick.
- **Bounded load:** exactly one window per scope per tick. A year backfills over ~4 ticks.
- **Atomicity:** ledger status advances only after each stage succeeds; a crash re-runs the same window; dedup + Neo4j MERGE make that free.
- **Idempotency:** stable `id = SHA(source+source_id)` + MERGE (unchanged from today).

### 2.4 Extraction-input reuse

The existing pipeline stages already read JSONL and are largely source-shaped via `common/sources.py`. For the tick's combined fetched data we either (a) run stages per-source-window and share the limiter singleton (simplest, chosen), or (b) merge then run once. Chosen: **(a)** — minimal change to existing stage entrypoints; the singleton guarantees one budget. Per-source fairness within a tick comes from round-robin ordering of source-windows before extraction.

### 2.5 CLI

```bash
python -m runners.backfill --org ezobooks                 # one step per scope, all sources
python -m runners.backfill --org ezobooks --source slack  # single source
python -m runners.backfill --org ezobooks --steps 2       # advance N windows this run
python -m runners.backfill --org ezobooks --dry-run       # plan windows, fetch nothing
python -m runners.backfill --org ezobooks --floor-months 6 # override floor
```

### 2.6 systemd (on-prem/EC2)

`lore-backfill.timer` → nightly `02:30`, `Persistent=true`, `RandomizedDelaySec`, fires `python -m runners.backfill --org $ORG`. Separate from `lore-delta.timer` (6h) so backfill never competes with the hot loop. One greppable `STAGE_SUMMARY` line per window via `common/stage_summary.py`.

### 2.7 P3 sub-tasks

| ID | Deliverable | Test |
|----|-------------|------|
| **P3.1** | Gmail `before:` in `build_query` + backward page walk + `fetch_backward` wrapper | unit w/ fake service: bounded window, floor stop, cursor resume |
| **P3.2** | Slack `fetch_backward` wrapper over `iter_channel_events_reverse` (3-tuple reconcile) + `MockSlackFetcher` reverse | unit w/ mock: newest-first, floor stop, checkpoint resume |
| **P3.3** | Jira `fetch_backward` wrapper over `fetch_issues_reverse` + 10k month-subsplit | unit w/ `MockJiraFetcher`: window JQL, overflow subsplit |
| **P3.4** | Shared Bedrock limiter singleton in `pipeline/extraction/rate_limit.py`; `extract_all` uses it | unit: two concurrent extract_all share one limiter |
| **P3.5** | `runners/backfill.py` orchestrator (fetch-parallel → extract → build → advance) | integration: mock fetchers end-to-end on a 2-quarter floor |
| **P3.6** | systemd unit + `STAGE_SUMMARY` observability + docs | manual: dry-run + one live window per source |

---

## Part 3 — Risks & open questions

1. **Gmail `before:`/`after:` both strict** — bounding a quarter needs `after:{from} before:{to}`; both are strict-inequality on `internalDate`. Edge messages exactly on a boundary epoch could be missed/duplicated; mitigated by ±1s window overlap + dedup (safe).
2. **Jira per-project vs combined** — reverse helper is per-project; the live forward loop uses `project in (...)`. Backfill iterates projects. Dynamic per-issue ACL grants must **merge** across windows (not overwrite) — `append_dynamic_issue_scope_rows` is already dedup-safe.
3. **Slack replies leak past floor** — `conversations.replies` pulls the whole thread regardless of floor. Accepted (completeness > strict floor); logged.
4. **Combined vs per-source extraction** — chosen per-source + shared limiter; if cross-source thread interleaving matters for fairness, escalate to `WeightedFairQueue` (already built, unused).
5. **`enable_thinking` extra_body on Bedrock** — open from P1; the live smoke test you're about to run resolves it. If rejected, drop it for `provider==bedrock` in `extract_one`/`correct_one`.
6. **floor_months change after backfill started** — lowering re-plans fewer windows (no-op for built ones); raising adds older pending windows. `ensure_scope` must reconcile the ledger without dropping `built` history.

---

## Build order

`P2.1 → P2.2 → P2.3 → P2.4` (state layer, fully unit-tested in isolation) → `P3.1 ∥ P3.2 ∥ P3.3` (per-connector `fetch_backward`, parallelizable) → `P3.4` (shared limiter) → `P3.5` (orchestrator) → `P3.6` (systemd + observability). Nothing here touches the live Gmail delta loop or `watermark.py`; that migration is P4.
