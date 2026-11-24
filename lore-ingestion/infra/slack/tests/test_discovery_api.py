"""
Tests for infra/slack/discovery_api.py — the Grid Discovery client.

Fully fake-driven (no Grid org). Validates the deterministic edit/delete
classification, ACL membership parsing, the recent→history change-stream,
both pagination styles, and the 7-day window guard.
"""

import pytest

from infra.slack.discovery_api import (
    ChangeRecord,
    SlackDiscoveryClient,
    SlackDiscoveryError,
    classify_change,
    iter_changes,
    message_source_id,
)


class FakeSlack:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def __call__(self, method, params):
        self.calls.append((method, dict(params)))
        # support offset/cursor keys for pagination
        key = (method, params.get("offset", "") or params.get("cursor", ""))
        if key in self.responses:
            return self.responses[key]
        return self.responses.get(method, {"ok": True})


def _client(responses):
    return SlackDiscoveryClient(token="xoxp-disc", requester=FakeSlack(responses))


# ── classification ───────────────────────────────────────────────────

def test_classify_delete_by_root_type():
    assert classify_change({"type": "deleted", "ts": "1.1"}) == "deleted"


def test_classify_delete_by_edits_subtype():
    msg = {"type": "message", "ts": "1.1", "edits": [{"subtype": "message_deleted"}]}
    assert classify_change(msg) == "deleted"


def test_classify_edit_by_edited_object():
    assert classify_change({"type": "message", "text": "new", "edited": {"ts": "9.9"}}) == "edited"


def test_classify_edit_by_edits_subtype():
    msg = {"type": "message", "edits": [{"subtype": "message_changed", "previous": {"text": "old"}}]}
    assert classify_change(msg) == "edited"


def test_classify_new_when_no_markers():
    assert classify_change({"type": "message", "text": "hi"}) == "new"


# ── ACL membership ───────────────────────────────────────────────────

def test_channel_members_from_info_key():
    responses = {"discovery.conversations.info": {
        "ok": True, "info": {"id": "C1", "is_private": True, "members": ["U1", "U2"]}}}
    assert _client(responses).channel_members("C1", "T1") == ["U1", "U2"]


def test_channel_members_from_channel_info_key():
    # defensive: some responses nest under channel_info
    responses = {"discovery.conversations.info": {
        "ok": True, "channel_info": {"id": "C1", "members": ["U9"]}}}
    assert _client(responses).channel_members("C1", "T1") == ["U9"]


# ── pagination ───────────────────────────────────────────────────────

def test_users_list_offset_pagination():
    responses = {
        ("discovery.users.list", ""): {"ok": True, "users": [{"id": "U1"}], "offset": "O2"},
        ("discovery.users.list", "O2"): {"ok": True, "users": [{"id": "U2"}], "offset": ""},
    }
    users = _client(responses).users_list()
    assert [u["id"] for u in users] == ["U1", "U2"]


def test_history_cursor_pagination():
    responses = {
        ("discovery.conversations.history", ""): {
            "ok": True, "messages": [{"ts": "1.1"}],
            "response_metadata": {"next_cursor": "C2"}},
        ("discovery.conversations.history", "C2"): {
            "ok": True, "messages": [{"ts": "2.2"}], "response_metadata": {"next_cursor": ""}},
    }
    msgs = list(_client(responses).conversations_history("C1", "T1"))
    assert [m["ts"] for m in msgs] == ["1.1", "2.2"]


# ── window guard + errors ────────────────────────────────────────────

def test_recent_window_guard_rejects_over_7_days():
    c = _client({})
    with pytest.raises(SlackDiscoveryError):
        c.conversations_recent(oldest=0, latest=8 * 24 * 3600)


def test_error_response_raises():
    with pytest.raises(SlackDiscoveryError):
        _client({"discovery.enterprise.info": {"ok": False, "error": "not_allowed_token_type"}}).enterprise_info()


# ── the change-stream (recent → history) ─────────────────────────────

def test_iter_changes_streams_edit_delete_new():
    responses = {
        "discovery.conversations.recent": {"ok": True, "channels": [{"id": "C1", "team": "T1"}]},
        "discovery.conversations.history": {
            "ok": True,
            "messages": [
                {"ts": "1.1", "type": "message", "text": "fresh"},                       # new
                {"ts": "1.2", "type": "message", "text": "updated", "edited": {"ts": "9"}},  # edit
                {"ts": "1.3", "type": "deleted"},                                         # delete
            ],
            "response_metadata": {"next_cursor": ""},
        },
    }
    changes = list(iter_changes(_client(responses), oldest=0, latest=3600, team="T1"))
    by_action = {c.action: c for c in changes}
    assert by_action["new"].source_id == "C1:1.1" and by_action["new"].body == "fresh"
    assert by_action["edited"].source_id == "C1:1.2" and by_action["edited"].body == "updated"
    assert by_action["deleted"].source_id == "C1:1.3" and by_action["deleted"].body == ""


def test_change_record_to_delta_matches_bot_path_shape():
    rec = ChangeRecord(source_id="C1:1.2", channel_id="C1", action="edited", ts="1.2", body="new text")
    delta = rec.to_delta(org="techtest")
    assert delta == {
        "source_id": "C1:1.2", "source": "slack", "org": "techtest",
        "reconcile_action": "edited", "body": "new text",
    }
    # delete delta carries no body (tombstone), same as the bot reconciler
    d2 = ChangeRecord(source_id="C1:1.3", channel_id="C1", action="deleted", ts="1.3").to_delta("techtest")
    assert "body" not in d2 and d2["reconcile_action"] == "deleted"
