"""
domain/retrieval/rank.py
────────────────────────
Unified ranking stage for the consolidated pipeline (docs/QUERY_PIPELINE_V2.md
§5). Operates on the MERGED SourcedChunk pool from both engines (unstructured +
Jira), so a single cross-encoder makes relevance comparable across sources —
this is what lets us run both engines unconditionally and drop whatever isn't
pertinent here, rather than gating engines up front.

  rank_chunks(query, chunks, reranker, *, settings) -> RankResult

Steps:
  1. BM25 over chunk text; min-max normalize; hybrid = α·engine_score + β·bm25.
     (engine_score is each engine's own signal — frontier graph score for
     unstructured, vector/heuristic score for Jira — normalized within the pool.)
  2. Truncate to the cross-encoder candidate limit by hybrid score.
  3. Cross-encoder rerank; final = w1·rerank + w2·hybrid. Degrade to hybrid on
     RerankError so a reranker outage never fails the query.
  4. Token-budgeted cut. Each kept chunk's score is set to its final fused score.

Pure except for the injected RerankProvider call. BM25 is optional; if absent,
hybrid degrades to the normalized engine score.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from core.exceptions import RerankError
from core.logging import get_logger
from core.observability import observe
from core.settings import Settings
from core.types import RankedDoc, RerankDoc, RerankProvider, SourcedChunk

log = get_logger(__name__)


@dataclass
class RankResult:
    chunks: list[SourcedChunk] = field(default_factory=list)  # ranked, score = final fused
    diagnostics: dict = field(default_factory=dict)


def _minmax(values: list[float]) -> list[float]:
    if not values:
        return []
    lo, hi = min(values), max(values)
    if hi <= lo:
        return [0.0] * len(values)
    return [(v - lo) / (hi - lo) for v in values]


def _approx_tokens(text: str) -> int:
    return max(1, len(text) // 4) + 8


def _bm25_scores(query: str, docs: list[str]) -> list[float] | None:
    try:
        from rank_bm25 import BM25Okapi
    except ImportError:
        log.warning("rank_bm25_missing", note="BM25 disabled; hybrid uses engine score only")
        return None
    tokenized = [d.lower().split() for d in docs]
    if not any(tokenized):
        return [0.0] * len(docs)
    bm25 = BM25Okapi(tokenized)
    return list(bm25.get_scores(query.lower().split()))


@observe(name="retrieval.rank")
def rank_chunks(
    query: str,
    chunks: list[SourcedChunk],
    reranker: RerankProvider | None,
    *,
    settings: Settings,
) -> RankResult:
    diag: dict = {"candidate_count": len(chunks)}
    if not chunks:
        return RankResult(chunks=[], diagnostics=diag)

    # ── Hybrid: engine score ⊕ BM25 ───────────────────────────────────
    engine_norm = _minmax([float(c.get("score", 0.0)) for c in chunks])
    bm25_raw = _bm25_scores(query, [c.get("text", "") for c in chunks])
    if bm25_raw is None:
        hybrid = engine_norm
        diag["bm25"] = False
    else:
        bm25_norm = _minmax(bm25_raw)
        a, b = settings.graph_weight, settings.bm25_weight
        hybrid = [a * g + b * m for g, m in zip(engine_norm, bm25_norm)]
        diag["bm25"] = True

    ranked = sorted(zip(chunks, hybrid), key=lambda t: t[1], reverse=True)
    ranked = ranked[: settings.rerank_candidate_limit]

    # ── Cross-encoder rerank (degrade to hybrid on failure) ───────────
    if reranker is not None and ranked:
        docs: list[RerankDoc] = [
            {"node_id": c.get("node_id", ""), "text": c.get("text", "")} for c, _ in ranked
        ]
        try:
            results: list[RankedDoc] = reranker.rerank(query, docs)
            rr_norm = dict(zip([r.node_id for r in results],
                               _minmax([r.score for r in results])))
            hybrid_by_id = {c.get("node_id", ""): h for c, h in ranked}
            w1, w2 = settings.rerank_weight, settings.hybrid_weight
            final_pairs = [
                (c, w1 * rr_norm.get(c.get("node_id", ""), 0.0)
                    + w2 * hybrid_by_id.get(c.get("node_id", ""), 0.0))
                for c, _ in ranked
            ]
            final_pairs.sort(key=lambda t: t[1], reverse=True)
            diag["reranked"] = True
        except RerankError as exc:
            log.warning("rerank_degraded", error=str(exc))
            final_pairs = ranked
            diag["reranked"] = False
    else:
        final_pairs = ranked
        diag["reranked"] = False

    # ── Token-budgeted final cut ──────────────────────────────────────
    budget = settings.context_token_budget
    spent = 0
    kept: list[SourcedChunk] = []
    for c, score in final_pairs:
        cost = _approx_tokens(c.get("text", ""))
        if kept and spent + cost > budget:
            break
        c["score"] = float(score)  # final fused score for sources/confidence
        kept.append(c)
        spent += cost

    diag.update(kept=len(kept), approx_tokens=spent,
                by_source=_count_by_source(kept))
    log.info("ranked", kept=len(kept), of=len(chunks),
             reranked=diag.get("reranked"), tokens=spent)
    return RankResult(chunks=kept, diagnostics=diag)


def _count_by_source(chunks: list[SourcedChunk]) -> dict:
    out: dict[str, int] = {}
    for c in chunks:
        out[c.get("source", "?")] = out.get(c.get("source", "?"), 0) + 1
    return out
