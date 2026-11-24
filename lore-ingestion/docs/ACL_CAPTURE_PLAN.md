> **Historical (as of 2026-09-25)** — superseded by `docs/RBAC/SCOPE_FILTER_HANDOFF.md` (AclScope/IN_SCOPE replaced by `scope_ids`/`scope_all_ids` arrays on 2026-06-06). Kept for context; do not treat as current.

# Loremind ACL Capture Plan — Tailored to lore-ingestion + Neo4j

**Status:** Design, ready to implement
**Inspiration:** `ACL slack and jira.md` (Glean architecture study; since removed, see git history)
**Supersedes the SpiceDB direction** in `../../docs/rbac-research/01-09` for the storage layer.
**Decisions locked (2026-05-25):** No SpiceDB. Postgres = policy/membership. Neo4j = knowledge + scope topology. Batch re-crawl only. Gmail + Slack + Jira. DMs skipped in v1.

---

## 0. The One Idea: Capture the *Scope*, Not the ACL List

Glean attaches a list of allowed principals ("restrict tokens") to every document in an
inverted index. We do not have documents and we do not have an inverted index — we have a
**Neo4j knowledge graph of LLM-extracted triples**, where many `Decision`/`Fact`/`Task`/`Event`
nodes are derived from a single conversation thread.

So we translate Glean's idea into graph-native form:

> **Every piece of knowledge records *which source container it came from* (the scope).
> Who can access that scope is resolved separately. Knowledge inherits its scope's access.**

A **scope** is the unit of access in the source system:

| Source | Scope = | Scope ID format | Who can access it |
|---|---|---|---|
| Gmail | the email thread | `gmail:thread:{thread_id}` | the thread participants (From/To/Cc/Bcc) |
| **Outlook Mail** | the mail conversation | `outlook:thread:{conversationId}` | the thread participants (From/To/Cc/Bcc) |
| Slack | the channel | `slack:channel:{channel_id}` | channel members (or whole workspace if public) |
| **MS Teams** | the team channel | `teams:channel:{teamId}:{channelId}` | team members (or private-channel members) |
| Jira | the project (+ issue security) | `jira:project:{key}`, `jira:seclevel:{id}` | resolved BROWSE_PROJECTS principals |

This is the same "container" insight as the prior provenance work, now expressed as Neo4j
topology instead of SpiceDB tuples. Note the **two scope shapes**, and that Microsoft 365
straddles both: **Outlook Mail is participant-bounded** (like Gmail — the thread *is* the
scope, no container) while **Teams channels are membership-bounded** (like Slack — the channel
is a container with a member list). The same connector emits both shapes.

---

## 1. Reconciling "store access control in Postgres" with "change the Neo4j graph"

Your instruction was *store policies/roles/access-control in Postgres* **and** *tell me what
changes to make in Neo4j*. These are not in conflict — the captured ACL has two halves that
live in two different stores, by design:

| Half | What it is | Mutability | Store | Why there |
|---|---|---|---|---|
| **Scope identity** | which scope each knowledge node came from | immutable (set once at ingestion) | **Neo4j** | it is provenance — it travels with the data, never changes |
| **Scope membership** | who can access each scope | mutable (people join/leave) | **Postgres** | it is policy — changes constantly, needs transactional updates and easy re-sync |

At query time we **join the two halves**:

```
user logs in
  → Postgres: who is this user, what scopes can their principals reach?  → allowed_scope_ids[]
  → Neo4j:    vector search WHERE the knowledge's scope ∈ allowed_scope_ids
```

That is exactly your sentence "*check if the data source ACL matches what the user can see*":
the data's scope is in Neo4j, what the user can see is computed from Postgres, and we match
them at query time.

---

## 2. Neo4j Graph Changes 

### 2.1 New node label: `:AclScope`

One node per source container. Created during the build stage.

