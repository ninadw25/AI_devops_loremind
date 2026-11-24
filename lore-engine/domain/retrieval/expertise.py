"""
domain/retrieval/expertise.py
─────────────────────────────
Expert Search — "who knows about X", ranked by real, scope-visible activity.

    find_experts(client, topic, scopes) → people connected to a topic through
    DECIDED / STATED_BY / ASSIGNED_TO evidence the caller is allowed to see,
    ranked deterministically.

Design rules (see docs/OFFBOARDING_ONBOARDING_DESIGN.md, CLAUDE.md, domain/scopes.py):

* Person / Topic are spine nodes — never scope-gated. We gate the *evidence*
  content nodes (Decision / Fact / Task) with scope_gate(), so a caller never
  ranks someone via evidence they are not allowed to see.
* Primary ranking key is the count of *visible* evidence, so every returned
  expert is backed by receipts the caller can actually open. EXPERT_IN.count is
  a precomputed rollup that is NOT scope-aware, so it is used only as a
  secondary signal, never on its own.
* Ranking is a deterministic aggregate — no ML, no LLM. Honours the
  "deterministic ranking" rule.

This is the primitive the offboarding report (sole-expert detection) and the
continuity radar reuse.
"""

from __future__ import annotations

from core.logging import get_logger
from core.observability import observe
from core.types import GraphRepository, UserScopes
from domain.scopes import scope_gate, scope_params

log = get_logger(__name__)

# How many evidence receipts to return per expert (full count is still reported
# in evidence_count). Trusted literal — interpolated into Cypher, never user input.
_EVIDENCE_SAMPLE = 8


# ── Cypher ────────────────────────────────────────────────────────────
# Three evidence shapes connect a Person to a Topic; each is scope-gated on the
# content node. Person/Topic themselves are spine and ungated. Built once at
# import; the {scope_gate('c')} fragment only references the trusted $allowed_scopes
# / $super_admin params, safe to interpolate.

_FIND_EXPERTS_CYPHER = f"""
CALL {{
    MATCH (t:Topic)<-[:REGARDING]-(c:Decision)<-[:DECIDED]-(p:Person)
    WHERE toLower(t.name) CONTAINS toLower($topic) AND {scope_gate('c')}
    RETURN p AS person, c.decision_id AS ev_id, 'Decision' AS ev_type,
           c.description AS ev_desc, c.source_thread_id AS ev_thread
    UNION
    MATCH (t:Topic)<-[:REGARDING]-(c:Fact)-[:STATED_BY]->(p:Person)
    WHERE toLower(t.name) CONTAINS toLower($topic) AND {scope_gate('c')}
    RETURN p AS person, c.fact_id AS ev_id, 'Fact' AS ev_type,
           c.description AS ev_desc, c.source_thread_id AS ev_thread
    UNION
    MATCH (t:Topic)<-[:REGARDING]-(c:Task)-[:ASSIGNED_TO]->(p:Person)
    WHERE toLower(t.name) CONTAINS toLower($topic) AND {scope_gate('c')}
    RETURN p AS person, c.task_id AS ev_id, 'Task' AS ev_type,
           c.description AS ev_desc, c.source_thread_id AS ev_thread
}}
WITH person, collect(DISTINCT {{
        id: ev_id, type: ev_type, description: ev_desc, thread_id: ev_thread
     }}) AS all_ev
WITH person, all_ev, size(all_ev) AS evidence_count
WHERE evidence_count > 0
CALL (person) {{
    OPTIONAL MATCH (person)-[ei:EXPERT_IN]->(et:Topic)
    WHERE toLower(et.name) CONTAINS toLower($topic)
    RETURN sum(coalesce(ei.count, 0)) AS expert_weight
}}
RETURN person.person_id                     AS person_id,
       person.name                          AS name,
       person.primary_email                 AS email,
       coalesce(person.is_internal, false)  AS is_internal,
       evidence_count                       AS evidence_count,
       coalesce(expert_weight, 0)           AS expert_weight,
       coalesce(person.total_activity, 0)   AS total_activity,
       all_ev[..{_EVIDENCE_SAMPLE}]         AS evidence
ORDER BY evidence_count DESC, expert_weight DESC, total_activity DESC, person_id
LIMIT $limit
"""


# ── Public API ────────────────────────────────────────────────────────

@observe(name="retrieval.find-experts")
def find_experts(
    client: GraphRepository,
    topic: str,
    scopes: UserScopes,
    *,
    limit: int = 10,
) -> list[dict]:
    """Rank people by scope-visible evidence of expertise on ``topic``.

    Returns a list of rows (highest evidence first). Each row:
        person_id, name, email, is_internal,
        evidence_count, expert_weight, total_activity,
        evidence: [{id, type, description, thread_id}, ...]  (sampled)

    Empty when the topic is blank, the graph is down, or — fail-closed — the
    caller has no scopes (and is not super-admin / open-access).
    """
    topic = (topic or "").strip()
    if not topic or not client.available:
        return []
    if scopes.is_empty and not scopes.super_admin:
        return []

    rows = client.run(
        _FIND_EXPERTS_CYPHER,
        topic=topic,
        limit=limit,
        **scope_params(scopes),
    ) or []
    log.info("find_experts", topic=topic, experts=len(rows))
    return rows
