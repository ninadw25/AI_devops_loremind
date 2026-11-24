#!/usr/bin/env python3
"""
Generate mock Slack + Jira data for pipeline testing.
Outputs: seed_data.json (single source of truth)
"""
import json
import random
from datetime import datetime, timedelta
from pathlib import Path

random.seed(42)

CONFIG = {
    "org": "techtest",
    "num_users": 10,
}

FIRST_NAMES = ["Alice", "Bob", "Carol", "David", "Emma", "Frank", "Grace", "Henry", "Ivy", "Jack"]
LAST_NAMES = ["Chen", "Smith", "Johnson", "Williams", "Brown", "Davis", "Miller", "Wilson", "Moore", "Taylor"]
TEAMS = ["Engineering", "Product", "Sales", "Support", "Ops"]

SLACK_MESSAGE_TEMPLATES = [
    "Hey team, just pushed a fix for {jira_key}. Can someone review?",
    "Anyone seen this error before? {jira_key} is blocked on this.",
    "Quick update: {jira_key} is now in progress.",
    "Decision: We're going with option A for {jira_key}.",
    "PR ready for review: {jira_key}",
]

JIRA_SUMMARY_TEMPLATES = [
    "Fix {component} crash on {action}",
    "Implement {feature} for {module}",
]

def make_slack_id():
    return "U" + "".join(random.choices("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789", k=8))

def make_jira_account_id():
    return "".join(random.choices("abcdef0123456789", k=24))

def random_date(start_days_ago=30):
    days_ago = random.randint(0, start_days_ago)
    return datetime.utcnow() - timedelta(days=days_ago, hours=random.randint(0, 23))

def generate_users():
    users = []
    for i in range(CONFIG["num_users"]):
        first = FIRST_NAMES[i % len(FIRST_NAMES)]
        last = LAST_NAMES[i % len(LAST_NAMES)]
        email = f"{first.lower()}.{last.lower()}@techtest.io"
        users.append({
            "name": f"{first} {last}",
            "email": email,
            "slack_id": make_slack_id(),
            "jira_account_id": make_jira_account_id(),
            "slack_profile": {"display_name": first.lower(), "real_name": f"{first} {last}", "email": email},
            "jira_profile": {"displayName": f"{first} {last}", "emailAddress": email},
            "team": random.choice(TEAMS),
        })
        users[-1]["jira_profile"]["accountId"] = users[-1]["jira_account_id"]
    return users

def generate_slack_channels():
    return [
        {"id": "C12345678", "name": "engineering", "is_channel": True, "is_private": False},
        {"id": "C23456789", "name": "general", "is_channel": True, "is_private": False},
        {"id": "C34567890", "name": "product", "is_channel": True, "is_private": False},
        {"id": "G12345678", "name": "leadership", "is_channel": False, "is_private": True},
    ]

def generate_jira_projects():
    return [
        {"id": "10000", "key": "ENG", "name": "Engineering"},
        {"id": "10001", "key": "SUP", "name": "Support"},
    ]

def generate_jira_issues(users, projects):
    issues = []
    for project in projects:
        for i in range(10):
            key = f"{project['key']}-{i+1}"
            reporter = random.choice(users)
            assignee = random.choice(users) if random.random() > 0.3 else None
            created = random_date(28)
            issue = {
                "id": f"{10000 + i}",
                "key": key,
                "fields": {
                    "summary": f"Task {key}: {random.choice(['Feature', 'Bug', 'Improvement'])}",
                    "description": f"Description for issue {key}",
                    "status": {"name": random.choice(["To Do", "In Progress", "Done"]), "id": "1"},
                    "priority": {"name": random.choice(["High", "Medium", "Low"]), "id": "2"},
                    "issuetype": {"name": random.choice(["Bug", "Task", "Story"]), "id": "10001"},
                    "assignee": {"accountId": assignee["jira_account_id"], "displayName": assignee["name"]} if assignee else None,
                    "reporter": {"accountId": reporter["jira_account_id"], "displayName": reporter["name"]},
                    "created": created.strftime("%Y-%m-%dT%H:%M:%S.000+0000"),
                    "project": {"id": project["id"], "key": project["key"], "name": project["name"]},
                    "comment": {"comments": [], "total": 0},
                },
            }
            issues.append(issue)
    return issues

def generate_slack_messages(users, channels, jira_issues):
    messages = []
    jira_keys = [issue["key"] for issue in jira_issues]
    for channel in channels[:3]:
        for i in range(15):
            author = random.choice(users)
            msg_time = random_date(6)
            ts = f"{int(msg_time.timestamp())}.{random.randint(100000, 999999)}"
            jira_key = random.choice(jira_keys)
            text = random.choice(SLACK_MESSAGE_TEMPLATES).format(jira_key=jira_key)
            messages.append({
                "ts": ts,
                "user": author["slack_id"],
                "text": text,
                "thread_ts": ts,
                "channel_id": channel["id"],
            })
    return messages

def main():
    output_path = Path(__file__).parent / "seed_data.json"
    print("Generating Mock Data...")
    
    users = generate_users()
    channels = generate_slack_channels()
    projects = generate_jira_projects()
    issues = generate_jira_issues(users, projects)
    messages = generate_slack_messages(users, channels, issues)
    
    data = {
        "meta": {"generated_at": datetime.utcnow().isoformat(), "config": CONFIG},
        "users": users,
        "slack": {"team_id": "T12345678", "team_name": "TechTest", "channels": channels, "messages": messages},
        "jira": {"base_url": "http://localhost:8091", "projects": projects, "issues": issues},
    }
    
    with open(output_path, "w") as f:
        json.dump(data, f, indent=2)
    
    print(f"✓ Generated: {output_path}")
    print(f"  - {len(users)} users, {len(channels)} channels, {len(messages)} messages, {len(issues)} issues")

if __name__ == "__main__":
    main()
