# Slack Admin Token Migration

Summary of changes made to switch the Slack pipeline from bot token (`xoxb-`) to admin user token (`xoxp-`) support.

---

## What Changed

### 1. Env var rename: `SLACK_BOT_TOKEN` → `SLACK_TOKEN`

The token field is now token-type-agnostic. Accepts both `xoxp-` (admin user) and `xoxb-` (bot).

| File | Change |
|---|---|
| `common/config.py` | Field renamed `slack_bot_token` → `slack_token` |
| `common/config.py` | `REQUIRED_BY_STAGE` keys updated for `slack_fetch` and `slack_discovery` |
| `.env.example` | `SLACK_BOT_TOKEN=` → `SLACK_TOKEN=` |
| `infra/slack/run.py` | `--token` default + help text updated |
| `infra/slack/discovery.py` | `--token` default + error message updated |
| `runners/slack_reconcile.py` | `--token` default + error message updated |

**Action required on existing deployments**: rename `SLACK_BOT_TOKEN` to `SLACK_TOKEN` in `.env`.

---

### 2. Runtime pathway detection

After `validate_auth()`, the pipeline now resolves and logs which pathway the token activates:

```
Token pathway: GRID_ADMIN (Enterprise Grid + admin scopes)
Token pathway: BOT_STANDARD
```

Detection is automatic — reads `enterprise_id` + granted scopes from `auth.test`. No config needed.

`is_admin_path` is computed once after auth and shared across both the channel listing and auto-join blocks.

---

### 3. Channel listing branches on pathway (`infra/slack/run.py`)

**Enterprise Grid (admin token with `admin.conversations:read`)**
Uses `SlackAdminClient.search_channels()` → `admin.conversations.search` — enumerates every channel in the org without the token needing to be a member.

**Standard workspace / bot token**
Uses `fetcher.list_channels()` → `conversations.list` — existing behaviour unchanged.

---

### 4. Auto-join skipped for admin path (`infra/slack/run.py`)

Bot tokens need to join public channels before `conversations.history` returns content. Admin user tokens on Enterprise Grid don't — they read via `admin.conversations.*`.

The auto-join block now only runs on `BOT_STANDARD` pathway. Private channel warning updated to suggest using an admin token.

---

### 5. Required scopes trimmed (`infra/slack/run.py`)

Hardcoded scope check reduced from `["channels:read", "channels:history", "groups:read", "groups:history", "users:read"]` to `["channels:read", "users:read"]`.

The old list was bot-specific and generated misleading warnings when an admin user token was used. `validate_auth()` already gates on the real requirement: token must be able to read messages by at least one pathway.

---

### 6. Windows Unicode fix (`common/logger.py`)

Windows console defaults to cp1252 encoding, which can't render the `═` box-drawing characters used in log separator lines. Added `sys.stdout.reconfigure(encoding="utf-8")` in `setup_logging()` before the console handler is created.

---

### 7. Docs updated

| File | Change |
|---|---|
| `CLAUDE.md` | Env var updated |
| `docs/client_requirements.md` | Token entry updated with admin token note |
| `docs/slack/SLACK_CONNECTOR_STUB.md` (since removed) | Env var updated |
| `docs/slack/SLACK_SETUP.md` | All `SLACK_BOT_TOKEN` references updated |
| `docs/slack/SLACK_ONBOARDING.md` | New — full onboarding guide for standard and Enterprise Grid |

---

## Pipeline Behaviour Summary

| Scenario | Channel listing | Auto-join | Private channels |
|---|---|---|---|
| Standard workspace, admin `xoxp-` | `conversations.list` | Yes (public only) | Admin must be invited |
| Enterprise Grid, admin `xoxp-` + admin scopes | `admin.conversations.search` (org-wide) | Skipped | Admin must be invited for content |
| Enterprise Grid + Discovery API | `admin.conversations.search` | Skipped | Full access without membership |

---

## Testing

Verified with dry-run against a standard workspace:

```
Token pathway: BOT_STANDARD
7 users cached
6 channels discovered (including private)
44 events found across 6 channels
0 channels failed
```

Command:
```bash
../.venv/Scripts/python.exe -m infra.slack.run --org <org> --dry-run
```
