"""Mock fetcher for Slack that reads from seed_data.json instead of hitting real API."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional
from dataclasses import dataclass, field


@dataclass
class MockSlackFetcher:
    """Slack fetcher that reads from pre-generated mock data."""
    
    seed_path: Path
    org: str = "techtest"
    
    # These are populated by load_user_cache() and list_channels()
    user_cache: dict[str, dict] = field(default_factory=dict)
    team_name: str = "TechTest"
    bot_allowlist: list[str] = field(default_factory=list)
    
    _data: dict = field(default_factory=dict)
    _channels: list[dict] = field(default_factory=list)
    _messages: list[dict] = field(default_factory=list)
    
    def __post_init__(self):
        """Load seed data from file."""
        with open(self.seed_path) as f:
            self._data = json.load(f)
        
        slack_data = self._data.get("slack", {})
        self._channels = slack_data.get("channels", [])
        self._messages = slack_data.get("messages", [])
        self.team_name = slack_data.get("team_name", "TechTest")
        
        # Build user cache from seed data users
        for user in self._data.get("users", []):
            self.user_cache[user["slack_id"]] = {
                "id": user["slack_id"],
                "name": user["slack_profile"]["display_name"],
                "real_name": user["name"],
                "profile": {
                    "email": user["email"],
                    "display_name": user["slack_profile"]["display_name"],
                },
            }
    
    def validate_auth(self, required_scopes: list[str] | None = None) -> None:
        """No-op for mock mode."""
        pass
    
    def load_user_cache(self) -> None:
        """Users already loaded in __post_init__."""
        pass
    
    def list_channels(
        self,
        types: str = "public_channel,private_channel",
        exclude_archived: bool = True,
        only_channels: list[str] | None = None,
    ) -> list[dict]:
        """Return channels from seed data."""
        channels = self._channels
        
        if exclude_archived:
            channels = [c for c in channels if not c.get("is_archived", False)]
        
        if only_channels:
            # Filter by channel ID or name
            channels = [
                c for c in channels
                if c.get("id") in only_channels or c.get("name") in only_channels
            ]
        
        return channels
    
    def iter_channel_events(
        self,
        channel: dict,
        since: Optional[datetime] = None,
    ) -> Iterator[tuple[dict, list[dict]]]:
        """Yield (parent_msg, replies) tuples for a channel."""
        channel_id = channel["id"]
        channel_msgs = [m for m in self._messages if m.get("channel_id") == channel_id]
        
        # Group by thread_ts
        threads: dict[str, list[dict]] = {}
        for msg in channel_msgs:
            thread_ts = msg.get("thread_ts", msg["ts"])
            if thread_ts not in threads:
                threads[thread_ts] = []
            threads[thread_ts].append(msg)
        
        for thread_ts, msgs in threads.items():
            # First message is parent, rest are replies
            msgs_sorted = sorted(msgs, key=lambda m: float(m["ts"]))
            parent = msgs_sorted[0]
            replies = msgs_sorted[1:] if len(msgs_sorted) > 1 else []
            yield parent, replies


def create_mock_fetcher(seed_path: Path, org: str) -> MockSlackFetcher:
    """Factory function to create a mock fetcher."""
    return MockSlackFetcher(seed_path=seed_path, org=org)
