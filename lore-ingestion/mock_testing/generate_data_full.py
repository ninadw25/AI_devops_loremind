#!/usr/bin/env python3
"""
Generate mock Slack + Jira data for pipeline testing (extended variant:
adds Jira comments and Slack<->Jira cross-references on top of generate_data.py).
Outputs: seed_data.json (single source of truth)

Recovered from an accidental nested lore-ingestion/lore-ingestion/ copy on 2026-06-11.
TODO: consolidate with generate_data.py — keep one generator.
"""
import json
import random
import string
from datetime import datetime, timedelta
from pathlib import Path

random.seed(42)  # Reproducible

# ============================================================================
# CONFIGURATION
# ============================================================================

CONFIG = {
    "org": "techtest",
    "num_users": 10,
    "num_slack_channels": 5,
    "num_slack_messages": 50,
    "num_jira_projects": 2,
    "num_jira_issues": 20,
    "num_jira_comments": 30,
    "num_cross_refs": 10,
}

# Names for realistic personas
FIRST_NAMES = ["Alice", "Bob", "Carol", "David", "Emma", "Frank", "Grace", "Henry", "Ivy", "Jack"]
LAST_NAMES = ["Chen", "Smith", "Johnson", "Williams", "Brown", "Davis", "Miller", "Wilson", "Moore", "Taylor"]
TEAMS = ["Engineering", "Product", "Sales", "Support", "Ops"]

# Message templates (no LLM needed)
SLACK_MESSAGE_TEMPLATES = [
    "Hey team, just pushed a fix for {jira_key}. Can someone review?",
    "Anyone seen this error before? {jira_key} is blocked on this.",
    "Quick update: {jira_key} is now in progress.",
    "Decision: We're going with option A for {jira_key}.",
    "PR ready for review: {jira_key}",
    "Deployed {jira_key} to staging. Looks good!",
    "Blocked on {jira_key} - need infra support.",
    "@channel Production issue - related to {jira_key}",
    "Standup: Finished {jira_key}, moving to {jira_key2}.",
    "Good catch on {jira_key}. Fixed in latest commit.",
    "Can someone explain the root cause of {jira_key}?",
    "Sprint planning: {jira_key} is a priority.",
    "FYI - {jira_key} is now closed.",
    "Adding {jira_key} to the sprint backlog.",
    "Thanks for the quick turnaround on {jira_key}!",
]

SLACK_THREAD_TEMPLATES = [
    "Sure, I can help with that.",
    "Looking into it now.",
    "What's the timeline?",
    "Done. Merged the PR.",
    "Can you share more context?",
    "+1",
    "Sounds good, let's do it.",
    "On it, will update shortly.",
]

# Jira templates
JIRA_SUMMARY_TEMPLATES = [
    "Fix {component} crash on {action}",
    "Implement {feature} for {module}",
    "Investigate {error} in production",
    "Add {feature} to user dashboard",
    "Refactor {module} for better performance",
]

JIRA_DESCRIPTION_TEMPLATE = """h2. Background

{background}

h2. Acceptance Criteria
* {criteria1}
* {criteria2}

h2. Technical Notes
{notes}"""

# ============================================================================
# HELPERS
# ============================================================================

def make_slack_id():
    return "U" + "".join(random.choices("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789", k=8))

def make_jira_account_id():
    return "".join(random.choices("abcdef0123456789", k=24))

def make_slack_ts(base_time):
    ts = base_time.timestamp()
    return f"{int(ts)}.{random.randint(100000, 999999)}"

def make_jira_key(project_key, number):
    return f"{project_key}-{number}"

def random_date(start_days_ago=30, end_days_ago=0):
    days_ago = random.randint(end_days_ago, start_days_ago)
    return datetime.utcnow() - timedelta(days=days_ago, hours=random.randint(0, 23), minutes=random.randint(0, 59))

# ============================================================================
# GENERATORS
# ============================================================================

def generate_users():
    """Generate cross-system users."""
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
            "slack_profile": {
                "display_name": first.lower(),
                "real_name": f"{first} {last}",
                "email": email,
            },
            "jira_profile": {
                "displayName": f"{first} {last}",
                "emailAddress": email,
                "accountId": "",
            },
            "team": random.choice(TEAMS),
        })
        users[-1]["jira_profile"]["accountId"] = users[-1]["jira_account_id"]
    
    return users

