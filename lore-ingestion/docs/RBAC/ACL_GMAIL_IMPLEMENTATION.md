# Gmail ACL — Implementation (LOR-83)

**Status:** Implemented + tested (LOR-83, 2026-06-18).
**Scope format agreed with:** Raghav (elysia-rbac).
**See also:** `SLACK_BACKEND_HANDOFF.md`, `MS365_BACKEND_HANDOFF.md`.

---

## Scope semantics

Gmail has no per-message channel ACL like Slack. Scope is derived from **thread participants** — every email address in from/to/cc/bcc across the full thread.

Format agreed with elysia-rbac:

```
person:{email}
```

Example: `person:alice@ezobooks.in`

This is defined in `common/scopes.py` as `person_email(email: str) -> str` and is locked by `pipeline/tests/test_scopes.py`.

**Why participant-based?**
Gmail threads are private by nature — only the people on the thread should see the knowledge extracted from it. There is no concept of a public channel or project to scope against.

**External senders:** External participants (outside the org domain) also receive a `person:{email}` scope id. Since elysia-rbac's `allowedScopes()` only resolves scopes for logged-in Lore users, external participants effectively cannot access the node — fail-closed by the rbac layer.

---

## How scope flows through the pipeline

| Stage | File | What happens |
|-------|------|-------------|
| Normalize | `infra/gmail/normalizer.py` | Computes `person:{email}` for every actor (from/to/cc/bcc); stamps `raw["scope_ids"]` and `raw["scope_id"]` on each `KnowledgeEvent` |
| Group | `pipeline/extraction/grouping.py` | Unions `raw["scope_ids"]` across all messages in the thread; sets `thread["scope_ids"]` and `thread["scope_id"]` |
| Extract | `pipeline/extraction/extract.py` | `_copy_thread_metadata()` copies `scope_ids` onto every extraction result (never touched by the LLM) |
| Validate | `pipeline/validator.py` | Passes `scope_ids` through unchanged |
| Build | `builders/unified_builder.py` | Writes `scope_ids` onto every knowledge node in Neo4j |

Outlook Mail follows the same format — `_populate_outlook_scopes()` in `extract.py` stamps `person:{email}` scope ids from actor emails after grouping.

---

## What knowledge nodes carry in Neo4j

```cypher
{
  decision_id: "dec:abc123",
  description: "Switch payment provider to Razorpay",
  scope_ids: ["person:alice@ezobooks.in", "person:bob@ezobooks.in"],
  scope_id:  "person:alice@ezobooks.in",   // first entry, for fast single-scope lookups
  source_ref: "18f2a3b4c5d6",              // Gmail thread id
  source_url: "https://mail.google.com/mail/u/0/#inbox/18f2a3b4c5d6"
}
```

---

## Fail-closed behavior

A knowledge node with an empty `scope_ids` array is **invisible to all users**. The builder's `_has_scope()` check gates the warning path — nodes without scope produce a loud log warning rather than silently entering the graph world-readable.

Verified by `infra/gmail/tests/test_acl_gate.py::test_empty_scope_fails_closed`.

---

## Backend wiring (elysia-rbac)

`allowedScopes()` must include `person:{logged_in_user_email}` in the user's scope set. The Neo4j query gate then resolves visibility:

```cypher
WHERE any(s IN n.scope_ids WHERE s IN $userScopes)
```

A Gmail knowledge node is visible to a user if and only if their email appears in the thread's participant list.

---

## Tests

| Test file | What it covers |
|-----------|---------------|
| `infra/gmail/tests/test_acl_gate.py` | Normalizer stamps scope, grouping unions across messages, validator preserves `scope_ids`, fail-closed on empty scope, single-message thread is scoped |
| `pipeline/tests/test_scopes.py` | `person_email()` format locked — breaking changes caught immediately |
| `tests/test_ms365_rbac.py` | Outlook Mail scope stamping via `_populate_outlook_scopes()` |

---

## Not done yet

- Postgres `scope_principals` backfill from Gmail participant lists.
- Backend query filter wiring (elysia-rbac `allowedScopes()` must add `person:{email}`).
- Full re-ingest with ACL on live data (LOR-103).
