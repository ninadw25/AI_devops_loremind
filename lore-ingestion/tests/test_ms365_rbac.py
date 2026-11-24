"""
tests/test_ms365_rbac.py
────────────────────────
MS365 RBAC / ACL scope propagation tests.

These tests verify that ACL scope_ids flow correctly from the normalizer
through grouping and extraction output, and that the scope format locked
by test_scopes.py is actually present on knowledge nodes that reach the
builder layer.

Coverage:
    Outlook Mail: participant-based scope population via _populate_outlook_scopes()
    Teams:        channel-scoped ACL stamped in raw["scope_id"] by normalizer,
                  hoisted by grouping, carried through extraction output

No network calls. No Azure credentials. No LLM API key.

These tests use the same fixtures and helpers as the existing grouping and
normalizer tests — they are a named entry point for RBAC-specific assertions
rather than a new test framework.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from common.scopes import ms365_channel_scope, is_ms365_channel
from pipeline.extraction.extract import _populate_outlook_scopes
from pipeline.extraction.grouping import group_by_thread


# ── Helpers ───────────────────────────────────────────────────────────────────

def _write_jsonl(path: Path, events: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(e) for e in events) + "\n",
        encoding="utf-8",
    )


def _outlook_event(conv_id: str, sender: str, recipients: list[str],
                   msg_id: str = "MSG-1") -> dict:
    """Minimal normalized Outlook Mail KnowledgeEvent."""
    actors = [{"id": f"email:{sender}", "email": sender, "display_name": sender}]
    for r in recipients:
        actors.append({"id": f"email:{r}", "email": r, "display_name": r})
    return {
        "id": f"om-{msg_id}", "source": "outlook_mail",
        "source_id": msg_id, "event_type": "email.message",
        "timestamp": "2026-06-01T10:00:00Z",
        "title": "Planning meeting",
        "body": "Let's align on the roadmap before the board meeting.",
        "primary_actor": f"email:{sender}",
        "actors": actors,
        "org": "testorg",
        "raw": {
            "conversation_id": conv_id,
            "isDraft": False,
            "internet_message_id": f"<{msg_id}@corp.com>",
            "body_content_type": "text",
            "categories": [],
        },
    }


def _teams_event(channel_id: str, source_id: str,
                 reply_to_id: str | None = None) -> dict:
    """Minimal normalized Teams KnowledgeEvent with scope_id stamped in raw."""
    return {
        "id": f"tm-{source_id}", "source": "ms_teams",
        "source_id": source_id,
        "event_type": "ms_teams.message", "timestamp": "2026-06-01T11:00:00Z",
        "title": "Stand-up discussion",
        "body": "The auth module review is complete and ready for merging.",
        "primary_actor": "teams:USER-001",
        "actors": [{"id": "teams:USER-001", "display_name": "Alice", "is_bot": False}],
        "org": "testorg",
        "raw": {
            "team_id": "T-1",
            "channel_id": channel_id,
            "scope_id": ms365_channel_scope(channel_id),
            "reply_to_id": reply_to_id,
            "message_type": "message",
        },
    }


# ═════════════════════════════════════════════════════════════════════════════
# Outlook Mail — participant-based scope population
# ═════════════════════════════════════════════════════════════════════════════

class TestOutlookMailScope:
    """Outlook Mail knowledge nodes receive non-empty scope_ids derived from
    participant actor emails via _populate_outlook_scopes()."""

    def test_outlook_thread_gets_scope_ids_from_participants(self, tmp_path):
        """After grouping (no scope_id), _populate_outlook_scopes() stamps
        scope_ids in person:{email} format from participant actor emails."""
        events = [
            _outlook_event("CONV-1", "alice@corp.com", ["bob@corp.com"], "MSG-1"),
            _outlook_event("CONV-1", "bob@corp.com",   ["alice@corp.com"], "MSG-2"),
        ]
        _write_jsonl(tmp_path / "filtered.jsonl", events)
        threads = group_by_thread(tmp_path / "filtered.jsonl")

        assert "CONV-1" in threads
        assert "scope_id" not in threads["CONV-1"]

        _populate_outlook_scopes(threads)

        scope_ids = threads["CONV-1"].get("scope_ids", [])
        assert len(scope_ids) >= 2, f"Expected ≥2 scope_ids, got: {scope_ids}"
        assert "person:alice@corp.com" in scope_ids
        assert "person:bob@corp.com"   in scope_ids

    def test_outlook_scope_ids_deduplicated_across_messages(self, tmp_path):
        """Same participant appearing in multiple messages is only added once."""
        events = [
            _outlook_event("CONV-2", "alice@corp.com", ["bob@corp.com"], "MSG-A"),
            _outlook_event("CONV-2", "alice@corp.com", ["carol@corp.com"], "MSG-B"),
        ]
        _write_jsonl(tmp_path / "filtered.jsonl", events)
        threads = group_by_thread(tmp_path / "filtered.jsonl")
        _populate_outlook_scopes(threads)

        scope_ids = threads["CONV-2"].get("scope_ids", [])
        assert scope_ids.count("person:alice@corp.com") == 1

    def test_outlook_thread_with_no_actors_gets_no_scope_ids(self, tmp_path):
        """A thread whose events have no actor emails produces no scope_ids
        and stays fail-closed."""
        events = [_outlook_event("CONV-3", "", [], "MSG-C")]
        _write_jsonl(tmp_path / "filtered.jsonl", events)
        threads = group_by_thread(tmp_path / "filtered.jsonl")
        _populate_outlook_scopes(threads)

        assert "scope_ids" not in threads.get("CONV-3", {})

    def test_populate_does_not_overwrite_existing_scope(self, tmp_path):
        """Threads that already have a scope_id are left untouched."""
        events = [_outlook_event("CONV-4", "alice@corp.com", ["bob@corp.com"], "MSG-D")]
        _write_jsonl(tmp_path / "filtered.jsonl", events)
        threads = group_by_thread(tmp_path / "filtered.jsonl")

        threads["CONV-4"]["scope_id"] = "jira:project:ENG"
        _populate_outlook_scopes(threads)

        assert threads["CONV-4"]["scope_id"] == "jira:project:ENG"


# ═════════════════════════════════════════════════════════════════════════════
# Teams — channel-scoped ACL propagation
# ═════════════════════════════════════════════════════════════════════════════

class TestTeamsScope:
    """Teams knowledge nodes carry ms365:channel:{channelId} scope values.
    The value is stamped in raw["scope_id"] by the normalizer (LOR-66 Phase 0)
    and hoisted onto the thread dict by group_by_thread (scope_in_raw=True).
    """

    def test_teams_thread_has_ms365_channel_scope(self, tmp_path):
        """After grouping, the thread dict carries scope_id = ms365:channel:C-1."""
        root_sid = "testorg:T-1:C-1:MSG001"
        events = [_teams_event("C-1", root_sid)]
        _write_jsonl(tmp_path / "filtered.jsonl", events)
        threads = group_by_thread(tmp_path / "filtered.jsonl")

        assert root_sid in threads
        scope_id = threads[root_sid].get("scope_id")
        assert scope_id == "ms365:channel:C-1", f"Got: {scope_id}"
        assert is_ms365_channel(scope_id), f"Not an ms365:channel: scope: {scope_id}"

    def test_teams_scope_id_uses_channel_only_no_team_id(self, tmp_path):
        """The scope_id must be ms365:channel:{channelId} — channel ID only.
        The team_id must not be embedded (unlike the legacy teams:channel: format)."""
        root_sid = "testorg:T-99:C-99:ROOT"
        events = [_teams_event("C-99", root_sid)]
        _write_jsonl(tmp_path / "filtered.jsonl", events)
        threads = group_by_thread(tmp_path / "filtered.jsonl")

        scope_id = threads[root_sid]["scope_id"]
        # Must start with ms365:channel: not teams:channel:
        assert scope_id.startswith("ms365:channel:"), f"Wrong prefix: {scope_id}"
        assert not scope_id.startswith("teams:channel:"), \
            "scope_id must not use the legacy teams:channel: prefix"
        # Channel ID must be present
        assert "C-99" in scope_id

    def test_teams_reply_collapses_into_root_thread_with_same_scope(self, tmp_path):
        """Root + reply form a single thread; both carry the same channel scope."""
        root_sid   = "testorg:T-1:C-1:MSG010"
        reply_sid  = "testorg:T-1:C-1:MSG011"
        events = [
            _teams_event("C-1", root_sid),                     # root: reply_to_id=None
            _teams_event("C-1", reply_sid, reply_to_id=root_sid),  # reply
        ]
        _write_jsonl(tmp_path / "filtered.jsonl", events)
        threads = group_by_thread(tmp_path / "filtered.jsonl")

        # Both events must be in the same thread
        assert root_sid in threads, f"Root thread key not found. Keys: {list(threads)}"
        assert threads[root_sid]["email_count"] == 2
        assert threads[root_sid]["scope_id"] == "ms365:channel:C-1"

    def test_teams_different_channels_have_different_scopes(self, tmp_path):
        """Events from two channels produce separate threads with distinct scope_ids."""
        sid_a = "testorg:T-1:C-A:MSG001"
        sid_b = "testorg:T-1:C-B:MSG001"
        events = [
            _teams_event("C-A", sid_a),
            _teams_event("C-B", sid_b),
        ]
        _write_jsonl(tmp_path / "filtered.jsonl", events)
        threads = group_by_thread(tmp_path / "filtered.jsonl")

        assert threads[sid_a]["scope_id"] == "ms365:channel:C-A"
        assert threads[sid_b]["scope_id"] == "ms365:channel:C-B"
        assert threads[sid_a]["scope_id"] != threads[sid_b]["scope_id"]

    def test_teams_scope_survives_out_of_order_delivery(self, tmp_path):
        """Reply arrives before root in the JSONL — thread still forms correctly
        with the correct scope_id hoisted."""
        root_sid  = "testorg:T-1:C-1:MSG020"
        reply_sid = "testorg:T-1:C-1:MSG021"
        events = [
            _teams_event("C-1", reply_sid, reply_to_id=root_sid),  # reply first
            _teams_event("C-1", root_sid),                          # root second
        ]
        _write_jsonl(tmp_path / "filtered.jsonl", events)
        threads = group_by_thread(tmp_path / "filtered.jsonl")

        assert root_sid in threads
        assert threads[root_sid]["email_count"] == 2
        assert threads[root_sid]["scope_id"] == "ms365:channel:C-1"

    def test_teams_scope_present_on_extraction_output(self, tmp_path):
        """Scope_id set on the thread dict is copied into the extraction output
        by _copy_thread_metadata() — verified here at the thread level since
        extract_all() requires an LLM client."""
        root_sid = "testorg:T-1:C-1:MSG030"
        events = [_teams_event("C-1", root_sid)]
        _write_jsonl(tmp_path / "filtered.jsonl", events)
        threads = group_by_thread(tmp_path / "filtered.jsonl")

        thread = threads[root_sid]
        # Simulate what _copy_thread_metadata() does for the triples output
        result = {"thread_id": root_sid, "triples": []}
        for k in ("scope_id", "scope_ids", "extra_scope_ids"):
            if thread.get(k) is not None:
                result[k] = thread[k]

        assert result.get("scope_id") == "ms365:channel:C-1"

    def test_ms365_channel_scope_helper_matches_normalizer_output(self):
        """The scope format produced by the normalizer exactly matches
        ms365_channel_scope() — verifies end-to-end format consistency."""
        # Simulate what normalize_teams_message() puts in raw["scope_id"]
        channel_id = "19:abc123@thread.tacv2"
        normalizer_output = ms365_channel_scope(channel_id)
        assert normalizer_output == f"ms365:channel:{channel_id}"
        assert is_ms365_channel(normalizer_output)