def generate_slack_channels(users):
    """Generate Slack channels."""
    channels = []
    
    # Public channels
    public_channels = ["engineering", "general", "product", "random"]
    for name in public_channels:
        channels.append({
            "id": "C" + "".join(random.choices("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789", k=8)),
            "name": name,
            "is_channel": True,
            "is_group": False,
            "is_private": False,
            "is_im": False,
            "is_mpim": False,
            "is_archived": False,
            "is_shared": False,
            "team_domain": "techtest",
        })
    
    # Private channel
    channels.append({
        "id": "G" + "".join(random.choices("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789", k=8)),
        "name": "leadership",
        "is_channel": False,
        "is_group": True,
        "is_private": True,
        "is_im": False,
        "is_mpim": False,
        "is_archived": False,
        "is_shared": False,
        "team_domain": "techtest",
    })
    
    return channels

def generate_jira_projects():
    """Generate Jira projects."""
    projects = [
        {"id": "10000", "key": "ENG", "name": "Engineering", "projectTypeKey": "software"},
        {"id": "10001", "key": "SUP", "name": "Support", "projectTypeKey": "service_desk"},
    ]
    return projects

def generate_jira_issues(users, projects):
    """Generate Jira issues."""
    issues = []
    issue_num = 1
    base_time = datetime.utcnow() - timedelta(days=30)
    
    for project in projects:
        project_issues = random.randint(8, 12)
        for i in range(project_issues):
            key = make_jira_key(project["key"], issue_num)
            issue_num += 1
            
            reporter = random.choice(users)
            assignee = random.choice(users) if random.random() > 0.2 else None
            
            created = random_date(start_days_ago=28, end_days_ago=1)
            
            issue = {
                "id": str(10000 + issue_num),
                "key": key,
                "fields": {
                    "summary": f"Implement feature {i+1} for {project['name']}",
                    "description": {
                        "type": "doc",
                        "version": 1,
                        "content": [
                            {
                                "type": "paragraph",
                                "content": [{"type": "text", "text": f"Description for issue {key}. This is a sample issue for testing."}]
                            }
                        ]
                    },
                    "status": {
                        "name": random.choice(["To Do", "In Progress", "Done", "Closed"]),
                        "id": str(random.randint(1, 4)),
                    },
                    "priority": {
                        "name": random.choice(["Highest", "High", "Medium", "Low"]),
                        "id": str(random.randint(1, 4)),
                    },
                    "issuetype": {
                        "name": random.choice(["Bug", "Task", "Story"]),
                        "id": str(random.randint(10001, 10003)),
                    },
                    "assignee": {
                        "accountId": assignee["jira_account_id"],
                        "displayName": assignee["name"],
                        "emailAddress": assignee["email"],
                    } if assignee else None,
                    "reporter": {
                        "accountId": reporter["jira_account_id"],
                        "displayName": reporter["name"],
                        "emailAddress": reporter["email"],
                    },
                    "created": created.isoformat() + ".000+0000",
                    "project": {
                        "id": project["id"],
                        "key": project["key"],
                        "name": project["name"],
                    },
                    "labels": random.sample(["backend", "frontend", "priority", "urgent"], k=random.randint(0, 2)),
                    "components": [{"name": random.choice(["API", "UI", "Database", "Auth"])}] if random.random() > 0.5 else [],
                    "comment": {
                        "comments": [],
                        "maxResults": 0,
                        "total": 0,
                    },
                },
                "changelog": {
                    "histories": [],
                },
            }
            issues.append(issue)
    
    # Add comments
    for issue in issues:
        num_comments = random.randint(0, 3)
        for _ in range(num_comments):
            commenter = random.choice(users)
            comment_time = random_date(start_days_ago=14, end_days_ago=0)
            issue["fields"]["comment"]["comments"].append({
                "id": str(random.randint(10000, 99999)),
                "author": {
                    "accountId": commenter["jira_account_id"],
                    "displayName": commenter["name"],
                },
                "body": {
                    "type": "doc",
                    "version": 1,
                    "content": [
                        {"type": "paragraph", "content": [{"type": "text", "text": random.choice([
                            "Looks good to me.",
                            "Can we add more details?",
                            "This is blocking the release.",
                            "Working on it now.",
                            "Thanks for the update!",
                        ])}]}
                    ]
                },
                "created": comment_time.isoformat() + ".000+0000",
            })
        issue["fields"]["comment"]["total"] = len(issue["fields"]["comment"]["comments"])
    
    return issues

