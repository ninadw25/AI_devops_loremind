"""
Tests for infra/slack/admin.py — the Enterprise Grid admin pathway.

Uses an injected `requester` returning canned responses, so the full admin
surface is exercised without a real Grid org. Validates cursor pagination,
defensive parsing, error handling, and the directory assembly.
"""

import pytest

from infra.slack.admin import (
    SlackAdminClient,
    SlackAdminError,
    build_grid_directory,
)


class FakeSlack:
    """Canned Slack admin API keyed by (method, cursor)."""

    def __init__(self, responses: dict):
        self.responses = responses
        self.calls = []

    def __call__(self, method: str, params: dict) -> dict:
        self.calls.append((method, dict(params)))
        cursor = params.get("cursor", "")
        key = (method, cursor)
        if key in self.responses:
            return self.responses[key]
        return self.responses.get(method, {"ok": True})


def _client(responses):
    return SlackAdminClient(token="xoxp-test", requester=FakeSlack(responses))


def test_search_channels_paginates():
    responses = {
        ("admin.conversations.search", ""): {
            "ok": True,
            "conversations": [{"id": "C1"}, {"id": "C2"}],
            "response_metadata": {"next_cursor": "PAGE2"},
        },
        ("admin.conversations.search", "PAGE2"): {
            "ok": True,
            "conversations": [{"id": "C3"}],
            "response_metadata": {"next_cursor": ""},
        },
    }
    chans = _client(responses).search_channels()
    assert [c["id"] for c in chans] == ["C1", "C2", "C3"]


def test_get_conversation_prefs_returns_prefs():
    responses = {
        "admin.conversations.getConversationPrefs": {
            "ok": True,
            "prefs": {"who_can_post": {"type": ["admin"], "user": ["U1"]}},
        }
    }
    prefs = _client(responses).get_conversation_prefs("C1")
    assert prefs["who_can_post"]["user"] == ["U1"]


def test_error_response_raises():
    responses = {"admin.users.list": {"ok": False, "error": "not_an_admin"}}
    with pytest.raises(SlackAdminError) as exc:
        _client(responses).list_users()
    assert "not_an_admin" in str(exc.value)


def test_build_grid_directory_assembles_identity_roles_groups():
    responses = {
        ("admin.users.list", ""): {
            "ok": True,
            "users": [
                {"id": "U1", "email": "a@x.com", "is_owner": True,
                 "profile": {"display_name": "Alice"}},
                {"id": "U2", "profile": {"real_name": "Bob", "email": "b@x.com"}},
                {"id": "U3", "deleted": True, "profile": {"display_name": "Gone"}},
            ],
            "response_metadata": {"next_cursor": ""},
        },
        ("admin.roles.listAssignments", ""): {
            "ok": True,
            "role_assignments": [
                {"role_id": "Radmin", "user_id": "U1"},
                {"role_id": "Rauditor", "user_id": "U2"},
            ],
            "response_metadata": {"next_cursor": ""},
        },
        "usergroups.list": {
            "ok": True,
            "usergroups": [{"id": "S1", "users": ["U1", "U2"]}],
        },
    }
    d = build_grid_directory(_client(responses))

    by_id = {p["user_id"]: p for p in d.principals}
    assert by_id["U1"]["principal_id"] == "slack:U1"
    assert by_id["U1"]["email"] == "a@x.com"
    assert by_id["U1"]["is_owner"] is True
    assert by_id["U1"]["display_name"] == "Alice"
    # email pulled from nested profile when not top-level
    assert by_id["U2"]["email"] == "b@x.com"
    assert by_id["U2"]["display_name"] == "Bob"
    # deactivated user still captured but flagged
    assert by_id["U3"]["deactivated"] is True

    assert d.roles_by_user["U1"] == ["Radmin"]
    assert d.roles_by_user["U2"] == ["Rauditor"]
    assert d.usergroup_members["S1"] == ["U1", "U2"]


def test_defensive_parsing_tolerates_missing_fields():
    # An admin.users.list entry with almost nothing must not crash.
    responses = {
        ("admin.users.list", ""): {
            "ok": True,
            "users": [{"id": "U9"}],
            "response_metadata": {"next_cursor": ""},
        },
        ("admin.roles.listAssignments", ""): {"ok": True, "role_assignments": []},
        "usergroups.list": {"ok": True, "usergroups": []},
    }
    d = build_grid_directory(_client(responses))
    p = d.principals[0]
    assert p["user_id"] == "U9"
    assert p["email"] is None
    assert p["display_name"] == "U9"  # falls back to id
