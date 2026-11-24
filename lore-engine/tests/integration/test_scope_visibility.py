"""
tests/integration/test_scope_visibility.py
───────────────────────────────────────────
Acceptance test — T8.3 Cypher Scope Gate (LINCHPIN).

Proves that different users receive different results from the same query:

    User A (OPS scope) — queries "What decisions were made about AWS?"
        → sees OPS-scoped content

    User B (ENG scope) — same query
        → sees no OPS content; their ENG content is visible if present

This maps directly to the ticket acceptance criteria:
    ✓ in-scope node visible
    ✓ out-of-scope node hidden
    ✓ empty-scope node hidden
    ✓ scope_all_ids AND semantics (accessed by the same scoped Cypher)
    ✓ scoped and unscoped users receive different results

Architecture context:
    All retrieval Cypher in seeds.py, frontier.py, and jira.py uses
    scope_gate() from domain/scopes.py. We fake the GraphRepository and
    control what the "database" returns based on the $allowed_scopes param,
    exactly as Neo4j would: if the user's scopes match a node's scope_ids,
    the row appears; otherwise it does not.

No live Neo4j is required — the fake graph acts as the RBAC oracle.
"""

from __future__ import annotations

import pytest

from core.types import UserScopes
from domain.retrieval.jira import retrieve_jira_sync
from domain.retrieval.seeds import vector_seeds
from domain.retrieval.frontier import expand
from domain.scopes import scope_params, SCOPE_PARAM


# ── Scenario constants ────────────────────────────────────────────────

# Scope sets for the two test personas
OPS_SCOPES  = UserScopes(["jira:project:OPS"])
ENG_SCOPES  = UserScopes(["jira:project:ENG"])
EMPTY_SCOPES = UserScopes([])

ZERO_DIM_EMBEDDING = [0.0] * 2048


# ── Fake graph helpers ────────────────────────────────────────────────

class ScopedFakeGraph:
    """In-memory graph that enforces scope isolation by inspecting the
    $allowed_scopes parameter on every run() call.

    Each node stored in ``_nodes`` is a dict with a ``scope_ids`` list.
    The fake mimics Neo4j's scope_gate() semantics: a node is returned only
    if the caller's allowed_scopes overlap with the node's scope_ids (OR-set).

    This does NOT replicate the AND-set (scope_all_ids) at the fake level —
    that is tested separately against the Cypher templates and the Python
    is_visible() mirror in test_scopes.py. Here we test source-level isolation.
    """

    available = True

    def __init__(self, nodes: list[dict]):
        """nodes: list of dicts, each with a ``scope_ids`` key and a ``pattern``
        key (substring matched against the Cypher query to decide which nodes
        to surface)."""
        self._nodes = nodes

    def _is_visible(self, node: dict, allowed: list[str]) -> bool:
        """Python mirror of scope_gate — must stay in sync with domain/scopes.py."""
        ids = node.get("scope_ids") or []
        if not ids:
            return False   # fail-closed
        if not allowed:
            return False   # fail-closed
        return any(s in set(allowed) for s in ids)

    def run(self, cypher: str, **params) -> list[dict]:
        allowed = params.get(SCOPE_PARAM, [])
        results = []
        for node in self._nodes:
            if not self._is_visible(node, allowed):
                continue
            # Only include nodes whose cypher pattern matches this query
            pattern = node.get("_pattern", "")
            if pattern and pattern not in cypher:
                continue
            results.append({k: v for k, v in node.items()
                            if not k.startswith("_")})
        return results


# ── OPS vs ENG content dataset ────────────────────────────────────────

def _ops_issue_row() -> dict:
    return {
        "_pattern": "queryNodes",
        "node_id":   "dec:ops-aws-001",
        "node_type": "Decision",
        "text":      "OPS team approved AWS contract renewal for 2026.",
        "scope_ids": ["jira:project:OPS"],
        "score":     0.92,
    }


def _eng_issue_row() -> dict:
    return {
        "_pattern": "queryNodes",
        "node_id":   "dec:eng-aws-002",
        "node_type": "Decision",
        "text":      "ENG team migrated AWS EC2 to ARM-based instances.",
        "scope_ids": ["jira:project:ENG"],
        "score":     0.88,
    }


