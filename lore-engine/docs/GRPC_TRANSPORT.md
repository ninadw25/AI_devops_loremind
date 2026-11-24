# gRPC Transport: elysia-backend (gateway) ⇄ lore-engine

**Status:** Proposal / ticket-prep — verified against code 2026-07-16
(gateway `3afe35d`, engine `1c41ac6`)
**Supersedes:** `INTEGRATION_ELYSIA.md` §9 ("stay on HTTP/JSON + SSE — gRPC deferred"). This doc reverses that decision; the ticket that comes out of this must say so explicitly and update §9.

---

> **As-built update (LOR-111, shipped 2026-07-19).** This document is the
> original *proposal*. Two intentional deviations from the §4 sketch shipped and
> are approved:
>
> 1. **Scopes travel in gRPC metadata, not a `ScopeContext` message field.**
>    §4 sketched `ScopeContext scopes = 4;` on `QueryRequest`; the shipped
>    `proto/lore/engine/v1/query.proto` carries **no** scope field on *any*
>    request message. Scopes ride as `x-scope-ids` / `x-scope-all-ids` /
>    `x-super-admin` call metadata (JSON-array / bool encoded), the same channel
>    as identity (`x-user-id` / `x-org` / ...). This is *stronger* than the
>    sketch: "no scope fields on any client-facing request" is a structural fact
>    of the schema, not a "server-populates-this-field" convention. See
>    `lore_engine_grpc/context.py` (`ScopeContext.from_metadata`).
> 2. **The SSE bridge is JSON-identical, not byte-identical.** The event
>    *framing* (`data: {json}\n\n`, ordering `thread_id → sources → token* →
>    done`) is preserved exactly, but the JSON object inside is re-serialized by
>    each side, so whitespace differs (Python `json.dumps` emits spaces; the
>    gateway's `JSON.stringify` does not). That is JSON-identical — what the
>    frontend parses — which is the actual guarantee. Read "byte-identical" /
>    "bit-for-bit" below as "JSON-identical".
>
> The proto sketch in §4 and the acceptance criteria in §8 are kept as the
> historical proposal; the source of truth for what shipped is the `.proto`
> files + `lore_engine_grpc/` + `src/ai-server/grpc/`.

---

## 1. Current state (ground truth)

> Pre-build snapshot (2026-07-16): "no gRPC exists" and "auth silently OFF" below describe the code before LOR-111. gRPC now ships (`lore_engine_grpc/`, `proto/lore/engine/v1/`) and auth fails closed on both transports.

### Transport today
- **Protocol:** HTTP/1.1 JSON + SSE. No gRPC/protobuf artifacts exist anywhere in the workspace — zero `.proto` files, zero declared grpc deps.
- **Single choke point on the gateway:** every engine call routes through `AIServer` (`elysia-backend/apps/core-backend/src/ai-server/client.ts`, 65 lines). Three methods: `forward()` (raw Bun `fetch`), `json<T>()`, `stream()` (zero-copy SSE body passthrough).
- **Single trust dependency on the engine:** `require_gateway` (`lore-engine/api/auth.py:47-92`), attached per-router in `api/main.py:214-249`.

### Auth between the services
| Mechanism | Detail |
|---|---|
| Internal secret | Gateway sends `X-Internal-Auth` on every call (`client.ts:36`, env `AI_SERVER_INTERNAL_SECRET`). Engine checks with constant-time compare (`auth.py:65-68`, env `INTERNAL_AUTH_SECRET`). **If the engine env var is empty, enforcement is silently OFF** (warning log only, `auth.py:69-74`). |
| User identity | Caller's EdDSA JWT (JWKS-verified at the gateway edge) is forwarded in `Authorization: Bearer`; engine decodes it **without signature verification** (`auth.py:80`) — trust model = internal secret + network isolation, by design. |
| Content scopes | Never in the JWT. Gateway fetches `allowedScopes` from elysia-rbac (30s TTL cache) and injects `scope_ids` / `scope_all_ids` / `super_admin` into the request **body** via spread ordering (`query/controller.ts:86,100`). |

### Endpoint inventory (what a gRPC service definition must cover)

Actively consumed by the gateway:

| Engine endpoint | Method | Notes |
|---|---|---|
| `/api/query` | POST | blocking query |
| `/api/query/stream` | POST | SSE — the critical path |
| `/api/threads`, `/api/threads/{id}/turns`, `/api/threads/{id}` | GET/GET/DELETE | conversation memory |
| `/api/gitlab/*` | ~24 GET + POST `/chat` | wildcard path segments (`code/file/*` etc.) — awkward 1:1 RPC mapping |
| `/api/chronograph/stats/{project_id}` | GET | admin-guarded |
| `/api/health` | GET | 2s-timeout probe |

Exposed behind `require_gateway` but not yet proxied (built for the gateway, unconsumed — must decide whether they enter the proto now): `/api/experts`, `/api/continuity/radar`, `/api/offboarding/report`, `/api/onboarding/path`, `/api/people/search`.

### Streaming contract (must be preserved bit-for-bit in a port)
Wire format today: `data: {json}\n\n`, no `event:`/`id:` fields, no heartbeat. Event ordering is a hard contract the frontend relies on (sources before tokens for citation rendering):

1. `{"type":"thread_id","data":"<id>"}` — emitted first by the route (`query.py:171`)
2. `{"type":"sources","data":[...]}` — exactly once, after retrieval, before any token
3. `{"type":"token","data":"<chunk>"}` — repeated (reasoning-kind chunks filtered out)
4. `{"type":"done","data":{"confidence":"high|medium|low"}}`
5. `{"type":"error","data":"<str(exc)>"}` — in-band only; HTTP status is already 200

Side effects in the route's `finally`: turn persistence (`memory.save_turn`, status `completed|failed`) and super-admin audit run **regardless of client disconnect** (`query.py:191-217`). A gRPC servicer must replicate this on RPC cancellation.

Request/response models: `QueryRequest` (`query.py:34-61`) — `query`, `thread_id?`, `k`, plus gateway-injected `scope_ids`/`scope_all_ids`/`super_admin` (names LOCKED per SCOPE_FILTER_HANDOFF). `QueryResponse` — `answer`, `confidence`, `sources[]`, `stats`, `thread_id`. Scoped endpoints share the `GatewayScopedRequest` base (`api/models.py:20-37`).

---

## 2. Why move (the actual problems gRPC solves)

These are verified defects in the current transport, not speculation:

1. **Untyped contract end-to-end.** Gateway calls `json<T=unknown>()` — no call site ever supplies `T`. Engine Pydantic models and gateway typebox models are hand-maintained twins; the real contract lives only in a markdown doc (`INTEGRATION_ELYSIA.md`). Protobuf makes the contract compiled and single-sourced. This is the strongest argument for the migration.
2. **Scope injection safety rests on spread ordering.** `query/model.ts:9-14` *accepts* client-supplied `scope_ids`/`super_admin`; the only thing preventing a client from setting them is `{...body, ...scopes}` ordering in two places. A proto message with server-populated fields (separate from the client-facing message) eliminates this bug class.
3. **Status-code destruction.** Every engine non-2xx becomes gateway HTTP 500 with the raw upstream body leaked in `detail` (`client.ts:41-44` + root `onError`). gRPC status codes + typed error details fix this structurally.
4. **No timeouts, retries, or deadlines** on the engine path (only the 2s health probe has one). A hung engine call hangs the client forever. gRPC deadlines are first-class.
5. **SSE fragility.** No heartbeat, no mid-stream error signaling at the transport level, upstream death = silent truncation. gRPC server-streaming has framing, trailers, and cancellation built in.
6. **No request-id propagation.** Gateway never sends `X-Request-Id`; engine mints its own — cross-service log correlation is broken today. Designed into gRPC metadata from day one. (Also a trivial HTTP fix — do it regardless, see §5.)

---

## 3. Constraints & prior art

- **Bun + `@grpc/grpc-js` is historically shaky** (Node-compat HTTP/2 client issues). The repo's own prior design — `RBAC_INTEGRATION.md` §5 (`:279-350`), the only concrete gRPC design in the workspace, written for gateway⇄rbac — already steers toward **ConnectRPC**: runs over fetch (HTTP/1.1 + 2), coexists with Elysia, keeps the current `Response`-passthrough model for streaming, and speaks the gRPC wire protocol to a `grpcio`/`connect-python` server. Recommendation: **ConnectRPC on the Bun side, and align the gateway⇄engine choice with the eventual gateway⇄rbac transport** so the gateway doesn't grow two RPC stacks.
- **Python side readiness:** Python 3.12, fully async FastAPI stack. `grpcio 1.80.0` + `protobuf 6.33.6` are present in the venv **transitively only** (via langfuse → opentelemetry) — neither is declared in `lore-engine/pyproject.toml`. Must be added explicitly; pin protobuf carefully against the opentelemetry constraint.
- **The engine pipeline is synchronous.** `QueryService.query/query_stream` are sync generators iterated *inside* the async event loop (`query.py:108,174`) — every query blocks the uvicorn loop today. A `grpc.aio` servicer inherits this unless the pipeline goes async or runs in an executor. This is pre-existing debt the migration must not paper over — decide up front whether async-ification is in scope (see §6).
- **Browser hop stays SSE.** The gateway does zero SSE parsing today (pipes `res.body` through). Frontend↔gateway remains SSE/fetch; only the gateway↔engine hop changes. The gateway therefore gains a translation responsibility: gRPC stream → SSE. With ConnectRPC's fetch-based streaming this is a thin async-iterator → SSE-formatter loop.
- **Wildcard GitLab routes** (`code/file/*`, `code/trace/*`, ...) don't map 1:1 to RPC methods. Options: one `GitLabQuery(path, params)` passthrough RPC (lazy, preserves today's behavior) vs. per-method RPCs (typed, ~25 methods). Given chronograph is being rebuilt anyway (decision 2026-07-15: "rebuilt, not patched"), **do not invest in per-method protos for GitLab/chronograph now** — passthrough RPC or leave those routes on HTTP until the rebuild lands.

