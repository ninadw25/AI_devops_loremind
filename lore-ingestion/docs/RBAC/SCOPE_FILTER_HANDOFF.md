# Scope Filtering — Backend Handoff

**Status:** LOCKED 2026-06-06. Supersedes the `AclScope`-node + `IN_SCOPE`-edge gate.
**Audience:** lore-engine (retrieval / RBAC). Ingestion now stamps scopes as
**array properties on each knowledge node** — the backend gate becomes a plain
array-intersection. No graph traversal, no `AclScope` nodes.

---

## TL;DR for backend

Every knowledge node (`Decision`, `Task`, `Fact`, `Event`) carries:

| Property | Meaning | Semantics | Example |
|----------|---------|-----------|---------|
| `scope_ids` | the containers/principals it was seen in | **OR** (any-match) | `['slack:channel:C123','slack:channel:C777']` |
| `scope_all_ids` | additional stacked restrictions (Jira only) | **AND** (all-match) | `['jira:seclevel:10000']` |

A logged-in user has a **principal set** the backend assembles (we do **not**):

```
userScopes = {own person-id}                       # for Gmail participant nodes
           ∪ {slack:channel:* the user is a member of}   # resolved live
           ∪ {jira:project:* / jira:U* / jira:G* grants}
```

**The gate (one Cypher predicate):**

```cypher
WHERE n.scope_ids IS NOT NULL AND size(n.scope_ids) > 0          // fail-closed
  AND any(s IN n.scope_ids        WHERE s IN $userScopes)        // OR  bucket
  AND all(s IN coalesce(n.scope_all_ids, []) WHERE s IN $userScopes)  // AND bucket
```

- **Fail-closed:** a node with null/empty `scope_ids` matches **nobody**. Never
  default-open. If you ever see unscoped nodes leaking, the bug is here.
- For Slack/Gmail, `scope_all_ids` is absent/empty → the `all(...)` clause is
  trivially true → pure OR. The AND bucket only bites for Jira security levels.

---

## Why this shape (first principles)

The old design made each scope a `(:AclScope)` node and linked every knowledge
node to it via `IN_SCOPE`. That only pays off if **membership also lives in the
graph** (`Person -[:MEMBER_OF]-> AclScope`) so visibility is one pattern-match.
It does not — the user→scope mapping lives in the **backend**. So the `AclScope`
node was structure we never traversed. Collapsing it to an array property:

- removes a redundant node label + edge type over ~50k+ nodes,
- makes the multi-scope **union** a clean set operation at build time,
- keeps the exact same fail-closed guarantee.

## How a node gets multiple scopes (the union)

Scope is attached at the **session/thread** level (`thread.scope_id`). A node
inherits the scope of **every** session it appears in. After **dedup merges**
the same Project-X `Decision` across the `#sales` and `#devops` channels, that
canonical node appears in **both** sessions, so the builder unions their scopes:

```
scope_ids = ['slack:channel:sales', 'slack:channel:devops']
```

→ a member of *either* channel sees it. This is computed in
`builders/unified_builder.py:_collect_node_scope_sets()` (one pass over all
sessions, union per node), then written once. Deterministic, idempotent — no
per-occurrence edge emission, no `apoc.coll.toSet`.

## Per-source scope vocabulary

| Source | `scope_ids` holds | Membership resolved… | Note |
|--------|-------------------|----------------------|------|
| **Slack** | `slack:channel:{C}` (one per channel the node appears in) | **dynamically** in backend — a new hire sees channel history | OR |
| **Jira** | `jira:project:{KEY}` | dynamically (project role) | OR (usually 1) |
| **Jira (stacked)** | `jira:seclevel:{id}` goes in **`scope_all_ids`** | dynamically | **AND** — required *in addition* to the project |
| **Gmail** | the thread participants as person-principals (to/cc/bcc) | **static** — recipient list is fixed; a new hire must NOT see old mail | OR |

The Slack/Jira "store the container" choice keeps membership **dynamic**
(resolved at query time). Gmail has no container, so it stores the **explicit
participants** — which is correct, because email visibility *is* static.

> ⚠️ **Gmail scope_ids is NOT wired yet.** Today Gmail threads carry no
> `scope_id`, so Gmail nodes get **empty `scope_ids` → invisible to scoped
> queries (fail-closed)**. Populating Gmail participants into `scope_ids` is a
> follow-up (see SLACK_PIPELINE.md / Gmail backlog). Until then, scope filtering
> is **Slack/Jira-only**; Gmail must be served on an unscoped path or it returns
> nothing. Flagged so backend doesn't silently get zero Gmail results.

## Tombstones (edit/delete delta)

When a Slack message is edited/deleted, the W6 delta path re-extracts the
session and **soft-deletes** removed nodes:

```cypher
SET n.deleted_at = datetime(), n.deleted = true,
    n.scope_ids = [], n.scope_all_ids = []   // empty → fails closed
```

So backend should additionally exclude `n.deleted = true` (defence in depth), but
even if it doesn't, the emptied `scope_ids` makes a tombstoned node invisible.

## What backend must change

1. Replace any `MATCH (n)-[:IN_SCOPE]->(s:AclScope) WHERE s.scope_id IN $userScopes`
   with the array predicate above.
2. Build `$userScopes` = own person-id ∪ live Slack channel memberships ∪ Jira grants.
3. Keep fail-closed: null/empty `scope_ids` ⇒ excluded.
4. (Optional) also filter `NOT coalesce(n.deleted,false)`.

`AclScope` nodes may still exist (Jira Pass-0 reference data) but are **no longer
the gate** — do not rely on `IN_SCOPE` edges; they are no longer emitted for
knowledge nodes.

## Open / untested

- Gmail `scope_ids` population (above) — **not built**.
- Jira `scope_all_ids` AND-semantics across a deduped node that spans two security
  levels — rare, **untested**; current behaviour unions both into the AND bucket
  (most-restrictive). Revisit if it ever fires.
- Incremental delta (W6) writes `scope_ids` from the **changed** sessions only; a
  node shared across changed + unchanged sessions could lose the unchanged scopes
  until the next full build. Full `unified_builder` run is always authoritative.
