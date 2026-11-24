# Integration Overview & Audit (lore-engine's view)

Index of how lore-engine connects to everything, and a checklist of loose ends.
Detail docs: `INTEGRATION_ELYSIA.md`, `INTEGRATION_NEO4J.md`,
`INTEGRATION_INGESTION.md`.

## The map

```
                         ┌─────────────────────────────────────────────┐
  browser ──EdDSA JWT──► │ elysia core-backend (public gateway)         │
                         │  verify JWT (rbac JWKS) · fetch allowedScopes │
                         │  · inject scope_ids/scope_all_ids into body   │
                         └───────┬───────────────────────┬──────────────┘
                                 │ HTTP + X-Internal-Auth │ internal secret
                                 ▼                        ▼
                     ┌────────────────────┐      ┌──────────────────┐
                     │ lore-engine (priv.)│      │ elysia-rbac       │
                     │ FastAPI query/AI   │      │ Better Auth + PlnB│
                     └───┬───────────┬────┘      └────────┬─────────┘
                read-only│           │ memory             │ reads artifacts
                         ▼           ▼                     ▼
                   ┌─────────┐  ┌──────────┐     ┌───────────────────┐
                   │ Neo4j   │  │ Postgres │     │ lore-ingestion    │
                   │ (graph) │  │ (engine  │     │ output/ artifacts │
                   │         │  │  memory) │     └─────────┬─────────┘
                   └────▲────┘  └──────────┘               │ writes
                        └──────────── writes ──────────────┘
```

## Connection status by edge

| Edge | Mechanism | Status |
|---|---|---|
| browser → core-backend | EdDSA JWT (Better Auth) | ✅ built (elysia) |
| core-backend → elysia-rbac | JWKS verify + internal-secret authz/scopes | ✅ built (elysia) |
| **core-backend → lore-engine** | HTTP/JSON + SSE, `X-Internal-Auth` secret, scopes in body, identity in forwarded JWT | ✅ built both sides; gRPC path also available (`GRPC_ENABLED`, `docs/GRPC_TRANSPORT.md`) |
| **lore-engine → Neo4j** | bolt/neo4j driver, read-only | ✅ code done; needs prod creds + readiness audit (`INTEGRATION_NEO4J.md`) |
| lore-engine → Postgres (memory) | async SQLAlchemy/asyncpg, alembic | ✅ built (engine) — separate DB from rbac's Postgres |
| ingestion → Neo4j | builder writes graph + index | ✅ built (ingestion); **invariants** in `INTEGRATION_INGESTION.md` §2 |
| ingestion → elysia-rbac | file artifacts (`output/`, local or S3) | ✅ built (elysia reads); S3 source still TODO on elysia |

## Loose ends / things easy to forget

1. **`X-Internal-Auth` is mandatory.** core-backend sends it (HTTP and gRPC).
   lore-engine fails closed: with `INTERNAL_AUTH_SECRET` unset every request
   gets 503 (`api/auth.py`). There is no permissive mode.
2. **Network-isolate lore-engine.** The secret is defense-in-depth; isolation is
   the load-bearing control. Body-injected scopes are forgeable if lore-engine
   is publicly reachable.
3. **Neo4j scope coverage ≥99%** (Gmail/Slack) before trusting results — run
   `scripts/audit_scope_coverage.py`. Fail-closed gates make unscoped nodes
   vanish (looks like a recall bug, isn't).
4. **Embedding-model lockstep.** Query-time and index-time embedding model+dim
   must be identical (both default `nvidia/llama-nemotron-embed-1b-v2`/2048).
   Any `.env` override must change ingestion AND engine together.
5. **`org` claim is a UUID, not a slug** (Better Auth `activeOrganizationId`).
   Harmless on the query path today; map to slug if any feature needs it.
6. **Atlassian `account_id` isn't in the JWT.** Jira person-proximity ranking
   stays off unless the gateway forwards `X-Account-Id`. Optional.
7. **Scopes mapping confirm:** lore-engine treats `allowed = scope_ids ∪
   scope_all_ids`. Confirm a *user's* `scope_all_ids` semantics with rbac.
   — `INTEGRATION_ELYSIA.md` §2.
8. **Two Postgres databases** exist (engine conversation-memory vs rbac IAM).
   Keep them separate; don't point one at the other.
9. **lore-engine pre-prod cleanup (tracked elsewhere):** delete superseded
   `domain/retrieval/{vector,expansion}.py` after the golden-set eval
   (`person.py` is still used by the onboarding/offboarding services);
   the legacy HS256 login path and `admin.py` were removed 2026-07-22.
10. **frontend → core-backend** only. The browser must never reach lore-engine
    or Neo4j directly.

## What is NOT a connection (by design)

- lore-engine ↔ lore-ingestion: **no runtime link** — shared graph contract only.
- lore-engine ↔ GitHub/chronograph: chronograph uses its **own** Postgres+pgvector
  store, not this Neo4j graph; out of scope for the query pipeline.
