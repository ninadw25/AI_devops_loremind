# Slack Onboarding Guide

Two paths depending on the client's Slack plan. Identify the plan first — everything else follows from that.

**How to check**: In Slack → workspace name → Settings & administration. If you see **Organisation settings**, it's Enterprise Grid. If only **Workspace settings**, it's a standard workspace.

---

## Standard Workspace

### Token

Generate an **admin user token** (`xoxp-`) for a Workspace Admin or Owner account.

In Slack app settings → OAuth & Permissions → User Token Scopes → install app → copy the `xoxp-` token.

Set in `.env`:
```
SLACK_TOKEN=xoxp-...
```

### Required Scopes (User Token)

| Scope | Purpose |
|---|---|
| `channels:read` | List public channels |
| `channels:history` | Read public channel messages |
| `groups:read` | List private channels the admin is in |
| `groups:history` | Read private channel messages the admin is in |
| `im:read` | List DMs |
| `im:history` | Read DM messages |
| `mpim:read` | List group DMs |
| `mpim:history` | Read group DM messages |
| `users:read` | User directory |
| `users:read.email` | User emails (needed for cross-source identity merge) |
| `usergroups:read` | Usergroup membership for ACL |
| `reactions:read` | Emoji reactions |
| `files:read` | File attachments |

### Private Channels

The admin token can only read private channels the admin account is **already a member of**.

During onboarding, add the admin account to every private channel that should be ingested:
1. Open each private channel in Slack
2. Click the channel name → Members → Add people → add the admin account
3. Repeat for all relevant private channels

There is no API method to bypass this on standard workspaces — membership is the only gate.

### What you get

- All public channel content (no bot join needed — admin token reads directly)
- Private channel content for channels the admin was invited to
- Full user directory with emails
- ACL: private channel membership resolved from `conversations.members`

---

## Enterprise Grid

### Token

Generate an **Org Owner user token** (`xoxp-`) — must be Org Owner, not just Workspace Admin.

In Slack app settings → OAuth & Permissions → User Token Scopes → install to org → copy the `xoxp-` token.

Set in `.env`:
```
SLACK_TOKEN=xoxp-...
```

### Required Scopes (User Token)

All standard workspace scopes above, plus:

| Scope | Purpose |
|---|---|
| `admin.conversations:read` | Enumerate ALL channels org-wide without membership |
| `admin.users:read` | Full org user directory with roles |
| `admin.roles:read` | Admin/owner role assignments |
| `admin.usergroups:read` | Org-wide usergroup membership for ACL |
| `admin.teams:read` | List all workspaces in the org |
| `auditlogs:read` | Audit log events (optional — for edit/delete tracking) |

### What the admin scopes unlock

With these scopes, the pipeline automatically switches to `GRID_ADMIN` tier:
- Enumerates every channel (public and private) org-wide — no bot membership needed
- Builds full user directory with roles from `admin.users.list`
- Resolves ACL deterministically (who is in which private channel) via `admin.conversations.*`

**Private channel content** still requires the admin account to be a member. The admin scopes give you metadata and ACL — not message content for channels you're not in.

### Private Channels (without Discovery API)

Same process as standard workspace — add the admin account to relevant private channels during onboarding.

### Private Channels (with Discovery API)

The Discovery API is the only way to read private channel content **without membership**. It is included in Enterprise Grid at no extra cost but requires Slack's approval.

#### What it gives you
- Full message content for all private channels org-wide
- Historical messages including edits and deletes
- No channel membership required

#### How to get it

**Step 1 — Client enables Discovery API**

The client's Org Owner must enable eDiscovery in their Slack org:
- Slack admin dashboard → Security → eDiscovery
- Enable the Discovery API for the org

**Step 2 — Build a Discovery app**

Discovery requires a **separate, dedicated Slack app** (not the same app used for ingestion). This app:
- Must be an "Org-Approved App" type
- Requires the `discovery:read` scope
- Cannot be a standard bot/user app

**Step 3 — Submit for Slack approval**

Slack manually reviews Discovery apps. Submit through Slack's enterprise sales/support:
- Use case must be framed as compliance, legal hold, or archiving — not general ingestion
- Slack typically approves: legal hold tools, DLP, compliance archiving
- Timeline: **weeks to months** — no fixed SLA

**Step 4 — Client approves your app**

Even after Slack approves Loremind's Discovery app globally, each new client must:
- Install the Discovery app to their org
- Grant org-level approval

#### Practical note

Discovery API approval is per-org and depends on Slack's review. Do not plan it as a default onboarding step. Pursue it only when:
- The client is on Enterprise Grid
- They specifically need retroactive full private channel coverage
- They are willing to drive the approval process on their end (it requires their legal/IT team)

For most clients, admin account membership is sufficient.

---

## Direct Messages (DMs)

### Current status

DM scopes (`im:read`, `im:history`, `mpim:read`, `mpim:history`) are already included in the scope list. DM ingestion is **off by default** — enabled via `--include-dms` flag when running the pipeline.

### Access limitations

With an admin user token, only DMs the **admin account is a participant in** are accessible. There is no API (including admin scopes on Enterprise Grid) to read DMs between other users. The only way to access all DMs org-wide is the **Discovery API**.

### Filtering

DMs fetched by the pipeline pass through the same filter and extraction stages as channels:
- Stage 2 (`pipeline/filter.py`) removes noise (~70% dropped)
- Stage 5 (`pipeline/triple_extractor.py`) only extracts decisions, tasks, facts — casual conversation produces no triples
- Stage 6 (`pipeline/validator.py`) rejects low-quality extractions

A DM like *"hey lunch at 1?"* is fetched but produces zero triples and is dropped. Only DMs with real knowledge content surface in the graph.

### When to enable

Enable `--include-dms` only when:
1. The client explicitly requests it
2. Legal/privacy requirements are addressed — raw DM content passes through the pipeline and is stored in `output/raw/` even if filtered out of the graph. Depending on jurisdiction and employee agreements, this may require explicit consent or a data processing agreement (GDPR etc.)

For most clients, channel ingestion alone covers the meaningful organizational knowledge. DMs are opt-in.

---

## Summary

| | Standard Workspace | Enterprise Grid (no Discovery) | Enterprise Grid + Discovery |
|---|---|---|---|
| Public channels | ✅ Full content | ✅ Full content | ✅ Full content |
| Private channels (member) | ✅ Full content | ✅ Full content | ✅ Full content |
| Private channels (non-member) | ❌ | ❌ | ✅ Full content |
| ACL accuracy | Heuristic | Deterministic | Deterministic |
| User directory | Partial | Full + roles | Full + roles |
| Setup effort | Low | Low + admin scopes | High (Slack approval) |
| Token type | `xoxp-` admin | `xoxp-` org owner | `xoxp-` + Discovery app |
