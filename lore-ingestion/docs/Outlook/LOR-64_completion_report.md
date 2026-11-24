# LOR-64 — Completion Report

**Ticket:** LOR-64 · LOR-63 S1: `infra/outlook/` — fetcher, normalizer, delta watermark, run.py  
**Parent:** LOR-63 · Outlook Mail + Microsoft Teams — lore-ingestion pipeline  
**Status:** ✅ Complete  
**Completed:** 2026-06-10  
**Author:** Riddhi Sharma

---

## 1. What LOR-64 Required

LOR-64 was the first sub-ticket of the Microsoft 365 ingestion track. Its scope was
**Layer 1 only** — the raw connector that fetches data from the Microsoft Graph API,
normalizes it into the project's `KnowledgeEvent` format, and writes JSONL output.
It was not permitted to import from `pipeline/` or `builders/` (enforced by
`pipeline/tests/test_layering.py`).

### Files required

```
infra/outlook/
  __init__.py        package marker
  fetcher.py         Graph API client + pagination + retry + delta token management
  normalizer.py      Raw Graph dict → KnowledgeEvent
  run.py             CLI entrypoint: --org, --stage fetch|smoke, --limit, --dry-run
```

### Schema additions required

| File | Change |
|------|--------|
| `common/schemas.py` | Add `EventType.TEAM_MESSAGE = "ms_teams.message"` |
| `common/scopes.py` | Add `teams_channel(team_id, channel_id)` and `outlook_thread(conversation_id)` scope builders |
| `common/config.py` | Add Azure AD credential fields so they load from `.env` |

### Acceptance criteria (verbatim from the issue)

1. `fetch_mail_for_user()` writes valid JSONL that `normalizer.py` can round-trip to `KnowledgeEvent`
2. Delta tokens are saved after every user/channel — a crash mid-run resumes cleanly
3. Teams replies are attached to their parent message (via `replyToId` set correctly)
4. `pipeline/tests/test_layering.py` passes (no `pipeline/` imports from `infra/`)
5. `--dry-run` flag: fetches but does not write to disk

---

## 2. Implementation Phases

Work was organized into 11 phases. Each phase was implemented, verified, and logged
in `docs/Outlook/OUTLOOK_README.md` before the next phase began.

| Phase | What was built | Key files |
|-------|---------------|-----------|
| 1 | Package skeleton + stubs | `infra/outlook/__init__.py`, `fetcher.py` (stub), `normalizer.py` (stub), `run.py` (stub) |
| 2 | Azure AD authentication | `get_access_token(settings)` in `fetcher.py` |
| 3 | Graph API client | `graph_get()` in `fetcher.py` |
| 4 | Delta token store | `DeltaStore` class in `fetcher.py` |
| 5 | Outlook mail fetch | `fetch_mail_for_user()` in `fetcher.py` |
| 6 | Teams channel discovery | `fetch_teams_channels()` in `fetcher.py` |
| 7 | Teams message fetch | `fetch_channel_messages()` in `fetcher.py` |
| 8 | Normalizers + schema additions | `normalizer.py` (complete), `common/schemas.py`, `common/scopes.py` |
| 9 | CLI runner + orchestration | `infra/outlook/run.py` (complete) |
| 10 | Unit tests | `tests/test_delta_store.py` (19 tests), `tests/test_outlook_normalizer.py` (60 tests) |
| 11A | End-to-end mock validation | `tests/test_phase11a_mock_validation.py` (58 tests) |
| Fix | Azure credential wiring | `common/config.py` — Settings fields + `REQUIRED_BY_STAGE` |

---

## 3. Files Created or Modified

### New files

| File | Purpose |
|------|---------|
| `infra/outlook/__init__.py` | Package marker |
| `infra/outlook/fetcher.py` | Graph API client, DeltaStore, all three fetch functions |
| `infra/outlook/normalizer.py` | `normalize_outlook_message()` and `normalize_teams_message()` |
| `infra/outlook/run.py` | CLI runner with `--stage fetch\|smoke`, `--dry-run`, `--limit` |
| `tests/__init__.py` | Makes `tests/` a proper Python package for pytest discovery |
| `tests/test_delta_store.py` | 19 unit tests for DeltaStore |
| `tests/test_outlook_normalizer.py` | 60 unit tests for both normalizers |
| `tests/test_phase11a_mock_validation.py` | 58 end-to-end mock validation tests |

