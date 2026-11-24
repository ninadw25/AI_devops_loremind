# Ingestion Operations Dashboard

**Date:** 2026-07-23 · **Audience:** Loremind operators (`lore_superadmin`),
never clients. The live, transparent view of a client's ingestion pipeline —
every fetch, every extraction, where work is loaded, and any errors.

## What it shows

A single auto-refreshing console (`/admin/ingestion`) covering the whole
substrate as it flows:

- **Top-line metrics** — overall progress %, extraction rate (conv/min +
  claims/min), backlog with ETA, and which daemons are alive.
- **Pipeline flow** — one card per layer (fetch → normalize → filter →
  conversations → registries → interaction graph → claim extraction → dedup),
  each showing done-count and, where meaningful, a backlog bar (amber = work
  pending, green = caught up). This is the "river" made visible: you can see
  exactly which stage is the bottleneck.
- **Live activity** — the feed of conversations the extractor just turned into
  knowledge, newest first, with the digest and claim count. This is the
  "watch mail become knowledge" view.
- **Knowledge extracted** — claim-kind distribution (Decision / Fact / Task /
  Event / Commitment / Process) as a bar + legend; click a kind to filter the
  recent-claims list below it. Each claim shows grounded (✓) vs flagged (⚠).
- **Mailboxes** — per-mailbox fetch state: messages contributed and how recently
  its cursor advanced.
- **Errors & warnings** — classified failures across all pipeline logs, newest
  first, with an all-clear indicator when there are none.
- **Logs** — raw tail of any pipeline log, level-colored, live-following.

## Architecture (three hops, each in its own trust zone)

```
lore-frontend  IngestionDashboard.tsx
  │  Better Auth JWT (lore_superadmin), polls every 5s
  ▼
lore-admin-backend  /api/v1/pipeline/*   (requireLoreAdmin)
  │  X-Internal-Auth shared secret
  ▼
lore-ingestion control API  /pipeline/*, /logs, /errors, /daemons
  │  read-only SQL + log tails
  ▼
the substrate (Postgres) + output/logs/
```

The dashboard never touches the ingestion DB directly — that lives in the
client data plane. The Python control API is its only gateway, and every one of
these endpoints is **read-only and side-effect free**, so polling them cannot
perturb a running pipeline.

## Backend — `control/observability.py`

Pure reads of each layer's own durable truth; nothing is recomputed:

| Function | Reads | Answers |
|---|---|---|
| `stage_snapshot` | `source_payloads`, `events`, L1/L2/L3 tables | per-stage done/pending across the pipeline |
| `throughput` | `claim_digest.created_at` | extraction rate + ETA (immune to log staleness) |
| `mailboxes` | `fetch_cursors`, `source_payloads.sightings` | per-mailbox fetch state |
| `recent_activity` | `claim_digest` + `entity_artifact` | live feed of extracted conversations |
| `recent_claims` | `claim` | recent claims, filterable by kind |
| `daemon_status` | `pgrep` | which pipeline processes are alive |
| `list_logs` / `read_log` | `output/logs/*.log` | log files + parsed tails |
| `recent_errors` | recent log tails | classified errors/warnings |
| `overview` | all of the above | whole-dashboard snapshot in one round trip |

`pending` on the claims stage uses the exact predicate the extractor's own
queue uses, so the dashboard number and the runner's number can never disagree.

**Two safety properties pinned by `pipeline/tests/test_observability.py`:**
- **Path safety** — `read_log` accepts a bare log *name* only; any traversal
  (`../`, absolute paths) resolves to a basename inside the log dir or is
  rejected. It can never read `/etc/passwd` or the `.env`.
- **Error classification by level, not text** — grepping message bodies for
  "429"/"error" matched hex claim ids and Neo4j batch payloads, burying real
  failures. Structured lines are classified by their parsed log level;
  unparseable lines (tracebacks) match a narrow set of anchored phrases only.
  Old logs (>24h) and giant stale logs are skipped so the scan stays fast.

## Endpoints

Python control API (`control/app.py`, gated by `X-Internal-Auth`):

```
GET /pipeline/{org}              whole-dashboard snapshot
GET /pipeline/{org}/stages       per-stage done/pending
GET /pipeline/{org}/throughput   rate + ETA (?minutes=)
GET /pipeline/{org}/mailboxes    per-mailbox fetch state
GET /pipeline/{org}/activity     live feed (?limit=)
GET /pipeline/{org}/claims       recent claims (?limit=&kind=)
GET /daemons                     process liveness
GET /logs                        available log files
GET /logs/{name}                 tail one log (?lines=)
GET /errors                      errors + warnings (?limit=)
```

lore-admin-backend proxy (`/api/v1/pipeline/*`, `requireLoreAdmin`) forwards
each to the control API with the shared secret, camelCases the payload, and
wraps it in the standard `ok()` envelope.

## Running the chain locally

```bash
# 1. control API (from lore-ingestion/)
setsid nohup ../.venv/bin/uvicorn control.app:app --host 0.0.0.0 --port 8100 \
    >> output/logs/control_api.log 2>&1 &
#    requires CONTROL_INTERNAL_SECRET set in .env

# 2. lore-admin-backend (:4001) — set matching env
#    INGESTION_CONTROL_URL=http://localhost:8100
#    CONTROL_INTERNAL_SECRET=<same secret>

# 3. lore-frontend — VITE_LORE_ADMIN_URL=http://localhost:4001
#    then open /admin → "Live pipeline", or /admin/ingestion directly
```

## Note on credential input

Client credential entry (Gmail key.json, Slack/Jira tokens, domains) is a
separate, already-built path: `elysia-rbac` owns source records
(`src/ingestion/`, credentials stored by reference, never returned), the
frontend `Setup.tsx` + `services/ingestionApi.ts` collect them, and the control
API's `RunSpec` accepts them at run trigger. This dashboard is the *observe*
half; that flow is the *configure* half. They meet at the control API's
`POST /runs`.
