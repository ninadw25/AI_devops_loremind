"""
domain/analytics/offboarding.py
───────────────────────────────
Graph analysis behind the offboarding cockpit (Screen 1). Two scope-gated,
deterministic reads:

    person_owned_content(client, pid, scopes)
        → what the leaver owns, WITH ids + thread for receipts
          (decisions / tasks / facts / events), each content node scope-gated.

    person_risk_topics(client, pid, scopes)
        → topics where the leaver is the sole (or near-sole) visible holder —
          the "at risk if they leave" worklist — plus other_holders, the
          suggested inheritors, in the same pass.

"Sole holder" is inherently approximate (you can't query what was never
captured); the service frames these as *areas at risk*, never as "here is what
we don't know". Person / Topic are ungated spine; evidence content is gated, so
we never flag a topic as sole-held using evidence the caller can't see.
"""

from __future__ import annotations

from core.logging import get_logger
from core.observability import observe
from core.types import GraphRepository, UserScopes
from domain.analytics.bus_factor import _EVIDENCE_UNION  # shared topic-evidence union (same package)
from domain.scopes import scope_gate, scope_params

log = get_logger(__name__)

_OWNED_CAP = 50  # trusted literal cap per content bucket (Cypher list slice)

_EMPTY_OWNED = {"decisions": [], "tasks": [], "facts": [], "events": []}


# ── Cypher ────────────────────────────────────────────────────────────

# What the person owns, with receipts. Each bucket is independently scope-gated
# on the content node; Person is ungated spine. CALL (p) {} variable-scope
# subqueries mirror domain/retrieval/person.py.
_PERSON_OWNED_CYPHER = f"""
MATCH (p:Person {{person_id: $pid}})
CALL (p) {{
    OPTIONAL MATCH (p)-[:DECIDED]->(c:Decision)
    WHERE {scope_gate('c')}
    RETURN [x IN collect(DISTINCT c) | {{id: x.decision_id, type: 'Decision',
            description: x.description, thread_id: x.source_thread_id}}][..{_OWNED_CAP}] AS decisions
}}
CALL (p) {{
    OPTIONAL MATCH (c:Task)-[:ASSIGNED_TO]->(p)
    WHERE {scope_gate('c')}
    RETURN [x IN collect(DISTINCT c) | {{id: x.task_id, type: 'Task',
            description: x.description, thread_id: x.source_thread_id}}][..{_OWNED_CAP}] AS tasks
}}
CALL (p) {{
    OPTIONAL MATCH (c:Fact)-[:STATED_BY]->(p)
    WHERE {scope_gate('c')}
    RETURN [x IN collect(DISTINCT c) | {{id: x.fact_id, type: 'Fact',
            description: x.description, thread_id: x.source_thread_id}}][..{_OWNED_CAP}] AS facts
}}
CALL (p) {{
    OPTIONAL MATCH (p)-[:PARTICIPATED_IN]->(c:Event)
    WHERE {scope_gate('c')}
    RETURN [x IN collect(DISTINCT c) | {{id: x.event_id, type: 'Event',
            description: x.description, thread_id: x.source_thread_id}}][..{_OWNED_CAP}] AS events
}}
RETURN decisions, tasks, facts, events
"""

# At-risk topics for one person: reuse the org-wide topic-evidence union, group
# by topic to get every holder, then keep only topics this person holds where
# the total holder count is small. other_holders → suggested inheritors.
_PERSON_RISK_CYPHER = f"""
CALL {{
{_EVIDENCE_UNION}
}}
WITH topic, person.person_id AS pid, person.name AS pname, count(DISTINCT ev_id) AS ev_count
WITH topic,
     collect({{person_id: pid, name: pname, evidence_count: ev_count}}) AS holders,
     size(collect(DISTINCT pid)) AS holder_count
WITH topic, holders, holder_count,
     [h IN holders WHERE h.person_id = $pid] AS mine
WHERE size(mine) > 0 AND holder_count <= $max_holders
RETURN topic.topic_id                                   AS topic_id,
       topic.name                                       AS topic_name,
       holder_count                                     AS holder_count,
       holder_count = 1                                 AS sole_holder,
       head(mine).evidence_count                        AS my_evidence,
       [h IN holders WHERE h.person_id <> $pid][..5]    AS other_holders
ORDER BY holder_count ASC, my_evidence DESC, topic_name
LIMIT $limit
"""


def _blocked(client: GraphRepository, scopes: UserScopes) -> bool:
    if not client.available:
        return True
    return scopes.is_empty and not scopes.super_admin


# ── Public API ────────────────────────────────────────────────────────

@observe(name="analytics.person-owned-content")
def person_owned_content(
    client: GraphRepository,
    person_id: str,
    scopes: UserScopes,
) -> dict:
    """Scope-visible content the person owns: {decisions, tasks, facts, events},
    each a list of {id, type, description, thread_id} (receipts)."""
    if not person_id or _blocked(client, scopes):
        return dict(_EMPTY_OWNED)
    rows = client.run(_PERSON_OWNED_CYPHER, pid=person_id, **scope_params(scopes)) or []
    return rows[0] if rows else dict(_EMPTY_OWNED)


@observe(name="analytics.person-risk-topics")
def person_risk_topics(
    client: GraphRepository,
    person_id: str,
    scopes: UserScopes,
    *,
    max_holders: int = 2,
    limit: int = 30,
) -> list[dict]:
    """Topics the person holds where total holders ≤ max_holders (sole/near-sole).

    Each row: topic_id, topic_name, holder_count, sole_holder, my_evidence,
    other_holders:[{person_id, name, evidence_count}]  (suggested inheritors).
    """
    if not person_id or _blocked(client, scopes):
        return []
    rows = client.run(
        _PERSON_RISK_CYPHER,
        pid=person_id,
        max_holders=max_holders,
        limit=limit,
        **scope_params(scopes),
    ) or []
    log.info("person_risk_topics", person_id=person_id, at_risk=len(rows))
    return rows
