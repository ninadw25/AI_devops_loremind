"""
W5: the same-source merge guard in pipeline/deduplicator.py.

RBAC-critical: dedup must NEVER merge nodes from different sources, because a
merged node unions the ACL scopes of every session it appears in — mixing a
slack:channel: scope with a Gmail/Jira scope would collapse two ACL domains
into one node (a leak). Cross-source sameness is a CORROBORATES link, not a
merge. See docs/RBAC/SCOPE_FILTER_HANDOFF.md and SLACK_PIPELINE.md §0.2 / W5.
"""

import numpy as np

from pipeline.deduplicator import (
    _node_source_map,
    _thread_source,
    cluster_nodes_by_type,
)


# ── source inference ────────────────────────────────────────────────────────
def test_thread_source_from_scope_id():
    assert _thread_source({"scope_id": "slack:channel:C1"}) == "slack"
    assert _thread_source({"scope_id": "jira:project:ENG"}) == "jira"
    assert _thread_source({"scope_id": None}) == "gmail"          # Gmail has none
    assert _thread_source({}) == "gmail"


def test_thread_source_falls_back_to_thread_id_prefix():
    assert _thread_source({"thread_id": "slack:session:C1:111.2"}) == "slack"
    assert _thread_source({"thread_id": "18c4f9a0b1"}) == "gmail"  # opaque gmail id


def test_node_source_map_attributes_each_node():
    validated = {
        "slack:session:C1:1": {
            "scope_id": "slack:channel:C1",
            "triples": [{"subject": {"node_id": "dec:s", "type": "Decision"},
                         "predicate": "REGARDING",
                         "object": {"node_id": "topic:x", "type": "Topic"}}],
        },
        "gmail_t1": {  # no scope_id → gmail
            "triples": [{"subject": {"node_id": "dec:g", "type": "Decision"},
                         "predicate": "REGARDING",
                         "object": {"node_id": "topic:x", "type": "Topic"}}],
        },
    }
    src = _node_source_map(validated)
    assert src["dec:s"] == "slack"
    assert src["dec:g"] == "gmail"
    assert src["topic:x"] in ("slack", "gmail")  # whichever thread it appeared in first


# ── the guard itself ────────────────────────────────────────────────────────
def _identical_embeddings(n):
    """n unit vectors all pointing the same way → pairwise cosine = 1.0."""
    return np.tile(np.array([[1.0, 0.0]], dtype=np.float32), (n, 1))


_NODES = [
    {"node_id": "dec:a", "type": "Decision", "thread_id": "slack:session:C1:1"},
    {"node_id": "dec:b", "type": "Decision", "thread_id": "slack:session:C2:1"},
    {"node_id": "dec:c", "type": "Decision", "thread_id": "gmail_t1"},
]


def _multi_clusters(clusters):
    return sorted(
        (sorted(c) for c in clusters if len(c) > 1), key=len, reverse=True
    )


def test_guard_blocks_cross_source_merge_but_allows_same_source():
    """Three identical-content Decisions: two Slack (different channels) + one
    Gmail. With the guard, the two Slack ones merge; Gmail stays separate.
    The blocked Slack↔Gmail pair becomes a CORROBORATES entry."""
    src = {"dec:a": "slack", "dec:b": "slack", "dec:c": "gmail"}
    clusters, corroborates = cluster_nodes_by_type(
        _identical_embeddings(3), _NODES,
        same_thread_threshold=0.92, cross_thread_threshold=0.90,
        source_by_node_id=src,
    )
    multi = _multi_clusters(clusters)
    assert multi == [[0, 1]]           # dec:a + dec:b (both slack) only
    # dec:c (gmail, index 2) is NOT in any multi-node cluster
    assert all(2 not in c for c in multi)
    # Blocked cross-source pairs become CORROBORATES, not merges
    # dec:a↔dec:c and dec:b↔dec:c are cross-source candidates
    corr_ids = {(p["node_id_a"], p["node_id_b"]) for p in corroborates}
    assert len(corroborates) == 2
    assert ("dec:a", "dec:c") in corr_ids or ("dec:c", "dec:a") in corr_ids
    assert all(p["source_a"] != p["source_b"] for p in corroborates)


def test_without_guard_all_three_would_merge():
    """Control: identical embeddings with no source map → everything merges,
    no CORROBORATES pairs emitted (guard is off)."""
    clusters, corroborates = cluster_nodes_by_type(
        _identical_embeddings(3), _NODES,
        same_thread_threshold=0.92, cross_thread_threshold=0.90,
        source_by_node_id=None,
    )
    assert _multi_clusters(clusters) == [[0, 1, 2]]
    assert corroborates == []          # no guard → no CORROBORATES
