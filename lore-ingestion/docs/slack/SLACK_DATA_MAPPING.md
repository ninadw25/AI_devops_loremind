# Slack → KnowledgeEvent Data Mapping

Reference for how every Slack field maps to the unified `KnowledgeEvent` schema (`common/schemas.py`).

---

## Message Mapping

Each message from `conversations.history` and `conversations.replies` becomes one KnowledgeEvent.

| Slack field | KnowledgeEvent field | Notes |
|---|---|---|
| `sha256("slack:{org}:{channel_id}:{ts}")[:16]` | `id` | Org-scoped hash; stable across re-fetches |
| `"slack"` | `source` | Fixed |
| See Event Types table | `event_type` | Derived from context (channel type + thread position) |
| `ts` (converted from Unix float) | `timestamp` | ISO 8601 UTC; Slack timestamps are float strings like `"1234567890.123456"` |
| `"{channel_id}:{ts}"` | `source_id` | Channel-scoped; globally unique within workspace |
| First 100 chars of `text` | `title` | Truncated for display; full text in `body` |
| `text` + file/attachment text | `body` | Plain text plus flattened attachment/file previews and text-based file content |
| `user` | `primary_actor` | Slack user ID prefixed: `"slack:{user_id}"` |
| `[user, parent_user_id (if reply)]` | `actors` | Deduped; parent user included in thread replies |
| `channel.name` | `project` | Channel name (e.g. `#engineering`) |
| `channel.id` | `raw.slack_meta.channel_id` | Raw Slack channel ID; keep in raw until schema grows |
| `[channel_type, topic_tags...]` | `tags` | `"public"`, `"private"`, `"dm"`, `"group_dm"` + extracted topics |
| `thread_ts` | `parent_id` | Points replies at the parent KnowledgeEvent id; parent messages keep `parent_id = null` |
| `edited.ts` if present | `raw.edited_at` | Stored in raw; not promoted to schema field |
| Full Slack message JSON | `raw` | Retained for re-processing and audit |

### Event Types

| Context | Condition | `event_type` |
|---|---|---|
| Public channel, thread parent | `is_channel=True`, `ts == thread_ts` | `slack.message.posted` |
| Public channel, thread reply | `is_channel=True`, `ts != thread_ts` | `slack.thread.reply` |
| Private channel, thread parent | `is_group=True`, `ts == thread_ts` | `slack.message.posted` |
| Private channel, thread reply | `is_group=True`, `ts != thread_ts` | `slack.thread.reply` |
| Direct message | `is_im=True` | `slack.dm.message` |
| Group DM | `is_mpim=True` | `slack.gdm.message` |
| Thread with ≥ 3 replies | any | Promoted to `slack.thread.discussion` in normalizer |

Backup: If channel type flags are all false (unknown type) → default to `slack.message.posted`.

---

## Thread Grouping

A Slack thread is a parent message plus all its replies. Threads are the primary unit for LLM extraction — analogous to Gmail threads.

| Thread reply count | Strategy |
|---|---|
| 0 (standalone) | Message body only, up to 3000 chars |
| 1–10 replies | Parent body (1500 chars) + all replies |
| > 10 replies | Parent (1000 chars) + first 3 replies + `[N middle omitted]` + last 3 replies |

How to detect thread parent: `message.thread_ts == message.ts` (Slack sets `thread_ts` on the parent itself when the first reply arrives, and on all replies).

Backup: If `thread_ts` missing → treat as standalone message with no parent.

---

## Channel Mapping

Each channel from `conversations.list` is stored in org config, not as a KnowledgeEvent. Used to drive the fetch loop and tag messages.

| Slack field | Org config / usage |
|---|---|
| `id` | Used as fetch key in `conversations.history` |
| `name` | `project` field on all messages from this channel |
| `is_private` | Determines whether channel is fetched (config: `include_private`) |
| `is_im` | DM flag; config controls whether DMs are ingested |
| `is_mpim` | Group DM flag |
| `is_archived` | Skipped unless `include_archived_channels: true` in org config |
| `topic.value` | Stored in org config; used to seed topic tags |
| `purpose.value` | Stored in org config; used for channel context in LLM prompts |

---

## Actor Mapping

| Slack field | Usage | Notes |
|---|---|---|
| `user.id` | `Actor.id` as `"slack:{user_id}"` | Stable; never changes even on rename |
| `user.profile.real_name` | `Actor.display_name` | Preferred over `name`; fallback to `display_name` if needed |
| `user.profile.email` | `Actor.email` | **Requires `users:read.email` scope**; may be absent |
| `user.is_bot` | Filter flag | Bot users excluded from Actor nodes |
| `user.deleted` | Filter flag | Deactivated users still stored (historical messages reference them) |

Cross-source merge: Slack `Actor` nodes merge with Gmail `Actor` nodes via `email` when `users:read.email` scope is present. Without the scope, Slack actors remain as separate `slack:{user_id}` nodes until resolved manually.

---

## Noise Filter Rules

Applied in `normalizer.py` before emitting any event. These are Slack-specific — applied before reaching any Gmail noise checks.

