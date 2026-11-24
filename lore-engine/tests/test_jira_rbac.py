"""
tests/test_jira_rbac.py
────────────────────────
RBAC fail-closed assertions for domain/retrieval/jira.py.

All tests use a fake in-memory graph — no live Neo4j required.
The fake returns controlled rows so each test exercises exactly
one RBAC invariant.
"""

import asyncio
import pytest
from domain.retrieval.jira import retrieve_jira, _classify_intent


# ── Fake graph ────────────────────────────────────────────────────────

class FakeGraph:
    """In-memory graph that returns pre-set rows per Cypher pattern."""

    available = True

    def __init__(self, rows: dict[str, list[dict]]):
        # keys are substrings matched against the Cypher query
        self._rows = rows

    def run(self, cypher: str, **_params) -> list[dict]:
        for pattern, rows in self._rows.items():
            if pattern in cypher:
                return rows
        return []


# ── Shared fixtures ───────────────────────────────────────────────────

# A minimal Issue row returned by neighbourhood expand
def _issue_row(key: str = "OPS-2", scope_ids: list[str] | None = None) -> dict:
    return {
        "key": key,
        "summary": f"Issue {key}",
        "status": "To Do",
        "issue_type": "Task",
        "scope_ids": scope_ids or ["jira:project:OPS"],
        "assignee": "Abhishek",
        "reporter": "Reia",
        "sprint": None,
        "components": [],
        "decisions": [],
        "facts": [],
    }


def _issue_lookup_row(key: str = "OPS-2", scope_ids: list[str] | None = None) -> dict:
    return {
        "issue_id": f"issue:{key.lower()}",
        "key": key,
        "summary": f"Issue {key}",
        "status": "To Do",
        "issue_type": "Task",
        "scope_ids": scope_ids or ["jira:project:OPS"],
    }


def _decision_row(scope_ids: list[str] | None = None) -> dict:
    return {
        "node_id": "dec:abc123",
        "node_type": "Decision",
        "text": "AWS contract renewal approved",
        "scope_ids": scope_ids or ["jira:project:OPS"],
        "score": 0.85,
    }


ZERO_DIM_EMBEDDING = [0.0] * 2048


def _run(coro):
    return asyncio.run(coro)


# ── 1. RBAC gate is present in every Cypher query ─────────────────────
#
# The fail-closed invariant is enforced at the Cypher level — Neo4j
# filters nodes before Python ever sees them. These tests verify that
# every query template contains the required WHERE conditions.

# NOTE: the RBAC gate is now the single-owner fragment from domain/scopes.py
# (scope_gate), shared with the unstructured path. Param name is $allowed_scopes.
def test_cypher_gate_present_in_vector_search():
    """Vector search Cypher must reject nodes with null/empty scope_ids
    AND restrict to Jira-derived nodes (source isolation)."""
    from domain.retrieval.jira import _VECTOR_SEARCH_CYPHER
    assert "size(coalesce(n.scope_ids, [])) > 0" in _VECTOR_SEARCH_CYPHER
    assert "any(s IN n.scope_ids WHERE s IN $allowed_scopes)" in _VECTOR_SEARCH_CYPHER
    # source isolation: the shared vector space is filtered to Jira-only nodes
    assert "STARTS WITH 'jira:'" in _VECTOR_SEARCH_CYPHER


def test_cypher_gate_present_in_all_queries():
    """Every Cypher template that touches scoped nodes must have the RBAC gate."""
    from domain.retrieval.jira import (
        _VECTOR_SEARCH_CYPHER,
        _DERIVED_FROM_CYPHER,
        _ISSUE_BY_KEY_CYPHER,
        _PERSON_ISSUES_CYPHER,
        _ISSUE_NEIGHBOURHOOD_CYPHER,
    )
    scoped_queries = {
        "_VECTOR_SEARCH_CYPHER":    _VECTOR_SEARCH_CYPHER,
        "_DERIVED_FROM_CYPHER":     _DERIVED_FROM_CYPHER,
        "_ISSUE_BY_KEY_CYPHER":     _ISSUE_BY_KEY_CYPHER,
        "_PERSON_ISSUES_CYPHER":    _PERSON_ISSUES_CYPHER,
    }
    for name, cypher in scoped_queries.items():
        assert "scope_ids, [])) > 0" in cypher, f"{name} missing size()/coalesce check"
        assert "$allowed_scopes" in cypher,      f"{name} missing allowed_scopes param"

    # Neighbourhood expand gates its derived Decision/Fact subqueries.
    assert "$allowed_scopes" in _ISSUE_NEIGHBOURHOOD_CYPHER


