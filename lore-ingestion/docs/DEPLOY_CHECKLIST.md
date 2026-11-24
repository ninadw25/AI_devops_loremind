# Deploy Checklist — lore-ingestion

For deploying ingestion to a **shared host** (staging/prod, or anywhere more than
one client's data can be reached). Local dev is the README Quickstart; nothing
here applies to a laptop.

**Status (2026-07-15):** ingestion is *not* in the Terraform service list
(`infra/services.tf` deploys core-backend, auth-service (elysia-rbac), lore-engine,
client-admin-backend, lore-admin-backend, loredash — not this repo). So this
checklist is written ahead of that deploy, not derived from one. When ingestion
gets a real deploy target, fold these into whatever sets its environment.

---

## 1. Turn on the per-org connection guard — REQUIRED on any multi-client host

```
REQUIRE_REGISTRY_CONNECTION=true
```

**Default is `false`, so this does nothing until you set it.** It is dormant
today by choice: with no multi-client ingestion host running yet, there is
nothing for it to protect, and defaulting it on would break the documented CLI
workflow (the README Quickstart, and every command in `CLAUDE.md`) for everyone
locally.

**What it prevents:** this process is org-agnostic. Normally elysia-rbac resolves
the right client's Neo4j from the registry and injects it per run. But a hand-run
`python -m builders.unified_builder --org clientA` skips all of that and uses
whatever `NEO4J_URI` is in the host's environment. If that points at clientB,
clientA's knowledge is written into clientB's graph — silently, and the run looks
clean. Neither client sees an error; clientB can then read clientA's data.

**What it does:** requires each run to carry the control plane's
`LORE_CONNECTION_ORG` stamp, naming the org being built. No stamp, or a stamp
naming a different org → the run refuses to connect
(`common/config.py:assert_connection_for_org`).

**Consequence — start runs through the control API, not the CLI.** With this on,
`POST /runs` on the ingestion control API is the only way a run gets a stamp.
Direct CLI invocation on that host will (correctly) refuse. Cypher-only runs
(`--skip-upload`) touch no client database and are not gated.

**Known limit:** the stamp is an env var, so it stops accidents — a mis-set
`.env` — not a determined operator who sets it by hand. That is the intended bar.

Verify after deploy — this must fail:

```bash
# On the host, with the guard on. Expected: ConnectionRoutingError, no connection.
python -m builders.unified_builder --org some-client --upload-only
```

## 2. Point the control plane at this host

elysia-rbac reaches ingestion via `INGESTION_CONTROL_URL` (default
`http://localhost:8100`) and authenticates with a shared secret. Both must be set
on the elysia-rbac side, and the secret must match this host's.

## 3. Confirm the registry has an active connection row for every client

`RegistryService.resolve()` fails closed when a client's `org_connection` row is
missing or its `status` is not `active` — the run fails rather than falling back
to a default database. That is correct, and it means a new client cannot ingest
until its row exists and is active.

## 4. Do not rely on this repo's `.env` for client connections

On a multi-client host, `NEO4J_URI`/`NEO4J_PASSWORD` in `.env` are a fallback the
guard exists to reject. They should be absent or point at nothing real. The
per-run values come from the registry via the control plane.

---

## Not covered here

Secrets delivery, container/host provisioning, and log shipping — ingestion has
no deploy target yet, so there is nothing to document. Revisit when it lands in
`infra/services.tf` or wherever it ends up running.