| Condition | Action |
|---|---|
| `subtype` present and in noise list | Skip — system event, not human content |
| `bot_id` present OR `subtype == "bot_message"` | Skip — bot output |
| `text` stripped length < 3 chars | Skip — reaction proxy or empty |
| `text` is only an emoji or URL | Skip — low-signal |
| Message is a channel join/leave/topic change | Skip — administrative noise |
| `user` field absent and no `bot_id` | Skip — unattributable message |
| Channel `is_archived` AND `include_archived_channels: false` | Skip entire channel |

**Noise subtypes** (skip unconditionally):
`channel_join`, `channel_leave`, `channel_topic`, `channel_purpose`,
`channel_name`, `channel_archive`, `channel_unarchive`,
`group_join`, `group_leave`, `group_topic`, `group_purpose`,
`bot_message`, `tombstone`, `thread_broadcast`

Backup: If noise filter drops > 60% of a channel's messages → log warning in `slack_meta.json`; do not block ingestion.

---

## Fields With No Clean Mapping

| Slack field | Problem | Proposed handling |
|---|---|---|
| `reactions` | Array of emoji + users; not text content | Store in `raw`; skip for now. Future: emit `slack.reaction.added` events for sentiment signals |
| `blocks` | Rich text / interactive components JSON | Flatten to plain text via block-kit text extraction; fall back to `text` field |
| `attachments` | Link previews, file cards | Extract `fallback` or `title` field as supplemental body text |
| `files` | Uploaded files; content not in API response | Store file metadata in `raw.files`; file content requires separate Files API call (out of scope v1) |
| `edited.ts` | Edit history — original text is lost | Store `edited_at` in raw; surface as `[edited]` suffix on title |
| Deleted messages | `{"type":"message","subtype":"tombstone"}` | Emit `slack.message.deleted` with deleted flag in `raw.slack_meta` |
| `parent_user_id` | Only present on replies | Used to populate `actors` list in reply events |

File-content handling now has two paths:

| Slack field | Current handling | Notes |
|---|---|---|
| `attachments[*].text/fallback/title` | Appended to `body` | Captures docs, PR links, and shared summaries |
| `files[*].content/plain_text/preview/initial_comment` | Appended to `body` when present | Best-effort indexing for text-based files and Slack file previews |
| `files.info` payload | Merged before normalization | Enables extraction of file text that Slack did not include in the message history payload |

Slack-only fields that map to our schema only indirectly:

| Slack field | Current handling | Notes |
|---|---|---|
| `channel` object | `project` + `raw.slack_meta.channel_id` | `KnowledgeEvent` has no dedicated channel field yet |
| `thread_ts` on parent | `raw.slack_meta.thread_ts` | Parent messages keep `parent_id = null` |
| `reply_count` | `raw.slack_meta.reply_count` | Useful for ranking and discussion detection |
| `subtype` | `raw.slack_meta.subtype` | Preserved for auditing even when filtered |
| `is_shared` / `is_ext_shared` / `is_org_shared` | `tags += shared`, `raw.slack_meta.is_shared` | Marks Slack Connect / shared channels explicitly |

---

## Schema Extension Proposals

These fields are valuable but not in the current `KnowledgeEvent` schema. Proposed as additions:

| Proposed field | Type | Rationale |
|---|---|---|
| `channel_id` | `str` | Raw Slack channel ID for reverse-lookup without parsing `source_id` |
| `reply_count` | `int` | Signals thread importance; useful for ranking in retrieval |
| `reaction_count` | `int` | Engagement proxy without storing individual reactions |
| `is_edited` | `bool` | Flags that body may differ from original |
| `channel_id` | `str` | Canonical channel lookup key for replay and debugging |
| `bot_allowlist` | `set[str]` in org config | Keeps approved Jira/GitHub/CI bot notifications in the stream |
| `is_deleted` | `bool` | True when the message is a tombstone or delete event |

Until schema is extended, store these in `raw` under `raw.slack_meta`.

---

## Incremental Sync

Slack's `conversations.history` accepts `oldest` (Unix timestamp) to fetch only messages after a point.

```
FUNCTION build_jql_equivalent(channel_id, since):
    # Slack uses Unix epoch float, not ISO date
    oldest = datetime_to_unix(since - clock_buffer)
    GET /conversations.history?channel={channel_id}&oldest={oldest}&limit=200
```

Checkpoint stores `{channel_id: last_ts}` per channel. On resume, `oldest = last_ts` for that channel.

Unlike Jira's 10k JQL limit, Slack has no hard pagination cap — cursor-based pagination handles any volume.

---

## Rate Limits

| Endpoint | Tier | Approx. rate |
|---|---|---|
| `conversations.list` | Tier 2 | ~20 req/min |
| `conversations.history` | Tier 3 | ~50 req/min (marketplace); throttled for non-marketplace |
| `conversations.replies` | Tier 3 | ~50 req/min (marketplace); throttled for non-marketplace |
| `users.list` | Tier 2 | ~20 req/min |
| `users.info` | Tier 4 | ~100 req/min |

On 429: read `Retry-After` header (seconds). Default backoff: 5s.
For non-marketplace apps (development): assume conservative ~4 req/min for Tier 3 and design accordingly.