def generate_slack_messages(users, channels, jira_issues):
    """Generate Slack messages with thread support."""
    messages = []
    jira_keys = [issue["key"] for issue in jira_issues]
    base_time = datetime.utcnow() - timedelta(days=7)
    
    for channel in channels[:4]:  # Skip DM channel
        num_messages = random.randint(8, 15)
        channel_messages = []
        
        for i in range(num_messages):
            author = random.choice(users)
            msg_time = random_date(start_days_ago=6, end_days_ago=0)
            ts = make_slack_ts(msg_time)
            
            # Pick template and fill in jira keys
            template = random.choice(SLACK_MESSAGE_TEMPLATES)
            jira_key = random.choice(jira_keys) if jira_keys else "ENG-1"
            jira_key2 = random.choice(jira_keys) if len(jira_keys) > 1 else jira_key
            
            text = template.format(jira_key=jira_key, jira_key2=jira_key2)
            
            msg = {
                "ts": ts,
                "user": author["slack_id"],
                "text": text,
                "thread_ts": ts,
                "reply_count": 0,
                "latest_reply": "",
                "subtype": "",
                "attachments": [],
                "files": [],
                "reactions": [],
                "channel_id": channel["id"],
            }
            
            # Add replies (create thread)
            num_replies = random.randint(0, 4)
            if num_replies > 0:
                msg["reply_count"] = num_replies
                reply_ts = msg_time + timedelta(minutes=random.randint(1, 30))
                msg["latest_reply"] = make_slack_ts(reply_ts)
                
                for j in range(num_replies):
                    replier = random.choice([u for u in users if u["slack_id"] != author["slack_id"]])
                    reply_time = msg_time + timedelta(minutes=random.randint(1, 60))
                    reply_ts_str = make_slack_ts(reply_time)
                    
                    reply = {
                        "ts": reply_ts_str,
                        "user": replier["slack_id"],
                        "text": random.choice(SLACK_THREAD_TEMPLATES),
                        "thread_ts": ts,  # Points to parent
                        "subtype": "",
                        "channel_id": channel["id"],
                    }
                    messages.append(reply)
            
            channel_messages.append(msg)
        
        messages.extend(channel_messages)
    
    return messages

# ============================================================================
# MAIN
# ============================================================================

def main():
    output_path = Path(__file__).parent / "seed_data.json"
    
    print("=" * 60)
    print("Generating Mock Data for Slack + Jira Pipeline Testing")
    print("=" * 60)
    
    # Generate users first (shared across systems)
    print(f"\n[1/5] Generating {CONFIG['num_users']} users...")
    users = generate_users()
    
    # Generate Slack data
    print(f"[2/5] Generating {CONFIG['num_slack_channels']} Slack channels...")
    channels = generate_slack_channels(users)
    
    # Generate Jira data
    print(f"[3/5] Generating {CONFIG['num_jira_projects']} Jira projects...")
    projects = generate_jira_projects()
    
    print(f"[4/5] Generating {CONFIG['num_jira_issues']} Jira issues...")
    issues = generate_jira_issues(users, projects)
    
    # Generate Slack messages (with jira key references)
    print(f"[5/5] Generating {CONFIG['num_slack_messages']} Slack messages...")
    messages = generate_slack_messages(users, channels, issues)
    
    # Compile into single output
    data = {
        "meta": {
            "generated_at": datetime.utcnow().isoformat(),
            "config": CONFIG,
        },
        "users": users,
        "slack": {
            "team_id": "T12345678",
            "team_name": "TechTest",
            "team_domain": "techtest",
            "channels": channels,
            "messages": messages,
        },
        "jira": {
            "base_url": "http://localhost:8091",
            "projects": projects,
            "issues": issues,
        },
    }
    
    # Write output
    with open(output_path, "w") as f:
        json.dump(data, f, indent=2)
    
    print(f"\n✓ Generated seed_data.json")
    print(f"  - {len(users)} users")
    print(f"  - {len(channels)} Slack channels")
    print(f"  - {len(messages)} Slack messages")
    print(f"  - {len(projects)} Jira projects")
    print(f"  - {len(issues)} Jira issues")
    print(f"  - {sum(1 for m in messages if 'jira_key' in str(m) or 'ENG-' in m.get('text', '') or 'SUP-' in m.get('text', ''))} messages with Jira references")
    print(f"\nOutput: {output_path}")
    print("=" * 60)

if __name__ == "__main__":
    main()