### Modified files

| File | What changed |
|------|-------------|
| `common/schemas.py` | Added `EventType.TEAM_MESSAGE = "ms_teams.message"` to the `EventType` class |
| `common/scopes.py` | Added `teams_channel(team_id, channel_id)` and `outlook_thread(conversation_id)` builders + `__all__` entries |
| `common/config.py` | Added `azure_tenant_id`, `azure_client_id`, `azure_client_secret` to `Settings`; added `"outlook_fetch"` to `REQUIRED_BY_STAGE` |
| `.env.example` | Added `AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET` entries |
| `docs/Outlook/OUTLOOK_README.md` | Phase-by-phase progress log + Azure Configuration section |

---

## 4. How Each Requirement Was Implemented

### 4.1 Azure AD authentication (`get_access_token`)

The issue required a plain `httpx` POST to the Azure AD token endpoint — no MSAL library.

**Implementation (`fetcher.py:123–184`):**
- `POST https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token` with `grant_type=client_credentials`
- In-memory token cache keyed by `tenant_id`
- Token refreshed 60 seconds before expiry (`expires_at - 60`)
- `RuntimeError` raised on HTTP failure or missing `access_token` / `expires_in` fields

**Credential loading:** `get_access_token(settings)` reads `settings.azure_tenant_id`,
`settings.azure_client_id`, `settings.azure_client_secret`. These are now declared Pydantic
fields in `common/config.py` (added in the deployment-readiness fix), so values placed in
`.env` are loaded automatically by `Settings`.

### 4.2 Graph API client (`graph_get`)

**Implementation (`fetcher.py:187–274`):**
- Accepts a path (`/users`) or a full URL (for `@odata.nextLink` and `@odata.deltaLink` replay)
- Pagination: follows `@odata.nextLink` automatically, accumulating all `value` arrays
- 429 handling: reads `Retry-After` header (supports both delta-seconds and HTTP-date formats), sleeps exactly that duration, retries — does not use its own backoff on 429
- 5xx transient errors (502/503/504): exponential backoff 2 → 4 → 8 → 16 → 32s, max 5 retries
- `return_last_response=True` parameter returns `(results, last_page_json)` so callers can extract `@odata.deltaLink` from the final page

### 4.3 Delta token store (`DeltaStore`)

**Implementation (`fetcher.py:31–98`):**

Storage format (matches LOR-14 §4.2 exactly):
```json
{
  "mail:alice@corp.com": {
    "delta_token": "https://graph.microsoft.com/...?$deltatoken=abc",
    "last_sync": "2026-06-10T09:00:00+00:00",
    "message_count": 1000
  },
  "teams:team1:channel1": {
    "delta_token": "xyz789",
    "last_sync": "2026-06-10T09:05:00+00:00",
    "message_count": 250
  }
}
```

- `load()`: reads file on construction; silently returns empty dict if file is missing or JSON is corrupt
- `get(key)`: returns `{delta_token, message_count}` or `None`
- `set(key, token, count)`: writes token + ISO timestamp + count in memory
- `save()`: atomic write via `common.checkpoint._atomic_write()` (temp file + `os.replace`) — crash-safe

**Path:** defaults to `common.checkpoint._state_dir() / f"{org}_outlook_delta_tokens.json"`.
The `OutlookPaths` canonical path class is deferred to LOR-66; a `store_path` parameter
allows tests and callers to override the path.

### 4.4 Outlook mail fetch (`fetch_mail_for_user`)

**Implementation (`fetcher.py:296–381`):**
- **First sync** (no stored token): calls `/users/{user_email}/messages/delta` with `$filter=receivedDateTime ge {scanback_date}`
- **Incremental sync** (token exists): if stored token is a full URL, replays it directly; otherwise calls the delta endpoint with `$deltatoken={token}`
- After all pages consumed: extracts `@odata.deltaLink` from the final page's JSON, parses out the `$deltatoken` value via `_extract_delta_token_from_link()`, calls `store.set()` then `store.save()`
- Yields each raw Graph message dict; delta token is only persisted after the generator is fully consumed

