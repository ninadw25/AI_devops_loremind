"""
W-X: cross-source CORROBORATES linking.

Tests that the deduplicator captures cross-source pairs as CORROBORATES
candidates and that unified_builder emits the correct Cypher for them.

CORROBORATES invariants:
  - Only for _DEDUP_TYPES (Decision/Task/Fact/Event), never Person/Org/Topic
  - Only when source guard is active (source_by_node_id provided)
  - Only pairs that met the merge threshold (otherwise they're just different)
  - node_id_a < node_id_b (lexicographic) for idempotency
  - source_a != source_b (always cross-source by definition)
  - Below-threshold cross-source pairs are NOT captured
"""

import json

import numpy as np
import pytest

from pipeline.deduplicator import cluster_nodes_by_type
from builders.unified_builder import generate_corroborates_cypher


# ── helpers ─────────────────────────────────────────────────────────────────

def _unit(v):
    a = np.array(v, dtype=np.float32)
    return a / np.linalg.norm(a)


def _embs(*vecs):
    return np.array([_unit(v) for v in vecs], dtype=np.float32)


# ── deduplicator: capture cross-source pairs ────────────────────────────────

def test_corroborates_captured_for_high_sim_cross_source():
    """A Slack Decision and a Gmail Decision with cosine ~ 1.0 (nearly identical)
    should yield one CORROBORATES pair and NOT be merged."""
    nodes = [
        {"node_id": "dec:slack1", "type": "Decision", "thread_id": "slack:session:C1:1"},
        {"node_id": "dec:gmail1", "type": "Decision", "thread_id": "gmail_t1"},
    ]
    # Identical vectors → cosine = 1.0
    embs = _embs([1.0, 0.0], [1.0, 0.0])
    src = {"dec:slack1": "slack", "dec:gmail1": "gmail"}

    clusters, corroborates = cluster_nodes_by_type(
        embs, nodes,
        same_thread_threshold=0.92,
        cross_thread_threshold=0.90,
        source_by_node_id=src,
    )

    # No merge (different sources)
    multi = [c for c in clusters if len(c) > 1]
    assert multi == []

    # One CORROBORATES pair
    assert len(corroborates) == 1
    pair = corroborates[0]
    assert {pair["node_id_a"], pair["node_id_b"]} == {"dec:slack1", "dec:gmail1"}
    assert pair["source_a"] != pair["source_b"]
    assert pair["similarity"] >= 0.99
    assert pair["type_a"] == "Decision"
    assert pair["type_b"] == "Decision"


def test_below_threshold_cross_source_not_captured():
    """Cross-source pair below the cross-thread threshold → neither merge nor CORROBORATES."""
    nodes = [
        {"node_id": "dec:s", "type": "Decision", "thread_id": "slack:session:C1:1"},
        {"node_id": "dec:g", "type": "Decision", "thread_id": "gmail_t1"},
    ]
    # Orthogonal vectors → cosine = 0.0 (well below any threshold)
    embs = _embs([1.0, 0.0], [0.0, 1.0])
    src = {"dec:s": "slack", "dec:g": "gmail"}

    _, corroborates = cluster_nodes_by_type(
        embs, nodes,
        same_thread_threshold=0.92,
        cross_thread_threshold=0.90,
        source_by_node_id=src,
    )

    assert corroborates == []


def test_same_source_high_sim_is_merge_not_corroborates():
    """Two Slack Decisions with identical embeddings → merge (same source), no CORROBORATES."""
    nodes = [
        {"node_id": "dec:a", "type": "Decision", "thread_id": "slack:session:C1:1"},
        {"node_id": "dec:b", "type": "Decision", "thread_id": "slack:session:C2:1"},
    ]
    embs = _embs([1.0, 0.0], [1.0, 0.0])
    src = {"dec:a": "slack", "dec:b": "slack"}

    clusters, corroborates = cluster_nodes_by_type(
        embs, nodes,
        same_thread_threshold=0.92,
        cross_thread_threshold=0.90,
        source_by_node_id=src,
    )

    multi = [c for c in clusters if len(c) > 1]
    assert len(multi) == 1   # merged
    assert corroborates == []


def test_corroborates_only_for_dedup_types():
    """Person/Organization/Topic nodes never generate CORROBORATES — they
    are spine nodes (identity-merged or topic-clustered), not content."""
    nodes = [
        {"node_id": "person:1", "type": "Person",       "thread_id": "slack:session:C1:1"},
        {"node_id": "person:2", "type": "Person",       "thread_id": "gmail_t1"},
        {"node_id": "org:1",    "type": "Organization", "thread_id": "slack:session:C1:1"},
        {"node_id": "org:2",    "type": "Organization", "thread_id": "gmail_t1"},
        {"node_id": "topic:1",  "type": "Topic",        "thread_id": "slack:session:C1:1"},
        {"node_id": "topic:2",  "type": "Topic",        "thread_id": "gmail_t1"},
    ]
    embs = _embs(
        [1.0, 0.0], [1.0, 0.0],   # person pair — identical
        [1.0, 0.0], [1.0, 0.0],   # org pair — identical
        [1.0, 0.0], [1.0, 0.0],   # topic pair — identical
    )
    src = {
        "person:1": "slack", "person:2": "gmail",
        "org:1":    "slack", "org:2":    "gmail",
        "topic:1":  "slack", "topic:2":  "gmail",
    }

    _, corroborates = cluster_nodes_by_type(
        embs, nodes,
        same_thread_threshold=0.92,
        cross_thread_threshold=0.90,
        source_by_node_id=src,
    )

    assert corroborates == []


