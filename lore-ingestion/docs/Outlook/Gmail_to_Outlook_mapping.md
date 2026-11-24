Gmail → Outlook (Microsoft Graph) component mapping for LOR-64
=============================================================

Purpose
-------
This document maps the implementation components in `infra/gmail/fetcher.py`
and `infra/gmail/run.py` to their Outlook / Microsoft Graph counterparts for
LOR-64. For each Gmail component we indicate whether a direct equivalent
exists (reusable pattern) or a new implementation is required because Graph
behaves differently.

Summary guidance
----------------
- Reuse repository conventions: CLI runner patterns, JSONL append, atomic
  writes, `common.checkpoint` semantics, `common.output_paths` usage, hashing
  and `KnowledgeEvent` normalization contract.
- Implement new Graph-specific primitives before fetcher/normalizer code:
  Azure AD token cache (client credentials), a `graph_get()` wrapper with
  pagination + 429/Retry-After handling, and `DeltaStore` to persist per-mailbox
  delta tokens. These are required because Graph offers delta queries and
  different paging/headers.

Mapping table
-------------

- Component: Domain user listing
  - Gmail implementation: Admin SDK via DWD (`GmailFetcher.list_users()`)
  - Outlook equivalent: Microsoft Graph `GET /users` (app-only or delegated)
  - Direct? Partial — same high-level function (list users) but auth + fields
    differ.
  - Notes / work required:
    - Implement `graph_list_users()` that paginates @odata.nextLink.
    - Decide auth mode: app-only (client credentials) is recommended for
      tenant-wide mailbox access (requires Application permissions).
    - Map suspended/inactive detection (Graph has `accountEnabled` / `signInActivity`).

- Component: Authentication
  - Gmail: Service account + Domain-Wide Delegation (DWD) via JSON key
  - Outlook: Azure AD (client credentials) or delegated tokens
  - Direct? No — new implementation required.
  - Notes:
    - Implement `get_access_token()` + in-process cache + persistent token
      cache if desired (short-lived tokens). Store secrets in env via
      `common/config.py` entries.
    - Add `azure_client_id`, `azure_client_secret`, `azure_tenant_id` to config.

- Component: Pre-fetch query building (`build_query()`)
  - Gmail: rich Gmail search operators (category:, from:, after:seconds)
  - Outlook: Graph supports `$filter`, `$search` (limited) and mailFolder scope
  - Direct? No — Graph search differs and is less expressive; requires new
    filtering design.
  - Notes:
    - Implement best-effort server-side filters (filter by date, from/to domains).
    - Fall back to client-side filtering for complex cases.

- Component: Listing message IDs (messages.list + pagination)
  - Gmail: `users.messages.list` with `pageToken` → collects IDs, then batch fetch
  - Outlook: `GET /users/{id}/mailFolders/inbox/messages` or `GET /users/{id}/messages`;
    Graph supports `$top` and `@odata.nextLink` pagination and `delta` queries
  - Direct? Partial — listing exists, but Graph's preferred incremental path
    is `delta`. Implement `graph_list_messages()` and `graph_messages_delta()`.
  - Notes:
    - For initial historical import, use paginated GET; for incremental sync,
      prefer delta queries and DeltaStore.

- Component: Batch fetch of message content (`_fetch_batch`)
  - Gmail: batch endpoint + fallback to individual fetches
  - Outlook: Graph has a batch API but with size/complexity limits and a
    different format (max 20 subrequests); direct usage may be limited.
  - Direct? Partial — pattern similar but implementation differs.
  - Notes:
    - Implement `graph_batch_get()` optionally, but expect to frequently use
      individual message GETs with concurrency control.

- Component: Single-message fetch with retry (`_get_message_with_retry`)
  - Gmail: honors `Retry-After`, exponential backoff on 5xx/network errors
  - Outlook: Graph requires identical behavior: honor `Retry-After` header and
    backoff on 5xx/transport errors
  - Direct? Yes — behavior same; must implement new `graph_get()` with this
    policy (note header parsing and HTTP-date support for Retry-After).

- Component: Message identifiers and watermarking
  - Gmail: `internalDate` (ms string) + Gmail message `id` + RFC-2822 Message-ID
  - Outlook: `receivedDateTime` (ISO 8601), `id` (Graph opaque id), `internetMessageId`
  - Direct? No — mapping required.
  - Notes:
    - Replace `internalDate` logic with `receivedDateTime` parsing; store epoch
      when creating watermarks or keep delta tokens (preferred).
    - For deduplication prefer `internetMessageId` when present; fallback to
      `id` or hashing combination.

- Component: Dry-run message counts (`dry_run_counts`)
  - Gmail: paginate `messages.list` to compute accurate counts
  - Outlook: list messages and count pages; Graph may return `@odata.count` for some queries
  - Direct? Partial — implement `graph_dry_run_counts()` but be careful with
    `resultSizeEstimate` analogs and performance.