```cypher
// Slack channel scope
MERGE (s:AclScope {scope_id: 'slack:channel:C123ABC'})
SET s.source = 'slack', s.kind = 'channel', s.is_public = false,
    s.tenant_id = 'ezobooks', s.created_at = datetime();

// Gmail thread scope
MERGE (s:AclScope {scope_id: 'gmail:thread:18f2a...'})
SET s.source = 'gmail', s.kind = 'thread', s.tenant_id = 'ezobooks';

// Outlook Mail conversation scope (participant-bounded, same shape as Gmail)
MERGE (s:AclScope {scope_id: 'outlook:thread:AAQkAD...'})
SET s.source = 'outlook_mail', s.kind = 'thread', s.tenant_id = 'ezobooks';

// MS Teams channel scope (membership-bounded, same shape as Slack)
MERGE (s:AclScope {scope_id: 'teams:channel:19:abc@thread.tacv2:def'})
SET s.source = 'ms_teams', s.kind = 'channel', s.is_private = false, s.tenant_id = 'ezobooks';

// Jira project scope + issue-security sub-scope
MERGE (s:AclScope {scope_id: 'jira:project:PROJ'})  SET s.kind = 'project';
MERGE (s:AclScope {scope_id: 'jira:seclevel:10001'}) SET s.kind = 'security_level';

// Workspace-wide scope (public Slack channels, "all licensed Jira users")
MERGE (s:AclScope {scope_id: 'slack:workspace:T456'}) SET s.kind = 'workspace';
```

### 2.2 New edge: `(:KnowledgeNode)-[:IN_SCOPE]->(:AclScope)` — **source of truth**

Every `Decision`/`Fact`/`Task`/`Event` links to the scope(s) it was derived from.

```cypher
MATCH (k:KnowledgeNode {fact_id: 'fact:abc'}), (s:AclScope {scope_id: 'slack:channel:C123ABC'})
MERGE (k)-[:IN_SCOPE]->(s);
```

This edge is the **authoritative** record of provenance. The property below is derived from it.

### 2.3 New properties on knowledge nodes — **denormalized for fast vector pre-filter**

The `IN_SCOPE` edge is the source of truth; these scalar/array properties are a denormalized
copy rebuilt from the edges, so the vector search can pre-filter inside the index (the
load-bearing performance finding from `06-neo4j-graphrag-acl.md`):

```cypher
SET k.scope_id        = 'slack:channel:C123ABC',   // PRIMARY scope — scalar, indexed, used in pre-filter
    k.extra_scope_ids = []                          // overflow for multi-scope derived nodes (see 2.5)
```

> **Source-of-truth rule:** if `IN_SCOPE` edges and `scope_id`/`extra_scope_ids` ever disagree,
> the edges win. The properties are rebuilt from edges by the builder; never hand-edit them.

Add a range index so the pre-filter is index-backed:
```cypher
CREATE INDEX knowledge_scope IF NOT EXISTS FOR (n:KnowledgeNode) ON (n.scope_id);
```

### 2.4 Group support: `:Group` nodes + `:MEMBER_OF` / `:CAN_ACCESS`

Source-native groups (Slack usergroups, Jira groups/project-roles) that grant scope access:

```cypher
MERGE (g:Group {group_id: 'jira:group:developers', tenant_id: 'ezobooks'});
MERGE (g)-[:CAN_ACCESS]->(:AclScope {scope_id: 'jira:project:PROJ'});
```

Person→scope and Person→group access edges are **also** mirrored into Postgres (§3); we keep
them in Neo4j too so "show me everything this person can see" is answerable as a pure graph
query when needed. Postgres remains the authority for the query-time allow-set computation.

### 2.5 Multi-scope derived nodes (DECISION — not left open)

`builders/post_processor.py` synthesizes cross-thread nodes (topic clusters, SUPERSEDES
chains). A node derived from threads in **two** channels must require access to **both**
(most-restrictive union, per `04-lineage-aware-acl.md`). Jira issues with a security level
are the same shape: knowledge from that issue is `IN_SCOPE` of **both** `jira:project:PROJ`
**and** `jira:seclevel:10001`.

**Chosen representation: scalar primary + overflow array.**
- `scope_id` = the primary scope (indexed → drives the vector pre-filter).
- `extra_scope_ids` = the additional required scopes (`[]` for the common single-scope case).
- A node is visible only if the user can access `scope_id` **AND every** id in `extra_scope_ids`.

Query pattern (single pre-filter + post-check, exact):
```cypher
WHERE k.scope_id IN $allowed_scope_ids
  AND all(s IN k.extra_scope_ids WHERE s IN $allowed_scope_ids)
```
95%+ of nodes have empty `extra_scope_ids` → the indexed `scope_id IN $allowed` pre-filter is
exact and fast. The `all(...)` post-check only does work on the multi-scope minority.

### 2.6 Remove the fake ACL

