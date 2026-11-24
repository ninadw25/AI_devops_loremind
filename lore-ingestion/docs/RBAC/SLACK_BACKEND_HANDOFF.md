# Slack ACL — Backend Handoff

**Status:** Ready for integration (2026-06-07).
**Audience:** lore-engine engineers wiring Slack query-time access control.

---

## What ingestion gives you

After a Slack ingestion run, two files land in `output/slack/`:

| File | What it contains |
|------|-----------------|
| `{org}_user_scopes.json` | **The one you need.** Flat lookup: for every Slack user, their full list of `scope_ids` they may read. Ready to load at startup. |
| `{org}_scope_principals.json` | Raw discovery artifact (normalised: scopes / principals / memberships as three lists). Source of truth for auditing. |

---

## `{org}_user_scopes.json` — full shape

```json
{
  "org": "ezobooks",
  "generated_at": "2026-06-07T08:30:00+00:00",
  "users": [
    {
      "principal_id": "slack:U789ABC",
      "display_name": "Ravi Kumar",
      "email": "ravi@ezobooks.in",
      "scope_ids": [
        "slack:workspace:T001",
        "slack:channel:C_GENERAL",
        "slack:channel:C_RANDOM",
        "slack:channel:C_DEVOPS_PRIVATE"
      ]
    },
    {
      "principal_id": "slack:U456DEF",
      "display_name": "Priya Shah",
      "email": "priya@ezobooks.in",
      "scope_ids": [
        "slack:workspace:T001",
        "slack:channel:C_GENERAL",
        "slack:channel:C_RANDOM"
      ]
    }
  ],
  "channel_directory": [
    { "scope_id": "slack:channel:C_GENERAL",       "name": "#general",  "is_public": true  },
    { "scope_id": "slack:channel:C_RANDOM",        "name": "#random",   "is_public": true  },
    { "scope_id": "slack:channel:C_DEVOPS_PRIVATE","name": "#devops",   "is_public": false }
  ]
}
```

**`scope_ids` is already expanded and ready.** Public channels are automatically included for every workspace member. Private channels appear only for users who are explicitly members. The backend does not need to do any joins or further expansion.

---

## How to wire it in the backend

### Step 1 — Load at startup (or per-request; file is small)

```python
import json
from pathlib import Path

user_scopes_path = Path("output/slack/{org}_user_scopes.json")
raw = json.loads(user_scopes_path.read_text())

# Build a lookup: principal_id → scope_ids
principal_to_scopes: dict[str, list[str]] = {
    u["principal_id"]: u["scope_ids"]
    for u in raw["users"]
}
```

### Step 2 — When a user logs in

The Bun.js backend authenticates the user and passes their identity to lore-engine. You need to map their Slack identity to a `principal_id`.

```python
def get_user_scope_ids(slack_user_id: str) -> list[str]:
    principal_id = f"slack:{slack_user_id}"   # format from common/scopes.py
    return principal_to_scopes.get(principal_id, [])
```

If the user is not in the file, return `[]` — the Neo4j gate will deny access to all scoped nodes (fail-closed). This is correct behaviour for a new hire not yet in the discovery artifact; re-run discovery to onboard them.

### Step 3 — Pass scope_ids to the Neo4j query

```python
user_scopes = get_user_scope_ids(slack_user_id)

cypher = """
CALL db.index.vector.queryNodes('knowledge_embeddings', $k, $embedding) YIELD node AS n, score
WHERE n.scope_ids IS NOT NULL
  AND size(n.scope_ids) > 0
  AND any(s IN n.scope_ids WHERE s IN $userScopes)
  AND all(s IN coalesce(n.scope_all_ids, []) WHERE s IN $userScopes)
  AND NOT coalesce(n.deleted, false)
RETURN n, score
ORDER BY score DESC
LIMIT $k
"""

results = session.run(cypher,
    embedding=query_embedding,
    k=20,
    userScopes=user_scopes,
)
```

The two WHERE clauses:
- `any(s IN n.scope_ids WHERE s IN $userScopes)` — OR gate: user must be in at least one of the node's channels
- `all(s IN n.scope_all_ids WHERE s IN $userScopes)` — AND gate: for Jira security levels (stacked restriction); always passes for Slack nodes because `scope_all_ids` is absent/empty

---

## What the scope_ids on Neo4j nodes look like

Every Slack knowledge node (`Decision`, `Task`, `Fact`, `Event`) in Neo4j carries:

```cypher
// Example node
{
  decision_id: "dec:abc123",
  description: "Switch to Razorpay for payments",
  scope_ids: ["slack:channel:C_DEVOPS_PRIVATE"],   // channel(s) where this was discussed
  scope_all_ids: [],                                // always empty for Slack
  source_thread_id: "slack:session:C_DEVOPS_PRIVATE:1717000000.0001",
  source_url: "https://ezobooks.slack.com/archives/C_DEVOPS_PRIVATE/p1717000000000100"
}
```

A node discussed in multiple channels (after deduplication merges it) accumulates all their scope_ids:

```json
"scope_ids": ["slack:channel:C_GENERAL", "slack:channel:C_DEVOPS_PRIVATE"]
```

→ Any member of either channel can see it.

---

## When to re-run

| Event | Action |
|-------|--------|
| New employee joins | Re-run discovery → re-run emitter. Until then, new user gets `[]` → sees nothing (safe). |
| New channel created | Same: re-run discovery → emitter. |
| User leaves a private channel | Re-run. Stale artifact means they can still read old nodes. Acceptable for now; re-run on any org-structure change. |
| Full re-ingestion | The emitter runs as part of the standard pipeline — nothing extra needed. |

**Command to refresh:**
```bash
# From lore-ingestion/
../.venv/bin/python -m infra.slack.run --org ezobooks --stage discovery
../.venv/bin/python -m builders.slack_acl_emitter --org ezobooks
```

---

## Failure modes

| Situation | Behaviour | Why safe |
|-----------|-----------|----------|
| `user_scopes.json` missing | Backend returns 0 results (or raise on startup) | File absence = not yet run discovery; don't serve blind |
| User not in file | `scope_ids = []` → query returns nothing | Fail-closed; not a data leak |
| Node has empty `scope_ids` | `size(n.scope_ids) > 0` guard excludes it | Fail-closed; unscoped nodes are invisible |
| Node marked `deleted: true` | `NOT coalesce(n.deleted, false)` excludes it | Soft-delete from W6 delta |

---

## What is NOT in scope here

- **Gmail nodes** — Gmail threads carry no `scope_ids` today (see `SCOPE_FILTER_HANDOFF.md`). Gmail nodes are currently invisible under scoped queries. This is a known open item.
- **Jira nodes** — handled separately; see `JIRA_BACKEND_HANDOFF.md`.
- **Cross-source identity** (mapping a Slack `principal_id` to the same person's Gmail address) — handled by the centralized identity resolver; see `CENTRALIZED_IDENTITY.md`.
