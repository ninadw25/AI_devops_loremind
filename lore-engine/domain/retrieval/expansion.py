"""
domain/retrieval/expansion.py
──────────────────────────────
Graph-traversal enrichment after vector search.

Two strategies run on the vector hits to pull in contextually related
nodes without additional embedding calls:

  thread_siblings  — other knowledge nodes from the same email thread
  topic_expansion  — nodes linked via REGARDING edges to the same topics
"""

from __future__ import annotations

from core.logging import get_logger
from core.observability import observe
from core.types import KnowledgeNode
from core.types import GraphRepository

log = get_logger(__name__)


# ── Cypher ────────────────────────────────────────────────────────────

_THREAD_SIBLINGS_CYPHER = """
MATCH (n:KnowledgeNode {source_thread_id: $tid})
WHERE any(g IN n.access_groups WHERE g IN $user_groups)
  AND NOT coalesce(n.decision_id, n.task_id, n.fact_id, n.event_id) IN $exclude_ids
RETURN
    coalesce(n.decision_id, n.task_id, n.fact_id, n.event_id) AS node_id,
    labels(n)[0]   AS type,
    n.description  AS description,
    n.source_thread_id AS source_thread_id,
    n.sensitivity_label AS sensitivity_label,
    0.7            AS score
ORDER BY type
LIMIT $limit
"""

_TOPIC_EXPANSION_CYPHER = """
UNWIND $topics AS tname
MATCH (t:Topic)
WHERE toLower(t.name) CONTAINS toLower(tname)
WITH t LIMIT 5
MATCH (n:KnowledgeNode)-[:REGARDING]->(t)
WHERE any(g IN n.access_groups WHERE g IN $user_groups)
WITH n, t
OPTIONAL MATCH (actor)-[:DECIDED]->(n)
RETURN DISTINCT
    coalesce(n.decision_id, n.task_id, n.fact_id, n.event_id) AS node_id,
    labels(n)[0]   AS type,
    n.description  AS description,
    n.source_thread_id AS source_thread_id,
    n.sensitivity_label AS sensitivity_label,
    [x IN collect(DISTINCT actor.name) WHERE x IS NOT NULL][..3] AS decided_by,
    t.name         AS matched_topic,
    0.65           AS score
ORDER BY type
LIMIT $limit
"""


# ── Public API ────────────────────────────────────────────────────────

@observe(name="retrieval.thread-siblings")
def thread_siblings(
    client: GraphRepository,
    hits: list[KnowledgeNode],
    user_groups: list[str],
    per_thread_limit: int = 3,
    max_threads: int = 4,
) -> list[KnowledgeNode]:
    """Pull in sibling knowledge nodes from the same threads as the top hits.

    When a node is retrieved by vector search, other nodes from the same
    email thread are likely contextually related — same conversation, same
    decision context. This enriches the answer without a second embed call.
    """
    seen_threads: set[str] = set()
    seen_ids: set[str] = {h.get("node_id", "") for h in hits if h.get("node_id")}
    siblings: list[KnowledgeNode] = []

    for hit in hits[:max_threads]:
        tid = hit.get("source_thread_id") or hit.get("thread_id", "")
        if not tid or tid in seen_threads:
            continue
        seen_threads.add(tid)
        rows = client.run(
            _THREAD_SIBLINGS_CYPHER,
            tid=tid,
            user_groups=user_groups,
            exclude_ids=list(seen_ids),
            limit=per_thread_limit,
        ) or []
        for n in rows:
            nid = n.get("node_id")
            if nid and nid not in seen_ids:
                seen_ids.add(nid)
                siblings.append(n)

    return siblings


@observe(name="retrieval.topic-expansion")
def topic_expansion(
    client: GraphRepository,
    hits: list[KnowledgeNode],
    user_groups: list[str],
    seen_ids: set[str],
    limit: int = 6,
) -> list[KnowledgeNode]:
    """Find knowledge nodes linked to topics surfaced in the top hits.

    Topics are lightweight hub nodes — following REGARDING edges outward
    surfaces related decisions and facts that may not have scored highly
    in pure vector similarity.
    """
    all_topics: list[str] = []
    for node in hits[:6]:
        all_topics.extend(node.get("topics") or [])

    if not all_topics:
        return []

    unique_topics = list(dict.fromkeys(all_topics))[:5]
    rows = client.run(
        _TOPIC_EXPANSION_CYPHER,
        topics=unique_topics,
        user_groups=user_groups,
        limit=limit,
    ) or []

    result: list[KnowledgeNode] = []
    for n in rows:
        nid = n.get("node_id")
        if nid and nid not in seen_ids:
            seen_ids.add(nid)
            result.append(n)
    return result