Delete `access_groups` from knowledge nodes (it was computed from LLM department tags in
`pipeline/validation/thread.py:79` via `common/rbac.py:compute_access_groups`). Keep
`department_tags` and `sensitivity` — but **only as soft hints / UI filters, never as the
access gate**.

---

## 3. Postgres Schema (the policy + membership store)

The advisor flagged this as the part most likely to be hand-waved — so here it is concretely.

```sql
-- A scope mirrors each :AclScope node (kept in sync by the builder)
CREATE TABLE scopes (
  scope_id    TEXT PRIMARY KEY,        -- 'slack:channel:C123ABC'
  tenant_id   TEXT NOT NULL,
  source      TEXT NOT NULL,           -- 'gmail' | 'outlook_mail' | 'slack' | 'ms_teams' | 'jira'
  kind        TEXT NOT NULL,           -- 'thread'|'channel'|'project'|'security_level'|'workspace'
  is_public   BOOLEAN DEFAULT FALSE
);

-- A principal = one source identity (Slack user, Jira account, email address)
CREATE TABLE principals (
  principal_id TEXT PRIMARY KEY,       -- 'slack:U012AB' | 'jira:5b10...' | 'email:alice@corp.com'
  tenant_id    TEXT NOT NULL,
  email        TEXT,                   -- cross-source merge key (nullable)
  display_name TEXT
);

-- THE load-bearing table: who can access which scope (captured at ingestion, re-synced each crawl)
CREATE TABLE scope_principals (
  scope_id     TEXT REFERENCES scopes(scope_id),
  principal_id TEXT,                   -- a user principal OR a group_id
  grant_source TEXT,                   -- 'gmail_participant' | 'outlook_participant' | 'slack_channel_membership' | 'teams_membership' | 'jira_browse_projects'
  PRIMARY KEY (scope_id, principal_id)
);

-- Source-native groups and their members
CREATE TABLE groups        ( group_id TEXT PRIMARY KEY, tenant_id TEXT, name TEXT, source TEXT );
CREATE TABLE group_members ( group_id TEXT, principal_id TEXT, PRIMARY KEY (group_id, principal_id) );

-- A logged-in Lore user maps to N source identities (alice the human = slack:U012 + jira:5b10 + email:alice@corp.com)
CREATE TABLE lore_users      ( user_id TEXT PRIMARY KEY, tenant_id TEXT, email TEXT, role TEXT );
CREATE TABLE user_principals ( user_id TEXT, principal_id TEXT, PRIMARY KEY (user_id, principal_id) );

-- Loremind's own admin IAM layer (the dual-authorization gate; coarse, Loremind-controlled)
CREATE TABLE iam_policies ( policy_id TEXT PRIMARY KEY, tenant_id TEXT, role TEXT, source TEXT, effect TEXT );
```

**Query-time allow-set computation (one SQL round-trip):**
```sql
-- Given the logged-in user, return every scope they may read
SELECT DISTINCT sp.scope_id
FROM user_principals up
LEFT JOIN group_members gm ON gm.principal_id = up.principal_id
JOIN scope_principals sp
  ON sp.principal_id = up.principal_id          -- direct grants
  OR sp.principal_id = gm.group_id              -- group grants
WHERE up.user_id = $user_id
UNION
SELECT scope_id FROM scopes WHERE is_public = TRUE AND tenant_id = $tenant_id;
```
The result `allowed_scope_ids[]` is passed straight into the Neo4j query from §2.5.

---

## 4. Per-Source Capture Plan

### 4.1 Gmail (already ingesting — lowest-effort win)

The ACL is **already parsed and then thrown away**. `infra/gmail/normalizer.py` extracts
sender (`From`) and recipients (`To`/`Cc`/`Bcc`) into `KnowledgeEvent.actors`, but the role is
flattened and the list never reaches the builder.

| Step | File | Change |
|---|---|---|
| Capture | `infra/gmail/normalizer.py` (`normalize_email_message`, ~L114-187) | Emit `scope_id = f"gmail:thread:{thread_id}"` and `participants = [{email, role}]` for from/to/cc/bcc onto the event. |
| Carry through thread | `pipeline/extraction/grouping.py` (`group_by_thread`) | Compute the thread's participant union and stamp `scope_id` on the grouped thread. |
| Carry through extract | `pipeline/triple_extractor.py` | Pass `scope_id` + `participants` through to the raw output dict (LLM does not touch them). |
| Preserve in validator | `pipeline/validation/thread.py` (L67-92) | Stop calling `compute_access_groups`. Copy `scope_id` + `participants` into the validated thread. |
| Emit to stores | `builders/` (new `acl_emitter.py`, §5) | Neo4j: `:AclScope` + `IN_SCOPE` + `scope_id`. Postgres: `scopes`, `principals`, `scope_principals` (one per participant). |

