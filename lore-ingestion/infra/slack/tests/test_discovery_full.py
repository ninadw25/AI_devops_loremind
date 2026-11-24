import json
from pathlib import Path

from infra.slack.discovery import discover_scopes


class FakeSlackFetcher:
    def __init__(self):
        self.team_id = "T_FAKE"
        self.team_name = "fake-workspace"
        # user cache: U1 valid, U2 bot, U3 external
        self._users = {
            "U1": {"id": "U1", "team_id": "T_FAKE", "profile": {"email": "alice@example.com", "display_name": "Alice"}},
            "U2": {"id": "U2", "team_id": "T_FAKE", "is_bot": True, "profile": {"email": "bot@example.com", "display_name": "Bot"}},
            "U3": {"id": "U3", "team_id": "T_OTHER", "profile": {"email": "charlie#EXT#@example.com", "display_name": "Charlie EXT"}},
        }
        # channels: public C111, private C222
        self._channels = [
            {"id": "C111", "name": "engineering", "is_private": False},
            {"id": "C222", "name": "private-team", "is_private": True},
        ]

    def validate_auth(self, required_scopes=None):
        # pretend auth is fine
        return True

    def load_user_cache(self):
        return dict(self._users)

    def _get(self, endpoint: str, params: dict):
        # emulate conversations.list and conversations.members
        if endpoint == "conversations.list":
            return {"channels": self._channels, "response_metadata": {"next_cursor": ""}}
        if endpoint == "conversations.members":
            channel = params.get("channel")
            if channel == "C222":
                return {"members": ["U1", "U2", "U3"], "response_metadata": {"next_cursor": ""}}
            return {"members": [], "response_metadata": {"next_cursor": ""}}
        raise RuntimeError(f"unexpected endpoint {endpoint}")

    def get_user(self, user_id: str):
        return self._users.get(user_id)


def test_discovery_writes_artifacts(tmp_path: Path):
    fetcher = FakeSlackFetcher()
    org = "testorg"
    out = tmp_path / "out"
    artifact = discover_scopes(fetcher=fetcher, org=org, output_dir=out, include_archived=False)

    # JSON artifact exists and shape
    json_path = out / "slack" / f"{org}_scope_principals.json"
    assert json_path.exists()
    data = json.loads(json_path.read_text(encoding="utf-8"))
    assert set(data.keys()) == {"scopes", "principals", "scope_principals"}

    # Workspace scope present
    scopes = data["scopes"]
    assert any(s["kind"] == "workspace" for s in scopes)

    # Public channel should produce no scope_principals rows
    sp = data["scope_principals"]
    assert all(item["scope_id"] != "slack:channel:C111" for item in sp)

    # Private channel should produce scope_principals for only non-bot/non-external (U1)
    assert any(item["principal_id"] == "slack:U1" and item["scope_id"] == "slack:channel:C222" for item in sp)

    # Cypher file exists and contains MERGE for workspace and channel
    cypher_path = out / "cypher" / f"{org}_slack_acl.cypher"
    assert cypher_path.exists()
    cypher_text = cypher_path.read_text(encoding="utf-8")
    assert "MERGE (s:AclScope {scope_id:'slack:workspace:T_FAKE'})" in cypher_text
    assert "MERGE (s:AclScope {scope_id:'slack:channel:C111'})" in cypher_text


def test_discovery_idempotent(tmp_path: Path):
    fetcher = FakeSlackFetcher()
    org = "testorg"
    out = tmp_path / "out"
    a1 = discover_scopes(fetcher=fetcher, org=org, output_dir=out)
    a2 = discover_scopes(fetcher=fetcher, org=org, output_dir=out)

    p1 = out / "slack" / f"{org}_scope_principals.json"
    p2 = out / "cypher" / f"{org}_slack_acl.cypher"
    assert p1.exists() and p2.exists()
    assert p1.read_text(encoding="utf-8") == p1.read_text(encoding="utf-8")