**Acceptance criterion 1 satisfied:** the yielded dicts are immediately consumable by `normalize_outlook_message()` to produce a `KnowledgeEvent`.

### 4.5 Teams channel discovery (`fetch_teams_channels`)

**Implementation (`fetcher.py:383–456`):**
- Queries `/groups?$filter=resourceProvisioningOptions/Any(x:x eq 'Team')` to find Teams-enabled groups
- For each team: queries `/teams/{team_id}/channels`
- Returns a flat list: `[{team_id, team_name, channel_id, channel_name, membership_type, ...}]`
- Preserves extra metadata fields (description, webUrl, tenantId, visibility) for later ACL discovery (LOR-65)

### 4.6 Teams message fetch (`fetch_channel_messages`)

**Implementation (`fetcher.py:459–625`):**
- Delta sync identical to mail: first sync uses `/teams/{team_id}/channels/{channel_id}/messages/delta` with no filter; incremental replays stored token
- **Replies:** for each root message, fetches `/teams/{team_id}/channels/{channel_id}/messages/{root_id}/replies`; injects `reply["replyToId"] = root_id` if the field is absent
- **channelIdentity guarantee:** synthesizes `{"teamId": team_id, "channelId": channel_id}` on every root message and reply if not already present in the Graph API response
- Metadata (`team_id`, `team_name`, `channel_id`, `channel_name`) injected on all messages
- Delta token saved after all root messages and their replies are yielded

**Acceptance criterion 3 satisfied:** every reply in the JSONL output has `replyToId` pointing to its root message.

### 4.7 Normalizers

**`normalize_outlook_message(msg, org, employee_email)`** (`normalizer.py:46–105`):

| KnowledgeEvent field | Source |
|---------------------|--------|
| `id` | `make_id("outlook_mail", msg["id"])` |
| `source` | `"outlook_mail"` |
| `event_type` | `EventType.EMAIL_MESSAGE` |
| `timestamp` | `msg["receivedDateTime"]` (already ISO 8601) |
| `title` | `msg.get("subject", "(no subject)")` |
| `body` | `strip_html(msg["body"]["content"])[:4000]` |
| `primary_actor` | `Actor(id=f"email:{sender_address}", source="outlook_mail")` |
| `actors` | Sender + toRecipients + ccRecipients + bccRecipients |
| `url` | `https://outlook.office.com/mail/{msg_id}` |
| `project` | Subject line |
| `tags` | `["email", "internal"\|"external"]` |
| `parent_id` | `make_id("outlook_mail.thread", conversationId)` if present |
| `raw` | `{conversation_id, internet_message_id, body_content_type, categories}` |

**`normalize_teams_message(msg, team_name, channel_name, org)`** (`normalizer.py:140–196`):

| KnowledgeEvent field | Source |
|---------------------|--------|
| `id` | `make_id("ms_teams", f"{org}:{team_id}:{channel_id}:{message_id}")` |
| `source` | `"ms_teams"` |
| `event_type` | `EventType.TEAM_MESSAGE` |
| `timestamp` | `msg["createdDateTime"]` |
| `title` | First 100 chars of cleaned body |
| `body` | `strip_html(msg["body"]["content"])[:4000]` (always stripped — Teams is always HTML) |
| `primary_actor` | `Actor(id=f"teams:{user_guid}", source="ms_teams")` |
| `url` | `msg.get("webUrl")` |
| `project` | `f"{team_name}#{channel_name}"` |
| `tags` | `["teams", f"team:{team_id}"]` |
| `parent_id` | `make_id("ms_teams", f"{org}:{team_id}:{channel_id}:{replyToId}")` if reply |
| `raw` | `{team_id, channel_id, reply_to_id, message_type}` |

### 4.8 CLI runner (`run.py`)

