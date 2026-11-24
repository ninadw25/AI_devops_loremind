> **Historical (as of 2026-09-25)** — superseded by `docs/RBAC/SCOPE_FILTER_HANDOFF.md` (the gate is the `scope_ids`/`scope_all_ids` arrays, not IN_SCOPE edges). Kept for context; do not treat as current.

# Slack ACL for RBAC

**Audience:** anyone wiring Slack visibility into the knowledge graph's RBAC, or
reasoning about who-can-see-what.
**Purpose:** exactly how Slack ACL/RBAC works in this pipeline — the data model, how we
obtain users + memberships on each pathway, the fail-closed guarantee, and what gets
written for query-time enforcement.

> Companion to [`SLACK_PERSON_ID_RESOLUTION.md`](./SLACK_PERSON_ID_RESOLUTION.md)
> (identity) and [`docs/RBAC/Identity_resolution.md`](../RBAC/Identity_resolution.md).
> Scope/principal string formats are owned by `common/scopes.py` (locked by
> `pipeline/tests/test_scopes.py` — changing them is a data migration).

---

## 0. TL;DR

- **What RBAC gates:** *content* nodes (Decision/Task/Fact/Event) — never spine
  (Person/Topic). A Slack message becomes content gated by the **channel** it lives in.
- **Scope** = a visibility container: `slack:channel:{id}` or `slack:workspace:{team}`.
- **Principal** = who's granted: `slack:{user_id}`.
- **scope_principals** = the grant edges (`scope_id → principal_id`). This is the ACL.
- **Membership is obtained two ways, auto-selected by token capability:**
  - **Grid (primary, ~90%):** org-wide via `admin.users.list` + Discovery
    `conversations.info` member lists.
  - **Fallback (bot, ~10%):** `users.list` + `conversations.members` (bot must be in the
    channel).
- **Fail-closed:** a content node whose scope has no resolvable principals gets **no
  IN_SCOPE edge** → it's invisible to everyone rather than leaking. Deny over over-share.

---

## 1. The data model (3 objects)

Produced by `infra/slack/discovery.py`, written to
`output/slack/{org}_scope_principals.json` + `output/cypher/{org}_slack_acl.cypher`.

### AclScope — a visibility container
```jsonc
{ "scope_id": "slack:channel:C0B7P5683B8", "source": "slack", "kind": "channel",
  "is_public": true, "name": "#test-general", "tenant_id": "techtest",
  "channel_id": "C0B7P5683B8" }
```
Two kinds:
- `slack:channel:{channel_id}` — one per channel (public, private, DM, group-DM).
- `slack:workspace:{team_id}` — the workspace-wide scope (everyone in the workspace).

### Principal — who is granted
```jsonc
{ "principal_id": "slack:U0B8KEP3SKS", "email": "ravi@ezobooks.in",
  "display_name": "Ravi Kumar" }
```
Format `slack:{user_id}` (`common/scopes.py:slack_user`). The **same principal id** is
what the query layer derives for a logged-in user, so node-scope ∩ user-principals is a
direct set intersection.

### scope_principal — the grant edge (THE ACL)
```jsonc
{ "scope_id": "slack:channel:Cpriv", "principal_id": "slack:U1",
  "grant_source": "grid_discovery_membership" }   // or "slack_channel_membership"
```
`grant_source` records **how** the grant was derived (provenance / debugging).

---

## 2. How a message becomes gated content

Scope is attached at normalization and rides through the whole pipeline **thread-level**:

1. **Normalizer** (`normalizer.py:build_knowledge_event`) stamps every event:
   ```python
   raw["scope_id"]   = scopes.slack_channel(channel_id)   # "slack:channel:C…"
   raw["source_ref"] = f"{channel_id}:{ref_ts}"
   raw["source_url"] = "https://{workspace}.slack.com/archives/…"  # citation
   ```
2. The pipeline groups by thread and carries `scope_id` end-to-end, thread-level,
   through every stage (fail-closed, no-edge guarantee).
3. **Builder** creates `(content)-[:IN_SCOPE]->(:AclScope {scope_id})` and the
   `AclScope`/grant edges from the discovery artifact.
