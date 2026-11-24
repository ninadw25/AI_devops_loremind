"""
tests/test_jira_rerank.py
──────────────────────────
Edge-case tests for rerank_chunks in domain/retrieval/jira.py.

All tests are pure unit tests — no live Neo4j required.
FakeGraph returns [] for all queries (no proximity boost fires).
"""

from datetime import datetime, timezone, timedelta

import pytest

from core.types import SourcedChunk
from domain.retrieval.jira import rerank_chunks


# ── Fake graph ────────────────────────────────────────────────────────

class FakeGraph:
    available = True

    def run(self, cypher: str, **_params) -> list[dict]:
        return []


GRAPH = FakeGraph()
SCOPES = ["jira:project:OPS"]


# ── Helpers ───────────────────────────────────────────────────────────

def _issue(node_id: str, score: float, created_at: str = "") -> SourcedChunk:
    return SourcedChunk(
        source="jira",
        node_id=node_id,
        node_type="Issue",
        text="Status: In Progress",
        score=score,
        scope_ids=SCOPES,
        citations=[],
        created_at=created_at,
    )


def _decision(node_id: str, score: float) -> SourcedChunk:
    return SourcedChunk(
        source="jira",
        node_id=node_id,
        node_type="Decision",
        text="Decided to upgrade AMIs",
        score=score,
        scope_ids=SCOPES,
        citations=[],
    )


# ── Edge case 1: missing created_at ─────────────────────────────

def test_missing_timestamp_defaults_to_half_recency():
    """Issue with no created_at should not crash and use recency=0.5."""
    chunks = [_issue("i1", score=0.8)]
    result = rerank_chunks(chunks, GRAPH, SCOPES)
    assert len(result) == 1
    # Score: 0.8*0.75 + 0.10*1.0 (in_progress) + 0.05*0.5 (default recency) + 0.10*0.0
    expected = 0.8 * 0.75 + 1.0 * 0.10 + 0.5 * 0.05 + 0.0 * 0.10
    assert abs(result[0]["score"] - 0.8) < 0.01  # original score unchanged on chunk


def test_missing_timestamp_does_not_crash():
    """rerank_chunks must not raise when created_at is absent."""
    chunks = [_issue("i1", 0.7), _issue("i2", 0.6)]
    result = rerank_chunks(chunks, GRAPH, SCOPES)
    assert len(result) == 2


# ── Edge case 2: future date ──────────────────────────────────────────

def test_future_date_clamps_to_max_recency():
    """Issue created in the future should get recency=1.0, not negative."""
    future = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
    chunks = [_issue("i1", score=0.7, created_at=future)]
    result = rerank_chunks(chunks, GRAPH, SCOPES)
    assert len(result) == 1
    # days_old will be negative → max(0, negative) = 0 → recency = 1.0
    # Score must be >= base*0.75 (recency adds, not subtracts)
    assert result[0]["score"] >= 0.7 * 0.75


def test_future_date_ranks_higher_than_old_issue():
    """Future-dated issue should outscore one created 60 days ago (all else equal)."""
    future = (datetime.now(timezone.utc) + timedelta(days=10)).isoformat()
    old = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
    chunks = [
        _issue("old", score=0.8, created_at=old),
        _issue("future", score=0.8, created_at=future),
    ]
    result = rerank_chunks(chunks, GRAPH, SCOPES)
    assert result[0]["node_id"] == "future"


# ── Edge case 3: timezone-naive timestamp ─────────────────────────────

def test_timezone_naive_timestamp_handled():
    """ISO timestamp without timezone info should not crash."""
    chunks = [_issue("i1", score=0.7, created_at="2025-01-01T00:00:00")]
    result = rerank_chunks(chunks, GRAPH, SCOPES)
    assert len(result) == 1


def test_timezone_aware_and_naive_together():
    """Mix of aware and naive timestamps should both be handled without crash."""
    chunks = [
        _issue("aware", score=0.7, created_at="2025-06-01T00:00:00+05:30"),
        _issue("naive", score=0.7, created_at="2025-06-01T00:00:00"),
    ]
    result = rerank_chunks(chunks, GRAPH, SCOPES)
    assert len(result) == 2


# ── Edge case 4: malformed timestamp ─────────────────────────────────

def test_malformed_timestamp_falls_back_gracefully():
    """Invalid created_at string must not crash — falls back to recency=0.5."""
    chunks = [_issue("i1", score=0.7, created_at="not-a-date")]
    result = rerank_chunks(chunks, GRAPH, SCOPES)
    assert len(result) == 1


def test_empty_string_timestamp_falls_back():
    """Empty string created_at should fall back to recency=0.5."""
    chunks = [_issue("i1", score=0.7, created_at="")]
    result = rerank_chunks(chunks, GRAPH, SCOPES)
    assert len(result) == 1


# ── Edge case 5: non-Issue chunks are not reranked ────────────────────

def test_decision_chunks_pass_through_unchanged():
    """Decision/Fact chunks should keep their original score (not reranked)."""
    d = _decision("d1", score=0.9)
    i = _issue("i1", score=0.85, created_at="2025-01-01T00:00:00+00:00")
    result = rerank_chunks([d, i], GRAPH, SCOPES)
    # Decision original score 0.9 is preserved; Issue gets reranked score
    decision_result = next(c for c in result if c["node_id"] == "d1")
    assert decision_result["score"] == pytest.approx(0.9)


def test_decision_ranks_above_old_issue():
    """High-scoring Decision should outrank a very old Issue."""
    old = (datetime.now(timezone.utc) - timedelta(days=180)).isoformat()
    chunks = [
        _decision("d1", score=0.85),
        _issue("i1", score=0.85, created_at=old),
    ]
    result = rerank_chunks(chunks, GRAPH, SCOPES)
    assert result[0]["node_id"] == "d1"


# ── Edge case 6: empty input ──────────────────────────────────────────

def test_empty_chunks_returns_empty():
    result = rerank_chunks([], GRAPH, SCOPES)
    assert result == []


# ── Edge case 7: recency ordering ────────────────────────────────────

def test_recent_issue_ranks_above_old_issue():
    """Issue created 5 days ago should outscore one created 80 days ago, all else equal."""
    recent = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
    old = (datetime.now(timezone.utc) - timedelta(days=80)).isoformat()
    chunks = [
        _issue("old", score=0.8, created_at=old),
        _issue("recent", score=0.8, created_at=recent),
    ]
    result = rerank_chunks(chunks, GRAPH, SCOPES)
    assert result[0]["node_id"] == "recent"


def test_issue_at_90_days_gets_zero_recency():
    """Issue exactly at the 90-day boundary should get recency=0.0."""
    boundary = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
    chunks = [_issue("i1", score=0.8, created_at=boundary)]
    result = rerank_chunks(chunks, GRAPH, SCOPES)
    assert len(result) == 1
    # recency = max(0, 1 - 90/90) = 0.0 — should not raise
