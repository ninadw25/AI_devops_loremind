"""
domain/retrieval/frontier.py
─────────────────────────────
Stage 4 of the unstructured query pipeline (docs/QUERY_PIPELINE_V2.md §4):
intent-aware, batched, frontier/beam expansion over the knowledge graph.

Ported from lore-graph-research/src/graph_expansion.py, corrected for the real
graph:
  - matches nodes per type on the indexed id field (no unified node_id);
  - gates content neighbours with the fail-closed scope gate + Jira-source
    filter inside the traversal Cypher, so out-of-scope / Jira nodes never enter
    the frontier, never score, never leak via paths;
  - traverses spine nodes (Person/Org/Topic) freely (they carry no scopes);
  - full ontology expansion rules + intent priors from QueryAnalysis.

Scoring (math unchanged from the prototype, proven in its evals):
    score = parent_score · relation_weight · hop_penalty
            · intent_relation_prior · intent_label_boost
  then per destination: best path wins; a convergence bonus rewards nodes
  reached by multiple paths; then beam-prune to beam_width.

Entry point:
  expand(client, seeds, analysis, scopes, *, beam_width, max_depth)
      → (list[BeamCandidate], diagnostics)
"""

from __future__ import annotations

from collections import defaultdict

from core.logging import get_logger
from core.observability import observe
from core.types import BeamCandidate, GraphRepository, QueryAnalysis, UserScopes
from domain.retrieval.ontology import ID_FIELD, SPINE_LABELS, node_id_coalesce
from domain.scopes import scope_gate, scope_params, unstructured_source_filter

log = get_logger(__name__)


# ── Expansion policy ──────────────────────────────────────────────────

# Undirected relations worth following FROM each label (mirrors the builder's
# edge ontology). Traversal is undirected so e.g. a Decision reaches its
# author via DECIDED and its rationale via BECAUSE.
EXPANSION_RULES: dict[str, list[str]] = {
    "Decision":     ["BECAUSE", "REGARDING", "SUBJECT_OF", "DECIDED", "CORROBORATES"],
    "Task":         ["ASSIGNED_TO", "REGARDING", "BECAUSE", "CORROBORATES"],
    "Fact":         ["STATED_BY", "REGARDING", "CORROBORATES"],
    "Event":        ["PARTICIPATED_IN", "REGARDING", "CORROBORATES"],
    "Person":       ["DECIDED", "ASSIGNED_TO", "STATED_BY", "PARTICIPATED_IN", "COMMUNICATES_WITH"],
    "Topic":        ["REGARDING", "SUBJECT_OF"],
    "Organization": ["REGARDING", "WORKS_AT"],
}

RELATION_WEIGHTS: dict[str, float] = {
    "BECAUSE": 1.0, "REGARDING": 0.9, "DECIDED": 0.8, "ASSIGNED_TO": 0.8,
    "CORROBORATES": 0.75, "STATED_BY": 0.7, "PARTICIPATED_IN": 0.6,
    "SUBJECT_OF": 0.6, "WORKS_AT": 0.4, "COMMUNICATES_WITH": 0.3,
}

HOP_PENALTIES: dict[int, float] = {1: 1.0, 2: 0.8, 3: 0.6, 4: 0.4, 5: 0.2}

# Intent → per-relation prior multipliers and per-label boosts (from
# lore-graph-research/src/intent_analyzer.py, extended for the v2 intents).
_INTENT_RELATION_PRIORS: dict[str, dict[str, float]] = {
    "PERSON_LOOKUP":     {"DECIDED": 1.5, "ASSIGNED_TO": 1.5, "STATED_BY": 1.4,
                          "PARTICIPATED_IN": 1.3, "COMMUNICATES_WITH": 1.3},
    "TASK_LOOKUP":       {"ASSIGNED_TO": 1.6, "REGARDING": 1.3},
    "DECISION_LOOKUP":   {"DECIDED": 1.5, "BECAUSE": 1.3, "REGARDING": 1.2},
    "REASONING":         {"BECAUSE": 1.8, "STATED_BY": 1.2},
    "EVENT_LOOKUP":      {"PARTICIPATED_IN": 1.5, "SUBJECT_OF": 1.4},
    "TOPIC_EXPLORATION": {"REGARDING": 1.4, "SUBJECT_OF": 1.4},
    "GLOBAL":            {"REGARDING": 1.3},
    "SUMMARY":           {},
}
_INTENT_LABEL_BOOSTS: dict[str, dict[str, float]] = {
    "PERSON_LOOKUP":     {"Person": 1.2},
    "TASK_LOOKUP":       {"Task": 1.15},
    "DECISION_LOOKUP":   {"Decision": 1.15},
    "REASONING":         {"Fact": 1.15, "Decision": 1.1},
    "EVENT_LOOKUP":      {"Event": 1.2},
    "TOPIC_EXPLORATION": {},
    "GLOBAL":            {},
    "SUMMARY":           {},
}

_CONVERGENCE_FACTOR = 0.1  # bonus = 1 + factor·(path_count − 1)


def _allowed_relations(labels: list[str]) -> set[str]:
    rels: set[str] = set()
    for lbl in labels:
        rels.update(EXPANSION_RULES.get(lbl, []))
    return rels


