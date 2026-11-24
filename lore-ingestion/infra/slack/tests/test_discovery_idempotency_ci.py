import json
from pathlib import Path

from infra.slack.discovery import discover_scopes


class FakeSlackFetcher:
    def __init__(self):
        self.team_id = "T_FAKE"
        self.team_name = "fake-workspace"
        self._users = {
            "U1": {
                "id": "U1",
                "team_id": "T_FAKE",
                "profile": {"email": "alice@example.com", "display_name": "Alice"},
            },
            "U2": {
                "id": "U2",
                "team_id": "T_FAKE",
                "is_bot": True,
                "profile": {"email": "bot@example.com", "display_name": "Bot"},
            },
            "U3": {
                "id": "U3",
                "team_id": "T_OTHER",
                "profile": {"email": "external#EXT#@example.com", "display_name": "External"},
            },
            "U4": {
                "id": "U4",
                "team_id": "T_FAKE",
                "profile": {"email": "bravo@example.com", "display_name": "Bravo"},
            },
        }
        # Intentionally out-of-order and with DM/MPIM to verify filtering and deterministic ordering.
        self._channels = [
            {"id": "D001", "name": "direct-alice", "is_private": True, "is_im": True, "is_mpim": False},
            {"id": "C300", "name": "ops-private", "is_private": True, "is_im": False, "is_mpim": False},
            {"id": "C100", "name": "general", "is_private": False, "is_im": False, "is_mpim": False},
            {"id": "G001", "name": "group-dm", "is_private": True, "is_im": False, "is_mpim": True},
            {"id": "C200", "name": "announcements", "is_private": False, "is_im": False, "is_mpim": False},
        ]

    def validate_auth(self, required_scopes=None):
        return {"ok": True}

    def load_user_cache(self):
        return dict(self._users)

    def get_user(self, user_id: str):
        return self._users.get(user_id)

    def _get(self, endpoint: str, params: dict):
        if endpoint == "conversations.list":
            return {
                "channels": self._channels,
                "response_metadata": {"next_cursor": ""},
            }
        if endpoint == "conversations.members":
            channel = params.get("channel")
            if channel == "C300":
                # Contains duplicate U1 intentionally to validate dedup.
                return {
                    "members": ["U1", "U1", "U2", "U3", "U4"],
                    "response_metadata": {"next_cursor": ""},
                }
            return {"members": [], "response_metadata": {"next_cursor": ""}}
        raise AssertionError(f"unexpected endpoint: {endpoint}")


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_discovery_idempotency_and_uniqueness(tmp_path: Path):
    org = "ciorg"
    out1 = tmp_path / "run1"
    out2 = tmp_path / "run2"

    discover_scopes(fetcher=FakeSlackFetcher(), org=org, output_dir=out1)
    discover_scopes(fetcher=FakeSlackFetcher(), org=org, output_dir=out2)

    json1 = out1 / "slack" / f"{org}_scope_principals.json"
    json2 = out2 / "slack" / f"{org}_scope_principals.json"
    cypher1 = out1 / "cypher" / f"{org}_slack_acl.cypher"
    cypher2 = out2 / "cypher" / f"{org}_slack_acl.cypher"

    assert json1.exists() and json2.exists()
    assert cypher1.exists() and cypher2.exists()

    payload1 = _read_json(json1)
    payload2 = _read_json(json2)

    # Idempotent JSON and deterministic cypher across reruns.
    assert payload1 == payload2
    assert cypher1.read_text(encoding="utf-8") == cypher2.read_text(encoding="utf-8")

    scopes = payload1["scopes"]
    principals = payload1["principals"]
    scope_principals = payload1["scope_principals"]

    # No duplicate entities.
    assert len({item["scope_id"] for item in scopes}) == len(scopes)
    assert len({item["principal_id"] for item in principals}) == len(principals)
    assert len({(item["scope_id"], item["principal_id"]) for item in scope_principals}) == len(scope_principals)

    # Exactly one workspace scope and channel scope format checks.
    workspace_scopes = [item for item in scopes if item["kind"] == "workspace"]
    assert len(workspace_scopes) == 1
    assert workspace_scopes[0]["scope_id"] == "slack:workspace:T_FAKE"
    channel_scopes = [item for item in scopes if item["kind"] == "channel"]
    assert all(item["scope_id"].startswith("slack:channel:C") for item in channel_scopes)

    # Public channels must not emit scope_principals.
    public_scope_ids = {
        item["scope_id"]
        for item in channel_scopes
        if item["is_public"]
    }
    assert all(item["scope_id"] not in public_scope_ids for item in scope_principals)

    # Cypher determinism/idempotency characteristics.
    cypher_text = cypher1.read_text(encoding="utf-8")
    assert "MERGE (s:AclScope" in cypher_text
    assert "slack:workspace:T_FAKE" in cypher_text
    assert "slack:channel:C100" in cypher_text
    assert "slack:channel:C200" in cypher_text
    assert "slack:channel:C300" in cypher_text
