"""
domain/retrieval/unstructured.py
────────────────────────────────
The unstructured retrieval engine (Gmail / Slack / Outlook / Teams), exposed
as one entry point that returns the same SourcedChunk envelope the Jira engine
returns — so the orchestrator can run both concurrently and merge + rank them
uniformly. See docs/QUERY_PIPELINE_V2.md.

    retrieve_unstructured(graph, embedding, analysis, scopes, *, k, beam_width,
                          max_depth) -> list[SourcedChunk]

Internally: seed (vector + entity anchors) → frontier/beam expand → render each
content node into a SourcedChunk. Rendering is ontology-aware: a node's
attribution (who decided it / is assigned / stated it) is read from the spine
Person nodes reached during expansion, and cross-source corroboration is folded
into the chunk text. This keeps ontology knowledge inside the engine; the
orchestrator stays source-agnostic.

Scope gating + Jira exclusion happen inside the seed/frontier Cypher (fail-closed
by construction), so every chunk this returns is in-scope and non-Jira.
"""

from __future__ import annotations

from core.logging import get_logger
from core.observability import observe
from core.types import BeamCandidate, GraphRepository, QueryAnalysis, SourcedChunk, UserScopes
from domain.retrieval.frontier import expand
from domain.retrieval.ontology import CONTENT_LABELS
from domain.retrieval.seeds import entity_anchors, merge_seeds, vector_seeds
from domain.scopes import source_label

log = get_logger(__name__)

_MAX_DESC = 400
_ATTRIB_REL = {
    "DECIDED": "decided by",
    "ASSIGNED_TO": "assigned to",
    "STATED_BY": "stated by",
    "PARTICIPATED_IN": "involved",
}


def _trunc(text: object, n: int = _MAX_DESC) -> str:
    s = str(text or "")
    return s[:n] + ("…" if len(s) > n else "")


def _parent_id(c: BeamCandidate) -> str | None:
    return c.path[-2][1] if len(c.path) >= 2 else None


def _is_corroborated(c: BeamCandidate) -> bool:
    return c.path_count > 1 or any(rel == "CORROBORATES" for rel, _ in c.path)


def _attribution(persons: list[BeamCandidate]) -> dict[str, list[str]]:
    """Map a content node_id → ["decided by: X", …] from the spine Person nodes
    reached from it during expansion (parent + relation on the person's path)."""
    out: dict[str, list[str]] = {}
    for p in persons:
        parent = _parent_id(p)
        rel = p.path[-1][0] if p.path else ""
        if parent and rel in _ATTRIB_REL and p.name:
            out.setdefault(parent, []).append(f"{_ATTRIB_REL[rel]}: {p.name}")
    return out


def _render_chunk(c: BeamCandidate, attribution: dict[str, list[str]]) -> SourcedChunk:
    """Render a content BeamCandidate into a SourcedChunk.

    The text carries the description plus ontology attribution and a
    corroboration marker — this is what both the cross-encoder reranker scores
    and the synthesis LLM reads, so attribution improves person-query ranking.
    """
    parts = [f"[{c.node_type}] {_trunc(c.description)}"]
    attrib = list(dict.fromkeys(attribution.get(c.node_id, [])))
    if attrib:
        parts.append("; ".join(attrib))
    if _is_corroborated(c):
        parts.append("corroborated across sources")
    return SourcedChunk(
        source=source_label(c.scope_ids),
        node_id=c.node_id,
        node_type=c.node_type,
        text=" | ".join(parts),
        score=float(c.score),
        scope_ids=c.scope_ids,
        citations=[c.source_thread_id] if c.source_thread_id else [],
        created_at=c.created_at or "",
    )


@observe(name="retrieval.unstructured")
def retrieve_unstructured(
    graph: GraphRepository,
    embedding: list[float],
    analysis: QueryAnalysis,
    scopes: UserScopes,
    *,
    k: int,
    beam_width: int,
    max_depth: int,
) -> list[SourcedChunk]:
    """Seed → frontier-expand → render content nodes as SourcedChunks."""
    if not graph.available or (
        scopes.is_empty and not scopes.super_admin
    ):
        return []

    seeds = merge_seeds(
        vector_seeds(graph, embedding, scopes, k=k, time_range=analysis.time_range),
        entity_anchors(graph, analysis, scopes),
    )
    candidates, _diag = expand(
        graph, seeds, analysis, scopes,
        beam_width=beam_width, max_depth=max_depth,
    )

    persons = [c for c in candidates if c.node_type == "Person"]
    attribution = _attribution(persons)

    chunks = [
        _render_chunk(c, attribution)
        for c in candidates
        if c.node_type in CONTENT_LABELS
    ]
    log.info("unstructured_retrieve_done", chunks=len(chunks),
             candidates=len(candidates), persons=len(persons))
    return chunks
