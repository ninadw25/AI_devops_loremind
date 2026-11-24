# Lore Platform — Security Audit

**Scope:** OAuth/secret/tenant-isolation review across `lore-ingestion`, `elysia-rbac`, `elysia-backend` (core-backend), `lore-engine`, `lore-frontend`.
**Round 1:** 2026-07-26 — report-only, 6 findings.
**Round 2:** 2026-07-27 — round-1 findings FIXED, plus a second sweep of all three backends that found 21 new issues; 16 fixed, 5 open.
**Threat model in focus:** (a) a third party breaching the platform and stealing one client's mail + OAuth `refresh_token`; (b) one tenant reading another tenant's data.

---

## Executive summary

Round 1's exposure was concentrated in **secrets** — plaintext at rest and in transit. Both are closed. Round 2 found the more serious class: **the internal trust boundaries were treated as authentication, not authorization.** Three services accepted whatever an authenticated caller sent — an ingestion `RunSpec` that could set arbitrary environment variables on the runner process, an RBAC engine that never filtered grants by organization, and a credential reference a tenant could point at another tenant's secret. Those are cross-tenant breaks, not just blast-radius issues, and they are now fixed.

What remains open is one design decision (scope-id namespacing, which spans two repos), the audit-logging gap on control-plane mutations, and a handful of deployment invariants that code cannot enforce.

**Current posture by area:**

| Area | State |
|---|---|
| Secrets at rest / in transit | Fixed — inline plaintext double-gated, plaintext internal hops refused in production |
| Cross-tenant isolation (query path) | Strong — per-org DB routing, fail-closed scope gate, non-forgeable scope fields |
| Cross-tenant isolation (control/RBAC path) | Fixed this round — was broken in three independent places |
| Within-tenant RBAC (department wall) | Fixed — role claim issued and enforced fail-closed |
| Rate limiting | Fixed — was absent on login and on the query path |
| Data egress to third parties | Fixed — Langfuse now opt-in, content capture off by default |
| Audit logging | **Open** — control-plane mutations are unrecorded |
| Scope-id tenant namespacing | **Open** — needs a cross-repo decision |

---

## Round 1 findings — all fixed

### HIGH-1 — Crown-jewel secrets stored plaintext at rest on any non-`production` NODE_ENV — **FIXED**
`storeSecret` returned `inline:<value>` whenever `NODE_ENV !== "production"`, with a default of `"development"`. One missing env var wrote every tenant's Zoho `refresh_token` and Gmail service-account key as cleartext into the shared central Postgres.

**Fix** (`elysia-rbac/src/registry/secrets.ts`, `src/config.ts`): inline storage is now double-gated — it requires an explicit `ALLOW_INLINE_SECRETS=true` opt-in *and* a loopback database host, and is refused outright in production. The bare-value-as-inline fallback in `resolveSecret` rides the same opt-in. A missing or typo'd `NODE_ENV` can no longer downgrade anyone; a staging deploy pointed at a shared RDS now fails loudly instead of silently writing plaintext.

### HIGH-2 — Secret-carrying rbac → control-API hop defaults to plaintext HTTP — **FIXED**
The `refresh_token` / Gmail key travel in the trigger request body to `INGESTION_CONTROL_URL`, which defaulted to `http://` with no TLS enforcement.

**Fix** (`elysia-rbac/src/config.ts`): `requireSecureInternalUrl()` rejects any non-loopback `http://` URL for both `INGESTION_CONTROL_URL` and `VAULT_PROXY_URL` when `NODE_ENV=production`. Boot fails rather than the first trigger, so the misconfiguration surfaces at deploy time. Moving the token behind a vault-proxy read so it never transits the hop at all remains the longer-term goal (see Open items).

### MEDIUM-1 — OAuth `state` nonce minted but never verified — **FIXED**
The nonce gave no single-use guarantee, leaving a bounded replay window on the public callback.

**Fix** (`elysia-rbac/src/ingestion/oauth.ts`): added `consumeState()`, which verifies the signature/expiry/DC as before and then rejects any nonce already spent, expiring entries as their state TTL passes. `ZohoOAuth.complete` now calls it. Covered by a replay test in `oauth.test.ts`. *Note: the spent-nonce set is in-process; a multi-instance rbac deployment needs it moved to a table.*

### MEDIUM-2 — Within-tenant RBAC (department/sensitivity) effectively disabled — **FIXED**
The JWT carried no `role`, so `user_access_groups_from_role()` returned full management access to everyone and the department filter was a no-op. Any authenticated user of an org could read that org's HR/finance/legal content.