4. **Query time** (lore-backend): a user's principal set is intersected with each
   candidate node's scope principals. No matching grant → the node is filtered out.

**Public channel content** is gated by the channel scope, but because the channel is
public its visibility is *workspace-wide* — see §5.

---

## 3. How we get users + memberships — the two pathways

Auto-routed by `infra/slack/discovery.py:run_discovery()` on
`capabilities.acl_pathway` (`ADMIN_ENUM` = Grid, else `BOT_MEMBERSHIP`).

### 3a. Fallback / bot pathway — `discover_scopes()`

```
users.list                → directory (principals: id, email, name)
conversations.list        → channels (public + private the bot can see)
for each PRIVATE channel:
    conversations.members → member user ids → scope_principals
                            grant_source = "slack_channel_membership"
public channels           → NO explicit principals (workspace-wide, see §5)
```

- **Membership needs the bot to be IN the channel.** `conversations.members` is
  visibility-filtered: a private channel the bot hasn't joined returns nothing → that
  channel's ACL is unresolvable on this pathway.
- External principals are filtered out (`_is_external_principal`: different `team_id`,
  bot, deleted, `#EXT#`).

### 3b. Grid pathway (primary) — `grid_discover_scopes()`

```
admin.users.list              → org-wide directory (build_grid_directory)
admin.roles.listAssignments   → roles_by_user (RBAC roles, §7)
usergroups.list (include_users)→ usergroup → member user ids (§6)
discovery.conversations.list  → ALL channels org-wide (no bot membership needed)
for each PRIVATE channel:
    discovery.conversations.info.members → member ids → scope_principals
                            grant_source = "grid_discovery_membership"
    (members not resolvable → emit NOTHING — fail-closed)
public channels               → NO explicit principals (workspace-wide, §5)
external/unknown member ids   → skipped (not in directory)
```

- **Why Grid is required for complete ACL:** Slack has **no admin Web-API method** that
  lists a private channel's members org-wide. Only the **Discovery API**
  (`conversations.info`) returns the full member set for private channels the app isn't in.
  That's why Discovery is mandatory on Grid for trustworthy private-channel ACL.
- **Fail-closed** is enforced in code: an unresolved private channel contributes zero
  principals → no one is granted → its content is invisible (logged as
  `… private channels unresolved → fail-closed`).

---

## 4. Coverage / completeness matrix

| Capability | Fallback (bot) | Grid (admin + Discovery) |
|------------|----------------|---------------------------|
| Public channels enumerated | bot-visible only | **all, org-wide** |
| Private channels enumerated | only those bot joined | **all, org-wide** |
| Private-channel members | only if bot is a member | **all (Discovery `conversations.info`)** |
| Cross-workspace (Grid org) | ❌ single workspace | ✅ all workspaces |
| Org roles (admin/owner/custom) | ❌ | ✅ `admin.roles.listAssignments` |
| Usergroups → members | ⚠️ `usergroups.list` if scope | ✅ incl. admin usergroups |
| Deactivated users in directory | partial | ✅ |
| **Trustworthy private ACL** | **partial / best-effort** | **complete & deterministic** |

**Used most: Grid.** Treat `grid_discover_scopes` as the production path; `discover_scopes`
is the correct, smaller-coverage fallback for non-Grid workspaces.

---

## 5. Public vs private visibility model

- **Private channel** → explicit principal set = its members. Only those users' content
  grants exist. This is the tightest, most important ACL.
- **Public channel** → emits a scope row but **no explicit `scope_principals`**. Public
  content is visible to **everyone in the workspace**, modeled via the
  `slack:workspace:{team_id}` scope (every workspace member is implicitly a principal of
  it). This avoids materializing N-members × M-public-channels grant edges.
- **Net rule for the query layer:** a node is visible to a user if
  `node.scope_principals ∩ user.principals ≠ ∅` **OR** the node's channel is public and the
  user belongs to that workspace.