def _score(parent_score: float, relation: str, hop: int,
           neighbor_labels: list[str], rel_priors: dict, label_boosts: dict) -> tuple[float, float]:
    rel_w = RELATION_WEIGHTS.get(relation, 0.0)
    hop_w = HOP_PENALTIES.get(hop, 0.0)
    rel_prior = rel_priors.get(relation, 1.0)
    label_boost = max((label_boosts.get(lbl, 1.0) for lbl in neighbor_labels), default=1.0)
    return parent_score * rel_w * hop_w * rel_prior * label_boost, label_boost


# ── Batched neighbour fetch (one query per parent type per hop) ────────

def _neighbor_cypher(label: str, id_field: str) -> str:
    """Build the per-type traversal Cypher. label/id_field come from the trusted
    ontology map (never user input) so interpolation is injection-safe."""
    return f"""
    UNWIND $ids AS fid
    MATCH (n:{label} {{{id_field}: fid}})-[r]-(m)
    WHERE type(r) IN $rels
      AND (
        m:Person OR m:Organization OR m:Topic
        OR ( {scope_gate('m')} AND {unstructured_source_filter('m')} )
      )
    RETURN fid                       AS parent_id,
           type(r)                   AS rel,
           labels(m)[0]              AS neighbor_type,
           {node_id_coalesce('m')}   AS neighbor_id,
           m.description             AS description,
           m.name                    AS name,
           toString(m.created_at)    AS created_at,
           m.source_thread_id        AS source_thread_id,
           m.scope_ids               AS scope_ids
    """


@observe(name="retrieval.frontier-expand")
def expand(
    client: GraphRepository,
    seeds: list[BeamCandidate],
    analysis: QueryAnalysis,
    scopes: UserScopes,
    *,
    beam_width: int = 30,
    max_depth: int = 3,
) -> tuple[list[BeamCandidate], dict]:
    """Beam-expand from seeds. Returns (all kept candidates incl. seeds, diagnostics)."""
    diag: dict = {"seed_count": len(seeds), "neo4j_query_count": 0, "hops": {}}
    if not client.available or not seeds or (
        scopes.is_empty and not scopes.super_admin
    ):
        return list(seeds), diag

    rel_priors = _INTENT_RELATION_PRIORS.get(analysis.intent, {})
    label_boosts = _INTENT_LABEL_BOOSTS.get(analysis.intent, {})

    frontier: list[BeamCandidate] = list(seeds)
    visited: set[str] = {c.node_id for c in seeds}
    all_kept: list[BeamCandidate] = list(seeds)

    for hop in range(1, max_depth + 1):
        # Group frontier by node type → one indexed batched query per type.
        by_type: dict[str, list[BeamCandidate]] = defaultdict(list)
        for c in frontier:
            lbl = c.labels[0] if c.labels else ""
            if lbl in ID_FIELD:
                by_type[lbl].append(c)

        parent_by_id = {c.node_id: c for c in frontier}
        raw_neighbors: list[dict] = []
        for label, members in by_type.items():
            rels = _allowed_relations([label])
            if not rels:
                continue
            rows = client.run(
                _neighbor_cypher(label, ID_FIELD[label]),
                ids=[c.node_id for c in members],
                rels=list(rels),
                **scope_params(scopes),
            ) or []
            raw_neighbors.extend(rows)
            diag["neo4j_query_count"] += 1

        # Generate scored candidates for unvisited neighbours.
        candidates: list[BeamCandidate] = []
        for row in raw_neighbors:
            nid = row.get("neighbor_id")
            if not nid or nid in visited:
                continue
            parent = parent_by_id.get(row.get("parent_id"))
            if parent is None:
                continue
            ntype = row.get("neighbor_type", "")
            new_score, _ = _score(parent.score, row.get("rel", ""), hop,
                                   [ntype], rel_priors, label_boosts)
            if new_score <= 0:
                continue
            candidates.append(BeamCandidate(
                node_id=nid,
                originating_seed=parent.originating_seed,
                score=new_score,
                hop=hop,
                labels=[ntype],
                path=parent.path + [(row.get("rel", ""), nid)],
                node_type=ntype,
                description=row.get("description") or "",
                name=row.get("name") or "",
                source_thread_id=row.get("source_thread_id") or "",
                created_at=row.get("created_at") or "",
                scope_ids=row.get("scope_ids") or [],
            ))

        # Per-destination: keep best path, apply convergence bonus.
        by_dest: dict[str, list[BeamCandidate]] = defaultdict(list)
        for c in candidates:
            by_dest[c.node_id].append(c)

        deduped: list[BeamCandidate] = []
        convergence_events = 0
        for nid, paths in by_dest.items():
            best = max(paths, key=lambda c: c.score)
            best.path_count = len(paths)
            if len(paths) > 1:
                convergence_events += 1
                best.convergence_bonus = 1.0 + _CONVERGENCE_FACTOR * (len(paths) - 1)
                best.score *= best.convergence_bonus
            deduped.append(best)

        deduped.sort(key=lambda c: (c.score, c.node_id), reverse=True)
        frontier = deduped[:beam_width]
        if not frontier:
            diag["hops"][hop] = {"frontier": 0, "before_prune": len(deduped),
                                 "convergence_events": convergence_events}
            break

        all_kept.extend(frontier)
        visited.update(c.node_id for c in frontier)
        diag["hops"][hop] = {
            "frontier": len(frontier),
            "before_prune": len(deduped),
            "convergence_events": convergence_events,
        }
        log.info("frontier_hop", hop=hop, kept=len(frontier),
                 candidates=len(candidates), convergence=convergence_events)

    diag["expanded_count"] = len(all_kept)
    return all_kept, diag