**Fix**, both halves:
- `elysia-rbac/src/auth/auth.ts` now issues a `role` claim in the JWT, resolved from the caller's verified Better Auth membership in the active org.
- `lore-engine/domain/rbac.py` maps it **fail-closed**: `owner`/`admin` → all departments, `member` → all except the confidential ones (hr/finance/legal), explicit department roles → that department, missing/unknown → `[]`.
- The wall is enforced in Cypher, not just Python: `domain/scopes.py:scope_gate()` gained a `$user_groups` clause, bound by `scope_params()` and threaded from `api/routes/query.py` through both `query()` and `query_stream()`. Untagged nodes (Slack/Jira, which have no department model) stay visible; tagged nodes require an intersection.

### LOW-1 — Engine decodes the forwarded JWT without signature verification — **ACCEPTED, unchanged**
By design: the gateway already verified via JWKS, and `X-Internal-Auth` + network isolation are the boundary. It fail-closes on an unset secret and settings force it in production. This remains a **deployment invariant to verify** — the engine port must be unreachable except from the gateway.

### LOW-2 — `.env.development` documented a gateway-bypass demo mode — **FIXED**
`lore-frontend/app/.env.development` now defaults to the gateway (`:8001`); the stale `QUERY_OPEN_ACCESS` note (a flag no code reads) is gone, replaced by an accurate note that the engine has no auth-off mode.

### LOW-3 — `resolve_run_env` swallowed a vault-proxy outage — **FIXED**
`lore-ingestion/common/vault_client.py` now raises a distinct `VaultEntryMissingError` on 404. Only "no entry" is swallowed; an unreachable proxy, a 401, or a 5xx fails the run.

---

## Round 2 — new findings

### Fixed

**R2-1 (CRITICAL) — `spec.env` was injected into the runner subprocess with no allowlist.**
`control/manager.py` merged a caller-supplied `dict[str,str]` into the run's environment. No file write was needed to exploit it: `HTTPS_PROXY` + `REQUESTS_CA_BUNDLE` intercepts all Gmail/Zoho traffic including the service-account key; `NEO4J_URI`/`DATABASE_URL` redirects a tenant's entire harvested mail into an attacker's database; `LD_PRELOAD`/`PYTHONPATH` reach code execution.
**Fix:** `RunSpec.env` is validated against a prefix allowlist (tenant DB routing + connector secrets only) in `control/models.py`. `PATH`, `PYTHON*`, `LD_*`, `*PROXY*`, `SSL_*`, CA-bundle vars and `LORE_CONNECTION_ORG` are refused.

**R2-2 (HIGH) — `spec.env` could forge `LORE_CONNECTION_ORG`, defeating the cross-tenant DB guard.**
The stamp that `common/config.py:assert_connection_for_org` checks is an env var, and `spec.env` was merged last — so a caller could point the databases at one tenant while stamping another, and the guard would pass green.
**Fix:** the merge order is inverted (vault creds now beat `spec.env`) and the stamp is written server-side from `spec.org` *after* the merge. No caller input can reach it.

**R2-3 (HIGH) — The runner inherited the control plane's full environment, including the vault token.**
Every tenant's runner could read `VAULT_PROXY_TOKEN` and fetch any other tenant's credentials, or authenticate as the control plane.
**Fix:** `_child_env()` strips `VAULT_PROXY_TOKEN`, `CONTROL_INTERNAL_SECRET` and `RBAC_INTERNAL_SECRET` before launch.

**R2-4 (HIGH) — `org` was unvalidated and became a filesystem path** (output artifacts, checkpoints, logs, S3 keys). `org="../../.."` wrote outside the data dir and could collide with another tenant's artifacts.
**Fix:** `RunSpec.org` is constrained to a slug pattern.

