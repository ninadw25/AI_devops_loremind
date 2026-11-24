from __future__ import annotations

import json
import tempfile
from pathlib import Path
import unittest

from infra.slack.discovery import discover_scopes


class FakeSlackFetcher:
    def __init__(self) -> None:
        self.team_id = "T123"
        self.team_name = "Test Team"
        self._user_cache = {
            "U001": {
                "id": "U001",
                "team_id": "T123",
                "is_bot": False,
                "deleted": False,
                "profile": {
                    "email": "alice@example.com",
                    "display_name": "Alice",
                    "real_name": "Alice A",
                },
            },
            "U002": {
                "id": "U002",
                "team_id": "T123",
                "is_bot": True,
                "deleted": False,
                "profile": {
                    "email": "bot@example.com",
                    "display_name": "Bot",
                    "real_name": "Bot",
                },
            },
            "U003": {
                "id": "U003",
                "team_id": "T999",
                "is_bot": False,
                "deleted": False,
                "profile": {
                    "email": "guest#EXT#@external.com",
                    "display_name": "Guest #EXT#",
                    "real_name": "Guest",
                },
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
                "channels": [
                    {"id": "C001", "name": "general", "is_private": False, "is_im": False, "is_mpim": False},
                    {"id": "C002", "name": "secret", "is_private": True, "is_im": False, "is_mpim": False},
                ],
                "response_metadata": {"next_cursor": ""},
            }
        if endpoint == "conversations.members":
            if params.get("channel") == "C002":
                return {
                    "ok": True,
                    "members": ["U001", "U002", "U003"],
                    "response_metadata": {"next_cursor": ""},
                }
            return {"ok": True, "members": [], "response_metadata": {"next_cursor": ""}}
        if endpoint == "users.list":
            return {"ok": True, "members": list(self._user_cache.values()), "response_metadata": {"next_cursor": ""}}
        raise AssertionError(f"Unexpected endpoint: {endpoint}")


class DiscoverySmokeTest(unittest.TestCase):
    def test_discovery_writes_json_and_cypher(self):
        fetcher = FakeSlackFetcher()
        with tempfile.TemporaryDirectory() as tmpdir:
            artifact = discover_scopes(fetcher=fetcher, org="ezobooks", output_dir=Path(tmpdir))

            json_path = Path(tmpdir) / "slack" / "ezobooks_scope_principals.json"
            cypher_path = Path(tmpdir) / "cypher" / "ezobooks_slack_acl.cypher"
            self.assertTrue(json_path.exists())
            self.assertTrue(cypher_path.exists())

            payload = json.loads(json_path.read_text(encoding="utf-8"))
            self.assertIn("scopes", payload)
            self.assertIn("principals", payload)
            self.assertIn("scope_principals", payload)

            scope_ids = {item["scope_id"] for item in payload["scopes"]}
            self.assertIn("slack:workspace:T123", scope_ids)
            self.assertIn("slack:channel:C001", scope_ids)
            self.assertIn("slack:channel:C002", scope_ids)

            workspace_rows = [row for row in payload["scopes"] if row["scope_id"] == "slack:workspace:T123"]
            self.assertEqual(workspace_rows, [{"scope_id": "slack:workspace:T123", "source": "slack", "kind": "workspace", "is_public": True, "tenant_id": "ezobooks"}])

            public_rows = [row for row in payload["scope_principals"] if row["scope_id"] == "slack:channel:C001"]
            private_rows = [row for row in payload["scope_principals"] if row["scope_id"] == "slack:channel:C002"]
            self.assertEqual(public_rows, [])
            self.assertEqual(private_rows, [{"scope_id": "slack:channel:C002", "principal_id": "slack:U001", "grant_source": "slack_channel_membership"}])

            principal_ids = {item["principal_id"] for item in payload["principals"]}
            self.assertEqual(principal_ids, {"slack:U001"})
            self.assertEqual(artifact.to_dict(), payload)

            cypher_text = cypher_path.read_text(encoding="utf-8")
            self.assertIn("MERGE (s:AclScope", cypher_text)
            self.assertIn("slack:workspace:T123", cypher_text)
            self.assertIn("slack:channel:C002", cypher_text)


if __name__ == "__main__":
    unittest.main()
