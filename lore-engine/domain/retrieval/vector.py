"""
domain/retrieval/vector.py
──────────────────────────
Vector search and per-hit graph expansion.

Entry points:
  vector_search(client, embedding, k, user_groups)
      → top-k KnowledgeNodes from the knowledge_embeddings index

  expand_hits(client, hits, user_groups)
      → same hits with graph edges resolved (decided_by, topics, etc.)
"""

from __future__ import annotations

from core.logging import get_logger
from core.observability import observe
from core.types import KnowledgeNode
from core.types import GraphRepository

log = get_logger(__name__)


# ── Cypher ────────────────────────────────────────────────────────────

_VECTOR_SEARCH_CYPHER = """
CALL db.index.vector.queryNodes('knowledge_embeddings', $k, $embedding)
YIELD node, score
WHERE any(g IN node.access_groups WHERE g IN $user_groups)
  AND ($node_types IS NULL OR labels(node)[0] IN $node_types)
RETURN
    coalesce(node.decision_id, node.task_id,
             node.fact_id, node.event_id)  AS node_id,
    labels(node)[0]                         AS type,
    node.description                        AS description,
    node.source_thread_id                   AS source_thread_id,
    node.department_tags                    AS department_tags,
    node.access_groups                      AS access_groups,
    node.sensitivity_label                  AS sensitivity_label,
    score
ORDER BY score DESC
LIMIT $limit
"""

_EXPAND_DECISION_CYPHER = """
MATCH (d:Decision {decision_id: $did})
OPTIONAL MATCH (actor)-[:DECIDED]->(d)
OPTIONAL MATCH (d)-[:BECAUSE]->(f:Fact)
OPTIONAL MATCH (f)-[:STATED_BY]->(src)
OPTIONAL MATCH (d)-[:REGARDING]->(t:Topic)
OPTIONAL MATCH (d)-[:SUBJECT_OF]->(subj)
RETURN
    d.description                                    AS decision,
    d.source_thread_id                               AS thread_id,
    d.sensitivity_label                              AS sensitivity,
    [n IN collect(DISTINCT actor.name) WHERE n IS NOT NULL][..5]                 AS decided_by,
    [e IN collect(DISTINCT {fact: f.description,
                             source: src.name}) WHERE e.fact IS NOT NULL][..5]    AS evidence,
    [n IN collect(DISTINCT t.name) WHERE n IS NOT NULL][..10]                    AS topics,
    coalesce(head(collect(DISTINCT subj.name)),
             head(collect(DISTINCT subj.description))) AS subject
"""

_EXPAND_TASK_CYPHER = """
MATCH (task:Task {task_id: $tid})
OPTIONAL MATCH (task)-[:ASSIGNED_TO]->(assignee)
OPTIONAL MATCH (task)-[:BECAUSE]->(f:Fact)
OPTIONAL MATCH (task)-[:REGARDING]->(t:Topic)
RETURN
    task.description                          AS task,
    task.source_thread_id                     AS thread_id,
    task.sensitivity_label                    AS sensitivity,
    [n IN collect(DISTINCT assignee.name) WHERE n IS NOT NULL][..5]     AS assigned_to,
    [f IN collect(DISTINCT f.description) WHERE f IS NOT NULL][..3]     AS context_facts,
    [n IN collect(DISTINCT t.name) WHERE n IS NOT NULL][..5]            AS topics
"""

_EXPAND_FACT_CYPHER = """
MATCH (f:Fact {fact_id: $fid})
OPTIONAL MATCH (f)-[:STATED_BY]->(src)
OPTIONAL MATCH (d:Decision)-[:BECAUSE]->(f)
OPTIONAL MATCH (actor)-[:DECIDED]->(d)
RETURN
    f.description                                           AS fact,
    f.source_thread_id                                      AS thread_id,
    [n IN collect(DISTINCT src.name) WHERE n IS NOT NULL][..3]              AS stated_by,
    [u IN collect(DISTINCT {decision: d.description,
                             by: actor.name}) WHERE u.decision IS NOT NULL][..5] AS used_in_decisions
"""

_EXPAND_EVENT_CYPHER = """
MATCH (e:Event {event_id: $eid})
OPTIONAL MATCH (p)-[:PARTICIPATED_IN]->(e)
OPTIONAL MATCH (e)-[:REGARDING]->(t:Topic)
OPTIONAL MATCH (d:Decision)
    WHERE d.source_thread_id = e.source_thread_id
      AND any(g IN d.access_groups WHERE g IN $user_groups)
RETURN
    e.description                               AS event,
    e.source_thread_id                          AS thread_id,
    [n IN collect(DISTINCT p.name) WHERE n IS NOT NULL][..10]            AS participants,
    [n IN collect(DISTINCT t.name) WHERE n IS NOT NULL][..5]             AS topics,
    [n IN collect(DISTINCT d.description) WHERE n IS NOT NULL][..5]      AS related_decisions
"""

_EXPANDERS = {
    "Decision": ("decision_id", _EXPAND_DECISION_CYPHER, "did"),
    "Task":     ("task_id",     _EXPAND_TASK_CYPHER,     "tid"),
    "Fact":     ("fact_id",     _EXPAND_FACT_CYPHER,     "fid"),
    "Event":    ("event_id",    _EXPAND_EVENT_CYPHER,    "eid"),
}


# ── Public API ────────────────────────────────────────────────────────

@observe(name="retrieval.vector-search")
def vector_search(
    client: GraphRepository,
    embedding: list[float],
    k: int,
    user_groups: list[str],
    node_types: list[str] | None = None,
) -> list[KnowledgeNode]:
    """Search Decision/Task/Fact/Event nodes by vector similarity."""
    k_query = k * 3 if node_types else k
    rows = client.run(
        _VECTOR_SEARCH_CYPHER,
        k=k_query,
        embedding=embedding,
        user_groups=user_groups,
        node_types=node_types,
        limit=k,
    )
    return rows or []


@observe(name="retrieval.expand-hits")
def expand_hits(
    client: GraphRepository,
    hits: list[KnowledgeNode],
    user_groups: list[str],
) -> list[KnowledgeNode]:
    """Resolve graph edges for each vector hit (decided_by, topics, etc.)."""
    expanded: list[KnowledgeNode] = []
    for hit in hits:
        ntype = hit.get("type", "")
        config = _EXPANDERS.get(ntype)
        if config and hit.get("node_id"):
            id_field, cypher, param_name = config
            rows = client.run(cypher, **{param_name: hit["node_id"]}, user_groups=user_groups)
            if rows:
                expanded.append({**hit, **rows[0], "_expanded": True})
                continue
        expanded.append(hit)
    return expanded