Scope granularity = thread. No container concept; external participants are recorded as
principals but are not Lore users, so they never appear in `user_principals`.

### 4.2 Slack (connector not built — see `docs/slack/` + `rbac-research/09-slack-acl.md`)

| Step | Where | Change |
|---|---|---|
| Fetch membership | new `connectors/slack/fetcher.py` | After `conversations.list`, call **`conversations.members`** per private/group channel. Public channel → `is_public=true`, link to `slack:workspace:{team}` scope (no per-member enumeration). |
| Normalize | new `connectors/slack/normalizer.py` | `scope_id = f"slack:channel:{channel_id}"`; carry channel members alongside. |
| Emit | `builders/acl_emitter.py` | Same three-store pattern. Filter out bots and Slack-Connect externals (`team_id != ours`). |

DMs (`is_im`) and group DMs skipped in v1.

### 4.3 Jira (designed in `ACL slack and jira.md` only — highest complexity)

Resolve the 4-layer permission stack into a principal set per project, then per issue:

| Layer | Resolution | Becomes |
|---|---|---|
| Project BROWSE_PROJECTS | expand user / group / projectRole / applicationRole grants to concrete principals | `scope_principals` rows for `jira:project:{key}` |
| Issue security level | `securitylevel/{id}/member` | second scope `jira:seclevel:{id}` → goes in the issue's `extra_scope_ids` (§2.5) |
| Comment visibility | narrow to role/group | comment knowledge gets the narrower scope |
| Dynamic (reporter/assignee) | resolve per-issue at crawl time | direct `scope_principals` rows for that issue's scope |

A Jira issue with a security level is the canonical multi-scope node: `scope_id =
jira:project:PROJ`, `extra_scope_ids = [jira:seclevel:10001]` — visible only to users who can
reach **both**. This is the same machinery as §2.5, which is why we resolved them together.

### 4.4 Outlook Mail (Microsoft 365 — reuses the Gmail path almost verbatim)

Outlook Mail is participant-bounded exactly like Gmail. The connector
(`connectors/outlook/normalizer.py`, designed in `docs/Outlook/LOR-14...md`) already produces
the **same `KnowledgeEvent` dataclass** as the Gmail normalizer — sender + To/Cc/Bcc recipients
in `actors`, `conversationId` as the thread key. So the ACL capture is the *same five steps as
§4.1*, with only the IDs changed.

| Step | Where | Change |
|---|---|---|
| Capture | `connectors/outlook/normalizer.py` (`normalize_outlook_message`) | Emit `scope_id = f"outlook:thread:{conversationId}"` and `participants = [{email, role}]` from `from` + `toRecipients`/`ccRecipients`/`bccRecipients`. Identity = AAD UPN/email (GUID already resolved to email by the `/mail/users` step). |
| Carry through | `pipeline/extraction/grouping.py` + `triple_extractor.py` + `validation/thread.py` | **No source-specific change** — these stages are already source-agnostic; the `scope_id`/`participants` ride through identically to Gmail. |
| Emit to stores | `builders/acl_emitter.py` | Same three-store pattern; `grant_source = 'outlook_participant'`. |

Scope granularity = conversation thread. No container, no extra membership API call — the ACL
is the message's own recipient set. External senders are recorded as principals but never enter
`user_principals`.

### 4.5 Microsoft Teams (Microsoft 365 — reuses the Slack path)

Teams channels are membership-bounded containers exactly like Slack channels. The access
boundary is the **team / channel membership**, NOT the reply-thread — so the scope is the
channel even though `replyToId` is used for threading inside extraction.

| Step | Where | Change |
|---|---|---|
| Fetch membership | `connectors/outlook/` Teams service | After listing teams (`/groups?$filter=...eq 'Team'`) and channels (`/teams/{id}/channels`), fetch members: **standard channel → team members** via `GET /groups/{teamId}/members`; **private channel → `GET /teams/{id}/channels/{channelId}/members`**. |
| Normalize | `connectors/outlook/normalizer.py` (`normalize_teams_message`) | Emit `scope_id = f"teams:channel:{teamId}:{channelId}"` (from `channelIdentity`); carry the channel/team member list alongside. |
| Emit | `builders/acl_emitter.py` | Same pattern as Slack; `grant_source = 'teams_membership'`. Filter out bot/application senders (`msg.from.application` not null) and external/shared-channel guests (AAD guest `#EXT#` UPNs) from the principal set. |