def test_corroborates_lexicographic_order():
    """node_id_a < node_id_b (lexicographic) regardless of which row/col the
    pair appears in — ensures MERGE is idempotent on repeated builder runs."""
    nodes = [
        {"node_id": "dec:zzz", "type": "Decision", "thread_id": "slack:session:C1:1"},
        {"node_id": "dec:aaa", "type": "Decision", "thread_id": "gmail_t1"},
    ]
    embs = _embs([1.0, 0.0], [1.0, 0.0])
    src = {"dec:zzz": "slack", "dec:aaa": "gmail"}

    _, corroborates = cluster_nodes_by_type(
        embs, nodes,
        same_thread_threshold=0.92,
        cross_thread_threshold=0.90,
        source_by_node_id=src,
    )

    assert len(corroborates) == 1
    pair = corroborates[0]
    assert pair["node_id_a"] == "dec:aaa"   # lexicographically first
    assert pair["node_id_b"] == "dec:zzz"


def test_three_sources_all_corroborate():
    """Gmail + Slack + Jira all describe the same Decision.
    Expected: 3 CORROBORATES pairs (all cross-source combinations),
    0 merges (each pair is from a different source)."""
    nodes = [
        {"node_id": "dec:g", "type": "Decision", "thread_id": "gmail_t1"},
        {"node_id": "dec:s", "type": "Decision", "thread_id": "slack:session:C1:1"},
        {"node_id": "dec:j", "type": "Decision", "thread_id": "jira:issue:OPS-1"},
    ]
    embs = _embs([1.0, 0.0], [1.0, 0.0], [1.0, 0.0])
    src = {"dec:g": "gmail", "dec:s": "slack", "dec:j": "jira"}

    clusters, corroborates = cluster_nodes_by_type(
        embs, nodes,
        same_thread_threshold=0.92,
        cross_thread_threshold=0.90,
        source_by_node_id=src,
    )

    assert [c for c in clusters if len(c) > 1] == []   # no merges
    assert len(corroborates) == 3
    pair_sources = {(p["source_a"], p["source_b"]) for p in corroborates}
    # All three cross-source combinations present
    assert len(pair_sources) == 3


# ── builder: Cypher emission ─────────────────────────────────────────────────

def test_generate_corroborates_cypher_empty():
    cypher = generate_corroborates_cypher([])
    assert "CORROBORATES" not in cypher or cypher.startswith("//")


def test_generate_corroborates_cypher_shape():
    pairs = [
        {
            "node_id_a": "dec:aaa",
            "node_id_b": "dec:zzz",
            "similarity": 0.9876,
            "type_a": "Decision",
            "type_b": "Decision",
            "source_a": "gmail",
            "source_b": "slack",
        }
    ]
    cypher = generate_corroborates_cypher(pairs)

    assert "MATCH" in cypher
    assert "MERGE" in cypher
    assert "CORROBORATES" in cypher
    assert "dec:aaa" in cypher
    assert "dec:zzz" in cypher
    assert "0.9876" in cypher
    assert "gmail" in cypher
    assert "slack" in cypher
    # Must be a valid-looking statement ending with semicolon
    statements = [l.strip() for l in cypher.splitlines() if l.strip().endswith(";")]
    assert len(statements) >= 1


def test_generate_corroborates_cypher_multiple_pairs():
    pairs = [
        {"node_id_a": f"fact:{i}a", "node_id_b": f"fact:{i}b",
         "similarity": 0.95, "type_a": "Fact", "type_b": "Fact",
         "source_a": "slack", "source_b": "gmail"}
        for i in range(5)
    ]
    cypher = generate_corroborates_cypher(pairs)
    assert cypher.count("MERGE") == 5
    assert cypher.count("CORROBORATES") >= 5


def test_generate_corroborates_uses_correct_id_field():
    """Each node type uses its proper ID field (fact_id, decision_id, etc.)."""
    for ntype, id_field in [("Decision", "decision_id"), ("Fact", "fact_id"),
                             ("Task", "task_id"), ("Event", "event_id")]:
        pairs = [{"node_id_a": "x:1", "node_id_b": "x:2", "similarity": 0.95,
                  "type_a": ntype, "type_b": ntype,
                  "source_a": "slack", "source_b": "gmail"}]
        cypher = generate_corroborates_cypher(pairs)
        assert id_field in cypher, f"Expected {id_field} for type {ntype}"