def _ops_jira_lookup_row() -> dict:
    """Returned by _ISSUE_BY_KEY_CYPHER / key-based lookup."""
    return {
        "_pattern": "key",
        "issue_id":   "issue:ops-2",
        "key":        "OPS-2",
        "summary":    "Renew AWS contract",
        "status":     "In Progress",
        "issue_type": "Task",
        "scope_ids":  ["jira:project:OPS"],
    }


def _ops_neighbourhood_row() -> dict:
    """Returned by _ISSUE_NEIGHBOURHOOD_CYPHER."""
    return {
        "_pattern": "issue_id",
        "issue_id":   "issue:ops-2",
        "key":        "OPS-2",
        "summary":    "Renew AWS contract",
        "status":     "In Progress",
        "issue_type": "Task",
        "scope_ids":  ["jira:project:OPS"],
        "assignee":   "Priya",
        "reporter":   "Arnav",
        "sprint":     None,
        "components": [],
        "decisions":  ["OPS approved AWS contract renewal"],
        "facts":      [],
    }


# ── 1. Jira scoped visibility ─────────────────────────────────────────

class TestJiraScopedVisibility:
    """OPS user sees OPS content; ENG user does not — same query, different scopes."""

    def test_ops_user_sees_ops_content(self):
        """User with OPS scope receives OPS Jira decisions."""
        graph = ScopedFakeGraph([_ops_issue_row(), _ops_neighbourhood_row(), _ops_jira_lookup_row()])
        chunks = retrieve_jira_sync(
            query="What decisions were made about AWS?",
            query_embedding=ZERO_DIM_EMBEDDING,
            user_scopes=OPS_SCOPES.allowed,
            k=10,
            graph=graph,
        )
        assert len(chunks) > 0, "OPS user must see at least one OPS chunk"
        scope_ids_seen = [sid for c in chunks for sid in (c.get("scope_ids") or [])]
        ops_scopes_present = any("jira:project:OPS" in s for s in scope_ids_seen)
        assert ops_scopes_present, "OPS user's chunks must carry jira:project:OPS scope"

    def test_eng_user_does_not_see_ops_content(self):
        """User with ENG scope must not see OPS-scoped content — same graph, same query."""
        # The graph contains only OPS nodes; ENG user's scopes produce no matches
        graph = ScopedFakeGraph([_ops_issue_row(), _ops_neighbourhood_row(), _ops_jira_lookup_row()])
        chunks = retrieve_jira_sync(
            query="What decisions were made about AWS?",
            query_embedding=ZERO_DIM_EMBEDDING,
            user_scopes=ENG_SCOPES.allowed,
            k=10,
            graph=graph,
        )
        assert len(chunks) == 0, (
            "ENG user must not receive OPS-scoped content — RBAC gate failed"
        )

    def test_ops_and_eng_users_get_different_results(self):
        """The canonical acceptance scenario: same query, different users, different results."""
        # Graph has both OPS and ENG nodes
        graph = ScopedFakeGraph([
            _ops_issue_row(),
            _ops_neighbourhood_row(),
            _ops_jira_lookup_row(),
            _eng_issue_row(),
        ])

        ops_chunks = retrieve_jira_sync(
            query="What decisions were made about AWS?",
            query_embedding=ZERO_DIM_EMBEDDING,
            user_scopes=OPS_SCOPES.allowed,
            k=10,
            graph=graph,
        )
        eng_chunks = retrieve_jira_sync(
            query="What decisions were made about AWS?",
            query_embedding=ZERO_DIM_EMBEDDING,
            user_scopes=ENG_SCOPES.allowed,
            k=10,
            graph=graph,
        )

        ops_ids = {c.get("node_id") for c in ops_chunks}
        eng_ids = {c.get("node_id") for c in eng_chunks}

        # OPS user receives OPS content
        assert len(ops_chunks) > 0, "OPS user should see content"
        # ENG user receives ENG content (or nothing if no eng nodes trigger)
        # Key assertion: the results differ
        assert ops_ids != eng_ids, (
            "OPS and ENG users received identical results — scope isolation failed"
        )
        # OPS content must not appear in ENG results
        assert "dec:ops-aws-001" not in eng_ids, (
            "OPS Decision appeared in ENG user's results — RBAC gate violated"
        )


