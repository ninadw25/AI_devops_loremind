# Mock Testing Plan - Fast Track

## Goal
Test Slack + Jira pipeline end-to-end with minimal but sufficient mock data. Target: **2-3 hours total**.

---

## Revised Structure

```
lore-ingestion/mock_testing/
├── docker-compose.yml        # WireMock servers
├── slack/
│   └── mappings/             # WireMock stubs
├── jira/
│   └── mappings/             # WireMock stubs
├── generate_data.py          # Single script, generates all mock data
├── seed_data.json            # Generated mock data (single source of truth)
├── run_test.sh               # One command to run everything
└── README.md                 # How to use
```

---

## How It Works

### Step 1: Generate Mock Data (Python Script)

**Script: `generate_data.py`**

Generates a single `seed_data.json` containing:
- **10 users** (shared across Slack + Jira)
- **5 Slack channels** (3 public, 1 private, 1 DM)
- **~50 Slack messages** with threads
- **2 Jira projects** (~20 issues)
- **~30 Jira comments**
- **Cross-references**: Slack messages mentioning Jira keys

```
python generate_data.py --output seed_data.json
```

### Step 2: WireMock Mappings

WireMock reads JSON stubs from `mappings/` directory. Each stub defines:
- Request matcher (URL, method, params)
- Response (JSON body from seed_data.json)

**Example Slack stub:**
```json
{
  "request": {
    "method": "GET",
    "urlPath": "/api/users.list"
  },
  "response": {
    "status": 200,
    "jsonBody": {
      "ok": true,
      "members": "[loaded from seed_data.json]"
    }
  }
}
```

### Step 3: Run Pipeline

```bash
# Start WireMock
docker-compose up -d

# Configure env
export SLACK_BASE_URL=http://localhost:8090
export JIRA_BASE_URL=http://localhost:8091

# Run Slack fetch
../.venv/bin/python -m infra.slack.run --org techtest

# Run Jira fetch  
../.venv/bin/python -m infra.jira.run --org techtest

# Run pipeline stages
../.venv/bin/python -m pipeline.filter --org techtest --skip-llm
../.venv/bin/python -m pipeline.person_registry --org techtest
../.venv/bin/python -m pipeline.triple_extractor --org techtest --limit 10
```

---

## What Needs to Be Built

### 1. Data Generator (`generate_data.py`)

**No LLM needed initially** - use templates:

```python
# Slack message templates
SLACK_TEMPLATES = [
    "@here Just deployed fix for {jira_key}",
    "Can someone review {jira_key}?",
    "Decision: We're going with option A for {jira_key}",
    "PR up for {jira_key}: {pr_url}",
    "Standup update: completed {jira_key}, starting on {jira_key2}",
]

# Jira issue templates
JIRA_TEMPLATES = {
    "bug": "When {action}, {error}",
    "task": "Implement {feature} for {module}",
    "story": "As a {user}, I want to {action}",
}
```

**Output**: `seed_data.json` with full Slack + Jira structures.

### 2. WireMock Stubs

For each endpoint, create a stub that:
1. Matches the request pattern
2. Returns data from seed_data.json

**Key insight**: WireMock can read response bodies from `__files/` or inline JSON.

### 3. Pipeline Integration

Add `--mock-mode` or environment variable support to `run.py` files to:
- Point to localhost WireMock URLs
- Use test token strings

---

## Minimal Viable Data

| Artifact | Count | Why |
|----------|-------|-----|
| Users | 10 | Test cross-source identity resolution |
| Channels | 5 | Test public/private/DM handling |
| Messages | 50 | Test threading, noise filtering, extraction |
| Jira Projects | 2 | Test multi-project handling |
| Jira Issues | 20 | Test different issue types (bug, task, story) |
| Comments | 30 | Test comment normalization |
| Cross-refs | 10 | Slack messages mentioning Jira keys |

---

## Implementation Order

| Step | What | Time | Who |
|------|------|------|-----|
| 1 | Create folder structure + docker-compose | 5 min | Agent |
| 2 | Write `generate_data.py` | 30 min | Agent |
| 3 | Generate `seed_data.json` | 2 min | Run script |
| 4 | Write WireMock stubs | 30 min | Agent |
| 5 | Test mock servers respond | 10 min | Verify |
| 6 | Add `--mock-mode` to run.py | 20 min | Agent |
| 7 | Run full pipeline | 10 min | Test |
| 8 | Debug & fix | 30 min | Iterate |

**Total: ~2.5 hours**

---

## Credentials Needed

| Service | Credential | How |
|---------|-----------|-----|
| WireMock | None | Runs locally |
| LLM (optional) | OpenRouter API key | For generating more realistic text (optional - templates work fine) |
| Slack/Jira | None for mock testing | Browser login only needed for REAL data |

**No Slack/Jira login needed for mock testing** - that's the whole point!

---

## Cross-System Identity Mapping

```json
{
  "users": [
    {
      "name": "Alice Chen",
      "email": "alice@techtest.io",
      "slack_id": "U001",
      "jira_account_id": "5a001",
      "slack_profile": { "display_name": "Alice", "real_name": "Alice Chen" },
      "jira_profile": { "displayName": "Alice Chen" }
    }
  ]
}
```

Person registry should merge `slack:U001` and `jira:5a001` by matching email.

---

## Next Steps

1. I'll create the folder structure
2. Write the data generator script
3. Generate seed data
4. Create WireMock stubs
5. Add mock-mode to run.py files
6. Test the pipeline

Ready to start?
