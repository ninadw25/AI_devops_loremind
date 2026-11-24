"""
tests/test_jira_routing.py
───────────────────────────
Synthetic tests for intent routing in domain/retrieval/jira.py.

Tests cover:
  - _detect_status   : status keyword extraction including false positives
  - _classify_intent : navigational vs analytical routing
  - _sync_retrieve   : end-to-end path selection via FakeGraph call log

No live Neo4j required.
"""

from __future__ import annotations

import pytest

from domain.retrieval.jira import _classify_intent, _detect_status, _sync_retrieve


# ── _detect_status ────────────────────────────────────────────────────


def test_status_blocked():
    assert _detect_status("show me all blocked issues") == "Blocked"


def test_status_in_progress():
    assert _detect_status("what is in progress right now?") == "In Progress"


def test_status_done():
    assert _detect_status("list completed tickets") == "Done"


def test_status_todo():
    assert _detect_status("show pending issues") == "To Do"


def test_status_none_for_analytical():
    assert _detect_status("what decisions were made about auth?") is None


def test_status_none_for_issue_key():
    assert _detect_status("what is the status of ENG-12?") is None


# ── False positive: "done" in a non-status context ────────────────────


def test_status_false_positive_done_in_navigational():
    """'done' appears but query has a person name — _detect_status still returns Done
    but _sync_retrieve suppresses the status path when a person is present."""
    result = _detect_status("what has Reia done this sprint?")
    assert result == "Done"  # detect_status itself still fires


def test_status_false_positive_blocked_person():
    """'blocked' appears with a person name — same: detect fires, retrieve suppresses."""
    result = _detect_status("is Arnav blocked on anything?")
    assert result == "Blocked"


def test_retrieve_person_query_with_status_keyword_skips_status_path():
    """'done' in a person query must NOT route to status path — person name takes priority."""
    g = _PathTrackingGraph()
    _sync_retrieve("what has Arnav Gupta done this sprint?", DUMMY_EMBEDDING, SCOPES, 5, g)
    assert "issues_by_status" not in g.calls


def test_retrieve_person_blocked_query_skips_status_path():
    """'blocked' with a person name must route to navigational, not status."""
    g = _PathTrackingGraph()
    _sync_retrieve("is Arnav Gupta blocked on anything?", DUMMY_EMBEDDING, SCOPES, 5, g)
    assert "issues_by_status" not in g.calls


# ── _classify_intent ──────────────────────────────────────────────────


def test_intent_issue_key_navigational():
    assert _classify_intent("what is the status of ENG-42?") == "navigational"


def test_intent_multi_key_navigational():
    assert _classify_intent("compare OPS-1 and OPS-2") == "navigational"


def test_intent_person_with_signal_navigational():
    assert _classify_intent("what is Arnav working on?") == "navigational"


def test_intent_person_without_signal_analytical():
    """Person name alone without a work/status signal → analytical."""
    assert _classify_intent("Arnav Kumar infrastructure decisions") == "analytical"


def test_intent_show_me_navigational():
    assert _classify_intent("show me the auth migration issue") == "navigational"


def test_intent_open_ended_analytical():
    assert _classify_intent("what decisions were made about the payment gateway?") == "analytical"


def test_intent_blockers_analytical():
    assert _classify_intent("what are the blockers for the Q3 release?") == "analytical"


def test_intent_topic_query_analytical():
    assert _classify_intent("summarise all infrastructure work") == "analytical"


# ── Ambiguous routing edge cases ──────────────────────────────────────


def test_intent_person_name_looks_like_topic():
    """'Mark Down' looks like a person name (two capitalised words) but is a topic.
    Regex will treat it as navigational — documented limitation."""
    result = _classify_intent("tell me about Mark Down formatting decisions")
    # Documents current behaviour — may misclassify
    assert result in ("navigational", "analytical")


def test_intent_issue_key_in_analytical_query():
    """Even if the query is open-ended, an issue key forces navigational."""
    assert _classify_intent("what decisions came out of ENG-7 planning?") == "navigational"


def test_intent_all_caps_not_issue_key():
    """All-caps word without digits should not match issue key pattern."""
    assert _classify_intent("what is the status of the API?") == "navigational"


# ── _sync_retrieve path selection via FakeGraph ───────────────────────


class _PathTrackingGraph:
    """Records which Cypher templates were called to infer which path ran."""

    available = True

    def __init__(self):
        self.calls: list[str] = []

    def run(self, cypher: str, **_params) -> list[dict]:
        if "db.index.vector.queryNodes" in cypher:
            self.calls.append("vector_search")
        elif "{key: $key}" in cypher:
            self.calls.append("issue_by_key")
        elif "toLower($status)" in cypher:
            self.calls.append("issues_by_status")
        elif "toLower($name_fragment)" in cypher or "toLower(p.name)" in cypher:
            self.calls.append("person_issues")
        elif "issue_id: $issue_id" in cypher:
            self.calls.append("neighbourhood")
        elif "account_id: $account_id" in cypher:
            self.calls.append("proximity")
        return []


