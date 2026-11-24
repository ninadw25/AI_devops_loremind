"""
domain/retrieval/seeds.py
─────────────────────────
Stage 3 of the unstructured query pipeline (docs/QUERY_PIPELINE_V2.md §3):
produce the frontier seed set for graph expansion.

Two seed sources, both deduped by stable id:

  vector_seeds(client, embedding, scopes, *, k, time_range)
      → ANN over knowledge_embeddings, scope- AND source-gated, time-filtered.
        Seeds carry their cosine score. These are content nodes.

  entity_anchors(client, analysis, scopes)
      → Person / Topic / Organization spine nodes resolved from the analysis
        entities. Anchors enter the frontier with score 1.0 so expansion is
        guaranteed to walk DECIDED / REGARDING / … out of them even when the
        question text embeds poorly. Spine nodes are NOT scope-gated (they carry
        no scopes); the content they reach IS gated during expansion.

Both return list[BeamCandidate] with hop=0, ready to seed frontier.expand().
"""

from __future__ import annotations

from core.logging import get_logger
from core.observability import observe
from core.types import BeamCandidate, GraphRepository, QueryAnalysis, UserScopes
from domain.retrieval.ontology import node_id_coalesce
from domain.scopes import scope_gate, scope_params, unstructured_source_filter

log = get_logger(__name__)


# ── Vector seeds ──────────────────────────────────────────────────────

# Content-only ANN: scope gate + Jira exclusion + optional time window.
_VECTOR_SEEDS_CYPHER = f"""
CALL db.index.vector.queryNodes('knowledge_embeddings', $k, $embedding)
YIELD node, score
WHERE {scope_gate('node')}
  AND {unstructured_source_filter('node')}
  AND ($since IS NULL OR node.created_at >= datetime($since))
  AND ($until IS NULL OR node.created_at <= datetime($until))
RETURN {node_id_coalesce('node')} AS node_id,
       labels(node)[0]            AS node_type,
       node.description           AS description,
       node.scope_ids             AS scope_ids,
       node.source_thread_id      AS source_thread_id,
       toString(node.created_at)  AS created_at,
       score
ORDER BY score DESC
LIMIT $k
"""


@observe(name="retrieval.vector-seeds")
def vector_seeds(
    client: GraphRepository,
    embedding: list[float],
    scopes: UserScopes,
    *,
    k: int,
    time_range=None,
) -> list[BeamCandidate]:
    """Top-k content seeds by vector similarity, fail-closed gated."""
    if not client.available or (
        scopes.is_empty and not scopes.super_admin
    ):
        return []
    since = time_range.start if time_range else None
    until = time_range.end if time_range else None
    rows = client.run(
        _VECTOR_SEEDS_CYPHER,
        k=k,
        embedding=embedding,
        since=since,
        until=until,
        **scope_params(scopes),
    ) or []
    seeds: list[BeamCandidate] = []
    for r in rows:
        nid = r.get("node_id")
        if not nid:
            continue
        seeds.append(BeamCandidate(
            node_id=nid,
            originating_seed=nid,
            score=float(r.get("score", 0.0)),
            hop=0,
            labels=[r.get("node_type", "")],
            path=[("SEED", nid)],
            node_type=r.get("node_type", ""),
            description=r.get("description") or "",
            source_thread_id=r.get("source_thread_id") or "",
            created_at=r.get("created_at") or "",
            scope_ids=r.get("scope_ids") or [],
        ))
    log.info("vector_seeds", count=len(seeds), k=k)
    return seeds


# ── Entity anchors ────────────────────────────────────────────────────

# Spine nodes are name-keyed and unscoped: match by case-insensitive name
# substring. Not gated (carry no scopes); the content reached from them is
# gated during expansion.
_ANCHOR_CYPHER = {
    "Person": """
        UNWIND $names AS nm
        MATCH (p:Person) WHERE toLower(p.name) CONTAINS toLower(nm)
        RETURN p.person_id AS node_id, p.name AS name, 'Person' AS node_type
        ORDER BY p.is_internal DESC, coalesce(p.total_activity, 0) DESC
        LIMIT $limit
    """,
    "Topic": """
        UNWIND $names AS nm
        MATCH (t:Topic) WHERE toLower(t.name) CONTAINS toLower(nm)
        RETURN t.topic_id AS node_id, t.name AS name, 'Topic' AS node_type
        LIMIT $limit
    """,
    "Organization": """
        UNWIND $names AS nm
        MATCH (o:Organization) WHERE toLower(o.name) CONTAINS toLower(nm)
        RETURN o.org_id AS node_id, o.name AS name, 'Organization' AS node_type
        LIMIT $limit
    """,
}


@observe(name="retrieval.entity-anchors")
def entity_anchors(
    client: GraphRepository,
    analysis: QueryAnalysis,
    scopes: UserScopes,
    *,
    limit: int = 5,
) -> list[BeamCandidate]:
    """Resolve analysis entities to spine anchor nodes (score 1.0, hop 0)."""
    if not client.available:
        return []
    wanted = (
        ("Person", analysis.persons),
        ("Topic", analysis.topics),
        ("Organization", analysis.orgs),
    )
    anchors: list[BeamCandidate] = []
    seen: set[str] = set()
    for label, names in wanted:
        names = [n for n in (names or []) if n.strip()]
        if not names:
            continue
        rows = client.run(_ANCHOR_CYPHER[label], names=names, limit=limit) or []
        for r in rows:
            nid = r.get("node_id")
            if not nid or nid in seen:
                continue
            seen.add(nid)
            anchors.append(BeamCandidate(
                node_id=nid,
                originating_seed=nid,
                score=1.0,            # anchors are guaranteed frontier entry points
                hop=0,
                labels=[label],
                path=[("ANCHOR", nid)],
                node_type=label,
                name=r.get("name") or "",
            ))
    log.info("entity_anchors", count=len(anchors))
    return anchors


def merge_seeds(*groups: list[BeamCandidate]) -> list[BeamCandidate]:
    """Union seed groups, deduping by node_id (first occurrence wins — vector
    seeds before anchors, so a node seeded both ways keeps its cosine score)."""
    out: list[BeamCandidate] = []
    seen: set[str] = set()
    for group in groups:
        for c in group:
            if c.node_id and c.node_id not in seen:
                seen.add(c.node_id)
                out.append(c)
    return out
