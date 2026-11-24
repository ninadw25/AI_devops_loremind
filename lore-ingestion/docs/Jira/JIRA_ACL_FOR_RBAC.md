# Jira ACL → RBAC: How It Actually Works

**Scope of this doc:** exactly how `lore-ingestion` discovers Jira access control, how
it gets users and memberships, what artifact it produces, and the visibility
semantics that artifact encodes for the query-time RBAC gate. Grounded in the
code as built (`infra/jira/discovery.py`, `infra/jira/run.py`,
`infra/jira/normalizer.py`, `common/scopes.py`) and verified against a live
Atlassian Cloud instance.

> **Separation of concerns.** Ingestion *materializes* the ACL graph into a JSON
> artifact. `lore-engine` *enforces* it at query time. This repo never makes an
> allow/deny decision — it only records, faithfully and fail-closed, who may see
> what. Everything below is about producing a correct, complete graph (and
> loudly flagging where it can't).

---

## 1. Mental model

Two things get produced by ingestion, and RBAC is the join between them:

1. **The ACL artifact** — `output/jira/{org}_scope_principals.json`. A normalized
   graph of *scopes* (things access is granted to), *principals* (users), *groups*
   + *group_members* (group→user expansion), and *scope_principals* (the grant
   edges). Built by discovery, then enriched at fetch time with per-issue dynamic
   grants.

2. **Scope tags on every event** — each normalized issue / comment / changelog
   carries a `scope_id` (its project) and `extra_scope_ids` (its security level,
   if any). Done in `infra/jira/normalizer.py`.

RBAC at query time = *"for user U, return only events whose scope tags are all
satisfied by grant edges that resolve to U."* The artifact is the only input that
decision needs.

```
                       discovery (3 phases)            ingest (per issue)
 Jira REST API  ───────────────────────────────►  {org}_scope_principals.json
   users, groups, projects,                          ├─ scopes[]              (what is gated)
   permission schemes,                               ├─ principals[]          (who exists)
   security schemes/levels                           ├─ groups[] + group_members[]
                                                      ├─ scope_principals[]    (grant edges)
                                                      ├─ project_permission_rules{}  (dynamic rules)
                                                      └─ acl_incomplete_projects[]   (gaps, fail-closed)

 each issue/comment/changelog  ──normalizer──►  event.scope_id = jira:project:KEY
                                                event.extra_scope_ids = [jira:seclevel:ID?]
```

---

## 2. Identifier formats — the contract (`common/scopes.py`)

These strings are persisted in the artifact **and** in Neo4j. A one-character
drift silently breaks the gate (it then denies everyone with no error), so they
have a single owner: `common/scopes.py`. Never inline these as f-strings.

| Kind | Format | Builder | Example |
|------|--------|---------|---------|
| Project scope | `jira:project:{KEY}` | `scopes.jira_project()` | `jira:project:ENG` |
| Security-level scope | `jira:seclevel:{levelId}` | `scopes.jira_seclevel()` | `jira:seclevel:10001` |
| User principal | `jira:U{accountId}` | `scopes.jira_user()` | `jira:U712020:f9038…` |
| Group principal | `jira:G{groupId}` | `scopes.jira_group()` | `jira:G8964f760-cf6…` |

**Gotchas baked into the contract:**
- **No colon after `U`/`G`.** It's `jira:U{id}`, not `jira:U:{id}`. The accountId
  itself may *contain* colons (Atlassian uses `712020:uuid`), so the principal
  looks like `jira:U712020:f9038…` — that's correct.
- `scopes.jira_group()` is **idempotent**: an already-`jira:`-prefixed id is
  returned unchanged (Jira sometimes hands back a prefixed value).
- To go back to the bare accountId for a backend lookup: `scopes.strip_jira_user()`
  (removes the `jira:U` prefix). `scopes.is_jira_user()` tests the prefix.

---

## 3. The artifact: file, shape, and real data

**Path:** `output/jira/{org}_scope_principals.json` (`common/output_paths.py:JiraPaths.scope_principals`).
Written by discovery, then **atomically rewritten** by the fetch stage so the
dynamic per-issue grants are included (`run.py` — tmp file + `replace()`; a torn
write here would corrupt the gate input).

**Top-level keys:**

| Key | Type | Meaning |
|-----|------|---------|
| `scopes` | list | Every access target: projects + security levels |
| `principals` | list | Every user (`principal_id`, `email`, `display_name`, `account_type`) |
| `groups` | list | Every group (`group_id`, `name`, `members[]`) |
| `group_members` | list | Flattened group→user edges (`group_id`, `principal_id`) |
| `scope_principals` | list | **The grant edges** (`scope_id`, `principal_id`, `principal_type`, `grant_source`) |
| `project_permission_rules` | dict | Per-project *dynamic* grant rules (reporter/assignee) |
| `acl_incomplete_projects` | list | Projects whose ACL could not be read — **deny-all, flagged** |

**A `scopes` row:**
```json
{ "scope_id": "jira:project:ENG", "kind": "project",
  "name": "Engineering", "is_org_wide": false, "tenant_id": "loremind" }
{ "scope_id": "jira:seclevel:10001", "kind": "security_level",
  "name": "Confidential", "is_org_wide": false, "tenant_id": "loremind" }
```

**A `scope_principals` (grant edge) row:**
```json
{ "scope_id": "jira:project:ENG",
  "principal_id": "jira:U712020:f9038…",
  "principal_type": "user",
  "grant_source": "jira_browse_user" }
```

**Live shape from the test instance (`loremind`):** 9 scopes (5 projects + 4
security levels), 18 principals, 13 groups, 35 group-member edges, 52 grant edges.
`grant_source` distribution: `jira_browse_role: 41`, `jira_browse_user: 5`,
`jira_seclevel: 5`, `jira_browse_group: 1`.

---

## 4. How we get the users and memberships (Phase 1 — identity crawl)

`JiraDiscovery.phase_1_identity_crawl()` builds the people + groups before any
project is touched.

### 4.1 Users → `principals`
- **Endpoint:** `GET /rest/api/3/users/search` with `startAt`/`maxResults=50`
  pagination (loops until a short page or `startAt >= total`).
- For each user with an `accountId`, store:
  ```json
  { "principal_id": "jira:U{accountId}", "email": "...", "display_name": "...",
    "account_type": "atlassian" }
  ```
  `account_type` distinguishes real people (`atlassian`) from `app` bots/customers.
- **Failure mode:** if the user crawl 4xx/5xx's, it logs a WARNING and continues
  with empty principals (it does not crash). Downstream, an empty principal set
  means most grants won't resolve — visible in the run summary, not silent.

### 4.2 Groups → `groups`
- **Endpoint (tried in order):** `/rest/api/3/group/bulk` → `/rest/api/3/groups/picker`
  → `/rest/api/3/groups`, `maxResults=100`. First one that returns a usable list
  wins.
- Each group is keyed canonically as `jira:G{groupId}` (`scopes.jira_group()`).

### 4.3 Group membership → `group_members`
- For each group, **`GET /rest/api/3/group/member?groupId={raw}`** (fallback
  `/rest/api/3/groups/{raw}/members`), `maxResults=50`, cursor/`isLast`
  pagination.
- **Prefix stripping:** the Jira API wants the *raw* group UUID, so discovery
  strips the `jira:G` prefix (`group_id.removeprefix(scopes.JIRA_GROUP_PREFIX)`)
  before the call, and re-canonicalizes on the way back. This is the #1 place a
  format bug would silently drop members.
- Result: `group_members[] = {group_id, principal_id}` — the edge the gate uses
  to expand a group grant into individual users.
- **Failure mode (hardened):** if *both* member endpoints fail for a group, it now
  logs a WARNING ("this group's members will be incomplete — grants may be
  missing") rather than silently emptying the group.

> **Why membership matters for RBAC:** a project or security level is frequently
> granted to a *group*, not to users. The gate can only turn that into a per-user
> decision by walking `group_members`. If membership is incomplete, real users get
> wrongly denied (fail-closed) — never wrongly allowed.

---

## 5. How we get project ACLs (Phase 2 — project crawl)

`JiraDiscovery.phase_2_project_acl_crawl()` produces the project scopes and the
bulk of the grant edges.

1. **List projects:** `fetcher.list_projects()` → tries `/rest/api/3/project/search`
   (then `/project`, then v2). Each becomes a scope row
   `{scope_id: jira:project:KEY, kind: "project", is_org_wide: false}`.

2. **Resolve the project's permission scheme:**
   `GET /rest/api/3/project/{key}/permissionscheme` → scheme id.

3. **Read the scheme's permissions:**
   `GET /rest/api/3/permissionscheme/{id}/permission`, and keep only grants for
   **`BROWSE_PROJECTS`** (the permission that controls "can see this project's
   issues").

4. **Translate each grant's holder into a grant edge.** The holder `type` decides
   how:

   | Holder type | `grant_source` | How it resolves to principals |
   |-------------|----------------|-------------------------------|
   | `user` | `jira_browse_user` | accountId taken from `parameter`/`value`/`accountId` (strips `accountId:` prefix); kept only if the user exists in `principals` |
   | `group` | `jira_browse_group` | group name (in `parameter`) → `jira:G{uuid}` via the Phase-1 name→id map → expand via `group_members` at query time |
   | `projectRole` | `jira_browse_role` | dereference the role: `GET /rest/api/3/project/{key}/role/{roleId}` → its `actors[]` → each user (`actorUser.accountId`) or group (`actorGroup`) becomes an edge |
   | `reporter` | *(rule, not an edge)* | recorded in `project_permission_rules[key].grant_sources` as `jira_dynamic_reporter` — resolved per-issue at ingest (see §7) |
   | `assignee` | *(rule, not an edge)* | recorded as `jira_dynamic_assignee` — resolved per-issue at ingest |
   | `applicationRole` / `authenticated` | *(flag)* | sets the scope's **`is_org_wide = true`** — any logged-in org user can browse; no per-user edges needed |

5. **Dedup.** Every `(scope_id, principal_id, grant_source)` edge is de-duplicated.

**Live example (`loremind`):** EDGE, ENG, HR, OPS, SCRUM. ENG/HR are restricted
(real per-user/role/group edges). EDGE and OPS are `is_org_wide: true` (browse
granted to any authenticated user). `jira_browse_role` dominates (41/52 edges)
because the scheme grants browse to project roles whose actor lists discovery
expands.

---

## 6. How we get issue-security ACLs (Phase 3 — security crawl)

Project-level browse is coarse. Jira **issue security levels** restrict individual
issues *within* a project to a subset of people. `phase_3_security_crawl()`:

1. **Schemes:** `GET /rest/api/3/issuesecurityschemes`.
2. **Levels:** `GET /rest/api/3/issuesecurityschemes/{schemeId}` — levels are
   embedded in the scheme detail (not a `/levels` sub-resource). Each level →
   scope row `{scope_id: jira:seclevel:{levelId}, kind: "security_level"}`.
3. **Level members:**
   `GET /rest/api/3/issuesecurityschemes/{schemeId}/members?issueSecurityLevelId={levelId}`
   (note: level id is a **query param**, not a path segment).
   - holder `type == "user"` → `jira:U{accountId}` edge, `grant_source: jira_seclevel`
     (kept only if the user is in `principals`).
   - holder `type == "group"` → resolve name → `jira:G{uuid}` → edge,
     `grant_source: jira_seclevel`.

**Live example:** 4 levels — `Internal` (1 group grant), `Confidential` (3 user
grants), `Exec Only` (1 user grant), `Empty` (**0 grants**).

> **`Empty` with zero members = deny-to-everyone.** This is intentional and
> correct: an issue tagged with a level nobody is a member of is visible to nobody.
> Fail-closed by construction.

---

## 7. Dynamic per-issue grants (resolved at fetch time)

`reporter`/`assignee` browse grants can't be precomputed per user — they depend on
each individual issue. So Phase 2 records the *rule* in
`project_permission_rules`, and during fetch
`append_dynamic_issue_scope_rows(artifact, issue)` (`infra/jira/discovery.py`)
stamps the concrete grant for each issue:

- If the project rule contains `jira_dynamic_reporter`, the issue's
  `fields.reporter.accountId` → a `jira:U…` edge on `jira:project:KEY` with
  `grant_source: jira_dynamic_reporter`.
- Same for `jira_dynamic_assignee` from `fields.assignee.accountId`.

This is why `run.py` **re-writes the artifact after fetch** (atomically): discovery's
first write has the static edges; the post-fetch write adds the dynamic ones.

> **Caveat (observed on the live instance):** all `project_permission_rules` came
> back with empty `grant_sources`, i.e. no project grants browse to Reporter/
> Assignee. So this path exists and is wired, but is currently **un-exercised** by
> the test data. To test it, add a permission scheme that grants `BROWSE_PROJECTS`
> to *Reporter* on one project, then re-run discovery + fetch.

---

## 8. How each ticket is tagged (`infra/jira/normalizer.py`)

The other half of RBAC: every emitted event carries the scope tags the gate
checks against.

- **Issue** (`normalize_issue`): `scope_id = jira:project:{KEY}`. If the issue has
  `fields.security`, `extra_scope_ids = ["jira:seclevel:{id}"]`.
- **Comment** (`normalize_comment`) and **Changelog** (`normalize_changelog`):
  inherit the parent issue's `scope_id` **and** its `extra_scope_ids`. A comment on
  a Confidential issue is itself Confidential — this inheritance is essential, or
  replies would leak around the issue's security level.

So an event is gated by: **its project scope** AND (**its security-level scope**, if
present).

---

## 9. The RBAC evaluation model (the semantics the artifact encodes)

For a querying user `U` (principal `jira:U{accountId}`), an event is visible iff:

```
visible(U, event) :=
      can_browse_project(U, event.scope_id)
  AND ( event.extra_scope_ids is empty
        OR  U ∈ members_of_every(event.extra_scope_ids) )

can_browse_project(U, projScope) :=
      scope is_org_wide                                  # applicationRole/authenticated
   OR (projScope, jira:U{U}, *) ∈ scope_principals       # direct user / role-expanded / dynamic
   OR ∃ G: (projScope, jira:G{G}, *) ∈ scope_principals  # group grant…
           AND (jira:G{G}, jira:U{U}) ∈ group_members    # …expanded to U
```

Key points for whoever builds the gate:
- **Project AND security-level** — it's an intersection. Project browse alone is
  not enough for a secured issue; the user must also be in the level.
- **Group grants require expansion** through `group_members`. Don't match groups
  directly to a user.
- **Org-wide short-circuits** per-user checks for that scope.
- **No grant edge ⇒ no access.** The default is deny. There is no implicit
  "everyone" except an explicit `is_org_wide` scope.

---

## 10. Safety properties & failure handling

Every place discovery can't read something, it fails in the **fail-closed**
direction (under-grant / hide), never fail-open (over-grant / leak). The recent
hardening makes those gaps *visible* instead of silent:

| Situation | Behavior | Where |
|-----------|----------|-------|
| Project permission scheme unreadable (e.g. team-managed 403) | Scope kept with **0 grants** (deny-all) + WARNING + recorded in `acl_incomplete_projects` (`project_key`, `scope_id`, `reason`, `http_status`) | `discovery.py` phase 2; summarized in `run.py` + run health summary |
| Group member fetch fails | WARNING; group members incomplete (grants may be missing) | `discovery.py` phase 1 |
| Issue references a security level **not** in the discovery artifact | WARNING at fetch; the dangling `jira:seclevel:` tag → gate denies all → re-run `--stage discovery` | `run.py` |
| Security level with no members (`Empty`) | Deny-to-everyone (intended) | phase 3 |
| Discovery returns 0 scopes | `check_nonempty` fails the run (an empty ACL artifact would deny everyone downstream) unless `--allow-empty` | `run.py` |

**HTTP error discipline (`infra/jira/fetcher.py`):** the transport layer re-raises
real errors (401/403/429/5xx) and only skips "endpoint not available" statuses
(400/404/405/410) when trying API-version fallbacks. So auth/rate-limit/server
failures surface; they are not silently turned into empty results.

**Observed live gap:** project `SCRUM` (team-managed) returns **403** on its
permission scheme. It now appears in `acl_incomplete_projects` with `http_status:
403` and triggers two warnings. Impact was nil only because SCRUM has 0 issues; if
it had issues they would be ingested and then invisible to everyone — which is why
this is now loud. **Fix path:** grant the connector's admin account *Browse
Projects* on team-managed projects (or wire a Grid/admin token), then re-run
discovery.

---

## 11. Endpoint reference

| Purpose | Method + path |
|---------|---------------|
| Users | `GET /rest/api/3/users/search?startAt=&maxResults=50` |
| Groups list | `GET /rest/api/3/group/bulk` → `/groups/picker` → `/groups` |
| Group members | `GET /rest/api/3/group/member?groupId={raw}` → `/groups/{raw}/members` |
| Projects | `GET /rest/api/3/project/search` → `/project` → v2 |
| Project permission scheme | `GET /rest/api/3/project/{key}/permissionscheme` |
| Scheme permissions | `GET /rest/api/3/permissionscheme/{id}/permission` |
| Project role actors | `GET /rest/api/3/project/{key}/role/{roleId}` |
| Security schemes | `GET /rest/api/3/issuesecurityschemes` |
| Security levels | `GET /rest/api/3/issuesecurityschemes/{schemeId}` (levels embedded) |
| Security level members | `GET /rest/api/3/issuesecurityschemes/{schemeId}/members?issueSecurityLevelId={levelId}` |

---

## 12. Running it

```bash
PY=../.venv/bin/python   # shared venv (Python 3.12)

# ACL only — writes output/jira/{org}_scope_principals.json
$PY -m infra.jira.run --org loremind --stage discovery

# Discovery + fetch + normalize (adds dynamic reporter/assignee grants,
# writes the run health summary with acl_incomplete_projects)
$PY -m infra.jira.run --org loremind --stage full
```

Credentials come from `.env`: `JIRA_BASE_URL`, `JIRA_EMAIL`, `JIRA_API_TOKEN`
(`JIRA_TYPE=cloud`). The token's account must be able to read users, groups,
permission schemes, and security schemes — anything it can't read becomes a
flagged, fail-closed ACL gap.

**Outputs:**
- `output/jira/{org}_scope_principals.json` — the ACL artifact (consumed by `lore-engine`).
- `output/jira/{org}_jira_meta.json` — run health summary (incl. `acl_incomplete_projects`).
- `output/raw/{org}_jira_{date}.jsonl` — normalized events, each carrying `scope_id` / `extra_scope_ids`.

---

## 13. Known limitations / TODO for centralized RBAC

- **Team-managed (next-gen) projects** can 403 on the classic permission-scheme
  endpoint. They are flagged in `acl_incomplete_projects` but currently get **no**
  grants. A Grid/admin pathway (as built for Slack) would close this; until then,
  fix per-project browse access for the connector account.
- **Dynamic reporter/assignee grants** are wired but un-tested against real data
  (no project in the test instance grants browse to Reporter/Assignee).
- **Identity unification across sources** (Jira `jira:U…` ↔ Gmail/Slack person) is done by
  the centralized resolver `pipeline/identity/resolve.py` (`from_jira` adapter →
  surrogate `person_id`), joining on canonical email (`common/email_canon.py`) and, for
  Jira's hidden-email case, bounded name match against the internal roster. See
  `docs/RBAC/CENTRALIZED_IDENTITY.md`. This doc covers Jira-native principals only.
- **`accountType`** is captured on principals — use it to exclude `app`/bot
  accounts from human-facing RBAC where appropriate.

---

## 14. Redesign deltas (P6) — knowledge-graph-builder-side correctness

> **Status:** DESIGN. Part of the Jira pipeline redesign
> (`JIRA_PIPELINE_REDESIGN.md`, problem **P6**). §1–13 above describe the
> *ingestion / artifact* side, which is correct and fail-closed as built. This
> section covers the three correctness items the redesign surfaced — one already
> satisfied, two open — that live at the **builder / dedup** boundary, not in
> discovery.

### 14.0 Scorecard

| P6 item | Status | Where it's handled |
|---------|--------|--------------------|
| #3 ACL-incomplete projects fail-closed (SCRUM 403) | ✅ **Already built** | §10, §13 — flagged in `acl_incomplete_projects`, 0 grants, loud warnings |
| #1 `extra_scope_ids` last-writer-wins under node merge | ⬜ Open | builder + dedup (below) |
| #2 Comment-visibility (Layer 4) not enforced | ⬜ Open | normalizer + scope tagging (below) |

### 14.1 #1 — gate on `IN_SCOPE` edges, not the scalar property

**The bug.** `extra_scope_ids` is written as a **scalar node property**
(`builders/unified_builder.py:511`, `node.extra_scope_ids = json.dumps(...)`).
If a knowledge node is emitted from two threads/issues — or merged by dedup —
with *different* scope sets, the property is **last-writer-wins**: a
public-issue write can overwrite a secured-issue value and **silently strip the
security level**. The query gate in §9 reads `event.extra_scope_ids`, so it
would then pass for everyone.

**The fix (two halves):**

1. **Dedup never merges across scope.** The Jira-native dedup rule
   (`JIRA_EXTRACTION_AND_DEDUP.md` §B5) makes `scope_id` + `extra_scope_ids` part
   of every dedup key, so a single node can never span two scope sets in the
   first place. Structured nodes dedup exact-by-key; prose nodes dedup only
   within one issue (one scope). This removes the *merge* source of the race.
2. **The gate reads `IN_SCOPE` edges, not the node property.** The builder
   already emits one `IN_SCOPE` edge per `[scope_id] + extra_scope_ids`
   (`unified_builder.py:547`). Make those edges authoritative and treat the
   scalar `extra_scope_ids` property as **display-only / denormalized**, never
   the gate input. The gate becomes: *a node is visible iff the user satisfies
   **every** `seclevel` scope reachable by an `IN_SCOPE` edge, AND a project
   scope it can browse* — edges can't be clobbered by a later write the way one
   scalar can.

**Assert (test):** build a synthetic secured-issue node + public-issue node that
would collide on content; confirm (a) dedup keeps them separate, and (b) the
secured node still carries its `jira:seclevel:*` `IN_SCOPE` edge after the full
build. This is the security-level-leak regression guard.

### 14.2 #2 — enforce comment-level visibility (Layer 4)

**The gap.** §8 inherits a comment's scope from its **issue** (project +
security level). But Jira comments can carry their **own** visibility restriction
(`role`/`group`-restricted comments) that is *tighter* than the issue. The
normalizer captures it (`normalize_comment` stores `raw.comment_visibility`,
`normalizer.py:96-97`) but nothing enforces it: knowledge extracted from a
role-restricted comment currently inherits the **wider** issue scope → potential
over-exposure of a restricted comment's content.

**The fix (graded, pick per appetite):**

- **Minimum (don't widen):** if `comment_visibility` is present, do **not** emit
  that comment's prose knowledge with the issue's open project scope. Fail-closed:
  drop it from extraction (store in `raw` only) until Layer 4 is modeled. Safe,
  loses some knowledge.
- **Target:** translate `comment_visibility` into an extra scope —
  `jira:role:{name}` or reuse `jira:G{groupId}` — add it to the comment event's
  `extra_scope_ids`, register the grant in `scope_principals`, and let the same
  stacked-scope gate (§9) enforce it. This needs a new scope kind owned by
  `common/scopes.py` and a discovery step to resolve the role/group to members.

Decide in P6 implementation. Until then, the **minimum** is the default so we
never leak a restricted comment.

### 14.3 What does NOT change

- Discovery (§4–§7), the artifact shape (§3), the identifier contract (§2), and
  the fail-closed properties (§10) are all unchanged. P6 is strictly about making
  the **builder/gate** as fail-closed as discovery already is.
- The intersection semantics (§9 — project AND every security level) stay; #1
  just changes *where the gate reads the security levels from* (edges, not a
  scalar).
```
