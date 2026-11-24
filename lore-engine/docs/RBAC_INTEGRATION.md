> **Historical (as of 2026-06-08)** — superseded by `domain/scopes.py` (the built fail-closed scope gate), `docs/INTEGRATION_ELYSIA.md` and `docs/GRPC_TRANSPORT.md`. "rbac-service" here is now the separate elysia-rbac repo. Kept for context; do not treat as current.

# RBAC Integration — lore-engine ⇄ elysia rbac-service

**Status:** Design / handoff · 2026-06-08
**Audience:** whoever builds the new context-retrieval architecture in `lore-engine`.
**Scope:** what `lore-engine` must do to enforce content-scope RBAC, how it talks to the
elysia `rbac-service`, the exact request/response contracts, and the production-grade
transport options (REST now, gRPC later).

> This is an **instruction doc, not an integration**. Nothing here is wired into the code
> yet — it tells you exactly what to build when you design the new retrieval path.

---

## 0. The one-paragraph version

`lore-engine` does **not** decide who can see what. The elysia `rbac-service` turns a
logged-in user into a flat set of `scope_id` strings (`$userScopes`). `lore-engine`'s only
job is a **set-intersection gate** in every Cypher query: a knowledge node is visible iff
its `scope_ids` array (OR-match) and its `scope_all_ids` array (AND-match) are satisfied by
`$userScopes`. Fail-closed: a node with empty `scope_ids` is invisible to everyone. The
scope set arrives **in the query request body** from the gateway (`core-backend`); you do
not call `rbac-service` on the hot path.

---

## 1. Two orthogonal planes (don't conflate them)

`lore-engine` already has an RBAC plane. The new one is **additional**, not a replacement.

| | Plane A — Department (existing) | Plane B — Content scope (new) |
|---|---|---|
| Question | "Is this user's *role* allowed this department?" | "Was this user a *member/participant* of the container this knowledge came from?" |
| Node property | `node.access_groups` (`['hr','finance',…]`) | `node.scope_ids`, `node.scope_all_ids` |
| User value | `UserContext.access_groups` (from `domain/rbac.py`) | `$userScopes` (from `rbac-service`) |
| Source of truth | `domain/rbac.py` role→groups table | ingestion ACL artifacts → `rbac-service` Postgres |
| Today | OPEN ACCESS (JWT has no role → full access) | not built |

**They compose with AND** — a node must pass *both* planes. Keep the existing
`any(g IN node.access_groups WHERE g IN $user_groups)` clause and add the Plane B clause
next to it. If you are replacing the department plane entirely in the new architecture,
that is a separate decision; this doc only specifies Plane B.

---

## 2. What `lore-engine` must do (the gate)

### 2.1 Node property contract (owned by ingestion, consumed by you)

Every `Decision | Task | Fact | Event` node carries two array properties (LOCKED contract,
see `lore-ingestion/docs/RBAC/SCOPE_FILTER_HANDOFF.md`):

| Property | Meaning | Semantics | Example |
|----------|---------|-----------|---------|
| `scope_ids` | containers/principals the node was seen in | **OR** (any-match) | `['slack:channel:C123','slack:channel:C777']` |
| `scope_all_ids` | stacked restrictions (Jira/GitHub only) | **AND** (all-match) | `['jira:seclevel:10000']` |

You do not write these. You only read them in `WHERE` clauses.

### 2.2 The gate — one predicate, every knowledge query

```cypher
WHERE n.scope_ids IS NOT NULL AND size(n.scope_ids) > 0                  // fail-closed
  AND any(s IN n.scope_ids                       WHERE s IN $userScopes) // OR  bucket
  AND all(s IN coalesce(n.scope_all_ids, [])     WHERE s IN $userScopes) // AND bucket
  AND NOT coalesce(n.deleted, false)                                     // defence-in-depth
```

This must be added to **every** Cypher template that returns knowledge nodes, not just the
vector search. In the current code that is:

