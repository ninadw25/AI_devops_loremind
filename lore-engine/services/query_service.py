"""
services/query_service.py
─────────────────────────
Consolidated query pipeline orchestrator. Two specialized retrieval engines
(because two ontologies) behind one router-free orchestrator and one shared
ranking + assembly + synthesis tail. See docs/QUERY_PIPELINE_V2.md.

Composes:
    (analyze ∥ embed)
      → run BOTH engines concurrently:
            unstructured (Gmail/Slack/Outlook/Teams — beam/frontier)
            jira         (Issues — intent-routed structured lookup)
      → merge → unified cross-encoder rank → assemble → synthesize

Both engines always run: an engine is just graph queries (no extra LLM call),
and the unified cross-encoder rank drops whatever isn't relevant — so there is
no up-front routing decision to get wrong. Each engine returns the same
SourcedChunk envelope, so merge/rank/assemble are source-agnostic.

All dependencies are injected — no module-level state, no env reads. The
analysis call runs concurrently with embedding under a hard timeout; on timeout
or failure it falls back to the keyword classifier so retrieval never stalls.

Scope model: callers pass `user_scopes` (the allowedScopes injected by
core-backend Plane B). Every retrieval Cypher is fail-closed on these — an
empty scope set sees nothing.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from datetime import datetime, timezone
from typing import Generator

from core.exceptions import EmbeddingError
from core.logging import get_logger
from core.observability import lf, observe
from core.settings import Settings
from core.types import QueryAnalysis, RerankProvider, SourcedChunk, UserScopes
from domain.analysis import analyze_query, keyword_analysis
from domain.context import build_context_chunks, _EMPTY_CTX
from domain.prompts import SYNTHESIS_SYSTEM_PROMPT, scrub_phones, build_user_message
from domain.retrieval.jira import retrieve_jira_sync
from domain.retrieval.rank import RankResult, rank_chunks
from domain.retrieval.unstructured import retrieve_unstructured
from domain.scoring import score_confidence_chunks
from domain.sources import format_sources_chunks
from infra.embeddings.base import EmbeddingProvider
from infra.llm.base import ChatProvider
from infra.neo4j_client import Neo4jClient

log = get_logger(__name__)


class QueryService:
    """Orchestrates the full v2 query pipeline."""

    def __init__(
        self,
        graph: Neo4jClient,
        chat: ChatProvider,
        embedder: EmbeddingProvider,
        settings: Settings,
        reranker: RerankProvider | None = None,
    ) -> None:
        self._graph    = graph
        self._chat     = chat
        self._embedder = embedder
        self._settings = settings
        self._reranker = reranker

    def close(self) -> None:
        self._graph.close()

    @property
    def graph_available(self) -> bool:
        return self._graph.available

    # ── Pipeline stages ───────────────────────────────────────────────

    def _embed_and_analyze(self, question: str) -> tuple[list[float], QueryAnalysis]:
        """Run embedding and query analysis concurrently.

        Embedding must succeed (raises EmbeddingError upward). Analysis has a
        hard deadline; on timeout/error it degrades to the keyword classifier.
        The lingering analysis thread (if any) is left to finish in the
        background — we do not wait on it.
        """
        now_iso = datetime.now(timezone.utc).date().isoformat()
        ex = ThreadPoolExecutor(max_workers=2)
        try:
            f_embed = ex.submit(self._embedder.embed, question)
            f_analyze = ex.submit(analyze_query, self._chat, question, now_iso=now_iso)
            embedding = f_embed.result()  # may raise EmbeddingError
            try:
                analysis = f_analyze.result(timeout=self._settings.analysis_timeout_ms / 1000)
            except FuturesTimeout:
                log.warning("analysis_timeout", ms=self._settings.analysis_timeout_ms)
                analysis = keyword_analysis(question)
        finally:
            ex.shutdown(wait=False)

        if analysis.intent == "GLOBAL":
            # Community summaries not built yet — degrade and measure demand.
            log.info("intent_global_unserved", question=question[:120])
            analysis.intent = "TOPIC_EXPLORATION"
        return embedding, analysis

    @observe(name="retrieval.pipeline")
    def _retrieve(
        self,
        question: str,
        embedding: list[float],
        analysis: QueryAnalysis,
        scopes: UserScopes,
        k: int,
        user_account_id: str | None,
    ) -> tuple[RankResult, dict]:
        """Run both engines concurrently, merge, then unified rank."""
        ex = ThreadPoolExecutor(max_workers=2)
        try:
            f_unstructured = ex.submit(
                retrieve_unstructured, self._graph, embedding, analysis, scopes,
                k=k,
                beam_width=self._settings.frontier_beam_width,
                max_depth=self._settings.frontier_max_depth,
            )
            f_jira = ex.submit(
                retrieve_jira_sync, question, embedding, list(scopes.allowed), k,
                self._graph, user_account_id, self._settings.jira_recency_window_days,
                scopes.super_admin,
            )
            unstructured_chunks = f_unstructured.result()
            jira_chunks = f_jira.result()
        finally:
            ex.shutdown(wait=False)

        merged = _merge_chunks(unstructured_chunks, jira_chunks)
        result = rank_chunks(question, merged, self._reranker, settings=self._settings)
        diag = {
            "unstructured": len(unstructured_chunks),
            "jira": len(jira_chunks),
            "merged": len(merged),
            "rank": result.diagnostics,
            "intent": analysis.intent,
        }
        return result, diag

    def _synthesize_messages(
        self, question: str, context: str, history_messages: list[dict] | None
    ) -> list[dict]:
        messages = [{"role": "system", "content": SYNTHESIS_SYSTEM_PROMPT}]
        if history_messages:
            messages.extend(history_messages)
        messages.append({
            "role": "user",
            "content": build_user_message(question, scrub_phones(context)),
        })
        return messages

    # ── Public API ────────────────────────────────────────────────────

    @observe(name="lore-query")
    def query(
        self,
        question: str,
        user_scopes: list[str],
        k: int | None = None,
        history_messages: list[dict] | None = None,
        user_account_id: str | None = None,
        super_admin: bool = False,
    ) -> dict:
        """Blocking query. Returns {answer, confidence, sources, stats}."""
        t0 = time.time()
        stats: dict = {}
        scopes = UserScopes(list(user_scopes or []), super_admin=super_admin)
        k = k or self._settings.vector_seed_top_k

        if scopes.is_empty and not scopes.super_admin:
            log.warning("query_zero_scopes")  # fail-closed: nothing visible
        elif scopes.super_admin:
            log.info("query_content_superadmin")  # content.read_all bypass (audited at gateway)

        # Stage 1+2: analyze ∥ embed
        t = time.time()
        try:
            embedding, analysis = self._embed_and_analyze(question)
        except EmbeddingError as exc:
            log.warning("embed_failed", error=str(exc))
            return self._empty_response(stats, t0, reason="Embedding failed")
        stats["embed_analyze_ms"] = round((time.time() - t) * 1000)
        stats["intent"] = analysis.intent

        # Stage 3-5: retrieve (both engines concurrently) → merge → rank
        t = time.time()
        result, diag = self._retrieve(question, embedding, analysis, scopes, k, user_account_id)
        stats["retrieve_ms"] = round((time.time() - t) * 1000)
        stats["chunk_count"] = len(result.chunks)
        stats["by_source"] = result.diagnostics.get("by_source", {})
        stats["graph_available"] = self._graph.available

        # Stage 6: assemble
        context = build_context_chunks(result.chunks)
        if context.strip() == _EMPTY_CTX:
            return self._empty_response(stats, t0)

        # Stage 7: synthesize
        t = time.time()
        answer = scrub_phones(self._chat.chat(
            self._synthesize_messages(question, context, history_messages),
            enable_thinking=True,
        ))
        stats["synthesis_ms"] = round((time.time() - t) * 1000)
        stats["total_ms"] = round((time.time() - t0) * 1000)

        confidence = score_confidence_chunks(result.chunks)
        sources = format_sources_chunks(result.chunks)

        log.info("query_complete", confidence=confidence, total_ms=stats["total_ms"])
        lf.update_current_span(
            input={"question": question, "k": k},
            output={"confidence": confidence, "answer": answer[:500]},
            metadata={**stats, "diag": diag},
        )
        lf.score_current_trace(
            name="confidence",
            value={"high": 1.0, "medium": 0.5, "low": 0.0}.get(confidence, 0.0),
            data_type="NUMERIC",
        )
        return {
            "answer": answer,
            "confidence": confidence,
            "sources": [vars(s) for s in sources],
            "stats": stats,
        }

    @observe(name="lore-query-stream")
    def query_stream(
        self,
        question: str,
        user_scopes: list[str],
        k: int | None = None,
        history_messages: list[dict] | None = None,
        user_account_id: str | None = None,
        super_admin: bool = False,
    ) -> Generator[dict, None, None]:
        """Streaming query. Yields SSE-style event dicts."""
        scopes = UserScopes(list(user_scopes or []), super_admin=super_admin)
        k = k or self._settings.vector_seed_top_k
        if scopes.is_empty and not scopes.super_admin:
            log.warning("query_zero_scopes")
        elif scopes.super_admin:
            log.info("query_content_superadmin")

        try:
            embedding, analysis = self._embed_and_analyze(question)
        except EmbeddingError:
            yield {"type": "token", "data": "Unable to embed query."}
            yield {"type": "done", "data": {"confidence": "low"}}
            return

        result, _diag = self._retrieve(question, embedding, analysis, scopes, k, user_account_id)
        sources = format_sources_chunks(result.chunks)
        yield {"type": "sources", "data": [vars(s) for s in sources]}

        context = build_context_chunks(result.chunks)
        if context.strip() == _EMPTY_CTX:
            yield {"type": "token", "data": _EMPTY_CTX}
            yield {"type": "done", "data": {"confidence": "low"}}
            return

        messages = self._synthesize_messages(question, context, history_messages)
        for kind, text in self._chat.chat_stream(messages, enable_thinking=True):
            if kind == "content" and text:
                yield {"type": "token", "data": scrub_phones(text)}

        yield {"type": "done", "data": {"confidence": score_confidence_chunks(result.chunks)}}

    def _empty_response(self, stats: dict, t0: float, reason: str = "") -> dict:
        if reason:
            log.warning("query_empty", reason=reason)
        stats["total_ms"] = round((time.time() - t0) * 1000)
        return {
            "answer": "No relevant information was found. Try rephrasing or ask about a specific person, decision, or topic.",
            "confidence": "low",
            "sources": [],
            "stats": stats,
        }


def _merge_chunks(*groups: list[SourcedChunk]) -> list[SourcedChunk]:
    """Union chunk groups from both engines, deduping by node_id (first wins).
    The two engines are source-disjoint, so collisions are rare — but a shared
    node reached both ways keeps the first (unstructured) instance."""
    out: list[SourcedChunk] = []
    seen: set[str] = set()
    for group in groups:
        for c in group or []:
            nid = c.get("node_id", "")
            if nid and nid not in seen:
                seen.add(nid)
                out.append(c)
    return out
