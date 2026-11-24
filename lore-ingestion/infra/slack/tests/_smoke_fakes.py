from __future__ import annotations

from datetime import datetime


class FakeSlackFetcher:
    def __init__(self) -> None:
        self.team_id = "T123"
        self.team_name = "Smoke Team"
        self.bot_allowlist: set[str] = set()
        self._channels = [
            {"id": "C100", "name": "public-alpha", "is_private": False, "is_im": False, "is_mpim": False},
            {"id": "C200", "name": "finance-private", "is_private": True, "is_im": False, "is_mpim": False},
            {"id": "D300", "name": "dm-user", "is_private": False, "is_im": True, "is_mpim": False},
        ]
        self._members = {
            "C200": ["U001", "U002", "U003"],
        }
        self._user_cache = {
            "U001": {
                "id": "U001",
                "team_id": "T123",
                "is_bot": False,
                "deleted": False,
                "profile": {"email": "alice@ezobooks.in", "display_name": "Alice"},
            },
            "U002": {
                "id": "U002",
                "team_id": "T123",
                "is_bot": False,
                "deleted": False,
                "profile": {"email": "bob@ezobooks.in", "display_name": "Bob"},
            },
            # External Slack Connect guest that should be filtered from discovery principals.
            "U003": {
                "id": "U003",
                "team_id": "T999",
                "is_bot": False,
                "deleted": False,
                "profile": {"email": "guest#EXT#@ext.example.com", "display_name": "Guest #EXT#"},
            },
        }

    def validate_auth(self, required_scopes=None):
        return {"ok": True}

    def load_user_cache(self):
        return self._user_cache

    @property
    def user_cache(self):
        return self._user_cache

    def get_user(self, user_id: str):
        return self._user_cache.get(user_id)

    def _get(self, endpoint: str, params: dict | None = None):
        params = params or {}
        if endpoint == "conversations.list":
            return {
                "ok": True,
                "channels": self._channels,
                "response_metadata": {"next_cursor": ""},
            }
        if endpoint == "conversations.members":
            return {
                "ok": True,
                "members": self._members.get(params.get("channel", ""), []),
                "response_metadata": {"next_cursor": ""},
            }
        raise AssertionError(f"Unexpected endpoint: {endpoint}")

    def iter_channel_events(self, channel: dict, since: datetime | None = None):
        cid = channel["id"]
        if cid == "C100":
            yield (
                {
                    "ts": "1717000000.000100",
                    "thread_ts": "1717000000.000100",
                    "text": "Decision made to ship Sprint 14 this week.",
                    "user": "U001",
                },
                [],
            )
            yield (
                {
                    "ts": "1717000001.000200",
                    "thread_ts": "1717000001.000200",
                    "text": "Task assigned: prepare rollout checklist by EOD.",
                    "user": "U002",
                },
                [],
            )
        elif cid == "C200":
            yield (
                {
                    "ts": "1717000002.000300",
                    "thread_ts": "1717000002.000300",
                    "text": "Finance update: VAT filing completed and submitted.",
                    "user": "U001",
                },
                [],
            )
            yield (
                {
                    "ts": "1717000003.000400",
                    "thread_ts": "1717000003.000400",
                    "text": "Budget note: approve cloud spend cap for next month.",
                    "user": "U002",
                },
                [],
            )


def demo_channels() -> list[dict]:
    return [
        {"id": "C100", "name": "public-alpha", "is_private": False, "is_im": False, "is_mpim": False},
        {"id": "C200", "name": "finance-private", "is_private": True, "is_im": False, "is_mpim": False},
    ]