def test_cypher_gate_blocks_empty_scope_ids():
    """Fail-closed: nodes with scope_ids = [] must be blocked.

    scope_gate's 'size(coalesce(n.scope_ids, [])) > 0' makes empty-list (and
    absent) scope_ids match nobody. Asserted present in every scoped template.
    """
    from domain.retrieval.jira import (
        _VECTOR_SEARCH_CYPHER,
        _DERIVED_FROM_CYPHER,
        _ISSUE_BY_KEY_CYPHER,
        _ISSUES_BY_STATUS_CYPHER,
        _PERSON_ISSUES_CYPHER,
    )
    templates = {
        "_VECTOR_SEARCH_CYPHER":    _VECTOR_SEARCH_CYPHER,
        "_DERIVED_FROM_CYPHER":     _DERIVED_FROM_CYPHER,
        "_ISSUE_BY_KEY_CYPHER":     _ISSUE_BY_KEY_CYPHER,
        "_ISSUES_BY_STATUS_CYPHER": _ISSUES_BY_STATUS_CYPHER,
        "_PERSON_ISSUES_CYPHER":    _PERSON_ISSUES_CYPHER,
    }
    for name, cypher in templates.items():
        assert "scope_ids, [])) > 0" in cypher, (
            f"{name} is missing 'size(coalesce(...scope_ids, [])) > 0' — "
            f"nodes with scope_ids=[] would not be blocked"
        )


# ── 2. Wrong scope → invisible ────────────────────────────────────────

def test_wrong_scope_returns_nothing_navigational():
    """User with ENG scope cannot see an OPS issue by key lookup."""
    graph = FakeGraph({
        # The real Cypher gate filters this out; fake returns empty to simulate
        "key": [],
    })
    chunks = _run(retrieve_jira(
        "what is the status of OPS-2?",
        ZERO_DIM_EMBEDDING,
        user_scopes=["jira:project:ENG"],
        org="test",
        graph=graph,
    ))
    assert chunks == [], "User without OPS scope must not see OPS issues"


def test_wrong_scope_returns_nothing_analytical():
    """Vector search returns nothing when user scopes don't match."""
    graph = FakeGraph({
        "queryNodes": [],  # Cypher gate filtered everything out
    })
    chunks = _run(retrieve_jira(
        "what decisions were made about AWS?",
        ZERO_DIM_EMBEDDING,
        user_scopes=["jira:project:ENG"],
        org="test",
        graph=graph,
    ))
    assert chunks == [], "User without matching scope must get no analytical results"


# ── 3. Correct scope → visible ────────────────────────────────────────

def test_correct_scope_navigational_returns_issue():
    """User with OPS scope sees OPS issue via key lookup."""
    graph = FakeGraph({
        "key":     [_issue_lookup_row()],
        "issue_id":[_issue_row()],
    })
    chunks = _run(retrieve_jira(
        "what is the status of OPS-2?",
        ZERO_DIM_EMBEDDING,
        user_scopes=["jira:project:OPS"],
        org="test",
        graph=graph,
    ))
    assert len(chunks) == 1
    assert chunks[0]["node_type"] == "Issue"
    assert chunks[0]["citations"] == ["OPS-2"]


def test_correct_scope_analytical_returns_chunks():
    """User with OPS scope sees Decision + linked Issue via analytical path."""
    graph = FakeGraph({
        "queryNodes": [_decision_row()],
        "DERIVED_FROM": [_issue_lookup_row()],
        "issue_id": [_issue_row()],
    })
    chunks = _run(retrieve_jira(
        "what decisions were made about AWS?",
        ZERO_DIM_EMBEDDING,
        user_scopes=["jira:project:OPS"],
        org="test",
        graph=graph,
    ))
    assert len(chunks) >= 1
    types = {c["node_type"] for c in chunks}
    assert "Decision" in types


# ── 4. scope_all_ids AND gate ─────────────────────────────────────────