- Component: Checkpoint & resume (per-run checkpoint JSON)
  - Gmail: `load_checkpoint()` + `save_checkpoint()` in `infra/gmail/run.py`
  - Outlook: use `common/checkpoint` for reverse-chron use or implement
    `DeltaStore` to persist Graph delta tokens per mailbox
  - Direct? New required: implement `DeltaStore` (save/load per-mailbox delta token)
  - Notes:
    - For incremental Graph sync, `delta` tokens are the canonical resume handle.
    - For a historical import (no delta), use checkpoint similar to Gmail's
      completed users + seen_message_ids approach.

- Component: Watermark reporting (per-user max internalDate)
  - Gmail: emits per-user max(internalDate) ms for runners/sync.py
  - Outlook: emit per-mailbox max(receivedDateTime) OR persist delta token
  - Direct? Partial — prefer delta token persistence; still emit max datetime
    for compatibility with runners.

- Component: Thread aggregation (`threadId` → `normalize_email_thread`)
  - Gmail: `threadId` groups messages into threads
  - Outlook: `conversationId` groups messages; semantics are similar but not
    identical (distribution across folders possible)
  - Direct? Partial — implement `conversationId`-aware aggregation and validate
    LOR-14 threading expectations.

- Component: Normalizer
  - Gmail: `infra/gmail/normalizer.py` maps raw Gmail payload → `KnowledgeEvent`
  - Outlook: `infra/outlook/normalizer.py` must perform equivalent mapping
  - Direct? New implementation required but largely parallel; reuse `common.hashing`,
    `Actor` conventions and `KnowledgeEvent` contract.
  - Notes:
    - HTML cleaning/truncation rules same as Gmail normalizer; reuse helper logic.
    - Map `internetMessageId` → message header id; map recipients/senders to `Actor`.

- Component: Functional mailbox heuristics (`is_functional_mailbox`)
  - Gmail: local-part heuristics + `org_config.extra_functional_mailboxes`
  - Outlook: same heuristic can be reused (email local-part), so reuse code.
  - Direct? Yes — reuse `is_functional_mailbox()` without change.

- Component: Output writing (JSONL append, atomic writes for ACL/meta)
  - Gmail: `append_events_to_jsonl()` + atomic checkpoint writes
  - Outlook: reuse same functions and `common/checkpoint` / atomic write helpers
  - Direct? Yes — reuse existing code and `common/output_paths` patterns.

- Component: Discovery (ACLs / Teams channels)
  - Gmail: N/A
  - Slack/Jira: discovery implemented (scope_principals.json patterns)
  - Outlook/Teams: required new discovery implementation
  - Notes:
    - Implement Teams channel discovery producing `scope_principals.json` like Slack.
    - Use `common.scopes` extension for `ms_teams_channel()` and `outlook_mailbox()`.

Implementation priorities (short list)
-----------------------------------
1. Add config vars to `common/config.py` for Azure AD and Graph settings.
2. Implement Azure AD token helper (`get_access_token()` + local cache).
3. Implement `graph_get()` wrapper that honors `Retry-After`, parses HTTP-date,
   supports `@odata.nextLink` paging and optional batching.
4. Implement `DeltaStore` (persist per-mailbox delta tokens) and decide
   delta-vs-paginated initial import strategy.
5. Implement `infra/outlook/fetcher.py` using `graph_get()` and `DeltaStore`.
6. Implement `infra/outlook/normalizer.py` and `infra/outlook/run.py` reusing
   CLI, JSONL, and checkpoint patterns.

Files/places to review & reuse
------------------------------
- `infra/gmail/fetcher.py` — rate-limit/backoff, batching, DWD pattern
- `infra/gmail/run.py` — CLI flags, watermark usage, per-user looping, JSONL writes
- `infra/slack/*` and `infra/jira/*` — discovery + ACL artifact patterns
- `common/checkpoint.py` — reverse-chron checkpoint schema
- `common/output_paths.py` — create `OutlookPaths` in same style
- `common/scopes.py` — add MS scope/principal helpers

Appendix: Key Graph differences to keep in mind
---------------------------------------------
- Delta queries are the preferred incremental mechanism; delta tokens are opaque
  strings that should be persisted per mailbox.
- Graph paging uses `@odata.nextLink` / `@odata.deltaLink` instead of `pageToken`.
- Retry-After header may be either delta-seconds or an HTTP-date — parse both.
- Graph batch API is limited (max 20 subrequests) and different from Gmail
  batch endpoint; consider parallel individual requests with concurrency control.
- Message timestamp fields are ISO strings; convert to epoch consistently.
- Conversation/thread semantics use `conversationId` and may cross folders.

---
Generated for LOR-64 readiness; place this file under `docs/Outlook` and iterate.
