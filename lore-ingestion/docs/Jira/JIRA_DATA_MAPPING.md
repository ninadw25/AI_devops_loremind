# Jira → KnowledgeEvent Data Mapping

Reference for how every Jira field maps to the unified `KnowledgeEvent` schema.

---

## Issue Mapping

| Jira field | KnowledgeEvent field | Notes |
|---|---|---|
| `sha256("jira:{org}:{issue.key}")[:16]` | `id` | Org-scoped hash; globally unique across multi-org deployments |
| `"jira"` | `source` | Fixed |
| See Event Types table below | `event_type` | Derived from `issue.fields.issuetype.name` |
| `issue.fields.created` | `timestamp` | ISO 8601 |
| `issue.key` | `source_id` | e.g. `ENG-1234` |
| `issue.fields.summary` | `title` | Plain string |
| `issue.fields.description` | `body` | ADF parsed to plaintext (see ADF section) |
| `issue.fields.reporter.accountId` | `primary_actor` | Stable Jira identifier |
| `[reporter, assignee, ...]` | `actors` | Deduped by `accountId`; reporter first |
| `issue.fields.project.key` | `project` | e.g. `ENG` |
| `issue.fields.labels + components` | `tags` | Merged into one list |
| `issue.fields.parent.key` (Sub-task) | `parent_id` | Links Sub-task to parent issue |
| Epic link custom field | `related_ids` | Epic key resolved via `jira_field_map` |
| `"archived"` appended if archived | `tags` | Added when `issue.fields.archived == true` |
| Full Jira issue JSON | `raw` | Kept for audit and reprocessing |

### Event Types by Issue Type

| Jira issue type | `event_type` |
|---|---|
| Epic | `jira.epic.created` |
| Story | `jira.story.created` |
| Bug | `jira.bug.created` |
| Sub-task | `jira.subtask.created` |
| Task / other | `jira.issue.created` |

Backup: If `issuetype.name` is missing or unrecognised → fall back to `jira.issue.created`.

---

## Comment Mapping

Each entry in `issue.fields.comment.comments[]` becomes a child event.

| Jira field | KnowledgeEvent field | Notes |
|---|---|---|
| `sha256("jira:{org}:{issue.key}#comment-{comment.id}")[:16]` | `id` | |
| `"jira"` | `source` | Fixed |
| `"jira.comment.added"` | `event_type` | Fixed |
| `comment.created` | `timestamp` | |
| `"{issue.key}#comment-{comment.id}"` | `source_id` | `#comment-` separator; issue key parseable by splitting on `#` |
| `comment.author.accountId` | `primary_actor` | |
| `[comment.author]` | `actors` | |
| `issue.key` | `parent_id` | Links to parent issue event |
| ADF-parsed `comment.body` | `body` | |
| `"Re: {issue.summary}"` | `title` | |

Backup: If `comment.author` is null (deleted user) → set `primary_actor` to `"unknown"` and skip actor entry.

---

## Changelog Mapping

Each entry in `issue.changelog.histories[]` is a child event, but only for significant fields.

**Significant fields:** `status`, `assignee`, `resolution`, `priority`
All other changelog fields are ignored.

| Jira field | KnowledgeEvent field | Notes |
|---|---|---|
| `sha256("jira:{org}:{issue.key}#change-{history.id}")[:16]` | `id` | |
| `"jira"` | `source` | Fixed |
| See Changelog Event Types table | `event_type` | Derived from changed field name |
| `history.created` | `timestamp` | |
| `"{issue.key}#change-{history.id}"` | `source_id` | |
| `history.author.accountId` | `primary_actor` | |
| `issue.key` | `parent_id` | |
| `"{field}: {from} → {to}"` | `title` | e.g. `Status: To Do → In Progress` |
| Full change detail | `body` | |

### Changelog Event Types by Changed Field

| Changed field | `event_type` |
|---|---|
| `status` | `jira.issue.transitioned` |
| `assignee` | `jira.issue.reassigned` |
| `resolution` | `jira.issue.resolved` |
| `priority` | `jira.issue.transitioned` |

