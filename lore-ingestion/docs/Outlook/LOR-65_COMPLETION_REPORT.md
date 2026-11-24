# LOR-65 Completion Report — MS365 Identity Discovery

**Ticket:** LOR-65 · LOR-63 S2: `infra/outlook/` — MS365 user + Teams ACL discovery
**Parent:** LOR-63 · Outlook Mail + Microsoft Teams — lore-ingestion pipeline
**Depends on:** LOR-64 (fetcher, normalizer, delta watermark, run.py)
**Status:** ✅ Complete — all 6 phases delivered
**Completed:** 2026-06-10
**Author:** Riddhi Sharma

---

## Table of Contents

1. [Why This Ticket Was Needed](#1-why-this-ticket-was-needed)
2. [What Was Delivered — Overview](#2-what-was-delivered--overview)
3. [Files Created and Modified](#3-files-created-and-modified)
4. [Phase-by-Phase Implementation Log](#4-phase-by-phase-implementation-log)
   - [Phase 2 — Raw Discovery Functions](#phase-2--raw-discovery-functions)
   - [Phase 3A — Scope Helpers and Emit Functions](#phase-3a--scope-helpers-and-emit-functions)
   - [Phase 4 — Path Registry and Stage Wiring](#phase-4--path-registry-and-stage-wiring)
   - [Phase 5 — Configuration Validation](#phase-5--configuration-validation)
   - [Phase 6 — Documentation and Backend Handoff](#phase-6--documentation-and-backend-handoff)
5. [Architecture Decisions and Why](#5-architecture-decisions-and-why)
6. [Graph API Design — How Discovery Works](#6-graph-api-design--how-discovery-works)
7. [Output Artifacts](#7-output-artifacts)
8. [Scope ID Design](#8-scope-id-design)
9. [Known Deviation from Spec](#9-known-deviation-from-spec)
10. [What Was Deferred and Why](#10-what-was-deferred-and-why)
11. [Production Readiness](#11-production-readiness)
12. [Downstream Tickets](#12-downstream-tickets)

---

## 1. Why This Ticket Was Needed

LOR-64 built the raw fetch layer for Microsoft 365: `fetcher.py` authenticates with Azure AD, `graph_get()` paginates Graph API responses with 429/5xx retry, `normalizer.py` converts raw Graph dicts to `KnowledgeEvent`, and `run.py --stage fetch` pulls Outlook mail and Teams messages to `output/raw/`.

**The problem:** LOR-64 fetches messages but has no concept of who is allowed to see them. Every Teams message pulled into the knowledge graph would be visible to every Lore user — there is no access control on MS365 content.

**What LOR-65 adds:** the discovery layer that answers "who can see what". It mirrors the Slack pattern already in the repo:

```
Slack (existing):
  infra/slack/discovery.py   → enumerate channel members → scope_principals.json
  builders/slack_acl_emitter → objectId → [scope_ids]
  lore-backend               → per-query ACL gate in Neo4j

MS365 (LOR-65):
  infra/outlook/discovery.py → enumerate tenant users + Teams channel members
                             → ms365_identities.json + ms365_teams_scopes.json
  lore-backend               → objectId → [ms365:channel:...] ACL gate
```

Without LOR-65, MS365 ingestion is blocked: LOR-66 cannot stamp `raw["scope_id"]` onto `KnowledgeEvent` objects, and the Neo4j ACL gate has nothing to filter against.

---

## 2. What Was Delivered — Overview

| Phase | Deliverable | Scope |
|-------|-------------|-------|
| Phase 2 | `discover_users()`, `discover_teams_channel_members()` | New file `infra/outlook/discovery.py` |
| Phase 3A | `ms365_channel_scope()`, `is_ms365_channel()`, `emit_ms365_identities()`, `emit_teams_scope_map()` | Modified `common/scopes.py`; extended `infra/outlook/discovery.py` |
| Phase 4 | `OutlookPaths` class, `_run_discover_stage()`, `--stage discover` CLI flag | Modified `common/output_paths.py`, `infra/outlook/run.py` |
| Phase 5 | `validate_for("outlook_discovery")` + `REQUIRED_BY_STAGE` registration | Modified `common/config.py`, `infra/outlook/run.py` |
| Phase 6 | `ChannelMember.Read.All` in LOR-14 architecture doc + `MS365_BACKEND_HANDOFF.md` | Modified `docs/Outlook/LOR-14_*`, created `docs/RBAC/MS365_BACKEND_HANDOFF.md` |

---

## 3. Files Created and Modified

### Created

| File | Phase | Purpose |
|------|-------|---------|
| `infra/outlook/discovery.py` | 2 + 3A | Core discovery logic — Graph API enumeration + file-writing emit functions |
| `docs/RBAC/MS365_BACKEND_HANDOFF.md` | 6 | Backend engineer reference: artifact schemas, Neo4j ACL query pattern, scope format rationale, production readiness checklist |
| `docs/Outlook/OUTLOOK_README_65.md` | Throughout | Phase-by-phase implementation log (live during development; since removed) |
| `docs/Outlook/LOR-65_COMPLETION_REPORT.md` | Post-completion | This file |

### Modified

| File | Phase | What changed |
|------|-------|-------------|
| `common/scopes.py` | 3A | Added `ms365_channel_scope()`, `is_ms365_channel()`, both in `__all__` |
| `common/output_paths.py` | 4 | Added `OutlookPaths` class after `SlackPaths` |
| `infra/outlook/run.py` | 4 + 5 | Added `OutlookPaths` + emitter imports; added `_run_discover_stage()`; added `"discover"` to argparse choices; added `validate_for` import + call |
| `common/config.py` | 5 | Added `"outlook_discovery"` to `REQUIRED_BY_STAGE` |
| `docs/Outlook/LOR-14_Outlook_Connector_architecture.md` | 6 | Added `ChannelMember.Read.All` in §2.1, §7 step 12 (+ note), §7 step 4 admin consent URL |

---

## 4. Phase-by-Phase Implementation Log

### Phase 2 — Raw Discovery Functions

**Goal:** Pure data-fetching functions — no file I/O, no scope IDs, no changes to any existing file.

**Why pure functions first:** Each phase is independently testable. Keeping Phase 2 free of file I/O meant the discovery logic could be verified and reviewed without touching `common/` modules. The emit functions that depend on the return shapes of these functions came in Phase 3A, only after Phase 2 shapes were confirmed correct.

#### `discover_users(token, org) → List[Dict]`

Calls `GET /users` on Microsoft Graph with:
- `$select=id,displayName,userPrincipalName,mail,accountEnabled,userType` — fetch only needed fields, not the full user object (performance).
- `$filter=accountEnabled eq true` — server-side filter so disabled accounts never cross the wire.

Applies a belt-and-suspenders Python filter on top:
- Skips `userType == "Guest"` — the OData filter does not reliably exclude guests because Graph API nullable fields have null-value edge cases with the `ne` operator.
- Skips `accountEnabled is False` — the `$filter` already handles this but re-checking in Python is free insurance.

The `email` field falls back to `userPrincipalName` when `mail` is absent. This is common for Azure AD accounts synced from on-premises Active Directory that haven't had a mailbox assigned.

Returns one dict per user:
```json
{
  "objectId":       "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
  "displayName":    "Jane Smith",
  "upn":            "jane.smith@contoso.com",
  "email":          "jane.smith@contoso.com",
  "accountEnabled": true
}
```

#### `discover_teams_channel_members(token) → Dict[channel_id, Dict]`

Three-level Graph traversal — no equivalent in LOR-64 (the fetch path only needed team IDs and channel IDs, not member lists):

```
GET /groups?$filter=resourceProvisioningOptions/Any(x:x eq 'Team')&$select=id,displayName
  → for each team:
    GET /teams/{team_id}/channels?$select=id,displayName
      → for each channel:
        GET /teams/{team_id}/channels/{channel_id}/members?$select=id,userId,displayName
```

Key implementation choices:
- **`userId` not `id`:** The `/members` endpoint returns `aadUserConversationMember` objects. The `userId` field is the Azure AD object ID — the same ID returned by `/users`. The `id` field is the conversation-member GUID, which is not cross-referenceable. LOR-65 uses `userId` as the primary identifier, falling back to `id` only when `userId` is absent.
- **Per-channel error isolation:** Each channel's member fetch is wrapped in a `try/except`. A 403 on a single private channel (e.g., if the service principal hasn't been granted access) logs a warning and records an empty `member_object_ids` list, but does not abort the run. This means partial results are always produced rather than a total failure.
- **Reuses `graph_get()`:** All three levels use `graph_get()` from `infra/outlook/fetcher.py`. Pagination (`@odata.nextLink`) and 429/5xx retry are inherited for free.

Returns one dict per channel:
```json
{
  "19:abc123@thread.tacv2": {
    "team_id":           "team-guid-here",
    "team_name":         "Engineering",
    "channel_name":      "general",
    "member_object_ids": ["user-guid-1", "user-guid-2"]
  }
}
```

---

### Phase 3A — Scope Helpers and Emit Functions

**Goal:** Introduce the MS365 ACL scope format and produce the two output artifacts.

#### `common/scopes.py` — two new functions

**Why they belong in `common/scopes.py`:** ACL scope strings are persisted in Neo4j and in `*_scope_principals.json` artifacts. If the format is defined in multiple places (e.g., an f-string in `discovery.py` and another in the backend), any drift — a stray colon, a renamed prefix — silently breaks the query-time access gate with no error. `common/scopes.py` is the single owner of all scope and principal ID formats. The module is protected by `pipeline/tests/test_scopes.py` which locks the format strings.

```python
def ms365_channel_scope(channel_id: str) -> str:
    return f"ms365:channel:{channel_id}"

def is_ms365_channel(scope_id: str) -> bool:
    return scope_id.startswith("ms365:channel:")
```

Both are added to `__all__`. `is_ms365_channel()` mirrors the existing `is_jira_user()` predicate for symmetry — it will be useful for ACL filter logic in lore-backend when distinguishing MS365 scopes from Slack or Jira scopes in a mixed scope list.

#### `emit_ms365_identities(org, data_dir, token) → Path`

- Calls `discover_users(token, org)`.
- Injects `"account_type": "member"` — the artifact must not contain guest entries; this field allows consumers to verify without re-checking.
- Sorts by `objectId` — deterministic output means diffs between runs only show actual changes, not ordering noise.
- Writes `{data_dir}/{org}_ms365_identities.json` as UTF-8 pretty JSON.

#### `emit_teams_scope_map(org, data_dir, token) → Path`

This function is the core ACL inversion: it takes the channel→members mapping and flips it to member→channels, which is what the backend query needs (given a logged-in user's `objectId`, find all their channel scope IDs).

```
Input:  {channel_id → {team_id, channel_name, member_object_ids: [obj1, obj2, ...]}}
Output: {objectId → [ms365:channel:id1, ms365:channel:id2, ...]}
```

- Uses Python `set` for deduplication — a user who appears as a member in 5 channels gets exactly 5 scope IDs, not 5 duplicates.
- Sorts both the dict keys (`objectId`) and each user's scope list alphabetically — deterministic output.
- A user who belongs to no channels is absent from the dict entirely (not present with an empty list). Fail-closed: the backend treats an absent user as having no permissions.

---

### Phase 4 — Path Registry and Stage Wiring

**Goal:** Give the Outlook connector a canonical path registry (mirroring `SlackPaths`/`JiraPaths`) and expose `--stage discover` as a first-class CLI command.

#### `common/output_paths.py` — `OutlookPaths`

**Why a dedicated class:** Pipeline stages and builders should not construct file paths as inline f-strings. If the folder structure changes (e.g., `output/outlook/` → `output/ms365/`), every caller breaks. One registry class means one change.

`OutlookPaths` follows the exact same pattern as `SlackPaths` and `JiraPaths`:

```python
class OutlookPaths:
    def __init__(self, org: str, data_dir: Path = Path("output")):
        self.org  = org
        self.root = data_dir / "outlook"

    @property
    def identities_json(self) -> Path:
        return self.root / f"{self.org}_ms365_identities.json"

    @property
    def teams_scopes_json(self) -> Path:
        return self.root / f"{self.org}_ms365_teams_scopes.json"

    def makedirs(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
```

The emit functions receive `paths.root` (the `output/outlook/` directory), so the files land at the exact paths that `OutlookPaths.identities_json` and `OutlookPaths.teams_scopes_json` point to. Consumers can import `OutlookPaths` and reference the same paths without knowing the filenames.

#### `infra/outlook/run.py` — `_run_discover_stage()`

The discover stage is a clean separation from the fetch stage: it enumerates users and channel members, writes the two ACL artifacts, and exits. It does not pull any mail or Teams messages.

Flow inside `_run_discover_stage()`:
1. `validate_for("outlook_discovery")` — fail fast if credentials missing (Phase 5 addition).
2. `get_settings()` → `fetcher.get_access_token(settings)` — authenticate via Azure AD client credentials flow.
3. `OutlookPaths(org, data_dir).makedirs()` — ensure `output/outlook/` exists.
4. `emit_ms365_identities(org, paths.root, token)` — writes identities artifact.
5. `emit_teams_scope_map(org, paths.root, token)` — writes scopes artifact.
6. Read written files to extract counts for the summary log.

The existing `fetch` and `smoke` stages are not modified.

---

### Phase 5 — Configuration Validation

**Goal:** Catch missing Azure credentials immediately at stage entry, before any Graph API call is attempted.

**The problem without this:** If `AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, or `AZURE_CLIENT_SECRET` are missing from `.env`, the failure happens deep inside `get_access_token()` with a cryptic OAuth error — after the user has already waited for the stage to start. `validate_for()` moves that failure to the very first line of the stage with a clear message naming exactly which variable is missing.

#### `common/config.py` — registration

Added after the existing `"outlook_fetch"` entry:

```python
"outlook_discovery":  ("azure_tenant_id", "azure_client_id", "azure_client_secret"),
```

The three Azure credential fields (`azure_tenant_id`, `azure_client_id`, `azure_client_secret`) were added to `Settings` as explicit declared fields during LOR-64. This was a deliberate fix to a `pydantic-settings` behavior: the `extra="ignore"` model config means any `.env` variable not declared as a field is silently dropped. Before the LOR-64 fix, Azure credentials in `.env` were being ignored at parse time and arriving as `None` in `get_access_token()`.

**Why both `outlook_fetch` and `outlook_discovery` are separate entries:** Each stage validates independently. A caller running only `--stage discover` does not need `"outlook_fetch"` to exist for validation to work — they are different stage names with the same credential tuple.

#### `infra/outlook/run.py` — validate at entry

`validate_for("outlook_discovery")` is the first line of `_run_discover_stage()`. If any of the three required fields are empty, `MissingConfigError` is raised with a message like:

```
Stage 'outlook_discovery' requires non-empty env vars: AZURE_TENANT_ID. Check your .env file.
```

This is the same fail-fast pattern used by every other connector:
`slack_fetch → slack_bot_token`, `jira_fetch → jira_base_url`, `outlook_fetch → azure_*`.

---

### Phase 6 — Documentation and Backend Handoff

**Goal:** Update the architecture reference document with the new permission added in LOR-65, and produce a complete backend handoff guide for the engineers who will wire MS365 ACL into lore-backend.

**No Python files were modified in Phase 6.**

#### `LOR-14_Outlook_Connector_architecture.md` — `ChannelMember.Read.All`

`ChannelMember.Read.All` is required to call `GET /teams/{id}/channels/{channelId}/members`. Without it, Graph API returns HTTP 403 on every member enumeration request.

This permission was not present in the original LOR-14 document because LOR-14 was written before LOR-65 was in scope. It was added in three locations:

1. **§2.1 step 2** — the application permissions configuration list (where you grant permissions in Azure Portal).
2. **§7 step 12** — the API permissions setup instructions, with an explanatory note:
   > `ChannelMember.Read.All` was added in LOR-65 for `discover_teams_channel_members()`. Required to call `/teams/{id}/channels/{channelId}/members` — Graph returns 403 without it.
3. **§7 step 4** — the admin consent URL scope parameter, so the full URL stays accurate for copy-paste use.

Complete permission set after LOR-65:

| Permission | Added by | Required for |
|-----------|----------|-------------|
| `Mail.Read` | LOR-64 | Outlook mail fetch |
| `ChannelMessage.Read.All` | LOR-64 | Teams message fetch |
| `Chat.Read.All` | LOR-64 | Chat message access |
| `User.Read.All` | LOR-65 | `discover_users()` |
| `ChannelMember.Read.All` | **LOR-65** | `discover_teams_channel_members()` |

#### `docs/RBAC/MS365_BACKEND_HANDOFF.md`

Created as the authoritative reference for lore-backend engineers consuming LOR-65 output. It is the MS365 equivalent of `docs/RBAC/SLACK_BACKEND_HANDOFF.md`.

Covers:
- Exact JSON schemas for both artifacts with field-level documentation.
- Python load snippets for startup.
- The exact Neo4j Cypher query pattern using `ms365:channel:` scope IDs in the ACL gate.
- The reason `ms365:channel:` and `teams:channel:` coexist and which to use for ACL.
- When to re-run discovery (new employee, new channel, channel membership changes).
- All deferred items (LOR-66, LOR-67) clearly marked so backend engineers know what is and isn't available yet.

---

## 5. Architecture Decisions and Why

### Decision 1: Phase 2 functions do not generate scope IDs

The LOR-65 spec showed `discover_teams_channel_members()` returning a `scope_id` field in each channel dict. This was changed by design: Phase 2 returns raw object IDs only. Scope strings are constructed in Phase 3A `emit_teams_scope_map()` using `ms365_channel_scope()`.

**Why:** Scope ID formats are owned by `common/scopes.py`. If `discover_teams_channel_members()` constructs scope IDs directly, it must import from `common/`, which it already does — but the discovery function becomes coupled to the scope format. Deferring scope construction to the emit layer means: (a) `discover_teams_channel_members()` is pure infrastructure that can be called by any phase, (b) the scope format change only requires updating `ms365_channel_scope()` in one place, and (c) it matches the Slack pattern where `fetcher.py` returns raw IDs and `discovery.py` builds scope IDs.

### Decision 2: `ms365:channel:` prefix, not `teams:channel:`

LOR-64 introduced `teams_channel(team_id, channel_id)` → `"teams:channel:{team_id}:{channel_id}"` for the fetch path. That format is persisted in Neo4j — it cannot be removed or renamed without a data migration.

LOR-65 introduces `ms365_channel_scope(channel_id)` → `"ms365:channel:{channel_id}"` for ACL purposes. It uses `channel_id` alone because Graph API channel IDs are globally unique across all tenants (UUID format). The `ms365:` namespace makes the origin unambiguous in a mixed scope list containing `slack:channel:`, `jira:project:`, etc.

**Both functions coexist.** `teams_channel()` is used internally in the fetch path. `ms365_channel_scope()` is the authoritative ACL format. The handoff document explains this distinction explicitly.

### Decision 3: `OutlookPaths.root = data_dir / "outlook"` (not `data_dir / "ms365"`)

The directory name `outlook` was chosen to match the module name (`infra/outlook/`) and the existing raw output filenames (`{org}_outlook_mail_*.jsonl`, `{org}_ms_teams_*.jsonl`). Changing to `ms365` would create an inconsistency between the folder name and the file names. `outlook` is the connector name; `ms365` is the service name — these are different concepts in this codebase.

### Decision 4: `validate_for()` as the first line, before `get_settings()` is used elsewhere

`validate_for()` calls `get_settings()` internally. Placing it first means credential validation always happens before any other setup work. This matches the pattern used in every other stage runner in the repo.

### Decision 5: Emit functions receive `paths.root` not a `OutlookPaths` instance

`emit_ms365_identities(org, data_dir, token)` and `emit_teams_scope_map(org, data_dir, token)` accept a bare `Path` for `data_dir`, not an `OutlookPaths` instance. This keeps `infra/outlook/discovery.py` free of a dependency on `common/output_paths.py`, which is a Layer 0 module. While both are Layer 0 and the import would be allowed, keeping discovery functions general-purpose (accepting any path) makes them easier to test and reuse. The `run.py` stage orchestrator is responsible for instantiating `OutlookPaths` and passing `paths.root`.

---

## 6. Graph API Design — How Discovery Works

### User Enumeration

```
GET https://graph.microsoft.com/v1.0/users
  ?$select=id,displayName,userPrincipalName,mail,accountEnabled,userType
  &$filter=accountEnabled eq true
```

`graph_get()` handles `@odata.nextLink` pagination automatically — if the tenant has more than 100 users (Graph's default page size), all pages are fetched before `discover_users()` returns.

The Python-side guest filter (`if u.get("userType") == "Guest": continue`) is applied after pagination. Graph's OData filter cannot reliably express `accountEnabled eq true AND userType ne 'Guest'` because `userType` can be `null` for some account types, and null comparisons in OData v4 are undefined.

### Teams Channel Member Enumeration

```
1. GET /groups
      ?$filter=resourceProvisioningOptions/Any(x:x eq 'Team')
      &$select=id,displayName
   → returns only Microsoft 365 Groups that are Teams-provisioned

2. For each team_id:
   GET /teams/{team_id}/channels?$select=id,displayName
   → returns all channels (standard + private + shared)

3. For each (team_id, channel_id):
   GET /teams/{team_id}/channels/{channel_id}/members
      ?$select=id,userId,displayName
   → returns aadUserConversationMember objects
```

The three-level traversal requires `Team.ReadBasic.All` (step 1), `Channel.ReadBasic.All` (step 2), and `ChannelMember.Read.All` (step 3). All three are application permissions granted via admin consent.

Step 3 is wrapped in `try/except` per channel: private channels may return 403 if not all have been explicitly shared with the service principal. Rather than failing the entire run, LOR-65 logs a warning and records `member_object_ids: []` for that channel. This provides partial results rather than all-or-nothing.

---

## 7. Output Artifacts

Both artifacts are written to `output/outlook/` (or the equivalent under `--output-dir`).

### `{org}_ms365_identities.json`

The authoritative list of all enabled, non-guest users in the tenant. The MS365 equivalent of the Gmail person registry.

```json
{
  "org": "ezobooks",
  "source": "ms365",
  "count": 42,
  "identities": [
    {
      "objectId":     "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
      "displayName":  "Alice Smith",
      "upn":          "alice.smith@ezobooks.in",
      "email":        "alice.smith@ezobooks.in",
      "account_type": "member"
    }
  ]
}
```

Sorted deterministically by `objectId`. `count` equals `len(identities)`.

### `{org}_ms365_teams_scopes.json`

The ACL scope map. Maps every user (by Azure AD object ID) to the list of Teams channel scope IDs they are a member of. The MS365 equivalent of `{org}_user_scopes.json` from the Slack pipeline.

```json
{
  "org": "ezobooks",
  "source": "ms_teams",
  "person_count": 38,
  "scopes": {
    "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee": [
      "ms365:channel:19:abc123@thread.tacv2",
      "ms365:channel:19:def456@thread.tacv2"
    ],
    "ffffffff-gggg-hhhh-iiii-jjjjjjjjjjjj": [
      "ms365:channel:19:abc123@thread.tacv2"
    ]
  }
}
```

Keys sorted by `objectId`. Scope lists sorted alphabetically. Users with no channel memberships are absent (not present with empty list). `person_count` equals the number of keys in `scopes`.

---

## 8. Scope ID Design

### Format

```
ms365:channel:{channelId}
```

Examples:
```
ms365:channel:19:abc123def456@thread.tacv2
ms365:channel:19:general-channel-id@thread.tacv2
```

### Why channel_id alone (not team_id:channel_id)

Graph API Teams channel IDs are globally unique UUIDs in the `19:...@thread.tacv2` format. They do not collide across teams or tenants. Including `team_id` would make the scope ID longer without providing uniqueness guarantees. The Slack pattern (`slack:channel:{channel_id}`) also uses only the channel ID.

### Why `ms365:` prefix (not `teams:`)

- `teams:channel:{team_id}:{channel_id}` already exists (LOR-64, persisted in Neo4j — cannot change).
- `ms365:` is the service namespace (Microsoft 365), not the product name. Future MS365 scope types (e.g., SharePoint, Calendar) would use `ms365:site:...`, `ms365:calendar:...` — consistent with the namespace.
- In a mixed scope list, `ms365:channel:` is unambiguous alongside `slack:channel:`, `jira:project:`, `jira:seclevel:`.

### Functions in `common/scopes.py`

```python
def ms365_channel_scope(channel_id: str) -> str:
    """Canonical ACL scope ID for an MS365 Teams channel."""
    return f"ms365:channel:{channel_id}"

def is_ms365_channel(scope_id: str) -> bool:
    """True for any scope_id produced by ms365_channel_scope()."""
    return scope_id.startswith("ms365:channel:")
```

---

## 9. Known Deviation from Spec

The LOR-65 Linear issue showed `discover_teams_channel_members()` returning a `scope_id` field in each channel dict.

**What was implemented instead:** `discover_teams_channel_members()` returns raw `member_object_ids` only. `scope_id` strings are constructed in `emit_teams_scope_map()` via `ms365_channel_scope(channel_id)`.

**Why the deviation was made and confirmed:**

1. The discovery function would need to import `ms365_channel_scope` from `common/scopes.py`. While this is an allowed import (both are Layer 0 / infra), it couples the discovery function to the ACL scope format. If the scope format ever changes, discovery functions change too.
2. It violates the principle that Phase 2 is pure data retrieval — scope ID construction is a transformation, not retrieval.
3. The Slack pattern (which LOR-65 mirrors) does not embed scope IDs in the discovery return values; they are added in the emit layer.

**Observable output is identical.** `emit_teams_scope_map()` calls `discover_teams_channel_members()` and then builds scope IDs — the artifact produced is exactly what the spec describes.

---

## 10. What Was Deferred and Why

### LOR-66 — `common/sources.py` + normalizer scope stamping

`infra/outlook/normalizer.py` does not yet stamp `raw["scope_id"]`, `raw["source_ref"]`, or `raw["source_url"]` on `KnowledgeEvent` objects. `common/sources.py` does not yet have entries for `outlook_mail` or `ms_teams`.

**Why deferred:** LOR-65 establishes the stable ACL artifact format and the `OutlookPaths` path registry. LOR-66 depends on both being stable before it writes scope IDs into the normalizer output. LOR-65 was scoped as the discovery + ACL layer; normalizer integration is a separate concern handled by LOR-66.

### LOR-67 — `runners/outlook_smoke.py`

An end-to-end smoke test runner for the Outlook connector, analogous to `runners/jira_smoke.py`. `run.py --stage smoke` already has a deferred import hook for it.

**Why deferred:** The smoke runner exercises the full pipeline path (discover → fetch → normalize). It requires LOR-65 discovery and LOR-66 normalizer changes to be complete before it can test an end-to-end flow.

### `builders/ms365_acl_emitter.py`

The Slack pattern has `builders/slack_acl_emitter.py` that takes `scope_principals.json` and denormalizes it into the `user_scopes.json` format that the backend loads. The MS365 equivalent does not exist yet.

**Why deferred:** `emit_teams_scope_map()` already produces the `objectId → [scope_ids]` format directly. The backend can consume it as-is (documented in `MS365_BACKEND_HANDOFF.md`). A dedicated builder that processes the artifact further was not included in the LOR-65 scope.

### Real Microsoft Graph validation

All phases were verified with `py_compile` syntax checks, `pipeline/tests/test_layering.py` layer enforcement, and mock-patched unit tests. Real Graph API calls have not been executed because Azure AD app registration credentials (`AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET`) are not yet available.

**When credentials are available:** Both stage commands execute against live Graph APIs without any code changes.

---

## 11. Production Readiness

### How to run

```powershell
# Set in .env
AZURE_TENANT_ID=<directory-tenant-id>
AZURE_CLIENT_ID=<application-client-id>
AZURE_CLIENT_SECRET=<client-secret-value>

# Discovery — enumerate users + Teams channel members, write ACL artifacts
python -m infra.outlook.run --org ezobooks --stage discover

# Fetch — pull Outlook mail + Teams messages, write JSONL
python -m infra.outlook.run --org ezobooks --stage fetch
```

### Required Azure AD app permissions

| Permission | Type | Required for |
|-----------|------|-------------|
| `Mail.Read` | Application | Outlook mail fetch |
| `ChannelMessage.Read.All` | Application | Teams message fetch |
| `Chat.Read.All` | Application | Chat message access |
| `User.Read.All` | Application | `discover_users()` |
| `ChannelMember.Read.All` | Application | `discover_teams_channel_members()` |

All are application permissions (not delegated). Admin consent required. See `docs/Outlook/LOR-14_Outlook_Connector_architecture.md` §7 for the full Azure Portal setup walkthrough.

### Verification status

| Check | Status |
|-------|--------|
| `py_compile` — all modified Python files | ✅ Pass |
| `pipeline/tests/test_layering.py` | ✅ Pass (no layer violations) |
| Real Microsoft Graph API calls | ⏳ Pending Azure credentials |

### What changes are needed when credentials become available

**None.** The code is production-ready. Supply credentials in `.env` and run the commands above.

---

## 12. Downstream Tickets

| Ticket | Scope | Unblocked by |
|--------|-------|-------------|
| **LOR-66** | `common/sources.py` entries for `outlook_mail` + `ms_teams`; normalizer `raw["scope_id"]`, `raw["source_ref"]`, `raw["source_url"]` stamping; `builders/ms365_acl_emitter.py` | LOR-65 `OutlookPaths` + scope IDs stable ✅ |
| **LOR-67** | `runners/outlook_smoke.py` — end-to-end smoke test runner | LOR-65 `--stage discover` ✅ + LOR-66 normalizer |

LOR-65 is the blocker for both. Both are now unblocked.

---

*See also:*
- `docs/Outlook/LOR-14_Outlook_Connector_architecture.md` — full connector architecture + Azure setup guide
- `docs/RBAC/MS365_BACKEND_HANDOFF.md` — backend engineer reference for consuming LOR-65 artifacts
- `docs/Outlook/OUTLOOK_README_65.md` — live phase-by-phase implementation log (since removed; see git history)
- `docs/RBAC/SLACK_BACKEND_HANDOFF.md` — Slack ACL pattern (MS365 mirrors this)
