"""
domain/retrieval/person.py
──────────────────────────
Person name extraction, graph lookup, and person-scoped decision retrieval.

Two entry points:
  resolve_persons(client, question)
      → list of Person nodes matched from the question text

  person_scoped_decisions(client, person_id, embedding, user_groups)
      → Decisions by that person, cosine-ranked against the query embedding
"""

from __future__ import annotations

import re

from core.logging import get_logger
from core.observability import observe
from core.types import KnowledgeNode
from core.types import GraphRepository

log = get_logger(__name__)

_NAME_RE = re.compile(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)\b")


# ── Cypher ────────────────────────────────────────────────────────────

_FIND_PERSONS_CYPHER = """
MATCH (p:Person)
WHERE toLower(p.name) CONTAINS toLower($fragment)
RETURN p.person_id     AS person_id,
       p.name          AS name,
       p.primary_email AS primary_email,
       p.is_internal   AS is_internal
ORDER BY p.is_internal DESC, coalesce(p.total_activity, 0) DESC
LIMIT $limit
"""

_EXPAND_PERSON_CYPHER = """
MATCH (p:Person {person_id: $pid})
CALL (p) {
    OPTIONAL MATCH (p)-[:WORKS_AT]->(o:Organization)
    RETURN head(collect(DISTINCT o.name)) AS organization
}
CALL (p) {
    OPTIONAL MATCH (p)-[:DECIDED]->(d:Decision)
    RETURN [x IN collect(DISTINCT d.description)
            WHERE x IS NOT NULL][..10] AS decisions_made
}
CALL (p) {
    OPTIONAL MATCH (task:Task)-[:ASSIGNED_TO]->(p)
    RETURN [x IN collect(DISTINCT task.description)
            WHERE x IS NOT NULL][..10] AS tasks_assigned
}
CALL (p) {
    OPTIONAL MATCH (f:Fact)-[:STATED_BY]->(p)
    RETURN [x IN collect(DISTINCT f.description)
            WHERE x IS NOT NULL][..10] AS facts_stated
}
CALL (p) {
    OPTIONAL MATCH (p)-[:PARTICIPATED_IN]->(e:Event)
    RETURN [x IN collect(DISTINCT e.description)
            WHERE x IS NOT NULL][..5] AS events
}
RETURN
    p.name          AS name,
    p.primary_email AS email,
    p.is_internal   AS is_internal,
    organization,
    decisions_made,
    tasks_assigned,
    facts_stated,
    events
"""

_EXPAND_PERSON_ORG_CYPHER = """
MATCH (p:Person {person_id: $pid})
CALL (p) {
    OPTIONAL MATCH (p)-[:REPORTED_TO]->(mgr:Person)
    RETURN head(collect(DISTINCT mgr.name)) AS reports_to
}
CALL (p) {
    OPTIONAL MATCH (sub:Person)-[:REPORTED_TO]->(p)
    RETURN [x IN collect(DISTINCT sub.name) WHERE x IS NOT NULL][..10] AS direct_reports
}
CALL (p) {
    OPTIONAL MATCH (p)-[:WORKS_AT]->(o:Organization)
    RETURN head(collect(DISTINCT o.name)) AS organization
}
RETURN reports_to, direct_reports, organization
"""

_PERSON_SCOPED_DECISIONS_CYPHER = """
MATCH (p:Person {person_id: $pid})-[:DECIDED]->(d:Decision)
WHERE any(g IN d.access_groups WHERE g IN $user_groups)
  AND d.embedding IS NOT NULL
WITH d, vector.similarity.cosine(d.embedding, $embedding) AS sim
WHERE sim IS NOT NULL
OPTIONAL MATCH (actor:Person)-[:DECIDED]->(d)
OPTIONAL MATCH (d)-[:BECAUSE]->(f:Fact)
OPTIONAL MATCH (d)-[:REGARDING]->(t:Topic)
WITH d, sim,
     [n IN collect(DISTINCT actor.name) WHERE n IS NOT NULL][..5]    AS decided_by,
     [e IN collect(DISTINCT f.description) WHERE e IS NOT NULL][..3] AS evidence,
     [n IN collect(DISTINCT t.name) WHERE n IS NOT NULL][..5]        AS topics
RETURN
    d.decision_id       AS node_id,
    'Decision'          AS type,
    d.description       AS description,
    d.source_thread_id  AS source_thread_id,
    d.sensitivity_label AS sensitivity_label,
    sim                 AS score,
    decided_by, evidence, topics
ORDER BY sim DESC LIMIT $limit
"""


# ── Public API ────────────────────────────────────────────────────────

@observe(name="retrieval.resolve-persons")
def resolve_persons(
    client: GraphRepository,
    question: str,
) -> list[KnowledgeNode]:
    """Extract capitalised names from the question and match against Person nodes."""
    if not client.available:
        return []

    candidate_names = _NAME_RE.findall(question)
    seen_ids: set[str] = set()
    persons: list[KnowledgeNode] = []

    for name in candidate_names:
        rows = client.run(_FIND_PERSONS_CYPHER, fragment=name, limit=2) or []
        for p in rows:
            pid = p.get("person_id", "")
            if pid and pid not in seen_ids:
                seen_ids.add(pid)
                persons.append(p)

    return persons


@observe(name="retrieval.expand-person")
def expand_person_profile(
    client: GraphRepository,
    person_id: str,
) -> KnowledgeNode | None:
    """Fetch the full profile for a person node."""
    rows = client.run(_EXPAND_PERSON_CYPHER, pid=person_id)
    if not rows:
        return None
    profile = rows[0]
    org_rows = client.run(_EXPAND_PERSON_ORG_CYPHER, pid=person_id)
    if org_rows:
        profile.update(org_rows[0])
    return profile


@observe(name="retrieval.person-decisions")
def person_scoped_decisions(
    client: GraphRepository,
    person_id: str,
    embedding: list[float],
    user_groups: list[str],
    limit: int = 15,
) -> list[KnowledgeNode]:
    """Return decisions made by a specific person, cosine-ranked against the query."""
    rows = client.run(
        _PERSON_SCOPED_DECISIONS_CYPHER,
        pid=person_id,
        embedding=embedding,
        user_groups=user_groups,
        limit=limit,
    ) or []
    for r in rows:
        r["_expanded"] = True
    return rows


@observe(name="retrieval.search-people")
def search_people(
    client: GraphRepository,
    fragment: str,
    limit: int = 10,
) -> list[KnowledgeNode]:
    """Fuzzy name search over Person nodes (spine — ungated), for UI pickers."""
    fragment = (fragment or "").strip()
    if not fragment or not client.available:
        return []
    return client.run(_FIND_PERSONS_CYPHER, fragment=fragment, limit=limit) or []