- `domain/retrieval/vector.py` → `_VECTOR_SEARCH_CYPHER` (the main entry)
- `domain/retrieval/vector.py` → `_EXPAND_*_CYPHER` (Decision/Task/Fact/Event expanders)
- `domain/retrieval/expansion.py` → thread-sibling + topic expansion
- `domain/retrieval/person.py` → `person_scoped_decisions` and any node-returning query

Any new retrieval path in the new architecture inherits the same rule: **if a Cypher query
can return a knowledge node, it carries the gate.** A single un-gated query is a leak.

### 2.3 Threading `$userScopes` through the call graph

`$userScopes` must reach every Cypher call. Mirror how `user_groups` flows today:

```
api/routes/query.py   →  qs.query(user_scopes=…, user_groups=…)
services/query_service.py  →  _retrieve_full(user_scopes=…)
domain/retrieval/*    →  client.run(cypher, userScopes=user_scopes, …)
```

Put it on `UserContext` so it travels with the request identity:

```python
# core/types.py  (sketch — your call in the new design)
@dataclass
class UserContext:
    user_id: str
    org: str
    role: str = "general"
    access_groups: list[str] = field(default_factory=list)   # Plane A
    scope_ids: list[str] = field(default_factory=list)        # Plane B — OR set
    # scope_all members are matched against this same set; there is ONE user set.
```

There is exactly **one** user set. The OR/AND distinction lives on the *node*, not the user:
`scope_ids` (OR) and `scope_all_ids` (AND) are both intersected against the single
`$userScopes`.

### 2.4 Fail-closed rules (non-negotiable)

1. Missing/empty `$userScopes` ⇒ pass `[]` ⇒ every node fails the OR bucket ⇒ zero results.
   Never "no scopes means see everything."
2. Node with null/empty `scope_ids` ⇒ invisible (the `size() > 0` clause).
3. If the gateway omits the scope field entirely (older client, bug), treat it as `[]`,
   not as "unscoped/open." Log it; do not open.
4. The failure mode is **over-denial**, never over-exposure.

### 2.5 Per-source semantics — Slack/Gmail vs Jira/GitHub

This is the distinction you flagged. It is fully expressed by *which bucket* a node's
scopes land in — your gate code is identical; the data differs per source.

**Slack / Gmail — single-container, OR-only (`scope_all_ids` empty):**

- **Slack:** `scope_ids = ['slack:channel:C…']`, one per channel the node appeared in.
  Membership is resolved **dynamically** by `rbac-service` (a new hire sees channel
  history). The user's `$userScopes` contains every `slack:channel:*` they're a member of.
- **Gmail:** `scope_ids =` the thread's participant person-principals (to/cc/bcc).
  Membership is **static** — the recipient list is fixed at send time, a new hire must NOT
  see old mail. The user's `$userScopes` contains their own person-id/email-principals.
- Both are pure OR: `scope_all_ids` is absent → the `all(...)` clause is trivially true.
- ⚠️ **Gmail `scope_ids` is not populated yet** (ingestion backlog). Until it is, Gmail
  nodes have empty `scope_ids` → invisible on the scoped path. If you need Gmail results
  before that lands, serve Gmail on an explicit **unscoped** path — do not relax the gate.

**Jira / GitHub — container + stacked restriction, OR **and** AND:**

- **Jira:** `scope_ids = ['jira:project:KEY']` (OR, usually one). A security level adds
  `scope_all_ids = ['jira:seclevel:ID']` — required *in addition to* the project. So a
  secured issue is visible iff the user has the project **and** the seclevel in
  `$userScopes`. Project browse alone is not enough.
- **GitHub (same shape):** model the repo as the OR container
  (`scope_ids = ['github:repo:org/name']`) and any stacked restriction (private-repo team
  grant, branch protection, org-visibility tier) as `scope_all_ids`
  (e.g. `['github:team:eng']`). Same gate, no code change — ingestion decides which IDs go
  in which bucket.
