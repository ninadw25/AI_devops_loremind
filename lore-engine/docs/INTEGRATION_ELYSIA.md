# Integration: lore-engine ↔ elysia-backend (core-backend gateway)

**Audience:** the engineer/agent who owns `elysia-backend`. **Status:** built on both
sides (lore-engine 2026-06-12, core-backend since). Engine auth fails closed as of 2026-07-22.

lore-engine is **not standalone** — it is a private AI service that runs behind
the elysia `core-backend` gateway. The gateway authenticates users, computes
content scopes, and forwards requests to lore-engine. This doc is the contract.

---

## 1. Topology & trust model (decided: internal secret + network isolation)

```
frontend ──Bearer(EdDSA JWT)──► core-backend (gateway, public)
                                  │  • verifies user JWT via elysia-rbac JWKS
                                  │  • fetches allowedScopes from elysia-rbac
                                  │  • injects scopes into the body
                                  ▼
                          lore-engine (PRIVATE — not publicly routable)
                            • trusts the gateway via X-Internal-Auth secret
                            • reads identity from the forwarded JWT (no re-verify)
                            • reads content scopes from the body
```

**Why this model:** content scopes are *computed* by elysia-rbac — lore-engine
can't derive them, so it must *trust* whoever sends them. If lore-engine were
publicly reachable, a client could send their own valid JWT + a forged
`scope_ids: [everything]` body and bypass the ACL. So trust rests on **two**
things, both required in production:

1. **Network isolation** — lore-engine is only reachable from core-backend
   (private subnet / k8s ClusterIP / security-group rule). It is never exposed
   to the public internet or the browser.
2. **Internal shared secret** — core-backend sends `X-Internal-Auth: <secret>`;
   lore-engine verifies it with a constant-time compare. This authenticates
   "this request came from the gateway", which is what makes the body-injected
   scopes trustworthy. Same pattern elysia already uses for core ↔ rbac.

We deliberately do **not** re-verify the EdDSA JWT inside lore-engine: the
gateway already did (via JWKS), and the secret + isolation are the real
boundary. lore-engine decodes the forwarded JWT *without* signature
verification, only to read identity claims.

---

## 2. The request contract (LOCKED)

core-backend → lore-engine, for `POST /api/query` and `POST /api/query/stream`:

**Headers**
| Header | Value | Required |
|---|---|---|
| `Authorization` | `Bearer <user EdDSA JWT>` (forwarded as-is) | yes — lore-engine reads `sub`, `org`, optional `account_id` from it |
| `X-Internal-Auth` | `<shared internal secret>` | **yes, always** — lore-engine returns 503 if its secret is unset and 401 if the header is wrong/missing |
| `X-Account-Id` | Atlassian accountId (optional) | optional — enables Jira person-proximity ranking; the Better Auth JWT has no `account_id`, so pass it here if available |
| `Content-Type` | `application/json` | yes |

**Body** (the gateway already injects `scope_ids` / `scope_all_ids`):
```jsonc
{
  "query": "why did we move GST to v2?",
  "thread_id": "th_…",          // null to start a new thread
  "k": 8,
  "scope_ids":     ["slack:channel:C…", "jira:project:OPS"],  // LOCKED name (OR-set the user holds)
  "scope_all_ids": []                                          // LOCKED name (optional)
}
```

**Identity is NOT taken from the body** — lore-engine reads `user_id`/`org` from
the forwarded JWT (`sub`/`org`). Do not put `user_id` in the body; it is ignored.

**Response:** unchanged. `/api/query` returns `{answer, confidence, sources,
stats, thread_id}`. `/api/query/stream` is SSE (`thread_id`, `sources`,
`token`*, `done` events) — your existing `AIServer.stream()` passthrough works.

### Scope semantics note (please confirm on the rbac side)