Backup: If `history.items` is empty or field name is unrecognised → skip; do not emit event.

---

## Actor Mapping

| Jira field | Usage | Notes |
|---|---|---|
| `user.accountId` | `Actor.id` as `"jira:{accountId}"` | Stable; never changes |
| `user.displayName` | `Actor.display_name` | May change; use for display only |
| `user.emailAddress` | `Actor.email` | Often hidden by Jira privacy settings |

Actor deduplication: within one event, actors are deduped by `accountId` using a dict. If reporter and assignee are the same person, only one actor entry is emitted.

Cross-source merge: Jira actors cannot automatically merge with Gmail actors until `accountId → email` resolution is implemented in the person registry. Until then they remain separate nodes.

---

## ADF → Plaintext Conversion

Jira description and comment bodies are in Atlassian Document Format (ADF), a deeply nested JSON tree.

```
FUNCTION adf_to_text(node):
    output = []
    stack = [node]

    WHILE stack not empty:
        current = stack.pop()
        IF current.type == "text":
            output.append(current.text)
        ELSE IF current.type == "hardBreak":
            output.append("\n")
        ELSE IF current.type == "codeBlock":
            output.append("\n```\n")
            push SENTINEL_CODEBLOCK_END onto stack
            push children in reverse order onto stack
        ELSE IF current == SENTINEL_CODEBLOCK_END:
            output.append("\n```\n")
        ELSE:
            push current.content children in reverse order onto stack

    collapse whitespace; return joined string
```

Why iterative (not recursive): Python's default recursion limit is 1000. Real Jira descriptions with nested tables, lists, and panels can exceed 30 levels. The iterative stack approach handles any depth.

Backup: If an unknown ADF node type is encountered → skip its text, still process its children. No crash; minor formatting loss only.

---

## Custom Field Discovery (30-Day TTL)

Custom field IDs (e.g. `customfield_10014` for Epic Link) change after Jira migrations.

```
FUNCTION discover_fields(force=False):
    IF NOT force AND cached field map exists:
        age = now - jira_field_map_meta.discovered_at
        IF age <= 30 days:
            RETURN cached map
        ELSE:
            log "field map is {age} days old — rediscovering"

    fields = GET /field
    field_map = {field.name: field.id for field in fields if field.custom}

    save to org_config:
        jira_field_map = field_map
        jira_field_map_meta.discovered_at = now

    RETURN field_map
```

Force rediscovery: `--rediscover-fields` CLI flag passes `force=True`.

Field map usage:
- Epic Link → `related_ids` (cross-issue relationship)
- Sprint → `tags`
- Other custom fields → `tags` or appended to `body`

Backup: If `/field` endpoint is unavailable → use cached map (even if expired). If no cache exists → skip custom field extraction; log warning. Epic links will be missing but issue fetch continues.

---

## Body Truncation by Comment Count

Concatenating all comments can exhaust LLM context. Tiers applied in `normalizer.py`:

| Comment count | Strategy |
|---|---|
| 0 | `body[:3000]` |
| 1 – 10 | `body[:1500]` + all comment text |
| > 10 | `body[:1000]` + first 3 comments + `[N middle omitted]` + last 3 comments |

Backup: If a single comment body exceeds 10k chars (e.g. pasted log dump) → truncate that comment to 2000 chars before applying tier logic.

---

## Noise Filter Rules

Applied in `normalizer.py` before emitting any event. Jira events bypass Gmail-specific noise checks in `pipeline/filter.py` (line ~165: `if source == "jira": return False`).

| Condition | Action |
|---|---|
| `title` has < 2 words AND `body` has < 20 chars | Skip issue |
| Comment body stripped length ≤ 1 char | Skip comment |
| Comment flagged as deleted/hidden | Skip comment |
| Issue has no `summary` field | Skip issue |
| Issue is archived AND `include_archived_issues` is False | Skip issue |

Backup: If filter is too aggressive (legitimate short issues dropped) → adjust thresholds in org config rather than changing normalizer code.
