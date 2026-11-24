# Outlook Mail + Microsoft Teams — Client Requirements & Setup Guide

**Purpose:** This document tells you exactly what you need from the client,
what the client's IT admin must configure in their Azure tenant, and the
step-by-step commands to run once credentials are in hand.

**Audience:** The developer or DevOps engineer setting up the connector for
the first time with real Microsoft 365 data.

---

## Part 1 — What You Need from the Client

Ask the client's IT administrator for the following three values.
**Nothing else is required from the client side once these are delivered.**

| Value | Where the client finds it | Example format |
|---|---|---|
| **Tenant ID** | Azure portal → Azure Active Directory → Overview → Tenant ID | `xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx` |
| **Client ID** | Azure portal → App registrations → [your app] → Overview → Application (client) ID | `xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx` |
| **Client Secret** | Azure portal → App registrations → [your app] → Certificates & secrets → Value (copy immediately after creation) | `xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx` |

> **Important:** The client secret value is only shown once at creation time.
> If the client did not copy it, they must create a new one.
> Never ask the client to email credentials — use a secure handoff method
> (encrypted message, 1Password share, or secure vault).

### What kind of Microsoft account is required?

Only **organisational Azure Active Directory accounts** are supported.
Personal Microsoft accounts (Outlook.com, Hotmail, Live) will not work —
they lack the admin consent mechanism needed for app-level access.

The client must have:
- An active Microsoft 365 Business or Enterprise subscription
- Microsoft Teams enabled (for Teams data)
- An Azure AD tenant (this comes with any M365 subscription)
- A Global Administrator or Application Administrator account to perform the setup

---

## Part 2 — What the Client's IT Admin Must Do

The IT admin performs this setup once. After this, no ongoing admin involvement
is needed.

### Step 1 — Register an Application in Azure AD

