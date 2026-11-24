# health/ — lore-ingestion

Self-contained, **synchronous**, zero-dependency health module. lore-ingestion has no
web framework, so it exposes endpoints via a tiny stdlib `http.server` shim started by
the poller runner (`runners/chronograph_poller.py`) in a daemon thread, in the same
process as the pollers.

- `GET /health` — **shallow** liveness. 200 if the process is up. No probes.
- `GET /health/deep` — runs every registered check **in parallel** (thread pool),
  aggregated status.

```json
// /health/deep
{
  "status": "healthy | degraded | down",
  "service": "lore-ingestion",
  "checks": {
    "store":             { "status": "...", "detail": "...", "last_check_at": "..." },
    "gitlab_api":        { ... },
    "github_api":        { ... },
    "nvidia_embeddings": { ... },
    "gitlab_poller":     { ... },
    "github_poller":     { ... }
  },
  "timestamp": "..."
}
```

Overall = down if any check is down, else degraded if any is degraded, else healthy.

## The contract

Every check returns `{ status, detail, last_check_at }` (`healthy`|`degraded`|`down`).
A success that returns *unexpected* data is `degraded`. Each check has a **timeout** and
**never raises** — `registry.py` runs each on a thread pool with a per-check timeout and
an outer guard, so one bad check is recorded `down` and never breaks `/health/deep`.

## Checks

| Check | Probes | Notes |
|-------|--------|-------|
| `store` | chronograph store via `make_store()` → `connect()` + a probe `get_commit_count()` | **PostgreSQL + pgvector** (the only backend), via `DATABASE_URL` / `POSTGRES_URI`. Connection fail = down; connected-but-query-fails = degraded. |
| `gitlab_api` | `GET /api/v4/version` with PAT | Not configured (no PAT) = degraded. Auth reject = down. |
| `github_api` | `GET /rate_limit` with PAT | Uses no quota. Not configured = degraded. |
| `nvidia_embeddings` | `GET {base}/models` with key | Token-free; cached 5 min. The poller's embed step uses this. Not configured = degraded (embed step skips). |
| `gitlab_poller` | in-process heartbeat | See staleness below. Disabled-by-config = healthy. |
| `github_poller` | in-process heartbeat | See staleness below. Disabled-by-config = healthy. |

**Intentionally NOT probed: Neo4j.** It's written only by the batch builders
(`builders/unified_builder.py`), not by the poller process hosting this shim — and it
isn't a dependency of this repo (`pyproject.toml`). Probing it here would test a path
this process never runs. Add a check if a builder ever gets its own shim.

## Staleness multiplier — a deliberate per-interval-class choice

The `3×` rule fits tight sub-minute loops (e.g. a 30s thread watcher → 90s
window). It does **not** fit 3-hour scheduled pollers — `3×` would mean a **9-hour** blind
spot before a dead poller shows DEGRADED. Scheduled jobs have predictable timing, so:

| Interval class | Multiplier | Window |
|----------------|-----------|--------|
| Tight (≤1 min, e.g. thread watcher) | 3× | ~90s |
| **Scheduled hours (these 3h pollers)** | **1.5×** | **4.5h** |

At 1.5×, a poller that takes >4.5h to complete a cycle is *already* a real problem
(rate limit, API degraded, dataset exploded) worth flagging, and a genuinely dead poller
surfaces within **one** missed cycle instead of three. Override with
`HEALTH_POLLER_STALE_MULTIPLIER`. Don't go below 1.5× — that starts false-flagging on
minor delays. If real timing data later shows GitHub (large org, rate limits) and GitLab
differ materially, split the multiplier per-poller then.

Heartbeats are written at the **start** of each cycle (in `_run_all_projects` /
`_run_all_repos`), so a job crashing mid-cycle reads as impaired, not freshly healthy.
Before the first cycle (process just started) a missing heartbeat reads as DEGRADED
("starting up") until one interval elapses, then DOWN.

## Heartbeat API

```python
from health import heartbeat
heartbeat("gitlab_poller")   # called at the start of each poll cycle
```

Backed by a **process-local, thread-safe map** — correct because the APScheduler poller
jobs and this shim run in the **same process**. If a poller is ever split into its own
process, back the heartbeat with a shared store (Redis / Postgres table).

## Adding a new check

1. Create `checks/<dep>.py` with `@register("name", timeout=...)` over a sync function
   that reuses existing clients/config and returns a `HealthResult`.
2. Add one import line to `checks/__init__.py`. No other edits.

## Config knobs (env)

| Var | Default | Meaning |
|-----|---------|---------|
| `HEALTH_PORT` | 8090 | shim port |
| `HEALTH_PROBE_TIMEOUT_SECONDS` | 3 | default per-check timeout |
| `HEALTH_LLM_CACHE_TTL_SECONDS` | 300 | NVIDIA probe cache |
| `HEALTH_POLLER_STALE_MULTIPLIER` | 1.5 | poller staleness multiplier |