---

## 4. Proposed design

### Proto sketch (core service — the part worth doing first)

```proto
syntax = "proto3";
package lore.engine.v1;

service QueryService {
  rpc Query(QueryRequest) returns (QueryResponse);
  rpc QueryStream(QueryRequest) returns (stream QueryStreamEvent);
}

service ThreadsService {
  rpc ListThreads(ListThreadsRequest) returns (ListThreadsResponse);
  rpc ListTurns(ListTurnsRequest) returns (ListTurnsResponse);
  rpc DeleteThread(DeleteThreadRequest) returns (google.protobuf.Empty);
}

message ScopeContext {           // server-populated by the gateway, never client-supplied
  repeated string scope_ids = 1;
  repeated string scope_all_ids = 2;
  bool super_admin = 3;
}

message QueryRequest {
  string query = 1;              // max 1000 chars, enforce in servicer
  optional string thread_id = 2;
  int32 k = 3;                   // default 8, clamp 1-25
  ScopeContext scopes = 4;
}

message QueryStreamEvent {
  oneof event {                  // ordering contract: thread_id → sources → token* → done
    string thread_id = 1;
    SourceList sources = 2;
    string token = 3;
    Done done = 4;
    StreamError error = 5;       // structured, replaces stringly-typed str(exc)
  }
}
```