**Implementation (`infra/outlook/run.py`):**
- `OutputPaths` — constructs timestamped JSONL paths: `output/raw/{org}_outlook_mail_{ts}.jsonl` and `output/raw/{org}_ms_teams_{ts}.jsonl`
- `JSONLWriter` — writes one JSON object per line; `dry_run=True` increments count but never opens a file
- `_run_fetch_stage()` — auth → user discovery → mail fetch → teams discovery → teams message fetch; unified `--limit` applied across both sources via a generator wrapper
- `--dry-run` guard: `JSONLWriter` opened with `dry_run=True`; no files created, counts still tracked
- `_run_smoke_stage()` — deferred import of `runners.outlook_smoke.run` (LOR-67 deliverable)

**Acceptance criteria 4 and 5 satisfied.**

### 4.9 Schema and scope additions

**`common/schemas.py`:** `TEAM_MESSAGE = "ms_teams.message"` added to the `EventType` class alongside existing Slack, Gmail, and GitHub event types.

**`common/scopes.py`:** Two new builders added and exported:
```python
def teams_channel(team_id: str, channel_id: str) -> str:
    return f"teams:channel:{team_id}:{channel_id}"

def outlook_thread(conversation_id: str) -> str:
    return f"outlook:thread:{conversation_id}"
```

**`common/config.py`:** Three `Settings` fields added:
```python
azure_tenant_id: str | None = None
azure_client_id: str | None = None
azure_client_secret: str | None = None
```
And `"outlook_fetch": ("azure_tenant_id", "azure_client_id", "azure_client_secret")` added to `REQUIRED_BY_STAGE` so `validate_for("outlook_fetch")` fails fast when credentials are absent.

---

## 5. Test Coverage

### Phase 10 — Unit tests (80 tests)

**`tests/test_delta_store.py`** — 19 tests across 6 classes:

| Class | Tests | What is verified |
|-------|-------|-----------------|
| `TestMissingFile` | 2 | Empty store on missing file; `get()` returns `None` |
| `TestSetAndSave` | 6 | Token stored; timestamp written; file created; valid JSON; overwrite; idempotent save |
| `TestLoad` | 2 | Reads existing file; round-trip save → reload |
| `TestAtomicWrite` | 2 | No `.tmp` files left after save; on-disk JSON is correct |
| `TestCorruptedFile` | 3 | Bad JSON recovers to empty; empty file recovers; `get()` after corrupt returns `None` |
| `TestMultipleKeys` | 4 | `mail:` and `teams:` keys coexist; unknown key returns `None` |

**`tests/test_outlook_normalizer.py`** — 60 tests across 15 classes:

Outlook (32 tests, 8 classes): `TestOutlookEventType`, `TestOutlookId`, `TestOutlookContent`,
`TestOutlookActors`, `TestOutlookParentId`, `TestOutlookTags`, `TestOutlookProjectAndOrg`,
`TestOutlookRawPayload`

Teams (28 tests, 7 classes): `TestTeamsEventType`, `TestTeamsId`, `TestTeamsContent`,
`TestTeamsActors`, `TestTeamsParentId`, `TestTeamsTags`, `TestTeamsProjectAndOrg`,
`TestTeamsRawPayload`

### Phase 11A — End-to-end mock validation (58 tests)

**`tests/test_phase11a_mock_validation.py`** — 58 tests across 8 checkpoint classes.
No Azure credentials required — all Graph API calls and token acquisition are patched.

| Checkpoint | Class | Tests | What is verified |
|-----------|-------|-------|-----------------|
| 1 | `TestRunFetchStage` | 4 | Fetch completes; per-user mail; per-channel teams; `--limit` stops fetch |
| 2 | `TestRunDryRun` | 3 | No `.jsonl` files written; fetchers still called; `JSONLWriter(dry_run=True)` |
| 3 | `TestJSONLOutputGeneration` | 8 | Line count; valid JSON; field preservation; count; dry-run; Unicode; 2 files created; correct records |
| 4 | `TestDeltaTokenPersistence` | 5 | Mail token survives reload; Teams token survives reload; overwrite; full deltaLink URL; multiple mailboxes independent |
| 5 | `TestTeamsRepliesPreserved` | 4 | All messages yielded; `replyToId` injected when missing; existing `replyToId` not overwritten; metadata on all |
| 6 | `TestChannelIdentityPresent` | 4 | Synthesized on root; synthesized on replies; all messages have it; existing identity not overwritten |
| 7 | `TestOutlookNormalizerEndToEnd` | 15 | `KnowledgeEvent` produced; event type/source; HTML strip + 4000-char cap; thread parent_id; internal/external tags; curated raw |
| 8 | `TestTeamsNormalizerEndToEnd` | 15 | `KnowledgeEvent` produced; event type/source; HTML strip + 4000-char cap; reply `parent_id == root.id`; project format; tags; curated raw |

