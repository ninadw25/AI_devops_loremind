#!/usr/bin/env python3
"""
Generate WireMock stubs from seed_data.json.
Creates mappings for Slack and Jira API endpoints.
"""
import json
from pathlib import Path

# Load seed data
seed_path = Path(__file__).parent / "seed_data.json"
with open(seed_path) as f:
    data = json.load(f)

users = data["users"]
slack_channels = data["slack"]["channels"]
slack_messages = data["slack"]["messages"]
jira_projects = data["jira"]["projects"]
jira_issues = data["jira"]["issues"]

sslack_dir = Path(__file__).parent / "slack" / "mappings"
jira_dir = Path(__file__).parent / "jira" / "mappings"
sslack_dir.mkdir(parents=True, exist_ok=True)
jira_dir.mkdir(parents=True, exist_ok=True)

# ============================================================================
# Slack Stubs
# ============================================================================

# 1. auth.test - with all required scopes in header
ALL_SCOPES = "channels:read,channels:history,groups:read,groups:history,im:read,im:history,mpim:read,mpim:history,users:read,users:read.email,usergroups:read,reactions:read,files:read"
auth_test_stub = {
    "request": {"method": "GET", "urlPath": "/api/auth.test"},
    "response": {
        "status": 200,
        "headers": {
            "Content-Type": "application/json",
            "X-OAuth-Scopes": ALL_SCOPES
        },
        "jsonBody": {"ok": True, "team": "TechTest", "team_id": "T12345678", "user": "mockbot", "user_id": "UBOT1234"}
    }
}
with open(sslack_dir / "auth-test.json", "w") as f:
    json.dump(auth_test_stub, f, indent=2)

# 2. users.list
users_list_body = {"ok": True, "members": [
    {"id": u["slack_id"], "name": u["slack_profile"]["display_name"], "real_name": u["name"],
     "profile": {"email": u["email"], "display_name": u["slack_profile"]["display_name"]}}
    for u in users
]}
users_list_stub = {
    "request": {"method": "GET", "urlPath": "/api/users.list"},
    "response": {"status": 200, "headers": {"Content-Type": "application/json"}, "jsonBody": users_list_body}
}
with open(sslack_dir / "users-list.json", "w") as f:
    json.dump(users_list_stub, f, indent=2)

# 3. conversations.list (channels)
conversations_list_body = {"ok": True, "channels": slack_channels}
conversations_list_stub = {
    "request": {"method": "GET", "urlPath": "/api/conversations.list"},
    "response": {"status": 200, "headers": {"Content-Type": "application/json"}, "jsonBody": conversations_list_body}
}
with open(sslack_dir / "conversations-list.json", "w") as f:
    json.dump(conversations_list_stub, f, indent=2)

# 4. conversations.history (per channel) - with cursor support
for channel in slack_channels:
    channel_msgs = [m for m in slack_messages if m["channel_id"] == channel["id"]]
    # Split into batches of 20 for pagination
    batch_size = 20
    for batch_num, i in enumerate(range(0, len(channel_msgs), batch_size)):
        batch = channel_msgs[i:i+batch_size]
        has_more = i + batch_size < len(channel_msgs)
        response_body = {"ok": True, "messages": batch, "has_more": has_more}
        if has_more:
            response_body["response_metadata"] = {"next_cursor": f"batch_{batch_num+1}"}
        
        stub = {
            "request": {
                "method": "GET",
                "urlPath": "/api/conversations.history",
                "queryParameters": {"channel": {"equalTo": channel["id"]}}
            },
            "response": {"status": 200, "headers": {"Content-Type": "application/json"}, "jsonBody": response_body}
        }
        with open(sslack_dir / f"conversations-history-{channel['name']}.json", "w") as f:
            json.dump(stub, f, indent=2)

print(f"✓ Generated Slack stubs: {len(list(sslack_dir.glob('*.json')))} files")

# ============================================================================
# Jira Stubs
# ============================================================================

# 1. myself (current user)
myself_stub = {
    "request": {"method": "GET", "urlPath": "/rest/api/3/myself"},
    "response": {"status": 200, "headers": {"Content-Type": "application/json"},
        "jsonBody": {"accountId": "sysadmin", "displayName": "System Admin", "emailAddress": "admin@techtest.io"}}
}
with open(jira_dir / "myself.json", "w") as f:
    json.dump(myself_stub, f, indent=2)

# 2. project list
project_stub = {
    "request": {"method": "GET", "urlPath": "/rest/api/3/project"},
    "response": {"status": 200, "headers": {"Content-Type": "application/json"},
        "jsonBody": [{"id": p["id"], "key": p["key"], "name": p["name"]} for p in jira_projects]}
}
with open(jira_dir / "project.json", "w") as f:
    json.dump(project_stub, f, indent=2)

# 3. search (JQL) - return all issues
jql_response = {
    "startAt": 0,
    "maxResults": 100,
    "total": len(jira_issues),
    "issues": jira_issues
}
search_stub = {
    "request": {"method": "POST", "urlPath": "/rest/api/3/search"},
    "response": {"status": 200, "headers": {"Content-Type": "application/json"}, "jsonBody": jql_response}
}
with open(jira_dir / "search.json", "w") as f:
    json.dump(search_stub, f, indent=2)

# 4. Get issue by key
for issue in jira_issues:
    issue_stub = {
        "request": {"method": "GET", "urlPath": f"/rest/api/3/issue/{issue['key']}"},
        "response": {"status": 200, "headers": {"Content-Type": "application/json"}, "jsonBody": issue}
    }
    # Don't create individual files - too many. Use a single search endpoint instead.

print(f"✓ Generated Jira stubs: {len(list(jira_dir.glob('*.json')))} files")

# ============================================================================
# Summary
# ============================================================================
print(f"\nWireMock stubs generated successfully!")
print(f"  Slack: {sslack_dir}")
print(f"  Jira:  {jira_dir}")