# ── 2. Empty scope → fail-closed ─────────────────────────────────────

class TestEmptyScopeFailClosed:
    """A user with no scopes (allowedScopes=[]) must see absolutely nothing."""

    def test_empty_scope_returns_nothing_from_jira(self):
        graph = ScopedFakeGraph([_ops_issue_row(), _ops_jira_lookup_row()])
        chunks = retrieve_jira_sync(
            query="What decisions were made about AWS?",
            query_embedding=ZERO_DIM_EMBEDDING,
            user_scopes=[],
            k=10,
            graph=graph,
        )
        assert chunks == [], "Empty scopes must produce no Jira results (fail-closed)"

    def test_empty_scope_returns_nothing_from_vector_seeds(self):
        """vector_seeds() must short-circuit before querying when scopes are empty."""

        class _ShouldNotBeQueried:
            available = True

            def run(self, *_, **__):
                raise AssertionError(
                    "vector_seeds queried the graph despite empty scopes"
                )

        seeds = vector_seeds(
            client=_ShouldNotBeQueried(),
            embedding=ZERO_DIM_EMBEDDING,
            scopes=EMPTY_SCOPES,
            k=10,
        )
        assert seeds == [], "vector_seeds must return [] for empty scopes"

    def test_empty_scope_returns_nothing_from_frontier(self):
        """expand() must return seeds unchanged and never query the graph
        when scopes are empty."""

        class _ShouldNotBeQueried:
            available = True

            def run(self, *_, **__):
                raise AssertionError(
                    "expand() queried the graph despite empty scopes"
                )

        from core.types import QueryAnalysis
        seed = vector_seeds.__wrapped__ if hasattr(vector_seeds, "__wrapped__") else None
        # Directly use a BeamCandidate seed
        from core.types import BeamCandidate
        visible_seed = BeamCandidate(
            node_id="dec:vis",
            originating_seed="dec:vis",
            score=1.0,
            hop=0,
            labels=["Decision"],
            path=[("SEED", "dec:vis")],
            node_type="Decision",
            scope_ids=["slack:channel:OPS"],
        )
        result, diag = expand(
            client=_ShouldNotBeQueried(),
            seeds=[visible_seed],
            analysis=QueryAnalysis(intent="DECISION_LOOKUP"),
            scopes=EMPTY_SCOPES,
            beam_width=30,
            max_depth=3,
        )
        # Must return seeds as-is, no expansion
        assert len(result) == 1
        assert result[0].node_id == "dec:vis"
        assert diag["neo4j_query_count"] == 0


# ── 3. scope_all_ids AND semantics ───────────────────────────────────

class TestScopeAllIdsAndSemantics:
    """Nodes with scope_all_ids require the caller to hold every entry — AND gate.

    This acceptance test validates the Python mirror (is_visible) of the
    Cypher AND-gate that scope_gate() applies via:
        all(s IN coalesce(node.scope_all_ids, []) WHERE s IN $allowed_scopes)
    """

    def test_missing_scope_all_ids_entry_hides_node(self):
        """User with only the OR-set scope but NOT all scope_all_ids entries
        must not see the node."""
        from domain.scopes import is_visible
        # Node requires both project scope AND a security-level scope
        scope_ids     = ["jira:project:OPS"]
        scope_all_ids = ["jira:seclevel:10001"]   # mandatory AND-set

        # User has project scope but not the security level
        allowed = ["jira:project:OPS"]
        assert is_visible(scope_ids, scope_all_ids, allowed) is False, (
            "User missing scope_all_ids entry must not see the node"
        )

    def test_all_scope_all_ids_present_grants_visibility(self):
        """User holding ALL required scopes must see the node."""
        from domain.scopes import is_visible
        scope_ids     = ["jira:project:OPS"]
        scope_all_ids = ["jira:seclevel:10001"]

        allowed = ["jira:project:OPS", "jira:seclevel:10001"]
        assert is_visible(scope_ids, scope_all_ids, allowed) is True

    def test_multiple_scope_all_ids_all_required(self):
        """When scope_all_ids has multiple entries, the user must hold every one."""
        from domain.scopes import is_visible
        scope_ids     = ["jira:project:OPS"]
        scope_all_ids = ["jira:seclevel:10001", "jira:seclevel:10002"]

        # Missing one
        assert is_visible(scope_ids, scope_all_ids,
                          ["jira:project:OPS", "jira:seclevel:10001"]) is False
        # Holding all
        assert is_visible(scope_ids, scope_all_ids,
                          ["jira:project:OPS", "jira:seclevel:10001", "jira:seclevel:10002"]) is True


