"""
domain/sources.py
─────────────────
Format retrieval results into source citations for the API response.

Pure function — no IO.
"""

from __future__ import annotations

from core.types import KnowledgeNode, RetrievalResult, Source, SourcedChunk


def format_sources(result: RetrievalResult) -> list[Source]:
    """Flatten primary + supporting into deduplicated Source citations."""
    sources: list[Source] = []
    seen_ids: set[str] = set()

    for node in result.primary[:6] + result.supporting[:4]:
        if "decisions_made" in node:
            # Person-expanded node
            nid = node.get("person_id") or node.get("name", "")
            if nid in seen_ids:
                continue
            seen_ids.add(nid)
            sources.append(Source(
                type="Person",
                description=node.get("name", ""),
                thread_id="",
                score=1.0,
                sensitivity="internal",
            ))
            continue

        nid = node.get("node_id", "")
        if nid and nid in seen_ids:
            continue
        if nid:
            seen_ids.add(nid)

        desc = (node.get("description") or node.get("decision") or
                node.get("task") or node.get("fact") or "")
        sources.append(Source(
            type=node.get("type", "unknown"),
            description=str(desc)[:200],
            thread_id=node.get("source_thread_id") or node.get("thread_id", ""),
            score=float(node.get("score", 0.0)),
            sensitivity=node.get("sensitivity_label", "internal"),
        ))

    return sources


def format_sources_chunks(chunks: list[SourcedChunk], limit: int = 10) -> list[Source]:
    """Flatten ranked SourcedChunks (any source) into Source citations.

    Same Source shape the SSE `sources` event and frontend already expect, so
    the response contract is unchanged across both engines. `thread_id` carries
    the citation pointer (email/chat thread id, or Jira issue key).
    """
    sources: list[Source] = []
    seen: set[str] = set()
    for c in chunks[:limit]:
        nid = c.get("node_id", "")
        if not nid or nid in seen:
            continue
        seen.add(nid)
        citation = (c.get("citations") or [""])[0]
        sources.append(Source(
            type=f"{c.get('node_type', 'unknown')} ({c.get('source', '?')})",
            description=str(c.get("text", ""))[:200],
            thread_id=citation,
            score=float(c.get("score", 0.0)),
            sensitivity="internal",
        ))
    return sources