### Additional tests

| File | Tests | Purpose |
|------|-------|---------|
| `scripts/test_run_mock.py` | 4 | Phase 9 CLI verification: fetch stage, dry-run, smoke deferred import, limit |
| `pipeline/tests/test_layering.py` | 1 | Confirms no `pipeline/` or `builders/` imports in `infra/outlook/` |

### Total: 143 tests, all passing

---

## 6. Acceptance Criteria Verification

| Criterion | How verified | Result |
|-----------|-------------|--------|
| `fetch_mail_for_user()` writes JSONL that normalizer can round-trip to `KnowledgeEvent` | Phase 11A `TestOutlookNormalizerEndToEnd` (15 tests) | ✅ PASS |
| Delta tokens saved after every user/channel; crash mid-run resumes cleanly | Phase 10 `TestAtomicWrite` (2 tests) + Phase 11A `TestDeltaTokenPersistence` (5 tests) | ✅ PASS |
| Teams replies attached via `replyToId` correctly | Phase 11A `TestTeamsRepliesPreserved` (4 tests) | ✅ PASS |
| `pipeline/tests/test_layering.py` passes | Run after every phase — 1 passed | ✅ PASS |
| `--dry-run` fetches without writing to disk | Phase 11A `TestRunDryRun` (3 tests) + `TestJSONLOutputGeneration` dry-run tests | ✅ PASS |

---

## 7. Deliberate Deviations from the Issue Spec

The following items diverge from the LOR-64 issue description or the LOR-14 architecture doc.
Each deviation is intentional and documented with its rationale.

### 7.1 `make_id` namespace for Outlook mail

| Spec (LOR-14 §5.4.1) | Implemented |
|---------------------|-------------|
| `make_id("outlook.message", raw_id)` | `make_id("outlook_mail", message_id)` |
| `make_id("outlook.thread", conv_id)` | `make_id("outlook_mail.thread", conversation_id)` |

**Rationale:** The `source` field is `"outlook_mail"` (underscore, per project convention matching
`"gmail"`, `"slack"`, `"jira"`). Using the same string as the `make_id` prefix ensures the ID
namespace is consistent with source naming across the codebase. The IDs are stable and
deterministic — the only effect is that they differ from what a LOR-14-only reader would expect.

### 7.2 `make_id` for Teams messages uses compound ID

| Spec (LOR-14 §5.4.2) | Implemented |
|---------------------|-------------|
| `make_id("ms_teams.message", raw_id)` | `make_id("ms_teams", f"{org}:{team_id}:{channel_id}:{msg_id}")` |

**Rationale:** Graph message IDs are unique within a channel but not globally across the tenant.
The compound key (`org:team:channel:msg`) is collision-resistant across orgs and channels,
matching the Slack connector's pattern (`make_id("slack.message", f"{channel_id}:{ts}")`).
All 58 Phase 11A tests are written against this format and pass.

### 7.3 Teams actor ID uses `teams:{guid}` not `email:{upn}`

| Spec (LOR-14 §5.4.2) | Implemented |
|---------------------|-------------|
| `Actor(id=f"email:{upn}", source="ms_teams")` | `Actor(id=f"teams:{object_id}", source="ms_teams")` |

**Rationale:** The Graph API's `/teams/{team_id}/channels/{channel_id}/messages` endpoint
returns the Azure AD Object ID (GUID) in `from.user.id`, not the UPN. The UPN requires
a separate `/users/{id}` lookup per message which would multiply API calls by the number
of unique senders. LOR-65 (identity discovery) will resolve GUIDs to UPNs and email
addresses in bulk via `/users` and create the `email:` cross-reference. Using `teams:{guid}`
now preserves the original identifier without fabricating a potentially incorrect UPN.