- The AND bucket is what enforces "most-restrictive intersection": losing any one stacked
  scope makes the node invisible even if the container matches.

**Bottom line for your code:** there is nothing source-specific in the gate. Slack/Gmail
simply leave `scope_all_ids` empty; Jira/GitHub fill it. Write the predicate once.

---

## 3. How `lore-engine` connects to elysia — contracts

There are two integration patterns. **Pattern A is recommended** and is what the elysia
side already implements.

### Pattern A — scopes ride in the request body (recommended, zero hot-path coupling)

`core-backend` (the gateway) authenticates the user, calls
`rbac-service GET /identity/allowed-scopes` itself, caches the result, and **injects** the
scope arrays into the body it forwards to `lore-engine`. `lore-engine` never calls
`rbac-service` on the query path — it just reads two fields.

**Request `lore-engine` receives** (`POST /api/query`, `POST /api/query/stream`):

```jsonc
{
  "query": "what did we decide about refunds?",
  "thread_id": "th_123",          // optional
  "k": 8,                          // optional, 1..25
  "scope_ids": [                   // NEW — the user's allow-set (LOCKED field name)
    "slack:channel:C123",
    "jira:project:ENG",
    "jira:seclevel:10001",
    "email:ravi@ezobooks.in"
  ],
  "scope_all_ids": []              // NEW — reserved; usually empty (see note)
}
```

- These are the **LOCKED** field names (`scope_ids` / `scope_all_ids`). Do not rename.
- In practice the gateway sends the user's full allow-set as `scope_ids` and an empty
  `scope_all_ids`; the *node's* `scope_all_ids` is what creates AND-conditions. (The body
  carries `scope_all_ids` only if you later decide to let the gateway pass a second
  user-side set — today treat it as empty.)
- **Trust boundary:** `lore-engine` trusts that `core-backend` populated these from a
  validated session. The client must not be able to set them directly — see §3.3.

**What you add in `lore-engine`:**

```python
# api/routes/query.py  (sketch)
class QueryRequest(BaseModel):
    query: str = Field(..., max_length=1000)
    thread_id: str | None = None
    k: int = Field(default=8, ge=1, le=25)
    scope_ids: list[str] = Field(default_factory=list)        # NEW
    scope_all_ids: list[str] = Field(default_factory=list)    # NEW
```

Then pass `req.scope_ids` into `qs.query(...)` and on into the Cypher `userScopes` param.

**Response is unchanged** — same `{answer, confidence, sources, stats, thread_id}`. RBAC
changes *which* sources come back, not the shape.

### Pattern B — `lore-engine` resolves scopes itself (only if you drop the gateway)

If the new architecture has clients hitting `lore-engine` directly (no `core-backend` in
front), then `lore-engine` must fetch the allow-set itself:

```
GET {RBAC_SERVICE_URL}/identity/allowed-scopes
Authorization: Bearer <the user's rbac-service JWT>
→ 200 { "scopeIds": ["slack:channel:C123", ...], "scopeAllIds": [] }
```

Note the response uses camelCase (`scopeIds`); the query body uses snake_case (`scope_ids`).
Map between them at the boundary. This adds a synchronous dependency to every query — cache
it (see §4) and fail-closed if `rbac-service` is unreachable. Prefer Pattern A.

### 3.1 The token / identity bridge

`rbac-service` (Better Auth) mints **EdDSA-signed JWTs** verified via its JWKS endpoint
(`/api/auth/jwks`). `lore-engine` today verifies **HS256** with its own `JWT_SECRET_KEY`
(`api/auth.py`). These are incompatible. Pick one:

1. **(Matches Pattern A)** Keep `lore-engine`'s own JWT for the AI-server hop. `core-backend`
   continues to forward a token `lore-engine` can verify, and the scopes come in the body.
   Smallest change — `lore-engine` auth is untouched, you only add the two body fields.
