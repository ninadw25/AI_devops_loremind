#!/usr/bin/env python3
"""
AI-Powered Slack Test Data Generator
Uses AWS Bedrock to generate realistic messages and posts via Slack Web API.

Usage:
    python generate_slack_test_data.py \
        --slack-token xoxb-your-token \
        --bedrock-model anthropic.claude-3-sonnet-20240229-v1:0 \
        --num-messages 200
"""

import boto3
import json
import random
import time
from datetime import datetime, timedelta
from typing import List, Dict, Any
import requests
import argparse

# Initialize Bedrock client
bedrock = boto3.client('bedrock-runtime', region_name='us-east-1')

class SlackTestGenerator:
    def __init__(self, slack_token: str, bedrock_model: str):
        self.slack_token = slack_token
        self.bedrock_model = bedrock_model
        self.headers = {'Authorization': f'Bearer {slack_token}', 'Content-Type': 'application/json'}
        
        # Will be populated during setup
        self.channels = {}
        self.users = []
        
    def generate_content_with_bedrock(self, prompt: str) -> str:
        """Generate content using AWS Bedrock."""
        body = json.dumps({
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 1000,
            "messages": [{"role": "user", "content": prompt}]
        })
        
        response = bedrock.invoke_model(
            modelId=self.bedrock_model,
            body=body
        )
        
        response_body = json.loads(response['body'].read())
        print(json.dumps(response_body, indent=2))

        choices = response_body.get('choices') if isinstance(response_body, dict) else None
        if isinstance(choices, list) and choices:
            first_choice = choices[0]
            if isinstance(first_choice, dict):
                message = first_choice.get('message')
                if isinstance(message, dict):
                    content = message.get('content')
                    if isinstance(content, str) and content.strip():
                        return content

                text = first_choice.get('text')
                if isinstance(text, str) and text.strip():
                    return text

        raise ValueError(
            "Unexpected Bedrock response format for DeepSeek model; expected "
            "choices[0].message.content or choices[0].text. Full response: "
            f"{json.dumps(response_body, indent=2)}"
        )
    
    def create_channels(self) -> Dict[str, str]:
        """Create test channels."""
        channels = {}
        
        channel_names = ['test-general', 'test-engineering', 'test-random', 'test-standup']
        
        for name in channel_names:
            url = "https://slack.com/api/conversations.create"
            payload = {"name": name, "is_private": False}
            
            response = requests.post(url, headers=self.headers, json=payload)
            if response.status_code == 200:
                data = response.json()
                if data.get('ok'):
                    channel_id = data['channel']['id']
                    channels[name] = channel_id
                    print(f"Created channel: #{name} ({channel_id})")
                    time.sleep(1)  # Rate limit
        
        return channels
    
    def get_users(self) -> List[Dict]:
        """Get list of users in workspace."""
        url = "https://slack.com/api/users.list"
        response = requests.get(url, headers=self.headers)
        
        if response.status_code == 200:
            data = response.json()
            if data.get('ok'):
                # Filter out bots and deleted users
                return [u for u in data['members'] if not u.get('is_bot') and not u.get('deleted')]
        
        return []
    
    def post_message(self, channel_id: str, text: str, thread_ts: str = None) -> Dict:
        """Post a message to a channel."""
        url = "https://slack.com/api/chat.postMessage"
        payload = {"channel": channel_id, "text": text, "parse": "full"}
        
        if thread_ts:
            payload["thread_ts"] = thread_ts
        
        response = requests.post(url, headers=self.headers, json=payload)
        
        if response.status_code == 200:
            return response.json()
        else:
            print(f"Error posting message: {response.status_code}")
            return None
    
    def edit_message(self, channel_id: str, ts: str, new_text: str) -> bool:
        """Edit an existing message."""
        url = "https://slack.com/api/chat.update"
        payload = {"channel": channel_id, "ts": ts, "text": new_text}
        
        response = requests.post(url, headers=self.headers, json=payload)
        return response.status_code == 200 and response.json().get('ok')
    
    def delete_message(self, channel_id: str, ts: str) -> bool:
        """Delete a message."""
        url = "https://slack.com/api/chat.delete"
        payload = {"channel": channel_id, "ts": ts}
        
        response = requests.post(url, headers=self.headers, json=payload)
        return response.status_code == 200 and response.json().get('ok')
    
    def add_reaction(self, channel_id: str, ts: str, reaction: str) -> bool:
        """Add reaction to a message."""
        url = "https://slack.com/api/reactions.add"
        payload = {"channel": channel_id, "timestamp": ts, "name": reaction}
        
        response = requests.post(url, headers=self.headers, json=payload)
        return response.status_code == 200
    
    def generate_test_messages(self, num_messages: int = 200):
        """Generate comprehensive test dataset."""
        
        print("Setting up channels...")
        self.channels = self.create_channels()
        
        if not self.channels:
            print("No channels created. Using existing channels...")
            # Get existing channels
            url = "https://slack.com/api/conversations.list"
            response = requests.get(url, headers=self.headers)
            if response.status_code == 200:
                data = response.json()
                if data.get('ok'):
                    for ch in data['channels'][:4]:
                        self.channels[ch['name']] = ch['id']
        
        print(f"Using channels: {list(self.channels.keys())}")
        
        print("\nFetching users...")
        self.users = self.get_users()
        print(f"Found {len(self.users)} users")
        
        # Track created messages for edit/delete testing
        messages_created = []
        edit_candidates = []
        delete_candidates = []

        # Ground-truth manifest: every created/edited/deleted message with its
        # id + text, so the ingestion pipeline can be validated deterministically
        # (which messages SHOULD show is_edited, which SHOULD be absent, etc.).
        created_records = []   # {channel_id, ts, text, kind}
        applied_edits = []     # {channel_id, ts, original_text, final_text, edited_twice}
        applied_deletes = []   # {channel_id, ts}
        
        # Topics for message generation
        topics = [
            "deployment update",
            "code review feedback",
            "meeting notes",
            "bug report",
            "feature discussion",
            "team announcement",
            "customer feedback",
            "sprint planning",
            "release notes",
            "technical question",
            "status update",
            "documentation request",
            "architecture proposal",
            "performance metrics",
            "security concerns"
        ]
        
        print(f"\n=== Generating {num_messages} messages ===")
        
        # Phase 1: Regular messages (60%)
        num_regular = int(num_messages * 0.6)
        print(f"\nPhase 1: Creating {num_regular} regular messages...")
        
        prompt_template = """Generate a realistic Slack message (1-3 sentences) for a software team.
Topic: {topic}
Tone: casual, professional

Return ONLY the message text, no quotes or formatting."""
        
        for i in range(num_regular):
            topic = random.choice(topics)
            channel_name = random.choice(list(self.channels.keys()))
            channel_id = self.channels[channel_name]
            
            try:
                text = self.generate_content_with_bedrock(prompt_template.format(topic=topic))
                
                # Occasionally add special formatting
                if random.random() > 0.7:
                    special_formats = [
                        f"```{text}```",  # Code block
                        f"*{text}*",  # Bold
                        f"_{text}_",  # Italic
                        f"> {text}",  # Quote
                        f":white_check_mark: {text}",  # Emoji
                    ]
                    text = random.choice(special_formats)
                
                result = self.post_message(channel_id, text)
                
                if result and result.get('ok'):
                    msg_ts = result['ts']
                    messages_created.append((channel_id, msg_ts))
                    created_records.append({
                        "channel_id": channel_id, "ts": msg_ts,
                        "text": text, "kind": "regular",
                    })

                    # 20% chance to add reactions
                    if random.random() > 0.8:
                        reactions = ['thumbsup', 'thumbsdown', 'white_check_mark', 'eyes', 'rocket', 'tada']
                        self.add_reaction(channel_id, msg_ts, random.choice(reactions))
                    
                    # Track for edit testing
                    if len(edit_candidates) < 15 and random.random() > 0.85:
                        edit_candidates.append((channel_id, msg_ts, text))
                    
                    # Track for delete testing
                    if len(delete_candidates) < 10 and random.random() > 0.95:
                        delete_candidates.append((channel_id, msg_ts))
                    
                    print(f"[{i+1}/{num_regular}] Posted to #{channel_name}")
                    
                    time.sleep(0.5)  # Rate limit
                    
            except Exception as e:
                print(f"Error: {e}")
                continue
        
        # Phase 2: Thread messages (20%)
        num_threads = int(num_messages * 0.2)
        print(f"\nPhase 2: Creating {num_threads} thread replies...")
        
        # Select random parent messages for threads
        parent_messages = random.sample(messages_created, min(10, len(messages_created)))
        
        for i, (channel_id, parent_ts) in enumerate(parent_messages):
            num_replies = random.randint(3, 8)
            
            for j in range(num_replies):
                try:
                    topic = random.choice(topics)
                    text = self.generate_content_with_bedrock(prompt_template.format(topic=topic))
                    
                    result = self.post_message(channel_id, text, thread_ts=parent_ts)

                    if result and result.get('ok'):
                        created_records.append({
                            "channel_id": channel_id, "ts": result['ts'],
                            "text": text, "kind": "thread_reply",
                            "thread_ts": parent_ts,
                        })
                        print(f"[{i+1}/{len(parent_messages)}] Thread reply {j+1}/{num_replies}")

                        time.sleep(0.3)
                        
                except Exception as e:
                    print(f"Error in thread: {e}")
                    continue
        
        # Phase 3: Edit messages (10% - 15 messages)
        print(f"\nPhase 3: Editing {len(edit_candidates)} messages...")
        
        for i, (channel_id, msg_ts, original_text) in enumerate(edit_candidates):
            try:
                edit_prompt = """Generate an edited version of this Slack message.
Original: {original}

Return ONLY the edited text, make it noticeably different."""

                new_text = self.generate_content_with_bedrock(edit_prompt.format(original=original_text))

                success = self.edit_message(channel_id, msg_ts, new_text)

                if success:
                    print(f"[{i+1}/{len(edit_candidates)}] Edited message")

                # Some get edited twice
                final_text = new_text
                edited_twice = False
                if i % 2 == 0:
                    time.sleep(0.5)
                    final_text = f"[UPDATED] {new_text}"
                    self.edit_message(channel_id, msg_ts, final_text)
                    edited_twice = True
                    print(f"  -> Edited again")

                if success:
                    applied_edits.append({
                        "channel_id": channel_id, "ts": msg_ts,
                        "original_text": original_text,
                        "final_text": final_text,
                        "edited_twice": edited_twice,
                    })

                time.sleep(0.5)
                
            except Exception as e:
                print(f"Error editing: {e}")
                continue
        
        # Phase 4: Delete messages (10% - 10 messages)
        print(f"\nPhase 4: Deleting {len(delete_candidates)} messages...")
        
        for i, (channel_id, msg_ts) in enumerate(delete_candidates[:10]):
            try:
                success = self.delete_message(channel_id, msg_ts)

                if success:
                    applied_deletes.append({"channel_id": channel_id, "ts": msg_ts})
                    print(f"[{i+1}/10] Deleted message")

                time.sleep(0.5)

            except Exception as e:
                print(f"Error deleting: {e}")
                continue

        # Ground-truth manifest. `deleted` ids should be ABSENT from a fetch;
        # `edited` ids should show is_edited=true with body == final_text; every
        # non-deleted `created` id should be present.
        deleted_ts = {d["ts"] for d in applied_deletes}
        manifest = {
            "workspace": getattr(self, "workspace_label", ""),
            "team": self.channels and list(self.channels.keys()),
            "generated_at": datetime.utcnow().isoformat() + "Z",
            "counts": {
                "created": len(created_records),
                "edited": len(applied_edits),
                "deleted": len(applied_deletes),
                "expected_present": len(created_records) - len(deleted_ts),
            },
            "created": created_records,
            "edited": applied_edits,
            "deleted": applied_deletes,
            "channels": dict(self.channels),
        }
        manifest_out = getattr(self, "manifest_out", "slack_test_manifest.json")
        with open(manifest_out, "w", encoding="utf-8") as fh:
            json.dump(manifest, fh, indent=2, ensure_ascii=False)

        # Summary
        print(f"\n{'='*60}")
        print(f"SUMMARY:")
        print(f"  Total messages created: {len(created_records)}")
        print(f"  Messages edited: {len(applied_edits)}")
        print(f"  Messages deleted: {len(applied_deletes)}")
        print(f"  Channels used: {list(self.channels.keys())}")
        print(f"  Ground-truth manifest: {manifest_out}")
        print(f"\nTest data generation complete!")

def main():
    parser = argparse.ArgumentParser(description="Generate Slack test data with AI")
    parser.add_argument('--slack-token', required=True, help='Slack bot token')
    parser.add_argument('--bedrock-model', default='deepseek.deepseek-v3-2')
    parser.add_argument('--num-messages', type=int, default=200)
    parser.add_argument('--manifest-out', default='slack_test_manifest.json',
                        help='Where to write the ground-truth manifest JSON.')
    parser.add_argument('--workspace', default='',
                        help='Workspace label recorded in the manifest.')

    args = parser.parse_args()

    generator = SlackTestGenerator(
        slack_token=args.slack_token,
        bedrock_model=args.bedrock_model
    )
    generator.manifest_out = args.manifest_out
    generator.workspace_label = args.workspace

    generator.generate_test_messages(args.num_messages)

if __name__ == '__main__':
    main()