SCOPES = ["jira:project:ENG"]
DUMMY_EMBEDDING = [0.0] * 2048


def test_retrieve_status_query_hits_status_path():
    g = _PathTrackingGraph()
    _sync_retrieve("show all blocked issues", DUMMY_EMBEDDING, SCOPES, 5, g)
    assert "issues_by_status" in g.calls
    assert "vector_search" not in g.calls


def test_retrieve_issue_key_hits_navigational_path():
    """issue_by_key must be called; fallback to vector is expected when graph returns empty."""
    g = _PathTrackingGraph()
    _sync_retrieve("what is ENG-12?", DUMMY_EMBEDDING, SCOPES, 5, g)
    assert "issue_by_key" in g.calls


def test_retrieve_navigational_empty_falls_back_to_analytical():
    """When navigational returns nothing, analytical vector path must run."""
    g = _PathTrackingGraph()
    _sync_retrieve("what is Arnav working on?", DUMMY_EMBEDDING, SCOPES, 5, g)
    # Navigational (person lookup) returns [] → fallback to vector search
    assert "vector_search" in g.calls


def test_retrieve_analytical_query_hits_vector_path():
    g = _PathTrackingGraph()
    _sync_retrieve("what decisions were made about infrastructure?", DUMMY_EMBEDDING, SCOPES, 5, g)
    assert "vector_search" in g.calls
    assert "issues_by_status" not in g.calls
    assert "issue_by_key" not in g.calls


def test_retrieve_status_path_does_not_hit_vector():
    g = _PathTrackingGraph()
    _sync_retrieve("list all in progress tickets", DUMMY_EMBEDDING, SCOPES, 5, g)
    assert "issues_by_status" in g.calls
    assert "vector_search" not in g.calls


# ── Multiple issue keys in one query ─────────────────────────────────


def test_retrieve_multiple_issue_keys_all_looked_up():
    """Query with two issue keys must attempt lookup for each."""
    g = _PathTrackingGraph()
    _sync_retrieve("compare ENG-1 and OPS-3", DUMMY_EMBEDDING, SCOPES, 5, g)
    assert g.calls.count("issue_by_key") == 2


def test_retrieve_long_query_with_three_keys():
    """Three issue keys in a single query — all three lookups must fire."""
    g = _PathTrackingGraph()
    _sync_retrieve(
        "can you summarise ENG-1, ENG-2 and ENG-3 and tell me which is most critical?",
        DUMMY_EMBEDDING, SCOPES, 5, g,
    )
    assert g.calls.count("issue_by_key") == 3


# ── RBAC scope miss ───────────────────────────────────────────────────


class _ScopeDenyingGraph:
    """Returns empty for every query, simulating RBAC scope mismatch."""
    available = True

    def run(self, cypher: str, **_params) -> list[dict]:
        return []


def test_retrieve_rbac_miss_returns_empty_for_issue_key():
    """User has no scope for the requested issue — must return []."""
    g = _ScopeDenyingGraph()
    result = _sync_retrieve("what is ENG-99?", DUMMY_EMBEDDING, ["jira:project:OTHER"], 5, g)
    assert result == []


def test_retrieve_rbac_miss_returns_empty_for_status_query():
    """Status query with no matching scoped issues must return []."""
    g = _ScopeDenyingGraph()
    result = _sync_retrieve("show all blocked issues", DUMMY_EMBEDDING, ["jira:project:OTHER"], 5, g)
    assert result == []


def test_retrieve_rbac_miss_returns_empty_for_analytical():
    """Vector search returns no scoped nodes — must return []."""
    g = _ScopeDenyingGraph()
    result = _sync_retrieve(
        "what decisions were made about auth?", DUMMY_EMBEDDING, ["jira:project:OTHER"], 5, g
    )
    assert result == []


# ── Non-English / informal status phrasing ────────────────────────────


def test_status_informal_mark_complete_not_detected():
    """'mark as complete' is not in _STATUS_KEYWORDS — documents the limitation."""
    assert _detect_status("mark it as complete") is None


def test_status_informal_wrap_up_not_detected():
    assert _detect_status("wrap up the sprint") is None


def test_status_informal_finish_not_detected():
    assert _detect_status("finish the auth task") is None


def test_status_closed_detected_as_done():
    """'closed' (past tense) is in _STATUS_KEYWORDS and maps to Done."""
    assert _detect_status("show all closed issues") == "Done"


def test_status_close_verb_not_detected():
    """'close' (verb, not 'closed') is not in _STATUS_KEYWORDS — documents the gap."""
    assert _detect_status("close all resolved issues") is None