lore-engine's Cypher gate uses a **single** allowed-scope list for both checks:
```
node visible ⟺ size(node.scope_ids) > 0
            AND any(s IN node.scope_ids     WHERE s IN $allowed)
            AND all(s IN node.scope_all_ids WHERE s IN $allowed)
```
It builds `$allowed = scope_ids ∪ scope_all_ids` (deduped) from the body. So for
a **user**, `scope_ids` should be every scope they hold; `scope_all_ids` is
treated as additional held scopes (today it's expected empty). If elysia-rbac
intends a different meaning for a *user's* `scope_all_ids`, flag it — this is the
one place the semantics aren't 100% pinned.

---

## 3. What elysia ALREADY has (verified — no change needed)

- `core-backend/src/ai-server/client.ts` — `AIServer.forward/json/stream`, env
  `AI_SERVER_URL`, forwards Bearer + body, SSE passthrough. ✅
- `core-backend/src/query/controller.ts` — fetches `allowedScopes` from
  `elysia-rbac/identity/allowed-scopes` (30s TTL cache) and injects
  `{...body, scope_ids, scope_all_ids}`. ✅
- `elysia-rbac` — Better Auth EdDSA JWT (15 min), JWKS at `/api/auth/jwks`;
  `allowedScopes(userId, org)` Plane-B computation; fail-closed. ✅
- core ↔ rbac internal shared secret with constant-time compare. ✅

## 4. What elysia does (done)

1. **Sends `X-Internal-Auth`** from `AI_SERVER_INTERNAL_SECRET` (must equal
   lore-engine's `INTERNAL_AUTH_SECRET`), on both the HTTP client
   (`src/ai-server/client.ts`) and the gRPC client (metadata `x-internal-auth`).
2. **Forwards the Atlassian accountId** (gRPC metadata `x-account-id`) when it
   has it — enables Jira person-proximity ranking. The HTTP client does not send
   `X-Account-Id` today, so on HTTP it's just off.
3. **Deploy lore-engine network-isolated** (not publicly routable). This is the
   load-bearing security control; the secret is defense-in-depth.

The threads/gitlab/chronograph passthroughs get the same gateway trust.

## 5. What lore-engine did (this repo, done)

- `api/auth.py::require_gateway` — constant-time `X-Internal-Auth` check
  (fail-closed: 503 when `INTERNAL_AUTH_SECRET` unset, no bypass) + identity from the
  forwarded JWT (decoded, not re-verified). Replaces per-route JWT verification
  on `/api/query*` and `/api/threads*`.
- `api/routes/query.py::QueryRequest` — accepts `scope_ids` / `scope_all_ids`;
  `allowed_scopes` = their dedup union → passed to the fail-closed Cypher gate.
- `core/settings.py` — `INTERNAL_AUTH_SECRET` (`internal_auth_secret`).

## 6. Env vars to coordinate

| lore-engine | elysia core-backend | must match? |
|---|---|---|
| `INTERNAL_AUTH_SECRET` | `AI_SERVER_INTERNAL_SECRET` | **yes** |
| (serves on `PORT`, default 8000) | `AI_SERVER_URL=http://lore-engine:8000` | URL points at it |

## 7. Cutover checklist

1. Deploy lore-engine privately (no public ingress). It refuses every request
   (503, logs `internal_auth_unconfigured`) until `INTERNAL_AUTH_SECRET` is set.
2. Set the **same** secret as core-backend's `AI_SERVER_INTERNAL_SECRET`; restart.
3. Verify: a direct request to lore-engine without the header → 401; a request
   through core-backend → 200. Confirm an out-of-scope user gets no nodes
   (fail-closed) and an in-scope user does.

> Prerequisite for *meaningful* results (separate from auth): the live Neo4j
> graph must have `scope_ids` populated on Gmail/Slack nodes — see
> `INTEGRATION_NEO4J.md` and `scripts/audit_scope_coverage.py`. With fail-closed
> gates, unscoped nodes are invisible, which looks like a recall bug.

## 8. Known mismatch to watch: `org` is a UUID, not a slug

The Better Auth JWT `org` claim is `activeOrganizationId` (a UUID), not the org
slug (`ezobooks`). lore-engine currently uses `org` only for logging/memory
correlation on this path, so it's harmless today — but if any feature keys on a
human org slug, the gateway must map UUID→slug (or include both). Flagged so it
doesn't bite later.

## 9. Transport: gRPC for the core query path (HTTP retained as rollback)

> **Updated LOR-111** — this reverses the prior "stay on HTTP/JSON + SSE,
gRPC deferred" decision for the gateway → engine hop. The earlier rationale
> (framing overhead is marginal vs retrieval/LLM latency) was sound on latency
> grounds but missed the real defects the gRPC move fixes: an untyped
> end-to-end contract, scope-injection safety resting on object-spread ordering,
> every engine error collapsing to a gateway 500 with the upstream body leaked,
> no deadlines, silent mid-stream SSE death, and no request-id propagation. Full
> design + proto sketch + decision log: [`GRPC_TRANSPORT.md`](./GRPC_TRANSPORT.md).

The gateway (`elysia-backend/apps/core-backend`) now talks to lore-engine over
**gRPC for `Query`, `QueryStream`, and the three `Threads` operations**
(`QueryService` / `ThreadsService` in `proto/lore/engine/v1/`). Everything else
(GitLab/chronograph routes, the unconsumed experts/continuity/offboarding/
onboarding/people endpoints) stays on HTTP until a gateway controller actually
consumes them — see `GRPC_TRANSPORT.md` §6 (open decisions) for the rationale.

Both sides run side by side; which one the gateway uses is a flag:

```
AI_SERVER_TRANSPORT=http   # default — the full rollback path (today's behavior)
AI_SERVER_TRANSPORT=grpc   # Query/QueryStream/Threads over ConnectRPC → grpcio
```

`http` is today's behavior unchanged. `grpc` is invisible to the frontend:
the gateway translates the gRPC stream back to JSON-identical SSE events the UI
parses (`thread_id` → `sources` → `token`* → `done`), and normalizes unary /
threads responses to the same JSON shapes.

Hardening that ships with the move (and also benefits the HTTP path — see
`GRPC_TRANSPORT.md` §5):

- **Typed contract.** Protobuf is the single source of truth; the gateway no
  longer parses engine responses as `unknown`.
- **Structural scope safety.** The client-facing request schema carries *no*
  scope fields at all — not just unset by convention, structurally absent from
  every `.proto` message. Scopes travel in **gRPC call metadata**
  (`x-scope-ids` / `x-scope-all-ids` / `x-super-admin`), the same channel as
  identity (`x-user-id` / `x-org` / ...), populated exclusively by the gateway
  after it authenticates the caller. (This is a deliberate, approved deviation
  from the `ScopeContext`-in-message design in `GRPC_TRANSPORT.md` §4 — it is
  stronger: a caller cannot forge scopes on either transport.)
- **Fail-closed auth.** The gRPC path refuses to serve if
  `INTERNAL_AUTH_SECRET` is unset (the HTTP path also fails closed since 2026-07-22 — 503 when unset).
- **Status fidelity.** Engine `LoreError`s surface at the gateway with the
  correct HTTP status (401/403/404/503…), not a blanket 500, and upstream bodies
  are no longer leaked.
- **Deadlines.** Unary calls carry a deadline (`AI_SERVER_UNARY_TIMEOUT_MS`);
  a hung engine call fails (504) instead of hanging the client.
- **Correlation.** The gateway's `x-request-id` flows into the engine's logging
  contextvar, so every engine log line for a request carries the gateway's id.

Engine settings: `GRPC_ENABLED` (off by default), `GRPC_HOST`, `GRPC_PORT`
(default 50051), `GRPC_MAX_WORKERS`. The gRPC server shares the FastAPI app's
services (tenant-routed QueryService / MemoryService) — see `api/main.py`
lifespan and the `lore_engine_grpc/` package.