# ── 4. Scope gate structural contract ─────────────────────────────────

class TestScopeGateCypherStructure:
    """Verify the Cypher gate strings emitted by scope_gate() and friends
    satisfy the structural contract required by the acceptance criteria.

    These are fast, no-graph structural tests that ensure the gate cannot
    silently regress to a broken fragment."""

    def test_scope_gate_rejects_empty_scope_ids(self):
        """size(coalesce(…scope_ids, [])) > 0 must be present so nodes with
        scope_ids=[] or scope_ids=NULL are invisible to everyone."""
        from domain.scopes import scope_gate
        gate = scope_gate("n")
        assert "size(coalesce(n.scope_ids, [])) > 0" in gate

    def test_scope_gate_or_set_clause(self):
        """any(s IN n.scope_ids WHERE s IN $allowed_scopes) — OR-set: at least
        one of the node's scope_ids must be in the caller's allowed set."""
        from domain.scopes import scope_gate, SCOPE_PARAM
        gate = scope_gate("n")
        assert f"any(s IN n.scope_ids WHERE s IN ${SCOPE_PARAM})" in gate

    def test_scope_gate_and_set_clause(self):
        """all(s IN coalesce(n.scope_all_ids, []) WHERE s IN $allowed_scopes) —
        AND-set: every entry in scope_all_ids must be in the caller's allowed set."""
        from domain.scopes import scope_gate, SCOPE_PARAM
        gate = scope_gate("n")
        assert f"all(s IN coalesce(n.scope_all_ids, []) WHERE s IN ${SCOPE_PARAM})" in gate

    def test_unstructured_seeds_cypher_has_scope_gate(self):
        """The vector seeds query must contain both scope_gate and
        unstructured_source_filter so Jira nodes never enter the unstructured path."""
        from domain.retrieval.seeds import _VECTOR_SEEDS_CYPHER
        assert "$allowed_scopes"                in _VECTOR_SEEDS_CYPHER
        assert "scope_ids, [])) > 0"            in _VECTOR_SEEDS_CYPHER
        assert "NOT s STARTS WITH 'jira:'"      in _VECTOR_SEEDS_CYPHER

    def test_frontier_neighbor_cypher_has_scope_gate(self):
        """Every frontier neighbour Cypher must gate content nodes."""
        from domain.retrieval.frontier import _neighbor_cypher
        cypher = _neighbor_cypher("Decision", "decision_id")
        assert "$allowed_scopes" in cypher
        assert "NOT s STARTS WITH 'jira:'" in cypher

    def test_jira_vector_search_has_scope_gate_and_source_filter(self):
        """Jira's shared-space vector search must gate + filter to Jira-only."""
        from domain.retrieval.jira import _VECTOR_SEARCH_CYPHER
        assert "$allowed_scopes" in _VECTOR_SEARCH_CYPHER
        assert "STARTS WITH 'jira:'" in _VECTOR_SEARCH_CYPHER

    def test_scope_params_binds_allowed_scopes(self):
        """scope_params(UserScopes([…])) must return {SCOPE_PARAM: […]}."""
        from domain.scopes import scope_params, SCOPE_PARAM
        result = scope_params(UserScopes(["jira:project:OPS", "jira:project:ENG"]))
        assert SCOPE_PARAM in result
        assert set(result[SCOPE_PARAM]) == {"jira:project:OPS", "jira:project:ENG"}