Identity (`sub`, `org`, `role`, `account_id`) rides in **gRPC metadata** alongside `x-internal-auth` and `x-request-id` — same trust model as today (gateway-verified, engine-trusted), now stated explicitly per call. `org` keys tenant routing (`dependencies.py:50`), so it must be in metadata, not the message.

### Engine servicer requirements (parity checklist)
- Interceptor: internal-secret check (constant-time), fail-closed — **remove the empty-secret-disables-auth behavior in the gRPC path**; refuse to serve without a secret configured.
- Interceptor: `x-request-id` metadata → `set_correlation()` contextvar (`core/logging.py`) so JSON log correlation works unchanged.
- `LoreError` → gRPC status mapping (mirror `api/errors.py:26-47`: 401→UNAUTHENTICATED, 403→PERMISSION_DENIED, 404→NOT_FOUND, 503→UNAVAILABLE).
- Stream cancellation → run the `finally` side effects (save_turn, audit) via `context.add_done_callback`.
- Langfuse `@observe` spans are transport-agnostic and survive unchanged — and add the missing span on `query_stream` while in there (`query_service.py:238` has none today; the primary prod path is the less-observed one).

### Gateway client requirements
- Generated ConnectRPC client replaces `AIServer.json/stream` for ported RPCs; `AIServer` stays for unported routes during migration.
- gRPC-stream → SSE translation preserving the exact event JSON the frontend parses today (zero frontend changes).
- Deadlines on every call (e.g. 30s unary query, none/long on stream but with idle-timeout), map DEADLINE_EXCEEDED → 504.
- Scope fetch/cache/injection logic unchanged — it just populates `ScopeContext` instead of spreading into a body.

---

## 5. Do-regardless fixes (worth their own small ticket, ship before/with phase 1)

These are one-line-ish HTTP fixes that shouldn't wait for gRPC:
1. Gateway sends `X-Request-Id` on every `AIServer.forward()` call — fixes cross-service log correlation today.
2. Root `onError` maps upstream 401/403/404 to matching gateway statuses instead of blanket 500, and stops leaking upstream body text.
3. `AbortSignal.timeout` on non-streaming engine calls (health already has one).

