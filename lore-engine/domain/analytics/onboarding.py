"""
domain/analytics/onboarding.py
──────────────────────────────
Graph reads behind the onboarding guided path (Screen 2) — a new hire learning
the predecessor's world.

    person_key_decisions(client, pid, scopes)
        → decisions the person made, scope-gated, WITH their BECAUSE rationale
          and REGARDING topics: the "key decisions & why" section.

    person_key_people(client, pid)
        → top COMMUNICATES_WITH contacts (spine, ungated): "people you'll work
          with, and roughly how much".

The predecessor's *content* (decisions + rationale facts) is scope-gated so the
path only ever shows what the new hire is permitted to see. Person / Topic are
ungated spine.

NOTE: the person-scoped "ask anything" (threading person_focus through the query
pipeline) and the package-as-authorization-boundary scope grant are deliberately
NOT here — they touch the query hot path and the (pending) package persistence,
and get their own pass. This module powers the deterministic path only.
"""

from __future__ import annotations

from core.logging import get_logger
from core.observability import observe
from core.types import GraphRepository, UserScopes
from domain.scopes import scope_gate, scope_params

log = get_logger(__name__)


def _blocked(client: GraphRepository, scopes: UserScopes) -> bool:
    if not client.available:
        return True
    return scopes.is_empty and not scopes.super_admin


# Decisions the person made, gated, with rationale (gated too) + topics (spine).
_KEY_DECISIONS_CYPHER = f"""
MATCH (p:Person {{person_id: $pid}})-[:DECIDED]->(d:Decision)
WHERE {scope_gate('d')}
CALL (d) {{
    OPTIONAL MATCH (d)-[:BECAUSE]->(f:Fact)
    WHERE {scope_gate('f')}
    RETURN [x IN collect(DISTINCT f.description) WHERE x IS NOT NULL][..3] AS rationale
}}
CALL (d) {{
    OPTIONAL MATCH (d)-[:REGARDING]->(t:Topic)
    RETURN [x IN collect(DISTINCT t.name) WHERE x IS NOT NULL][..5] AS topics
}}
RETURN d.decision_id      AS id,
       d.description       AS description,
       d.source_thread_id  AS thread_id,
       rationale,
       topics
ORDER BY d.created_at DESC
LIMIT $limit
"""

# Top people the person communicates with. Person is spine (ungated); weight is
# message volume from the comm graph.
_KEY_PEOPLE_CYPHER = """
MATCH (p:Person {person_id: $pid})-[r:COMMUNICATES_WITH]-(o:Person)
RETURN o.person_id                    AS person_id,
       o.name                         AS name,
       coalesce(o.is_internal, false) AS is_internal,
       coalesce(r.weight, 0)          AS weight
ORDER BY weight DESC, name
LIMIT $limit
"""


@observe(name="analytics.person-key-decisions")
def person_key_decisions(
    client: GraphRepository,
    person_id: str,
    scopes: UserScopes,
    *,
    limit: int = 20,
) -> list[dict]:
    """Scope-visible decisions by the person, with rationale + topics."""
    if not person_id or _blocked(client, scopes):
        return []
    return client.run(
        _KEY_DECISIONS_CYPHER, pid=person_id, limit=limit, **scope_params(scopes)
    ) or []


@observe(name="analytics.person-key-people")
def person_key_people(
    client: GraphRepository,
    person_id: str,
    *,
    limit: int = 15,
) -> list[dict]:
    """Top comm-graph contacts of the person (spine, ungated)."""
    if not person_id or not client.available:
        return []
    return client.run(_KEY_PEOPLE_CYPHER, pid=person_id, limit=limit) or []