Teams **chats** (1-on-1 and group chats, `GET /chats/...`) are the DM equivalent → **skipped in
v1** (same decision as Slack DMs). Private and shared channels follow the same Slack edge-cases:
private channel = own member list; shared channel = filter external-tenant guests.

---

## 5. Pipeline Wiring: one new stage, minimal edits elsewhere

```
… existing stages …
  → Validator            (stop computing access_groups; pass scope_id + participants through)
  → Unified Builder      (add :AclScope nodes + IN_SCOPE edges + scope_id property; drop access_groups)
  → NEW: ACL Emitter     builders/acl_emitter.py
         ├── Neo4j:    MERGE :AclScope, MERGE (:Person)-[:CAN_ACCESS]->, (:Group)-[:CAN_ACCESS]->
         └── Postgres: upsert scopes, principals, scope_principals, groups, group_members
```

`acl_emitter.py` reads the validated triples (for `scope_id` per knowledge node) plus the
per-source membership captured during fetch, and writes both stores. Keeping it separate from
`unified_builder.py` means the ACL can be re-emitted (e.g. after a membership re-crawl)
without rebuilding the knowledge graph.

**Re-crawl (batch freshness):** each scheduled run re-fetches membership and does an upsert +
tombstone diff on `scope_principals` only. Knowledge nodes and `IN_SCOPE` edges are immutable
and untouched — membership drift is a Postgres-only update, which is the whole point of the
two-halves split.

---

## 6. Query-Time Flow (lore-backend, for context — not in scope to build here)

```
1. Authenticate user → lore_users row (user_id, role, tenant_id)
2. IAM gate (Postgres): does this role's iam_policies allow querying this source at all?
3. Allow-set (Postgres): run the §3 query → allowed_scope_ids[]
4. Neo4j vector search with the §2.5 pre-filter:
     CALL db.index.vector.queryNodes('knowledge_embeddings', $k, $query_vec) YIELD node AS k, score
     WHERE k.tenant_id = $tenant_id
       AND k.scope_id IN $allowed_scope_ids
       AND all(s IN k.extra_scope_ids WHERE s IN $allowed_scope_ids)
     RETURN k, score
5. Expansion queries (person/topic/thread siblings) MUST re-apply the same WHERE — today
   only the vector query filters; the expansion Cypher leaks. Fix when wiring the backend.
```

The LLM synthesizer only ever receives nodes that passed step 4/5 — denied content never
reaches the model.

---

## 7. Build Order

1. **Gmail capture** (§4.1) — ACL data already exists, just stop discarding it. Proves the
   `:AclScope` + Postgres model end-to-end on data you already ingest.
2. **Postgres schema + `acl_emitter.py`** (§3, §5) — the shared spine every other source reuses.
3. **Backend query filter** (§6) — make a real user see a real subset; validate the join works.
4. **Outlook Mail capture** (§4.4) — near-free once Gmail works; same participant-bounded path,
   different IDs. Bundle with the Outlook connector build.
5. **Slack + Teams channel capture** (§4.2, §4.5) — same membership-bounded path; do them
   together since the `:AclScope`-as-container logic and the bot/guest filtering are shared.
6. **Jira capture** (§4.3) — last, because the 4-layer resolution is the most work and reuses
   all the machinery proven in steps 1-5.

Two scope *shapes* (participant-bounded vs membership-bounded), five sources. Build one of each
shape first (Gmail, Slack), then the rest fall in as variants.

---

## 8. What This Deliberately Does NOT Do (v1)

- No SpiceDB / Zanzibar (dropped per your decision).
- No real-time webhooks (batch re-crawl only).
- No DM / 1:1 indexing — covers Slack DMs/group-DMs **and** Teams chats (`/chats/...`).
- No Calendar or SharePoint (out of scope for the Outlook connector itself, per LOR-14).
- No "see only messages after your join date" temporal ACL — current membership grants access
  to full scope history, exactly like the source systems behave natively.
- No per-message granularity — knowledge is thread-derived, so the **thread/channel/project is
  the finest ACL unit**, which matches both the source models and the extraction architecture.