**R2-5 (HIGH) — `data_dir` was unvalidated and handed to elysia-rbac as `artifactBase`.**
Beyond arbitrary directory creation, the completion webhook told rbac where to load identity/scope artifacts from — so whoever called `POST /runs` could choose the Plane B identity→scope mapping.
**Fix:** `data_dir` must resolve inside `LORE_DATA_ROOT` (or the server's state dir).

**R2-6 (HIGH) — RBAC grants were never filtered by organization.**
`getEffectiveGrants` queried `user_roles` / `user_authorities` by user id alone. A user who was Org Admin of A and a plain member of B could aim a check at A's scope via the `?scopeType=&scopeId=` query params, match her A grants, and have the handler operate on B — full RBAC admin of another tenant, including granting herself `content.read_all` there.
**Fix**, both ends: `belongsToOrg()` in `src/rbac/engine.ts` filters every grant source to the checking org (denies still survive from anywhere — deny-wins can only remove access); and `src/auth/plugin.ts` now validates the caller-supplied scope type against the enum, refuses `global`, and refuses an `organization` scope naming anything but the active org. `global` is also gone from the tenant-facing `ScopeSchema`. Covered by `tests/tenant-isolation.test.ts`.

**R2-7 (HIGH) — A tenant admin could point an ingestion source at another tenant's secrets.**
`credentialSecretRef` was accepted from the client and stored verbatim. The `sm:` names are deterministic, so org A's admin could reference `sm:lore/<orgB>/ingestion/gmail-Gmail` and ingest org B's mailboxes into org A's graph; `env:` equally pulled the process's own secrets into the RunSpec.
**Fix:** refs must start with this org's own secret-name prefix, else rejected.

**R2-8 (HIGH) — Pre-registered account squatting hijacked an employee's content scopes.**
Sign-up is open and no email-verification flow exists, so an outsider could register `cfo@client.com`; when the admin later approved that employee, `TeamService.approve` silently reused the attacker's account and attached the CFO's person record, scopes, membership and role.
**Fix:** reuse now requires either a verified email or an account already linked to that person; otherwise it 409s with a collision message for a human to resolve.

**R2-9 (HIGH) — Every query and its retrieved content was exported to an external Langfuse host, and the documented off-switch did nothing.**
`get_client()` was called with no arguments, so `langfuse_disabled`, `langfuse_host` and the key settings were dead config, and `@observe` captured input/output by default — meaning question text, retrieved knowledge and conversation turns from *every tenant* funnelled into one external project, defaulting to `cloud.langfuse.com`.
**Fix:** tracing is opt-in and fails safe — it initialises only when not disabled *and* both keys are configured, with host/keys passed explicitly rather than picked up from ambient env. Content capture is separately gated by `LANGFUSE_CAPTURE_CONTENT` (default off); the explicit content-shipping calls in `query_service.py` and `infra/embeddings/nvidia.py` respect it too.

**R2-10 (MEDIUM) — No rate limiting anywhere, including unauthenticated login.**
Better Auth had `rateLimit: { enabled: false }`; the engine built a `Limiter` but set no default limits and decorated no route, so its middleware was inert.
**Fix:** Better Auth limiting enabled with tight sign-in (5/min) and sign-up (3/min) rules; the engine now applies a default per-caller limit keyed on the **forwarded user id**, not the IP — behind a gateway every tenant shares one source address.

**R2-11 (MEDIUM) — Assignment routes didn't bind the target user or role to the org.**
`removeRole` deleted by `(userId, roleId)` with no org check, and `assignRole` never verified the grantee was a member of the acting org.
**Fix:** both now verified.

**R2-12 (MEDIUM) — `role.update` could self-escalate to content super-admin.**
`attachAuthority` looked up the authority with no org predicate (despite its comment) and ignored the `privileged` flag, so any Org Admin could attach `content.read_all` to a role and assign it to themselves.
**Fix:** the lookup is org-filtered, and attaching a content-ACL bypass (`content.read_all` or a wildcard) requires already holding it. Ordinary privileged authorities like `ingestion.configure` stay delegable — administering those is the Org Admin's job.

**R2-13 (MEDIUM) — Zoho access token could land in the log.** An unexpected token response was logged whole, and logs are readable through the control API. **Fix:** keys only.

**R2-14 (LOW) — Argument injection into the runner argv.** `org_domains`/`extra_functional` were splatted after a `nargs="*"` flag, so an element starting with `-` was parsed as a new option (e.g. `--data-dir`). **Fix:** rejected at the model.

**R2-15 (LOW) — SSE error path streamed the raw exception to the browser**, contradicting the policy the same client applies to unary calls. **Fix:** detail logged, generic message streamed.

**R2-16 (LOW) — Thread pagination cursor wasn't ownership-checked**, giving an existence-and-timestamp oracle for an arbitrary thread id. **Fix:** cursor subquery bound to the caller's own threads.

Also fixed: the offboarding report now fail-closes for a zero-scope caller exactly as the onboarding report does (it was leaking the person header — name, email, org, reports_to), and `cors_origins` no longer defaults to `*` with credentials.

### Open — needs a decision or a deployment change

**O-1 (HIGH) — Plane B allowed-scopes is computed across all tenants.** `identity/service.ts` queries `scope_principal` / `jira_group_member` by principal string with no org filter, and the key space is global by schema (`person_alias.alias_key` and `scope.scope_id` are bare primary keys). Ingestion scope ids carry no org namespace — `jira:project:ENG`, `slack:channel:C123`, `person:email`. Any principal shared by two tenants (a contractor's email, or the same Jira project key) injects one org's scope ids into another org's user; worse, the loader replaces grants per `scope_id` regardless of org, so one tenant's load can delete another's grants.
**Why it's open:** the fix spans two repos and changes a persisted data format — `common/scopes.py` must namespace scope ids by org, and rbac must key `person_alias`/`scope` on `(org, id)`. Both halves must land together with a migration and a graph rebuild. This is the highest-priority remaining item.

**O-2 (MEDIUM) — The control API has no per-org authorization.** Possession of the shared secret grants full cross-tenant authority: `GET /runs` returns every org's runs, `/pipeline/{org}/*` reads any org's claims, and `/logs`, `/logs/{name}`, `/errors` return raw log content across all orgs. One confused-deputy or SSRF in the gateway or rbac reads every tenant. Needs the caller bound to an org (a signed org claim or per-org secrets) and list/read endpoints filtered by it. *The RunSpec hardening above removes the write-side escalation; this is the read side.*

**O-3 (MEDIUM) — Unbounded run spawn and unbounded in-memory run history.** Each `POST /runs` starts a Python subprocess with no global cap, and `RunManager._runs` is never evicted. Needs a concurrency cap and a registry-existence check on `org`.

**O-4 (MEDIUM) — `callbackRunId` is echoed to rbac unvalidated**, so a caller can close out another tenant's `ingestion_run` row. The URL base is server config, so there's no SSRF here. Fix belongs on the rbac side: verify the run id belongs to the org whose run it started.

**O-5 (LOW) — Control-plane mutations are unaudited.** `authorization_audit_logs` only receives Plane A writes. Nothing records registry changes, ingestion source create/update, credential rotation, schedule changes, triggers, or team account creation; the OAuth callback swallows every failure into a redirect with no record. Closing this properly needs the acting user forwarded to those internal endpoints, which today authenticate with a shared secret and have no actor identity.

### Deployment invariants (not code-fixable)

- The engine port must be reachable **only** from the gateway (see LOW-1). Verify the security group / network policy.
- The OAuth spent-nonce set is in-process — a multi-instance rbac deployment needs it in Postgres before horizontal scaling.
- Move the Zoho `refresh_token` and Gmail key behind a vault-proxy read so they never transit the rbac→ingestion hop at all (the transport is now TLS-enforced, but the secret still crosses it).
- `ALLOW_INLINE_SECRETS` must never be set outside local development; production refuses it regardless, but staging should be configured with AWS Secrets Manager rather than relying on that refusal.

---

## Verified-OK (do not re-investigate)

Confirmed across both rounds:

- **Query-path scope fields cannot be client-forged.** `scope_ids`/`scope_all_ids`/`super_admin` are absent from the request schema and computed server-side; Elysia strips unknown body keys (verified empirically), so extra-field smuggling fails on every route.
- **No ingestion endpoint trusts a client-supplied `org`** — the gateway injects it from the verified JWT on every route.
- **Per-tenant DB isolation** — engine routes Neo4j/Postgres/chronograph by `user.org` via the tenant registry.
- **Fail-closed scope gate** — nodes with empty/NULL `scope_ids` are invisible unless `super_admin`; the Python mirror (`is_visible`) implements the same predicate as the Cypher fragment so they cannot diverge.
- **Engine data plane fully gated** — every router sits behind `require_gateway`, which fail-closes on an unset internal secret; settings force it in production.
- **Internal-auth is constant-time and fail-closed** on both the rbac and control-API sides.
- **OAuth SSRF / open-redirect / token-leak guards** — DC allowlist, config-only redirect targets, server-side exchange, Zoho's raw body never echoed.
- **Cypher is parameterized everywhere.** Every f-string template interpolates only trusted ontology labels, integer literals, and the `scope_gate()` fragment; all user values are bound parameters.
- **The gateway does not forward client headers** — the header set is built from scratch, so a client-supplied `X-Internal-Auth` or `X-Account-Id` never reaches the engine. The admin proxy does not decode `%2e%2e%2f` in its wildcard param.
- **Thread ownership is enforced** on both HTTP and gRPC transports; the gRPC interceptor is fail-closed.
- **No dangerous primitives** — no `shell=True`, `pickle`, `yaml.load`, `eval`, `exec`, or `verify=False` in any repo; every HTTP call has a timeout. Recursive group-tree SQL is parameterized.
- **No secrets committed** — only `.env.example` files are tracked; frontend env files hold public `VITE_*` URLs only.

---

## Known-latent (not exploitable today, fix before wiring)

- `lore-engine/infra/pg_knowledge_repository.py` — the staged pgvector backend has **no scope gate on graph expansion** and implements only half the ACL predicate (no `scope_all_ids` anywhere). Nothing imports it today, but it is the intended Neo4j replacement; wiring it as written would turn one-hop expansion into a full ACL bypass. Route every query through a shared fragment mirroring `scope_gate()` first.
- `lore-engine/api/routes/chronograph.py` — `POST /ingest-json` takes an arbitrary server-side filesystem path and `/ingest-gitlab` takes any project id fetched with the platform-wide `GITLAB_PAT`. The gateway no longer routes to them; deleting them is smaller than hardening them.
- `domain/retrieval/{vector,expansion,person}.py` carry the superseded `access_groups`-only gate with no scope gate. They fail closed by accident and nothing imports them — delete before someone re-wires them.
