# Content Super-Admin — "Read Everything" Access

**Status:** Implemented · 2026-07-03
**Repos touched:** `elysia-rbac`, `elysia-backend/core-backend`, `lore-engine`
**Related:** `docs/ACL_MVP_PLAN.md` (Plane B), `docs/RBAC.md` (Plane A), `docs/RBAC_CONSOLIDATION_PLAN.md`, `lore-ingestion/docs/RBAC/SCOPE_FILTER_HANDOFF.md` (LOCKED gate contract)

---

## 1. What this is

A **legitimate, per-user, tenant-bounded, audited** way for a designated admin to read
*all* organizational knowledge (Gmail / Slack / Outlook / Teams / Jira) — bypassing the
fail-closed content-scope ACL (Plane B) that normally restricts each user to the
containers/threads they belong to.

It is driven by a normal RBAC authority (`content.read_all`), resolved by elysia-rbac,
forwarded by the gateway, and honored by the lore-engine query gate. Since 2026-07-15 it
is the **only** way past the gate — the global `QUERY_OPEN_ACCESS` switch is gone (§6).

> This grants the power to read private 1:1 emails and DMs across the tenant. It is
> surveillance-sensitive by nature. Treat it like break-glass: grant sparingly, prefer
> time-boxed assignments, and keep the audit trail.

---

## 2. The two planes (recap)

Loremind has two orthogonal access-control planes:

| Plane | Question | Mechanism |
|-------|----------|-----------|
| **A — IAM authority** | "May this user *perform* this action?" | `resource.action` authorities → roles → users/groups, deny-wins (`elysia-rbac/src/rbac/engine.ts`) |
| **B — Content scope** | "Which *knowledge nodes* may this user *see*?" | `scope_ids` / `scope_all_ids` on Neo4j nodes ∩ the caller's `allowedScopes()` set, fail-closed |

The content super-admin is the **one place the two planes meet**: a **Plane A** authority
(`content.read_all`) flips a **Plane B** bypass.

---

## 3. End-to-end flow

```
Browser ──JWT──▶ core-backend  POST /api/query
   │  1. verify JWT (elysia-rbac JWKS)
   │  2. GET elysia-rbac /identity/allowed-scopes
   │        → { scopeIds, scopeAllIds, superAdmin }        ← superAdmin = can(user, "content.read_all")
   │  3. forward body to lore-engine, injecting:
   │        { ...query, scope_ids, scope_all_ids, super_admin }   ← super_admin is SERVER-set, never from client
   ▼
lore-engine  POST /api/query   (require_gateway: trusted via X-Internal-Auth)
   │  4. QueryService.query(..., super_admin=req.super_admin)
   │        → UserScopes(allowed, super_admin=True)
   │  5. scope_gate() Cypher:  ( $super_admin OR ( <normal fail-closed predicate> ) )
   │        → every node visible for THIS request only
   ▼
answer over ALL tenant knowledge (bounded to the caller's active org)
```

Tenant boundary: lore-engine is per-org routed (`TenantRegistry.neo4j_for(org)`), and the
Plane A check is scoped to the caller's **active organization**. A Content Admin in org A
gets no bypass in org B.

---

## 4. The authority: `content.read_all`

Defined in the permission catalogue (`elysia-rbac/src/db/seed.ts`) as **privileged**:

```ts
["content.read_all", "Read ALL organizational knowledge, bypassing content ACL (Plane B super-admin)", true],
```

Assign it via a role. The seed ships two ways to hold it:

- **`Content Admin`** system role — grants `content.read_all` plus the basic query
  authorities (`query.ask`, `query.cross_source`, `map.view`, `citation.view_source`).
  Give this to a customer's compliance/owner user who must see all knowledge **without**
  the full action-wildcard.
- **`Company Admin`** (`*.*`) — the action wildcard matches `content.read_all` too
  (`authorityMatches("*.*", "content", "read_all") === true`), so a Company Admin is also a
  content super-admin.

Grant/revoke is a normal RBAC operation (roles, groups, direct `user_authorities`
overrides with optional `expiresAt` for break-glass). Every grant change is audited by the
existing Plane A change-audit (`authorization_audit_logs`).

---

## 5. Where the flag is computed, carried, and enforced

| Layer | File | Behavior |
|-------|------|----------|
| **Plane A resolve** | `elysia-rbac/src/identity/controller.ts` | `GET /identity/allowed-scopes` runs `can(db, userId, activeOrgUuid, "content", "read_all", {organization})` and returns `superAdmin: boolean` alongside `scopeIds` / `scopeAllIds`. No active org ⇒ `superAdmin:false` (fail-closed). |
| **Gateway inject** | `core-backend/src/query/controller.ts` | `fetchUserScopes()` reads `superAdmin` and forwards `super_admin` in the body: `{ ...body, ...scopes }`. `scopes` is spread **last**, so a client-supplied `super_admin` is always overwritten — it cannot be forged. Cached per bearer token (short TTL). |
| **Gateway model** | `core-backend/src/query/model.ts` | `super_admin` is an optional documented field, injected by the gateway. |
| **Engine request** | `lore-engine/api/routes/query.py` | `QueryRequest.super_admin: bool = False`. Trusted only because `require_gateway` authenticates the request via the internal secret. |
| **Engine service** | `lore-engine/services/query_service.py` | `query()` / `query_stream()` take `super_admin` → `UserScopes(allowed, super_admin=...)`. Logs `query_content_superadmin` when active. |
| **Engine gate** | `lore-engine/domain/scopes.py` | `scope_gate()` emits `( $super_admin OR ( <fail-closed predicate> ) )`; `scope_params()` binds `$super_admin`; `is_visible(..., super_admin)` mirrors it. |
| **Engine retrieval** | `seeds.py`, `frontier.py`, `jira.py` | Empty-scope bail-outs allow a super-admin through (`... and not scopes.super_admin`); jira threads `super_admin` to every scoped query. |