2. **(If you want a single token)** Have `lore-engine` verify the `rbac-service` JWT via
   JWKS (use `pyjwt[crypto]` + `PyJWKClient` against `/api/auth/jwks`, algorithm `EdDSA`).
   Then the same token flows end-to-end. More work; do this only if you collapse the token
   model across all services.

The JWT carries `{ sub: userId, org: <org-uuid> }`. `org` is the Better Auth org **UUID**;
`rbac-service` maps it to the org **slug** internally for scope resolution. `lore-engine`
should treat `org` as opaque and never parse `sub`/`org`.

### 3.2 Endpoints `lore-engine` cares about on `rbac-service`

| Method | Path | Purpose | Auth |
|--------|------|---------|------|
| `GET` | `/identity/allowed-scopes` | user → `{ scopeIds, scopeAllIds }` | user session/JWT |
| `GET` | `/api/auth/jwks` | public keys to verify rbac JWTs (Pattern B / token-bridge #2) | public |
| `POST` | `/internal/authz/check` | Plane-A authority yes/no (admin routes) | internal shared secret |

`lore-engine` normally needs only `/identity/allowed-scopes` (Pattern B) or nothing on the
hot path (Pattern A).

### 3.3 Securing the scope fields (critical)

In Pattern A the scopes are in a request body, so you must guarantee the **end client cannot
forge them**:

- `lore-engine` must only be reachable from `core-backend` (private network / service mesh /
  mTLS), never exposed publicly. If a client can POST directly to `lore-engine` with a
  hand-written `scope_ids`, RBAC is bypassed entirely.
- Defence in depth: if `lore-engine` is on a shared network, prefer the token-bridge
  (#3.1 option 2) + Pattern B so scopes are derived from a verified token, not trusted from
  the body.

---

## 4. Caching & resilience

- **Pattern A:** caching is the gateway's job (`core-backend` already caches allowed-scopes
  per session, short TTL). `lore-engine` does nothing.
- **Pattern B:** cache `allowed-scopes` in `lore-engine` keyed by `(user_id, org)` with a
  short TTL (30–60s). A membership change propagates within the TTL — acceptable because
  the knowledge graph is immutable and scope changes are rare relative to queries.
- **Deadlines:** give the `allowed-scopes` call a tight timeout (e.g. 500ms). On
  timeout/error → empty scope set → zero results (fail-closed), surfaced as "no results,"
  not a 500, unless you want callers to retry.
- **Never** cache "allow-all." A cache miss or error is `[]`, not open.

---

## 5. Production-grade transport — REST vs gRPC

### 5.1 Recommendation

- **Keep Pattern A (scopes in the body) as the default.** It removes the hot-path RPC
  entirely: the gateway resolves scopes once per session and forwards them. This is the
  lowest-latency, simplest, most resilient option. No gRPC needed for the query path.
- Introduce **gRPC only for the gateway ⇄ rbac-service** authorization calls
  (`allowed-scopes`, `authz/check`) if/when REST/JSON latency or throughput becomes a
  bottleneck. `lore-engine` itself rarely needs it under Pattern A.

### 5.2 If you do go gRPC — service definition

A single Authorization service, owned by `rbac-service`, consumed by `core-backend` (and by
`lore-engine` under Pattern B):

```proto
// authz.proto
syntax = "proto3";
package loremind.authz.v1;

service Authorization {
  // Plane B: user → content-scope allow-set.
  rpc GetAllowedScopes(AllowedScopesRequest) returns (AllowedScopesResponse);
  // Plane A: action permission (admin routes).
  rpc CheckAuthority(CheckAuthorityRequest) returns (CheckAuthorityResponse);
}

message AllowedScopesRequest {
  string user_id = 1;
  string org     = 2;   // org UUID (opaque)
}
message AllowedScopesResponse {
  repeated string scope_ids     = 1;   // LOCKED names preserved on the wire
  repeated string scope_all_ids = 2;
}

message CheckAuthorityRequest {
  string user_id   = 1;
  string org       = 2;
  string resource  = 3;   // e.g. "chronograph"
  string action    = 4;   // e.g. "ingest"
}
message CheckAuthorityResponse {
  bool allowed = 1;
}
```

Implementation notes:

- **Server:** `rbac-service` is Bun/TypeScript — use `@grpc/grpc-js` or **ConnectRPC**
  (Connect lets the same service answer gRPC, gRPC-Web, and JSON, so you don't lose the
  existing REST callers during migration).
- **Client (`lore-engine`, Python):** `grpcio` + generated stubs, or `connect-python`.
- **Security:** mTLS between services (SPIFFE/mesh certs) so identity is mutual; the
  internal shared-secret on `/internal/*` is replaced by the mesh's peer identity.
- **Deadlines:** set a per-call deadline (`grpc.Deadline`, ~500ms). Deadline exceeded →
  fail-closed `[]`.
- **Health/observability:** gRPC health-checking protocol + per-RPC tracing. Wrap the
  `lore-engine` client call in `@observe(name="authz.allowed-scopes")` so it shows up in the
  same trace as the query.
- **Backpressure:** the response is small (a list of strings); no streaming needed. Keep it
  unary.

### 5.3 Migration path (no big bang)

1. Today: REST + Pattern A (scopes in body). Build the Cypher gate. **Ship this first.**
2. If needed: add a ConnectRPC surface to `rbac-service` exposing the same two methods.
   Gateway switches the `allowed-scopes` call from REST to gRPC; body contract to
   `lore-engine` is unchanged.
3. Optional: collapse the token model (JWKS verification in `lore-engine`) and move to
   Pattern B + gRPC only if direct-to-engine clients appear.

---

## 6. Concrete checklist for the new retrieval architecture

**Inside `lore-engine`:**

- [ ] Add `scope_ids` (+ `scope_all_ids`, reserved) to the query request model.
- [ ] Carry the user scope set on `UserContext` (or however identity flows in the new design).
- [ ] Add the §2.2 gate predicate to **every** knowledge-node Cypher template.
- [ ] Thread `userScopes` into every `client.run(...)` that can return a node.
- [ ] Default missing/empty scopes to `[]` and verify zero results (fail-closed test).
- [ ] Add `NOT coalesce(n.deleted,false)` for tombstone defence-in-depth.
- [ ] Decide the token model (§3.1) — keep HS256 + Pattern A (recommended) or JWKS bridge.
- [ ] If Pattern B: add a cached, deadlined `allowed-scopes` client; never cache allow-all.
- [ ] Ensure `lore-engine` is not publicly reachable if trusting body-supplied scopes (§3.3).

**Cross-service (coordinate with elysia + ingestion):**

- [ ] Confirm `core-backend` injects `scope_ids`/`scope_all_ids` into the forwarded body
      (already implemented on the elysia side).
- [ ] Confirm ingestion stamps `scope_ids`/`scope_all_ids` on knowledge nodes
      (Slack/Jira done; Gmail `scope_ids` is a known gap — §2.5).
- [ ] Decide Gmail handling until its `scope_ids` lands (unscoped path vs invisible).

---

## 7. References

- `lore-ingestion/docs/RBAC/SCOPE_FILTER_HANDOFF.md` — **LOCKED** node-property + gate contract.
- `elysia-backend/RBAC_CONSOLIDATION_PLAN.md` — the rbac-service design (Plane A + Plane B).
- `elysia-backend/RBAC_IDENTITY_HANDOFF.md` — how the user→scope allow-set is computed.
- `elysia-backend/rbac-service/src/identity/` — `allowedScopes()` + the `/identity/allowed-scopes` endpoint.
- This repo: `domain/rbac.py` (Plane A, department), `domain/retrieval/*` (where the gate goes),
  `api/auth.py` (token model), `core/types.py` (`UserContext`).