### 7.4 Teams `raw["scope_id"]` not stamped by normalizer

| Spec (LOR-64 Phase 8) | Implemented |
|----------------------|-------------|
| `raw["scope_id"] = ms365_channel_scope(channel_id)` | Not present |

**Rationale:** The `ms365_channel_scope()` function referenced in the spec was never
defined. The equivalent `common/scopes.teams_channel(team_id, channel_id)` exists and
returns `"teams:channel:{team_id}:{channel_id}"`. Stamping `scope_id` in `raw` is the
first step of ACL wiring, which is the primary concern of LOR-65 (`discovery.py`).
Doing it here without the full ACL artifact pipeline would produce a field with no
consumer. Deferred to LOR-65.

### 7.5 Mail HTML stripping is unconditional

| Spec (LOR-14 §5.4.1) | Implemented |
|---------------------|-------------|
| Strip HTML only when `contentType == "html"` | `strip_html()` called on all mail bodies |

**Rationale:** The regex-based `strip_html()` is a no-op on plain text (there are no HTML
tags to remove). The result for plain-text emails is identical whether the conditional is
present or not. The unconditional call is simpler and avoids a bug class where emails
with an incorrect or missing `contentType` field retain raw HTML in the knowledge graph.

### 7.6 `azure_*` field names instead of `ms365_*`

| Spec (LOR-63 parent issue) | Implemented |
|---------------------------|-------------|
| `ms365_tenant_id`, `ms365_client_id`, `ms365_client_secret` | `azure_tenant_id`, `azure_client_id`, `azure_client_secret` |

**Rationale:** The fetcher was implemented using `getattr(settings, "azure_*", None)` — the
`azure_` prefix matches the Azure AD terminology used throughout the codebase (token endpoint,
credential docs). Renaming to `ms365_*` would require changing both `config.py` and `fetcher.py`
with no functional benefit. The field names match what `.env.example` now documents.

---

## 8. Deferred to Future Tickets

| Item | Reason | Ticket |
|------|--------|--------|
| `OutlookPaths` in `common/output_paths.py` | Canonical output path registry for the Outlook connector | LOR-66 |
| `infra/outlook/discovery.py` | Teams channel ACL capture, channel membership crawl, scope principal artifact | LOR-65 |
| `runners/outlook_smoke.py` | End-to-end smoke test with real or near-real data; `--stage smoke` target | LOR-67 |
| `common/sources.py` — `"outlook_mail"` and `"ms_teams"` entries | Needed before outlook data flows through `pipeline/extraction/grouping.py` | LOR-66 |
| Teams `raw["scope_id"]` and `raw["source_ref"]` stamping | Requires LOR-65 scope format and ACL wiring to be complete | LOR-65 |

---

## 9. How to Run the Full Verification Suite

```powershell
# From project root with venv activated
Set-Location "C:\Users\hp\Desktop\lore-ingestion"
.\.venv\Scripts\Activate.ps1

# All unit + integration tests (138 tests)
python -m pytest tests/test_delta_store.py tests/test_outlook_normalizer.py tests/test_phase11a_mock_validation.py -v

# Layering test (1 test)
python -m pytest pipeline/tests/test_layering.py -v

# CLI mock verification (4 tests)
python scripts/test_run_mock.py

# Credential fast-fail check (no Azure creds needed)
python -c "from common.config import validate_for, MissingConfigError; validate_for('outlook_fetch')"
# Expected: MissingConfigError listing the three AZURE_* vars
```

**Total: 143 tests passing, 0 failing.**

---

## 10. Configuration Reference

Add these to `.env` for real Microsoft Graph connectivity (Phase 11B and beyond):

```env
AZURE_TENANT_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
AZURE_CLIENT_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
AZURE_CLIENT_SECRET=your-client-secret-value
```

Required Azure AD application permissions (grant admin consent org-wide):

| Permission | Scope |
|-----------|-------|
| `Mail.Read` | Read all users' mail |
| `ChannelMessage.Read.All` | Read Teams channel messages |
| `User.Read.All` | Enumerate org users and resolve identities |

See `docs/Outlook/LOR-14_Outlook_Connector_architecture.md` Section 7 for the full admin consent flow.
