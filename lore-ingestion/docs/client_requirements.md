# Lore Ingestion: Client Credentials Requirements

## Overview

This document details all the credentials and permissions required from clients to enable Lore's knowledge ingestion pipeline. Lore connects to three primary sources: **Google Workspace (Gmail)**, **Slack**, and **Jira**. Each requires specific authentication tokens, API access, and organizational permissions.

---

## Table of Contents

1. [Google Workspace (Gmail)](#1-google-workspace-gmail)
2. [Slack](#2-slack)
3. [Jira](#3-jira)
4. [Summary Table](#4-summary-table)

---

## 1. Google Workspace (Gmail)

### 1.1 What We Need From the Client

| Credential | Format | Description |
|------------|--------|-------------|
| **Google Service Account JSON Key** | `*.json` file | OAuth 2.0 service account credentials |
| **Super Admin Email** | `admin@company.com` | Google Workspace Super Admin for Domain-Wide Delegation |
| **Primary Domain** | `company.com` | Main Google Workspace domain |
| **Additional Domains** (optional) | `subsidiary.com` | Secondary domains if the org uses multiple |

### 1.2 What We Provide to the Client

We will provide:
- A **Google Cloud Service Account** with specific OAuth scopes
- Instructions to enable **Domain-Wide Delegation (DWD)**
- Verification steps to confirm proper setup

### 1.3 Setup Instructions for Client

#### Step 1: Create Google Cloud Project (if not exists)
Client needs a Google Cloud project with:
- Google Workspace APIs enabled

#### Step 2: Enable Required APIs
```
Gmail API
Admin SDK API
```

#### Step 3: Create Service Account with Domain-Wide Delegation
1. Go to **Google Cloud Console → IAM & Admin → Service Accounts**
2. Click **Create Service Account**
3. Name: `lore-ingestion-service`
4. Grant no specific roles (the account acts on its own)
5. Enable **Domain-Wide Delegation** and note the Client ID
6. Create and download the JSON key file

#### Step 4: Authorize API Scopes in Google Workspace Admin

The client must add these OAuth scopes in their **Google Workspace Admin Console**:

**Required Scopes:**
```
https://www.googleapis.com/auth/gmail.readonly
https://www.googleapis.com/auth/admin.directory.user.readonly
```

**To add scopes:**
1. Go to **Admin Console → Security → Access and data control → API controls**
2. Click **Manage Domain Wide Delegation**
3. Click **Add new**
4. Enter the **Client ID** from the service account
5. Add both OAuth scopes above (comma-separated)
6. Click **Authorize**

### 1.4 API Quotas & Rate Limits

| Resource | Quota | Our Usage |
|----------|-------|-----------|
| Gmail API | 250 quota units/user/second | ~50 units/second (batch calls of 50 messages) |
| Admin SDK Directory API | 1500 requests/100 seconds | ~10 requests for user listing |

### 1.5 What Data We Fetch

- **Users**: List of all users in the domain
- **Emails** (filtered):
  - Excludes: `category:promotions`, `category:social`, `category:updates`, `category:forums`
  - Excludes senders: `noreply*`, `notifications@*`, `calendar-notification`, `postmaster`
  - Functional mailboxes: Only `SENT` folder + internal conversations
  - Personal mailboxes: All emails except filtered categories
- **Threads**: Full thread conversations, not individual emails only

---

## 2. Slack

### 2.1 What We Need From the Client

| Credential | Format | Description |
|------------|--------|-------------|
| **Bot Token** | `xoxb-...` | OAuth bot token with specific scopes |
| **App Token** (Socket Mode) | `xapp-...` | App-level token for real-time streaming |
| **Workspace Domain** | `company.slack.com` | For verification |

### 2.2 What We Provide to the Client

We provide:
- A **Slack App Manifest** (JSON/YAML) with all required configuration
- OAuth redirect URL for installation
- Optional: Pre-built app for their Enterprise Grid (if applicable)

### 2.3 Required OAuth Scopes

#### Standard Workspace (Bot Token - `xoxb-`)

**Required Scopes:**
```
channels:read          # View public channel metadata
channels:history       # Read public channel messages
groups:read            # View private channel metadata
groups:history         # Read private channel messages
im:read                # View direct message metadata
im:history             # Read direct message history
mpim:read              # View group DM metadata
mpim:history           # Read group DM history
users:read             # View user profiles
users:read.email       # View user email addresses (REQUIRED)
usergroups:read        # View user groups
reactions:read         # View message reactions
files:read             # View files shared in messages
```

**App-Level Scopes** (for Socket Mode - `xapp-`):
```
connections:write      # Open Socket Mode connections
```

### 2.4 Enterprise Grid: Admin Token Path (Primary - ~90% of clients)

If the client is on **Slack Enterprise Grid**, we can use **Admin APIs** for deterministic, org-wide access:

**Additional Admin Scopes:**
```
admin.conversations:read      # Read all channel metadata org-wide
admin.users:read              # Read all user data
admin.teams:read              # List workspaces in org
admin.roles:read:1            # Read role assignments
```

**How to obtain Admin Token:**
1. The app must be installed at the **Org level** (not workspace level)
2. Only available to Enterprise Grid customers
3. Requires approval by **Org Owner** or **Org Admin**

### 2.5 Enterprise Grid: Discovery API Path (Future)

For **edit/delete detection** and **complete private channel ACLs**:

**Discovery Scope:**
```
discovery:read               # Enterprise Grid Discovery API access
```

**Prerequisites:**
- Enterprise Grid only
- App pre-approved by Slack
- Requires special approval from Slack (not self-serve)

### 2.6 Installation Steps for Client

#### Option A: Manual Installation (Standard)

1. Go to `https://api.slack.com/apps`
2. Click **Create New App → From manifest**
3. Paste our provided manifest JSON
4. Go to **OAuth & Permissions**
5. Add all Bot Token Scopes listed above
6. Click **Install to Workspace**
7. Copy the **Bot Token** (`xoxb-...`) and **App Token** (`xapp-...`)

#### Option B: Direct Install URL (Simpler)

We can provide a direct install URL:
```
https://slack.com/oauth/v2/authorize?client_id=YOUR_CLIENT_ID&scope=channels:read,channels:history,groups:read,groups:history,im:read,im:history,mpim:read,mpim:history,users:read,users:read.email,usergroups:read,reactions:read,files:read
```

### 2.7 Rate Limits by App Tier

| Tier | Token Type | Rate Limit |
|------|------------|------------|
| **Restricted** | Non-Marketplace (post-2025) | 1 request/min for history |
| **Tier 3** | Standard | 50 conversations.history/min |
| **Enterprise** | Grid | Higher limits |
| **Marketplace** | Published App | Standard limits |

### 2.8 What Data We Fetch

- **Channels**: Public + Private (where bot is member)
- **Messages**: All messages, threads, replies
- **Users**: Profile data including email (for identity resolution)
- **Usergroups**: For ACL expansion
- **Files**: Metadata and content references
- **Reactions**: Emoji reactions on messages

**Note:** For private channels, the bot must be a member to read messages. Use Admin APIs or ask the client to invite the bot to private channels.

---

## 3. Jira

### 3.1 What We Need From the Client

| Credential | Format | Required For |
|------------|--------|--------------|
| **Jira Base URL** | `https://company.atlassian.net` | All modes |
| **Email** | `admin@company.com` | Basic Auth |
| **API Token** | `ATATT3x...` | Basic Auth |
| **OR OAuth Token** | `eyJ...` | OAuth 2.0 |
| **OR Personal Access Token (PAT)** | `MDU0...` | Jira Data Center/Server |

### 3.2 What We Provide to the Client

For OAuth setups, we provide:
- OAuth App Client ID/Secret (or instructions to create)
- Redirect URI for OAuth flow
- Verification checklist

For API Token setup, we provide:
- Instructions to generate API tokens
- Troubleshooting guide

### 3.3 Authentication Options

#### Option A: API Token (Cloud - Easiest)

**Steps for client:**
1. Go to **Atlassian Account Settings → Security → API Tokens**
2. Click **Create API Token**
3. Name: `Lore Ingestion`
4. Copy the token immediately (shown once)
5. Provide: Base URL, Email, API Token

#### Option B: OAuth 2.0 (Cloud - Better Long-term)

Requires OAuth app registration with Atlassian.

**Scopes needed:**
```
read:jira-work          # Read project and issue data
read:jira-user          # Read user data
```

#### Option C: Personal Access Token (Data Center/Server)

For self-hosted Jira instances.

### 3.4 Required API Access

We need read access to:

**REST API Endpoints:**
```
GET /rest/api/3/myself                    # Validate credentials
GET /rest/api/3/users/search              # List users (identity resolution)
GET /rest/api/3/group/bulk                # List groups
GET /rest/api/3/group/member              # Group memberships
GET /rest/api/3/project/search            # List projects
GET /rest/api/3/permissionscheme          # Project permission schemes (ACL)
GET /rest/api/3/issueSecuritySchemes      # Issue-level security schemes
GET /rest/api/3/search                    # Search issues (JQL)
GET /rest/api/3/issue/{key}/changelog     # Issue change history
```

### 3.5 API Permissions Needed

| Action | Required Jira Permission |
|--------|--------------------------|
| Read all issues | Browse Projects (global) |
| Read user directory | User Picker (global) |
| Read groups | View Group (global) |
| Read permission schemes | Administer Jira (or specific project admin) |

**Note:** Permission scheme reading may require elevated permissions. Without it, we can still ingest issues but ACLs may be incomplete (fail-closed: denies access unless proven otherwise).

### 3.6 Rate Limits

| Plan | Rate Limit |
|------|------------|
| Jira Cloud Free | 1000 requests/5 min |
| Jira Cloud Standard | 1000 requests/5 min |
| Jira Cloud Premium | Higher limits |
| Jira Data Center | Configurable by admin |

Our connector uses: 0.2s delay between requests + exponential backoff on 429 errors.

### 3.7 What Data We Fetch

**Identity Resolution:**
- All users (accountId, email, displayName, accountType)
- All groups and group memberships

**Project ACL Discovery:**
- All projects
- Permission schemes (who can browse each project)
- Issue security schemes (issue-level visibility)

**Issue Data:**
- Issues: Key, summary, description, type, status, reporter, assignee
- Comments: All comments per issue
- Changelog: Status changes, field updates, edit history
- Security level: For issue-level ACLs

---

## 4. Summary Table

### Credentials Required by Source

| Source | Required Credentials | Optional Credentials | Setup Complexity |
|--------|---------------------|---------------------|------------------|
| **Gmail** | Service Account JSON, Admin Email, Domain | Additional domains | High (DWD setup) |
| **Slack** | Bot Token (`xoxb-`) | App Token (`xapp-`), Admin Token (Grid) | Low-Medium |
| **Jira** | Base URL, Email, API Token | OAuth Token, PAT | Low |

### What We Give Client

| Source | What We Provide |
|--------|-----------------|
| **Gmail** | Service account setup guide, OAuth scope list, DWD instructions, troubleshooting guide |
| **Slack** | App manifest, installation URL, scope list, Admin API guide (Grid) |
| **Jira** | API token generation guide, troubleshooting, OAuth app instructions |

### Data Fetched Summary

| Source | Data Types | ACL Granularity |
|--------|-----------|-----------------|
| **Gmail** | Emails (filtered), threads, users | Per-user (inbox-level) |
| **Slack** | Messages, threads, files, users, channels | Per-channel + Usergroups |
| **Jira** | Issues, comments, changelogs, users | Per-project + Issue-level security |

---

## Appendix A: Sample JSON for Backend Storage

This is how you might structure the credentials in your backend:

```json
{
  "org_id": "acme-corp",
  "integrations": {
    "gmail": {
      "enabled": true,
      "domain": "acme.com",
      "admin_email": "admin@acme.com",
      "service_account_key_path": "/secure/gmail/acme-key.json",
      "additional_domains": ["acme-subsidiary.com"],
      "dwd_enabled": true,
      "last_sync": "2026-06-07T12:00:00Z"
    },
    "slack": {
      "enabled": true,
      "workspace_domain": "acme.slack.com",
      "bot_token": "xoxb-***",
      "app_token": "xapp-***",
      "app_tier": "marketplace",
      "is_enterprise_grid": true,
      "discovery_api_enabled": false,
      "last_sync": "2026-06-07T12:00:00Z"
    },
    "jira": {
      "enabled": true,
      "base_url": "https://acme.atlassian.net",
      "auth_type": "api_token",
      "email": "admin@acme.com",
      "api_token": "ATATT3x***",
      "project_whitelist": ["PROJ", "DEV"],
      "last_sync": "2026-06-07T12:00:00Z"
    }
  }
}
```

## Appendix B: Validation Checklist

Before first sync, validate:

### Gmail
- [ ] Service account key file is valid JSON
- [ ] DWD is enabled and scopes are authorized in Admin Console
- [ ] Admin email has Super Admin role
- [ ] Gmail API is enabled in Google Cloud
- [ ] Admin SDK API is enabled

### Slack
- [ ] Bot token starts with `xoxb-`
- [ ] All required scopes are granted
- [ ] Bot is invited to private channels (if not using Admin API)
- [ ] For socket mode: App token starts with `xapp-`
- [ ] For Grid: App is installed at Org level

### Jira
- [ ] Base URL is reachable
- [ ] Admin email has access to Jira
- [ ] API token is valid (not expired)
- [ ] User has "Browse Projects" permission globally
- [ ] User has "Administer Jira" OR individual project admin for ACL discovery

---

## Appendix C: Environment Variables

The ingestion pipeline expects these environment variables (all optional - can also be passed via CLI):

```bash
# Gmail (loaded from JSON file, no env vars needed)

# Slack
SLACK_ORG=acme-corp
SLACK_TOKEN=xoxp-...  # admin user token (preferred) or xoxb- bot token
SLACK_APP_TOKEN=xapp-...
SLACK_INGEST_MODE=polling  # polling | socket | hybrid
SLACK_SCANBACK_DAYS=14
SLACK_APP_TIER=marketplace  # dev | tier3 | enterprise | marketplace | restricted

# Jira
JIRA_BASE_URL=https://acme.atlassian.net
JIRA_EMAIL=admin@acme.com
JIRA_API_TOKEN=ATATT3x...
JIRA_REQUEST_DELAY=0.2
JIRA_MAX_RETRIES=5
```

CLI equivalents:
```bash
# Gmail
python -m infra.gmail.run --org acme --domain acme.com --admin-email admin@acme.com --key /path/to/key.json

# Slack
python -m infra.slack.run --org acme --token xoxb-... --app-token xappp-...

# Jira
python -m infra.jira.run --org acme --base-url https://acme.atlassian.net --admin-email admin@acme.com --api-token ATATT3x...
```

---

*Document generated for lore-ingestion pipeline v1.0*
