"""
Tests for the Grid ACL discovery assembly (discovery.grid_discover_scopes) +
the fail-closed private-channel rule. Uses fake admin/Discovery requesters.
"""

from infra.slack.admin import SlackAdminClient
from infra.slack.discovery import grid_discover_scopes
from infra.slack.discovery_api import SlackDiscoveryClient


class Fake:
    def __init__(self, responses):
        self.responses = responses

    def __call__(self, method, params):
        key = (method, params.get("offset", "") or params.get("cursor", ""))
        return self.responses.get(key) or self.responses.get(method, {"ok": True})


def _admin(responses):
    return SlackAdminClient(token="xoxp", requester=Fake(responses))


def _disc(responses):
    return SlackDiscoveryClient(token="xoxp", requester=Fake(responses))


def test_grid_discovery_builds_private_channel_principals(tmp_path):
    admin = _admin({
        ("admin.users.list", ""): {"ok": True, "users": [
            {"id": "U1", "email": "a@x.com", "profile": {"display_name": "Alice"}},
            {"id": "U2", "email": "b@x.com", "profile": {"display_name": "Bob"}},
        ], "response_metadata": {"next_cursor": ""}},
        ("admin.roles.listAssignments", ""): {"ok": True, "role_assignments": []},
        "usergroups.list": {"ok": True, "usergroups": []},
    })
    disc = _disc({
        ("discovery.conversations.list", ""): {"ok": True, "channels": [
            {"id": "Cpub", "name": "general", "is_private": False},
            {"id": "Cpriv", "name": "secret", "is_private": True, "team": "T1"},
        ], "offset": ""},
        "discovery.conversations.info": {"ok": True, "info": {"id": "Cpriv", "members": ["U1", "U2"]}},
    })

    art = grid_discover_scopes(admin, disc, org="techtest", team_id="T1", output_dir=tmp_path)

    # private channel got both members as principals
    priv_grants = [sp for sp in art.scope_principals if sp["scope_id"] == "slack:channel:Cpriv"]
    assert {g["principal_id"] for g in priv_grants} == {"slack:U1", "slack:U2"}
    assert all(g["grant_source"] == "grid_discovery_membership" for g in priv_grants)
    # public channel has NO explicit principals (workspace-wide visibility)
    assert not [sp for sp in art.scope_principals if sp["scope_id"] == "slack:channel:Cpub"]
    # artifact written
    assert (tmp_path / "slack" / "techtest_scope_principals.json").exists()


def test_grid_discovery_fail_closed_on_unresolved_members(tmp_path):
    admin = _admin({
        ("admin.users.list", ""): {"ok": True, "users": [{"id": "U1", "profile": {}}],
                                    "response_metadata": {"next_cursor": ""}},
        ("admin.roles.listAssignments", ""): {"ok": True, "role_assignments": []},
        "usergroups.list": {"ok": True, "usergroups": []},
    })
    disc = _disc({
        ("discovery.conversations.list", ""): {"ok": True, "channels": [
            {"id": "Cpriv", "name": "ghost", "is_private": True, "team": "T1"},
        ], "offset": ""},
        # members unresolved → empty
        "discovery.conversations.info": {"ok": True, "info": {"id": "Cpriv", "members": []}},
    })
    art = grid_discover_scopes(admin, disc, org="techtest", team_id="T1", output_dir=tmp_path)
    # fail-closed: the unresolved private channel grants access to NO ONE
    assert not [sp for sp in art.scope_principals if sp["scope_id"] == "slack:channel:Cpriv"]


def test_grid_discovery_skips_external_unknown_members(tmp_path):
    # A member id not in the directory (external/guest) must not be granted.
    admin = _admin({
        ("admin.users.list", ""): {"ok": True, "users": [{"id": "U1", "profile": {}}],
                                    "response_metadata": {"next_cursor": ""}},
        ("admin.roles.listAssignments", ""): {"ok": True, "role_assignments": []},
        "usergroups.list": {"ok": True, "usergroups": []},
    })
    disc = _disc({
        ("discovery.conversations.list", ""): {"ok": True, "channels": [
            {"id": "Cpriv", "is_private": True, "team": "T1"},
        ], "offset": ""},
        "discovery.conversations.info": {"ok": True, "info": {"members": ["U1", "Uexternal"]}},
    })
    art = grid_discover_scopes(admin, disc, org="techtest", team_id="T1", output_dir=tmp_path)
    grants = {sp["principal_id"] for sp in art.scope_principals if sp["scope_id"] == "slack:channel:Cpriv"}
    assert grants == {"slack:U1"}  # Uexternal skipped