1. Go to [https://portal.azure.com](https://portal.azure.com)
2. Sign in as **Global Administrator** or **Application Administrator**
3. Navigate to: **Azure Active Directory → App registrations → New registration**
4. Fill in:
   - **Name:** `Lore Ingestion Connector` (or any recognisable name)
   - **Supported account types:** `Accounts in this organizational directory only`
   - **Redirect URI:** leave blank
5. Click **Register**
6. On the overview page, copy:
   - **Application (client) ID** → this is the `Client ID`
   - **Directory (tenant) ID** → this is the `Tenant ID`

### Step 2 — Create a Client Secret

1. In the app registration, go to **Certificates & secrets → Client secrets → New client secret**
2. Set a description (e.g. `lore-connector-secret`) and expiry (24 months recommended)
3. Click **Add**
4. **Copy the Value immediately** — it will not be shown again
5. This is the `Client Secret`

### Step 3 — Add API Permissions

1. In the app registration, go to **API permissions → Add a permission → Microsoft Graph → Application permissions**
2. Search for and add each of the following permissions:

| Permission name | Type | Required for |
|---|---|---|
| `Mail.Read` | Application | Reading all users' Outlook mail |
| `ChannelMessage.Read.All` | Application | Reading Teams channel messages |
| `User.Read.All` | Application | Enumerating all org users |
| `ChannelMember.Read.All` | Application | Reading Teams channel memberships for ACL |

> **Why Application permissions and not Delegated?**
> Application permissions allow the connector to run as a background service
> without any user signing in. Delegated permissions only work when a specific
> user is actively logged in, which is not viable for automated ingestion.

3. Click **Add permissions** after selecting all four

### Step 4 — Grant Admin Consent

After adding permissions:

1. On the **API permissions** page, click **Grant admin consent for [tenant name]**
2. Confirm by clicking **Yes**
3. All four permissions should show a green tick under **Status: Granted**

> This step requires Global Administrator or Privileged Role Administrator rights.
> Without admin consent, the connector will receive `403 Forbidden` on all API calls.

### Step 5 — Verify the Setup (Optional but Recommended)

The admin can verify the app is working before handing over credentials by using
the [Graph Explorer](https://developer.microsoft.com/en-us/graph/graph-explorer)
or running this quick token test:

```powershell
# Quick verification — replace the placeholders
$body = @{
    grant_type    = "client_credentials"
    client_id     = "<CLIENT_ID>"
    client_secret = "<CLIENT_SECRET>"
    scope         = "https://graph.microsoft.com/.default"
}
$token = Invoke-RestMethod `
    -Uri "https://login.microsoftonline.com/<TENANT_ID>/oauth2/v2.0/token" `
    -Method POST -Body $body
Write-Host "Token obtained. Expires in: $($token.expires_in) seconds"
```

A successful response (HTTP 200 with `access_token`) confirms the credentials work.

---

## Part 3 — Local Environment Setup

### Prerequisites

Before running any commands, ensure the following are installed on your machine:

- Python 3.11 or higher
- `uv` or `pip` for package installation
- The `lore-ingestion` repository cloned locally

### Step 1 — Install dependencies

```bash
cd lore-ingestion
pip install -r requirements.txt
# or with uv:
uv pip install -r requirements.txt
```

### Step 2 — Create the `.env` file

Copy the example file and fill in the Microsoft 365 credentials:

```bash
cp .env.example .env
```

Open `.env` in your editor and set the following values:

```dotenv
# ── Microsoft 365 / Outlook + Teams ──────────────────────────────────
AZURE_TENANT_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
AZURE_CLIENT_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
AZURE_CLIENT_SECRET=your-client-secret-value

# Alternative naming (both are supported by the connector):
# MS365_TENANT_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
# MS365_CLIENT_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
# MS365_CLIENT_SECRET=your-client-secret-value

# Organisation identifier (used to namespace all output files)
ORG=your-org-name
```

> Both `AZURE_*` and `MS365_*` naming schemes work. The connector tries
> `MS365_*` first and falls back to `AZURE_*`. Either set is sufficient.

### Step 3 — Set the org config

Create an org config file so the pipeline knows the organisation's email domain(s):

```bash
mkdir -p output/config
```

Create `output/config/{org}_org_config.json` (replace `{org}` with your ORG value):

```json
{
  "org_domains": ["corp.com"],
  "discovered_noise_domains": [],
  "allowlisted_domains": [],
  "extra_functional_mailboxes": []
}
```

Replace `"corp.com"` with the actual organisation domain (e.g. `"company.co.uk"`).
Add multiple domains if the org uses more than one: `["corp.com", "corp.co.uk"]`.

---

## Part 4 — Running the Full Pipeline

Run these commands in order. Each stage produces files that the next stage reads.

### Stage 1 — Validate credentials

Before any data fetch, verify the credentials work:

```bash
python -m infra.outlook.run --org your-org-name --stage discover --output-dir ./output
```

Expected output:
```
Discover Summary
Org:             your-org-name
Identities:      42 users
Teams scopes:    38 users with channel memberships
Identities file: output/outlook/your-org-name_ms365_identities.json
Scopes file:     output/outlook/your-org-name_ms365_teams_scopes.json
```

If you see `Authentication failed` or `403 Forbidden`, go back to Part 2 and verify
the admin consent step was completed correctly.

**What this produces:**
```
output/
  outlook/
    {org}_ms365_identities.json    ← all enabled org users
    {org}_ms365_teams_scopes.json  ← which users can see which Teams channels
```

### Stage 2 — Fetch raw data

Fetch Outlook Mail and Teams messages. Use `--limit` on first run to test
a small sample before committing to a full fetch:

```bash
# Test run — first 100 items only
python -m infra.outlook.run --org your-org-name --stage fetch --limit 100 --output-dir ./output

# Full run (no limit — may take minutes to hours depending on org size)
python -m infra.outlook.run --org your-org-name --stage fetch --output-dir ./output
```

**What this produces:**
```
output/
  raw/
    {org}_outlook_mail_{timestamp}.jsonl   ← raw Outlook Mail messages
    {org}_ms_teams_{timestamp}.jsonl       ← raw Teams messages
```

> The fetch stage uses delta tokens so subsequent runs only fetch new/changed
> messages — the first run is a full sync, all later runs are incremental.

### Stage 3 — Run the smoke pipeline

The smoke pipeline runs filter → person registry → triple extraction
on the raw data. Point it at the raw files produced in Stage 2:

```bash
# Run both Outlook Mail and Teams
python -m infra.outlook.run --org your-org-name --stage smoke \
  --raw-mail output/raw/{org}_outlook_mail_{timestamp}.jsonl \
  --raw-teams output/raw/{org}_ms_teams_{timestamp}.jsonl \
  --output-dir ./output
```

Or use the stable path (if you copied/renamed the timestamped files):

```bash
# Copy to stable paths first
cp output/raw/{org}_outlook_mail_{timestamp}.jsonl output/raw/{org}_outlook_mail.jsonl
cp output/raw/{org}_ms_teams_{timestamp}.jsonl     output/raw/{org}_ms_teams.jsonl

# Then run without explicit paths (uses OutlookPaths defaults)
python -m infra.outlook.run --org your-org-name --stage smoke --output-dir ./output
```

To test only one source:

```bash
python -m infra.outlook.run --org your-org-name --stage smoke --source outlook_mail
python -m infra.outlook.run --org your-org-name --stage smoke --source ms_teams
```

**What this produces:**
```
output/
  filtered/
    {org}_outlook_mail_filtered.jsonl
    {org}_ms_teams_filtered.jsonl
  knowledge/
    triples/
      {org}_outlook_mail_triples_raw.json
      {org}_ms_teams_triples_raw.json
    registry/
      {org}_person_registry.json
      {org}_email_lookup.json
  checkpoints/
    {org}_outlook_mail_triple_extraction_checkpoint.json
    {org}_ms_teams_triple_extraction_checkpoint.json
```

### Stage 4 — Run the builder (generate Neo4j Cypher)

Once triples are extracted, the builder generates the Neo4j Cypher files:

```bash
# Dry run — generate Cypher files without uploading to Neo4j
python -m builders.unified_builder \
  --org your-org-name \
  --source outlook_mail \
  --skip-upload \
  --output-dir ./output

python -m builders.unified_builder \
  --org your-org-name \
  --source ms_teams \
  --skip-upload \
  --output-dir ./output

# Full run — upload to Neo4j (requires NEO4J_URI and NEO4J_PASSWORD in .env)
python -m builders.unified_builder \
  --org your-org-name \
  --output-dir ./output
```

**What this produces:**
```
output/
  cypher/
    {org}_pass0_schema.cypher
    {org}_pass1_persons.cypher
    {org}_pass2_chunk_*.cypher   ← knowledge nodes and edges
    {org}_pass2_corroborates.cypher
```

---

## Part 5 — Incremental Re-runs

After the first full sync, subsequent runs only need Stage 2 and Stage 3.
The delta tokens stored in `output/outlook/{org}_delta_tokens.json` ensure
only new/changed messages are fetched.

```bash
# Incremental fetch (Stage 2 only — delta tokens used automatically)
python -m infra.outlook.run --org your-org-name --stage fetch --output-dir ./output

# Re-run smoke pipeline on the new files (Stage 3)
python -m infra.outlook.run --org your-org-name --stage smoke --output-dir ./output
```

---

## Part 6 — Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `Authentication failed: Azure AD token request failed` | Incorrect tenant ID, client ID, or client secret | Double-check all three values in `.env`; ensure no trailing spaces |
| `403 Forbidden` on first Graph call | Admin consent not granted | IT admin must click "Grant admin consent" in Azure portal (Part 2 Step 4) |
| `0 users found. Skipping mail fetch.` | `User.Read.All` permission missing or not consented | Verify all four permissions are added and consented (Part 2 Steps 3-4) |
| `404 Not Found` on Teams endpoints | Teams license not enabled for the tenant, or `ChannelMessage.Read.All` missing | Verify Teams is enabled; re-check application permissions |
| `RuntimeError: Graph GET failed: 410 Gone` | Delta token expired (>30 days since last fetch) | Delete `output/outlook/{org}_delta_tokens.json` and re-run fetch (triggers full re-sync) |
| Empty `ms365_identities.json` (count: 0) | `User.Read.All` missing or all accounts are Guest-type | Verify permission; check that org has enabled member accounts |
| `FileNotFoundError: Outlook Mail raw JSONL not found` | Stage 3 run before Stage 2, or wrong `--raw-mail` path | Run Stage 2 first; check path argument points to the correct file |
| Extraction produces 0 triples | LLM API key missing or extraction model unreachable | Set `BEDROCK_API_KEY` / `OPENROUTER_API_KEY` in `.env` and verify the model endpoint is reachable |

---

## Part 7 — Summary of Credentials Required

| Credential | Where to set it | Who provides it |
|---|---|---|
| `AZURE_TENANT_ID` | `.env` | Client IT admin |
| `AZURE_CLIENT_ID` | `.env` | Client IT admin |
| `AZURE_CLIENT_SECRET` | `.env` | Client IT admin |
| `ORG` | `.env` or `--org` flag | You (use a short slug like `acme`) |
| `BEDROCK_API_KEY` / `OPENROUTER_API_KEY` | `.env` | Your team (for LLM extraction) |
| `NEO4J_URI` + `NEO4J_PASSWORD` | `.env` | Your team (for graph upload) |

The first three must come from the client. The rest are your own infrastructure.

---

## Part 8 — Security Notes

- **Never commit `.env` to version control.** The `.gitignore` already excludes it.
- **Rotate the client secret** if it is ever exposed. Set a reminder before expiry (default 24 months).
- **The client secret has org-wide read access** to all mail and Teams. Treat it with the same care as a master password.
- **Data stays in your infrastructure.** The connector pulls data from Microsoft Graph into your local `output/` directory. Nothing is sent anywhere except your own Neo4j instance.
- **The connector uses read-only permissions.** `Mail.Read`, `ChannelMessage.Read.All`, `User.Read.All`, and `ChannelMember.Read.All` grant read access only — no write, no delete, no send.