## 6. Open decisions (resolve before implementation)

| # | Decision | Options / recommendation |
|---|---|---|
| 1 | RPC framework | **ConnectRPC (recommended — matches RBAC_INTEGRATION.md §5, Bun-safe)** vs raw `@grpc/grpc-js` + `grpc.aio` |
| 2 | Scope of first cut | **Query + QueryStream + Threads only (recommended)** vs everything incl. GitLab/chronograph (chronograph is being rebuilt — don't proto it now) |
| 3 | Sync pipeline | Port as-is with executor (inherits event-loop debt) vs async-ify `QueryService` first. Recommendation: executor in phase 1, async-ification as its own ticket — don't couple the two. |
| 4 | Unconsumed endpoints (experts/continuity/offboarding/onboarding/people) | Include in proto now vs when a gateway controller actually consumes them (recommended: when consumed) |
| 5 | Dual-running window | Serve HTTP + gRPC side by side with a gateway feature flag (`AI_SERVER_TRANSPORT=http|grpc`) — recommended; enables per-env rollout and instant rollback |
| 6 | Proto repo location | Shared `proto/` — but the workspace is 5 independent repos with no shared package registry. Options: proto files live in lore-engine + codegen committed into gateway; or a small `lore-proto` repo. Must be decided before codegen. |

## 7. Suggested phasing (ticket breakdown)

- **Phase 0 — HTTP hardening (small, independent):** §5 items. No proto work.
- **Phase 1 — Contract + engine servicer:** proto definitions (Query/QueryStream/Threads + ScopeContext), grpcio deps declared, servicer with interceptors (auth, correlation, error mapping), stream-cancellation side effects, parity tests against the HTTP path (same inputs → same events in same order).
- **Phase 2 — Gateway client + flag:** ConnectRPC codegen, gRPC→SSE translation, deadlines, `AI_SERVER_TRANSPORT` flag, scope injection into ScopeContext. Frontend untouched.
- **Phase 3 — Rollout + retire:** flip flag per env, monitor (request-id correlation now works, so this is finally observable), delete `AIServer.json/stream` for ported routes, update `INTEGRATION_ELYSIA.md` §9.

Explicitly **out of scope:** GitLab/chronograph routes (await chronograph rebuild), gateway⇄rbac gRPC (separate design, `RBAC_INTEGRATION.md` §5), async-ification of QueryService (own ticket), frontend transport changes (none needed).

## 8. Acceptance criteria (for the ticket)

- [ ] `QueryStream` over gRPC produces JSON-identical SSE output at the gateway edge vs the HTTP path (same event types, same ordering, same JSON shapes; framing `data: {json}\n\n` preserved — but not byte-identical, since each side re-serializes the JSON object).
- [ ] Scope fields are structurally impossible to set from the client-facing request (gateway populates `ScopeContext`; client message has no such fields).
- [ ] Engine refuses gRPC connections when internal secret is unset (no silent-permissive mode).
- [ ] Client disconnect mid-stream still persists the turn (`status="failed"`) and writes the super-admin audit row.
- [ ] `x-request-id` flows gateway→engine and appears on every engine log line for the request.
- [ ] Engine `LoreError`s surface at the gateway with correct HTTP statuses (not 500).
- [ ] Deadlines enforced: a hung engine call fails the gateway request within the configured deadline.
- [ ] `AI_SERVER_TRANSPORT=http` fully restores today's behavior (rollback path proven).

## 9. Key file reference

| Concern | File |
|---|---|
| Gateway HTTP client (all engine calls) | `elysia-backend/apps/core-backend/src/ai-server/client.ts` |
| Scope fetch + injection | `elysia-backend/apps/core-backend/src/query/controller.ts:43-100` |
| Engine gateway auth | `lore-engine/api/auth.py:47-92` |
| SSE route + finally side effects | `lore-engine/api/routes/query.py:146-223` |
| Stream event generator | `lore-engine/services/query_service.py:238-277` |
| Correlation middleware | `lore-engine/api/middleware.py:21-29` |
| Error mapping | `lore-engine/api/errors.py:26-47` |
| Tenant routing DI | `lore-engine/api/dependencies.py:31-57` |
| Prior transport decision (to supersede) | `lore-engine/docs/INTEGRATION_ELYSIA.md` §9 |
| Prior gRPC design (rbac, framework guidance) | `lore-engine/docs/RBAC_INTEGRATION.md` §5 |