> Implementation note: the workspace-membership grant (who is in the workspace) is the
> directory itself (`principals[]`). The query layer treats workspace membership as the
> implicit grant for public scopes.

---

## 6. Usergroups (Slack `@team-handle`)

Usergroups (`usergroups.list` / `admin.usergroups`) are **expanded to their member user
IDs** (`build_grid_directory().usergroup_members`) rather than introduced as a new
principal type. This keeps the ACL model **user-level** — every grant is ultimately
`slack:{user_id}` — so the query-time intersection stays a simple set op. If a channel's
posting/visibility is governed by a usergroup, expand the group → users → principals.

---

## 7. RBAC roles vs channel ACL (Grid)

Two distinct layers — don't conflate:

| Layer | Source | Question it answers |
|-------|--------|---------------------|
| **Channel ACL** (visibility) | membership (§3) | *Can this user SEE this content?* |
| **RBAC roles** | `admin.roles.listAssignments` → `roles_by_user` | *What ORG ROLE does this user hold (admin/owner/custom)?* |

Channel ACL is what gates knowledge nodes. Roles are extra signal (e.g. an org admin may
get broader query privileges, or roles may inform sensitivity policy) — captured on Grid,
attached to the principal, and available for higher-level RBAC policy. Roles are **not**
auto-expanded into channel grants.

---

## 8. DMs & group DMs

- `im` (DM) and `mpim` (group DM) are scopes too (`slack:channel:{id}`), with principals =
  the conversation participants.
- Require `im:read/history` / `mpim:read/history` (bot) or Discovery (Grid).
- These are the most sensitive scopes — same fail-closed rule applies. By default the
  fetcher excludes DMs unless `--include-dms`.

---

## 9. External users & Slack Connect

- Members from a **different `team_id`**, or carrying `#EXT#`, are **external**
  (`_is_external_principal`). They are **excluded from internal principal sets** so external
  collaborators don't silently gain internal grants, and so an external id isn't mistaken
  for an internal person.
- On Grid, Slack Connect shared channels are enumerable; external members should be marked
  external and handled by policy (typically excluded from internal RBAC).

---

## 10. Artifacts produced

```
output/slack/{org}_scope_principals.json   # {scopes[], principals[], scope_principals[]}
output/cypher/{org}_slack_acl.cypher       # MERGE (:AclScope) + grant edges
```

`ScopePrincipalArtifact` (`discovery.py`) is sorted deterministically (idempotent
re-runs → byte-identical output; locked by `test_discovery_idempotency_ci.py`).

---

## 11. Query-time enforcement (how it actually denies)

1. A logged-in user resolves to a set of principals: `{slack:U…}` (+ workspace membership,
   + their `person_id`'s other source principals).
2. Each candidate knowledge node carries its `scope_principals` (via `IN_SCOPE` → `AclScope`
   → grants) in Neo4j.
3. **Visible iff** the intersection is non-empty (or public-channel + workspace member).
4. **No IN_SCOPE edge / no grant ⇒ denied.** A node that lost its scope (unresolved
   membership, fail-closed) simply never matches → invisible. This is the safety property:
   the failure mode is *over-denial*, never *over-exposure*.

---

## 12. Gotchas & TODO

- **Bot must be a channel member** on the fallback path or it reads nothing / resolves no
  members — auto-join (`run.py`) handles public channels; private channels need a manual
  invite. (We hit this in testing: 0 events until the bot joined.)
- **Grid private ACL depends on Discovery being enabled** (Org-Owner-approved app). Until
  provisioned, private-channel ACL on a Grid org falls back / fail-closes.
- **Public-scope query rule must be implemented in the backend** (§5) — the artifact
  intentionally omits per-member public grants; the query layer supplies the implicit
  workspace grant.
- **`admin.users.list` / `conversations.info` field shapes are `# ASSUMED`** in code
  (`admin.py`, `discovery_api.py`) — verify against the first real Grid token before
  relying on Grid ACL in production.
- **Usergroup-driven channel visibility** (channels whose access is granted via a
  usergroup) — expansion logic exists for group→users; wiring group→channel grants is a
  TODO for fuller Grid ACL.
