# AI-Powered Test Data Generation

**For Slack test data.** (A Jira generator was planned, but `generate_jira_test_data.py` was never added to this repo.)

Use these scripts to generate comprehensive test data in hours instead of days!

---

## Quick Start

### Prerequisites

1. **AWS Bedrock Access** (Ninad will provide API key)
   - Region: `us-east-1`
   - Model: `deepseek.deepseek-v3-2` (DeepSeek V3.2)
   - Setup: `export AWS_ACCESS_KEY_ID=...` and `export AWS_SECRET_ACCESS_KEY=...`

2. **Python Dependencies**
   ```bash
   pip install boto3 requests
   ```

---

## For Riddhi: Slack Test Data

### Setup

1. **Create Slack App**
   - Go to: https://api.slack.com/apps
   - Create new app: "Lore Test Data Generator"
   - Add OAuth scopes:
     - `channels:manage`
     - `channels:history`
     - `chat:write`
     - `chat:edit`
     - `reactions:write`
   - Install to workspace
   - Copy Bot User OAuth Token (`xoxb-...`)

2. **Invite Bot to Channels**
   - Create channels: `test-general`, `test-engineering`, `test-random`, `test-standup`
   - Or use existing channels

### Run

```bash
cd /home/zeiko/work/loremind/lore/scripts

# Set AWS credentials
export AWS_ACCESS_KEY_ID=your_aws_key
export AWS_SECRET_ACCESS_KEY=your_aws_secret

# Run generator
python generate_slack_test_data.py \
  --slack-token xoxb-your-token \
  --num-messages 200
```

### What It Creates

- **200 total messages** with:
  - 120 regular messages across 4 channels
  - 40 thread replies (10 threads with 3-8 replies)
  - 15 edited messages (some edited twice)
  - 10 deleted messages

- **Reactions**: ~40 messages with emoji reactions
- **Formats**: Code blocks, bold, italic, quotes, emoji
- **Channels**: Auto-creates `test-*` channels

### Manual Steps Required

None - all actions automated!

---

## Time Estimates

| Task | Traditional | With Scripts |
|------|------------|--------------|
| Slack setup | 1 day | 1 hour |
| Data generation | 2 days | 30 minutes |
| **Total** | **3 days** | **1.5 hours** |

---

## AWS Bedrock Costs

Estimate for generating 250 pieces of content:

- DeepSeek V3.2: ~$0.0001 per 1K tokens (10x cheaper than Claude!)
- Average prompt: ~100 tokens input, ~100 tokens output
- Total: ~250 requests × 200 tokens = ~50K tokens
- **Cost: ~$0.005 total (less than 1 cent!)**

---

## Troubleshooting

### Slack: "missing_scope"
- Add these scopes: `channels:manage`, `chat:write`, `chat:edit`
- Reinstall app to workspace after adding scopes

### AWS: "Unable to locate credentials"
```bash
export AWS_ACCESS_KEY_ID=your_key
export AWS_SECRET_ACCESS_KEY=your_secret
export AWS_DEFAULT_REGION=us-east-1
```

### Rate Limits
- Script has built-in delays (`time.sleep(0.5)`)
- If hitting limits, increase delays in code

---

## Next Steps

After generating test data:

1. **Notify in Linear**: Comment on LOR-54/LOR-55 when done
2. **Share credentials**: Provide Slack test account access to Ninad
3. **Pipeline test**: We'll run the ingestion pipeline on your test data
4. **Validate**: Check that edit/delete detection works correctly

---

## Contact

Questions? Ask in Slack #loremind-engineering or comment on Linear tickets.
