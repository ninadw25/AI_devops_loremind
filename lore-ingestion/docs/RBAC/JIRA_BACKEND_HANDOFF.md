# Jira RBAC — Backend Handoff

**Audience:** elysia-backend (Bun.js gateway) + lore-engine (Python query service).  
**Status:** LOCKED — reflects the as-built ingestion pipeline as of 2026-06-07.

This document tells the backend exactly what ingestion produces, where to find it,
and what to do with it to enforce Jira access control at query time.

---

## 1. The model in one sentence

Ingestion stamps every Jira knowledge node in Neo4j with the scopes it belongs to.
The backend looks up the logged-in user's scopes from a pre-built artifact and
passes them to the query layer as a filter. No graph traversal, no re-computation.

---

## 2. What ingestion produces (the two artifacts)

### 2.1 `output/jira/{org}_jira_person_scopes.json` — **the primary RBAC artifact**

A pre-flattened map: **accountId → list of scope IDs the person can access**.

```json
{
  "org": "loremind",
  "source": "jira",
  "person_count": 6,
  "scopes": {
    "712020:f90382db-...": [
      "jira:project:ENG",
      "jira:project:OPS",
      "jira:project:HR",
      "jira:seclevel:10001"
    ],
    "712020:9942dc19-...": [
      "jira:project:ENG",
      "jira:seclevel:10000",
      "jira:seclevel:10001"
    ]
  }
}
```

- Keys are **bare accountIds** (no `jira:U` prefix) — same format Jira returns in
  its API responses and what you get when a user authenticates via Atlassian OAuth.
- Values are the complete, pre-expanded scope list: direct grants + group membership
  grants + security levels, all in one flat array.
- App/bot accounts (`account_type == "app"`) are **excluded**. Only human Atlassian
  accounts appear here.
- Re-generated on every ingestion run by `pipeline/jira/identity.py::emit_person_scope_map()`.

### 2.2 `output/jira/{org}_jira_identities.json` — person directory

Who exists in Jira (identity facts, not scopes). Used by the central identity
resolver to link Jira accounts to persons across sources.

```json
{
  "org": "loremind",
  "source": "jira",
  "count": 18,
  "identities": [
    {
      "accountId": "712020:f90382db-...",
      "display_name": "Reia Baid",
      "email": "reiab1101@gmail.com",
      "account_type": "atlassian",
      "source": "acl_principals"
    }
  ]
}
```

The backend does **not** need this for query-time RBAC. It is consumed by the
identity resolver during ingestion to link persons across Gmail/Slack/Jira.

---

## 3. What Neo4j contains (node-side scoping)

Every Jira knowledge node in Neo4j carries two array properties:

| Property | Type | Semantics | Example |
|---|---|---|---|
| `scope_ids` | `string[]` | OR-bucket — user needs **any one** match | `["jira:project:OPS"]` |
| `scope_all_ids` | `string[]` | AND-bucket — user needs **all** matches | `["jira:seclevel:10001"]` |

Node types that carry scopes (all have the `:KnowledgeNode` secondary label):

| Node type | Label | Scope source |
|---|---|---|
| `Issue` | `:Issue:KnowledgeNode` | `jira:project:KEY` from the issue's project; `jira:seclevel:ID` if the issue had a security level |
| `Decision` | `:Decision:KnowledgeNode` | Inherited from the Jira issue thread it was extracted from |
| `Fact` | `:Fact:KnowledgeNode` | Same |
| `Topic` | `:Topic:KnowledgeNode` | Unioned from every thread/issue that mentioned it |

Node types that do **not** carry scopes (structural/identity, not queried directly):

| Node type | Label | Why no scope |
|---|---|---|
| `Sprint` | `:Sprint` | Container label, no content — only reachable by traversing from a gated Issue |
| `Component` | `:Component` | Same |
| `Version` | `:Version` | Same |
| `Person` (Jira) | `:Person:JiraPerson` | Identity anchor, not content — the accountId IS the scope principal |

---

## 4. The query flow (step by step)