def test_scope_all_ids_requires_all_scopes():
    """User missing one of the scope_all_ids entries must not see the node.

    This simulates the Cypher AND gate:
      all(s IN coalesce(n.scope_all_ids, []) WHERE s IN $user_scopes)
    The fake returns empty rows to simulate the gate blocking the result.
    """
    # User has project scope but not the security level
    graph = FakeGraph({
        "queryNodes": [],   # gate blocked — security level not in user scopes
        "key": [],
    })
    chunks = _run(retrieve_jira(
        "what decisions were made about AWS?",
        ZERO_DIM_EMBEDDING,
        user_scopes=["jira:project:OPS"],  # missing jira:seclevel:10001
        org="test",
        graph=graph,
    ))
    assert chunks == [], "User missing scope_all_ids entry must see nothing"


def test_scope_all_ids_passes_when_all_present():
    """User with all required scopes including security level sees the node."""
    graph = FakeGraph({
        "queryNodes": [_decision_row(scope_ids=["jira:project:OPS", "jira:seclevel:10001"])],
        "DERIVED_FROM": [_issue_lookup_row()],
        "issue_id": [_issue_row()],
    })
    chunks = _run(retrieve_jira(
        "what decisions were made about AWS?",
        ZERO_DIM_EMBEDDING,
        user_scopes=["jira:project:OPS", "jira:seclevel:10001"],
        org="test",
        graph=graph,
    ))
    assert len(chunks) >= 1


# ── 5. Status-filter path is also gated ──────────────────────────────

def test_status_cypher_gate_present():
    """_ISSUES_BY_STATUS_CYPHER must contain the RBAC gate."""
    from domain.retrieval.jira import _ISSUES_BY_STATUS_CYPHER
    assert "size(coalesce(i.scope_ids, [])) > 0" in _ISSUES_BY_STATUS_CYPHER
    assert "$allowed_scopes" in _ISSUES_BY_STATUS_CYPHER


def test_status_query_wrong_scope_returns_nothing():
    """User with wrong scope gets no results from status-filter path."""
    graph = FakeGraph({"toLower(i.status)": []})  # gate filtered everything
    chunks = _run(retrieve_jira(
        "which issues are blocked?",
        ZERO_DIM_EMBEDDING,
        user_scopes=["jira:project:ENG"],
        org="test",
        graph=graph,
    ))
    assert chunks == []


def test_status_query_correct_scope_returns_issues():
    """User with correct scope sees issues from status-filter path."""
    graph = FakeGraph({
        "toLower(i.status)": [_issue_lookup_row()],
        "issue_id":          [_issue_row()],
    })
    chunks = _run(retrieve_jira(
        "which issues are in progress?",
        ZERO_DIM_EMBEDDING,
        user_scopes=["jira:project:OPS"],
        org="test",
        graph=graph,
    ))
    assert len(chunks) >= 1
    assert all(c["node_type"] == "Issue" for c in chunks)


def test_status_detection():
    """_detect_status correctly maps query keywords to Jira status values."""
    from domain.retrieval.jira import _detect_status
    assert _detect_status("which issues are blocked?")   == "Blocked"
    assert _detect_status("what is in progress?")        == "In Progress"
    assert _detect_status("show me all done issues")     == "Done"
    assert _detect_status("what is pending?")            == "To Do"
    assert _detect_status("what decisions were made?")   is None


# ── 7. Graph unavailable → empty ─────────────────────────────────────

def test_graph_unavailable_returns_empty():
    """If Neo4j is down, retrieve_jira returns [] without raising."""
    class UnavailableGraph:
        available = False
        def run(self, *_, **__): return []

    chunks = _run(retrieve_jira(
        "what is the status of OPS-2?",
        ZERO_DIM_EMBEDDING,
        user_scopes=["jira:project:OPS"],
        org="test",
        graph=UnavailableGraph(),
    ))
    assert chunks == []


# ── 8. Intent classification ──────────────────────────────────────────

@pytest.mark.parametrize("query,expected", [
    ("what is the status of OPS-2?",             "navigational"),
    ("what is Arnav working on?",                 "navigational"),
    ("who is working on ENG-15?",                 "navigational"),
    ("what decisions were made about AWS?",       "analytical"),
    ("what hiring decisions were made?",          "analytical"),
    ("what infrastructure issues exist?",         "analytical"),
])
def test_intent_classification(query, expected):
    assert _classify_intent(query) == expected, (
        f"Expected '{expected}' for: {query!r}"
    )