### Why the bypass is a bound Cypher parameter, not a code branch
The retrieval Cypher fragments (`_VECTOR_SEEDS_CYPHER`, jira queries, the frontier
`_neighbor_cypher`) are built **once at import time** via f-strings that call `scope_gate()`.
A per-request bypass therefore cannot be a `scope_gate()` argument — it must ride on a
**bound query parameter**. `scope_gate()` bakes `$super_admin` into the fragment, and
`scope_params()` (spread into every scoped `client.run()`) binds it. Adding it to
`scope_params()` means every scoped query gets the parameter automatically; no query uses
`scope_gate()` without `scope_params()`, so there is no "missing parameter" path.

---

## 6. `QUERY_OPEN_ACCESS` — removed 2026-07-15

There used to be a second "see everything" switch: a deploy-time env flag that made
`scope_gate()` return literal `"true"`, disabling the content ACL for **everyone** on the
instance. It existed so demos could query a graph dump whose nodes have no `scope_ids`.

It is **deleted** — setting, call sites, and `.env` entry. Reasons:

- It was **on in `lore-engine/.env`**, which is the normal state for a laptop. A switch
  that is routinely on is a switch that eventually ships.
- Nothing in code prevented it in production, where it would expose every tenant's
  private mail and DMs to every caller.
- It silently **disabled the gate's own tests** — with the flag on, the whole scope suite
  tested the open path. A test asserting something *is* visible stayed green while
  asserting nothing. Removing the flag removed that failure mode; the tests no longer
  depend on anyone's `.env`.

`content.read_all` is the replacement and covers the legitimate need, but properly: it is
per-user, per-org, assignable, revocable and audited, rather than global and invisible.

**Consequence for old data:** the pre-2026 ezobooks fetch has no `scope_ids`, so it is
invisible to a normal caller — correctly, since the gate is fail-closed. Read it with a
content super-admin, or re-ingest so nodes carry scopes.

---

## 7. Flags & fields reference

| Name | Location | Type | Meaning |
|------|----------|------|---------|
| `content.read_all` | elysia-rbac authority | privileged authority | Holding it makes the user a content super-admin. |
| `superAdmin` | `GET /identity/allowed-scopes` response | `boolean` | Result of the Plane A `can("content.read_all")` check for the caller in their active org. |
| `super_admin` | query body (gateway → engine) | `boolean` | Server-injected bypass flag. Client-supplied values are overwritten. |
| `UserScopes.super_admin` | lore-engine `core/types.py` | `bool` | In-engine carrier of the bypass. |
| `$super_admin` | Cypher param via `scope_params()` | bool | Disjunct that opens `scope_gate()`. |
| `SUPER_ADMIN_PARAM` | `lore-engine/domain/scopes.py` | const `"super_admin"` | Name of the bound param. |

---

## 8. Fail-closed guarantees (unchanged for non-admins)

- No active org, unknown user, or `/allowed-scopes` failure ⇒ `superAdmin:false`, empty scopes.
- Gateway fetch failure ⇒ `super_admin:false`, empty scopes (deny).
- A client cannot set `super_admin` — the gateway always overwrites it from the authority check.
- With `super_admin=false` and empty scopes, a node with empty/absent `scope_ids` is invisible
  (the normal fail-closed rule); the `$super_admin` disjunct only opens when the flag is true.

---

## 9. Auditing

- **Grant-time:** assigning/removing `content.read_all` (via role or direct override) is written
  to `authorization_audit_logs` by the existing Plane A change-audit.
- **Check-time:** the `/identity/allowed-scopes` bypass check runs with `{ audit: false }` to
  avoid a log row on every query (it is a read, not an action). The engine logs
  `query_content_superadmin` on each bypassed query.
- **Recommended hardening (follow-up):** write an `access_audit_log` row (per `ACL_MVP_PLAN.md §4`)
  on every super-admin-bypassed query (`user_id`, `query`, `results_count`, `super_admin=true`),
  and prefer time-boxed grants (`user_authorities.expiresAt`) for break-glass windows.

---

## 10. Out of scope / notes

- **`infra/pg_knowledge_repository.py`** uses a separate pgvector SQL path
  (`n.scope_ids.op("&&")(...)`) and is **not** wired into `QueryService` (the active Neo4j
  path). The bypass covers the Neo4j retrieval path only. If that pgvector path is ever put on
  the query hot path, replicate the `super_admin` disjunct there.
- **Spine nodes** (Person/Organization/Topic) carry no scopes and are already traversable but
  never citable; the bypass changes only *content* visibility, not this design.
- **Source separation** (`unstructured_source_filter` / `jira_source_filter`) is a routing
  concern, not ACL, and is intentionally **not** bypassed — a super-admin still gets Gmail
  content from the unstructured engine and Jira content from the Jira engine, never crossed.
