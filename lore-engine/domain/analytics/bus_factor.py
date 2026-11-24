"""
domain/analytics/bus_factor.py
──────────────────────────────
Continuity Radar — "which knowledge is stuck in one person's head right now?"

Two deterministic, scope-gated aggregates over the knowledge graph:

    topic_coverage(client, scopes)   → at-risk topics (fewest holders first),
                                       each with its holder(s) + open-evidence.
    coverage_summary(client, scopes) → headline counts (total / sole-held /
                                       at-risk) — the "N% single point of
                                       failure" metric.

A *holder* of a topic is a person with scope-visible evidence on it
(DECIDED a Decision / STATED a Fact / ASSIGNED a Task that is REGARDING the
topic). Person / Topic are ungated spine; the evidence content node is gated
with scope_gate(), so coverage is computed only from what the caller may see —
we never reveal that a topic is "sole-held" using evidence the caller can't open.

No LLM, no scores. Runs off the hot path (analytics), so cost is acceptable;
can be precomputed/cached later if needed.
"""

from __future__ import annotations

from core.logging import get_logger
from core.observability import observe
from core.types import GraphRepository, UserScopes
from domain.scopes import scope_gate, scope_params

log = get_logger(__name__)

_HOLDER_SAMPLE = 8  # trusted literal for the Cypher list slice

# Shared evidence shapes: a Person holds a Topic through gated content. Built
# once; scope_gate('c') only references the trusted $allowed_scopes/$super_admin.
_EVIDENCE_UNION = f"""
    MATCH (t:Topic)<-[:REGARDING]-(c:Decision)<-[:DECIDED]-(p:Person)
    WHERE {scope_gate('c')}
    RETURN t AS topic, p AS person, c.decision_id AS ev_id
    UNION
    MATCH (t:Topic)<-[:REGARDING]-(c:Fact)-[:STATED_BY]->(p:Person)
    WHERE {scope_gate('c')}
    RETURN t AS topic, p AS person, c.fact_id AS ev_id
    UNION
    MATCH (t:Topic)<-[:REGARDING]-(c:Task)-[:ASSIGNED_TO]->(p:Person)
    WHERE {scope_gate('c')}
    RETURN t AS topic, p AS person, c.task_id AS ev_id
"""

_TOPIC_COVERAGE_CYPHER = f"""
CALL {{
{_EVIDENCE_UNION}
}}
WITH topic, person, count(DISTINCT ev_id) AS person_evidence
WITH topic,
     collect({{person_id: person.person_id, name: person.name,
               is_internal: coalesce(person.is_internal, false),
               evidence_count: person_evidence}}) AS holders,
     sum(person_evidence) AS total_evidence
WITH topic, holders, size(holders) AS holder_count, total_evidence
WHERE holder_count >= 1 AND holder_count <= $max_holders
RETURN topic.topic_id           AS topic_id,
       topic.name               AS topic_name,
       holder_count             AS holder_count,
       holder_count = 1         AS sole_holder,
       total_evidence           AS total_evidence,
       holders[..{_HOLDER_SAMPLE}] AS holders
ORDER BY holder_count ASC, total_evidence DESC, topic_name
LIMIT $limit
"""

_COVERAGE_SUMMARY_CYPHER = f"""
CALL {{
{_EVIDENCE_UNION}
}}
WITH topic, count(DISTINCT person) AS holder_count
RETURN count(topic)                                          AS total_topics,
       sum(CASE WHEN holder_count = 1  THEN 1 ELSE 0 END)    AS sole_held,
       sum(CASE WHEN holder_count <= 2 THEN 1 ELSE 0 END)    AS at_risk
"""

_EMPTY_SUMMARY = {"total_topics": 0, "sole_held": 0, "at_risk": 0}


def _blocked(client: GraphRepository, scopes: UserScopes) -> bool:
    """Fail-closed short-circuit: graph down, or caller can see nothing."""
    if not client.available:
        return True
    return scopes.is_empty and not scopes.super_admin


@observe(name="analytics.topic-coverage")
def topic_coverage(
    client: GraphRepository,
    scopes: UserScopes,
    *,
    max_holders: int = 2,
    limit: int = 50,
) -> list[dict]:
    """At-risk topics (holder_count ≤ max_holders), fewest holders first.

    Each row: topic_id, topic_name, holder_count, sole_holder, total_evidence,
    holders:[{person_id, name, is_internal, evidence_count}] (sampled).
    """
    if _blocked(client, scopes):
        return []
    rows = client.run(
        _TOPIC_COVERAGE_CYPHER,
        max_holders=max_holders,
        limit=limit,
        **scope_params(scopes),
    ) or []
    log.info("topic_coverage", at_risk=len(rows), max_holders=max_holders)
    return rows


@observe(name="analytics.coverage-summary")
def coverage_summary(client: GraphRepository, scopes: UserScopes) -> dict:
    """Headline counts across all scope-visible topics:
    {total_topics, sole_held, at_risk}."""
    if _blocked(client, scopes):
        return dict(_EMPTY_SUMMARY)
    rows = client.run(_COVERAGE_SUMMARY_CYPHER, **scope_params(scopes)) or []
    return rows[0] if rows else dict(_EMPTY_SUMMARY)