```
1. User authenticates via Atlassian OAuth
   → Bun.js backend receives accountId (e.g. "712020:f90382db-...")

2. Bun.js backend looks up accountId in {org}_jira_person_scopes.json
   → gets userScopes = ["jira:project:ENG", "jira:project:OPS", "jira:seclevel:10001"]

3. User sends a query ("what decisions were made about the payment API?")
   → Bun.js forwards { query, userScopes } to lore-engine

4. lore-engine runs Neo4j query with the scope filter:

   MATCH (n:KnowledgeNode)
   WHERE n.scope_ids IS NOT NULL AND size(n.scope_ids) > 0
     AND any(s IN n.scope_ids WHERE s IN $userScopes)
     AND all(s IN coalesce(n.scope_all_ids, []) WHERE s IN $userScopes)
   ...

5. Only nodes the user can access are returned. Done.
```

---

## 5. Scope ID format reference

All scope ID strings are owned by `common/scopes.py` in lore-ingestion. Never
hardcode these — use the constants if building any tooling that reads the artifact.

| Format | Meaning | Example |
|---|---|---|
| `jira:project:{KEY}` | A Jira project — all issues in this project | `jira:project:ENG` |
| `jira:seclevel:{id}` | A Jira issue security level — specific issues only | `jira:seclevel:10001` |

Security levels are **stacked on top of** the project scope. A user needs:
- Their `scope_ids` to contain the project scope (`jira:project:ENG`), AND
- Their `scope_ids` to contain the security level (`jira:seclevel:10001`)

...to see a security-restricted issue. This is why the scope list in the artifact
includes both project scopes and security level scopes for the same person.

---

## 6. The Neo4j Cypher gate (copy-paste ready)

```cypher
WHERE n.scope_ids IS NOT NULL
  AND size(n.scope_ids) > 0
  AND any(s IN n.scope_ids WHERE s IN $userScopes)
  AND all(s IN coalesce(n.scope_all_ids, []) WHERE s IN $userScopes)
```

- `$userScopes` — the list from step 2 above, passed as a query parameter.
- `scope_ids` check (OR): passes if the user is in at least one of the node's
  project scopes.
- `scope_all_ids` check (AND): passes if the user is in every security level the
  node requires. Empty `scope_all_ids` → trivially passes (no restriction).
- **Fail-closed**: `scope_ids IS NOT NULL AND size > 0` means a node with null or
  empty scopes matches nobody. This is intentional — a node that lost its scope
  during a bad build is invisible, not public.

---

## 7. What the backend does NOT need to do

- **No group expansion** — groups are already expanded into per-person scope lists
  in the artifact. The backend gets a flat list, not a group hierarchy.
- **No discovery re-computation** — the artifact is pre-built by ingestion and
  refreshed on each run.
- **No cross-source scope merging** — Jira scopes are Jira-only. A Jira node is
  never visible because the user has a Slack scope, and vice versa. Each source
  is a separate query context.
- **No person_id translation for Jira** — the artifact is keyed by raw `accountId`,
  which is what Atlassian OAuth returns directly. No lookup table needed.

---

## 8. Where the files live and when they update

| Artifact | Path | Updated by | When |
|---|---|---|---|
| Person scope map | `output/jira/{org}_jira_person_scopes.json` | `pipeline/jira/identity.emit_person_scope_map()` | Every ingestion run (smoke or full) |
| Identities | `output/jira/{org}_jira_identities.json` | `pipeline/jira/identity.emit_identities()` | Every ingestion run |
| Raw ACL artifact | `output/jira/{org}_scope_principals.json` | `infra/jira/discovery.py` + `run.py` | Every fetch (discovery phase + per-issue dynamic grants) |

The **person scope map** is derived from the raw ACL artifact — if you re-run
discovery without re-running the smoke/full pipeline, the map may be stale. Always
run the full pipeline (`infra.jira.run --stage smoke` or `--stage full`) to keep
the map in sync.

---

## 9. Incomplete ACL projects

If discovery could not read a project's permission scheme (e.g. SCRUM returned
HTTP 403), that project has **zero grant edges** in the artifact. No user will
have `jira:project:SCRUM` in their scope list → all issues from that project are
invisible to everyone. This is **intentional fail-closed behaviour**, not a bug.

Check `output/jira/{org}_jira_meta.json` → `acl_incomplete_projects` for a list
of affected projects after each run.
