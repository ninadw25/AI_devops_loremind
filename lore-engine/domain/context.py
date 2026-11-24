"""
domain/context.py
─────────────────
Assemble a retrieval result into a structured text block for the LLM.

Pure function — no IO, no side effects. Input is the RetrievalResult
dataclass; output is a plain string the synthesis step consumes.
"""

from __future__ import annotations

from core.observability import observe
from core.types import KnowledgeNode, RetrievalResult, SourcedChunk

_MAX_CONTEXT_NODES = 18
_MAX_DESC          = 400
_EMPTY_CTX         = "No relevant information found in the knowledge graph."


def _trunc(text: object, n: int = _MAX_DESC) -> str:
    s = str(text or "")
    return s[:n] + ("…" if len(s) > n else "")


@observe(name="domain.build-context")
def build_context(result: RetrievalResult) -> str:
    """Format primary + supporting nodes into the LLM context block."""
    lines: list[str] = []

    if result.persons:
        lines.append("§ PEOPLE MENTIONED")
        for p in result.persons[:3]:
            tag   = "internal" if p.get("is_internal") else "external"
            email = p.get("primary_email", "")
            lines.append(f"  {p.get('name')} <{email}> [{tag}]")

    if result.primary:
        lines.append("\n§ PRIMARY RESULTS")
        for node in result.primary[:_MAX_CONTEXT_NODES]:
            if "decisions_made" in node:
                # Person-expanded node
                lines.append(f"\n[Person] {node.get('name')} <{node.get('email', '')}>")
                if node.get("organization"):
                    lines.append(f"  Organisation: {node['organization']}")
                if node.get("reports_to"):
                    lines.append(f"  Reports to: {node['reports_to']}")
                if node.get("direct_reports"):
                    lines.append(f"  Direct reports: {', '.join(node['direct_reports'][:8])}")
                for d in node.get("decisions_made", [])[:10]:
                    lines.append(f"  Decided: {_trunc(d, 300)}")
                for t in node.get("tasks_assigned", [])[:8]:
                    lines.append(f"  Task: {_trunc(t, 250)}")
                for f in node.get("facts_stated", [])[:6]:
                    lines.append(f"  Stated: {_trunc(f, 200)}")
                for e in node.get("events", [])[:4]:
                    lines.append(f"  Event: {_trunc(e, 160)}")
                continue

            ntype = node.get("type", "Node")
            desc  = (node.get("description") or node.get("decision") or
                     node.get("task") or node.get("fact") or "")
            line  = f"[{ntype}] {_trunc(desc)}"
            if node.get("decided_by"):
                line += f" | by: {', '.join(node['decided_by'][:2])}"
            if node.get("assigned_to"):
                line += f" | assigned: {', '.join(node['assigned_to'][:2])}"
            if node.get("topics"):
                line += f" | topics: {', '.join(node['topics'][:3])}"
            thread = node.get("source_thread_id") or node.get("thread_id", "")
            if thread:
                line += f" [thread:{thread}]"
            lines.append(line)

    if result.supporting:
        lines.append("\n§ SUPPORTING CONTEXT")
        for node in result.supporting[:10]:
            ntype = node.get("type", "Node")
            desc  = (node.get("description") or node.get("decision") or
                     node.get("task") or node.get("fact") or "")
            line  = f"[{ntype}] {_trunc(desc, 300)}"
            if node.get("decided_by"):
                line += f" | by: {', '.join(node['decided_by'][:2])}"
            if node.get("matched_topic"):
                line += f" | topic: {node['matched_topic']}"
            if node.get("source_thread_id"):
                line += f" [thread:{node['source_thread_id']}]"
            lines.append(line)

    return "\n".join(lines) if lines else _EMPTY_CTX


# ── Consolidated assembly from the merged SourcedChunk pool ───────────
#
# Source-agnostic by design: each engine already rendered its nodes into good
# `text` (the unstructured engine folds in ontology attribution + corroboration;
# the Jira engine folds in status/assignee/sprint). The assembler just groups
# the ranked chunks under per-source headers so the synthesis LLM can tell
# email/chat knowledge from Jira tickets. No inline citation tokens —
# provenance is delivered separately via the sources event.

_SOURCE_HEADER = {
    "gmail": "EMAIL & CHAT", "slack": "EMAIL & CHAT",
    "outlook": "EMAIL & CHAT", "teams": "EMAIL & CHAT",
    "jira": "JIRA ISSUES",
}
# Stable ordering: communications first, then Jira.
_SECTION_ORDER = ["EMAIL & CHAT", "JIRA ISSUES", "OTHER"]


@observe(name="domain.build-context-chunks")
def build_context_chunks(chunks: list[SourcedChunk]) -> str:
    """Assemble the LLM context block from a merged, ranked SourcedChunk pool."""
    if not chunks:
        return _EMPTY_CTX

    grouped: dict[str, list[SourcedChunk]] = {}
    for c in chunks:
        header = _SOURCE_HEADER.get(c.get("source", ""), "OTHER")
        grouped.setdefault(header, []).append(c)

    lines: list[str] = []
    for header in _SECTION_ORDER:
        group = grouped.get(header)
        if not group:
            continue
        lines.append(f"§ {header}")
        for c in group:
            text = _trunc(c.get("text", ""), 600)
            cite = (c.get("citations") or [None])[0] or (c.get("citations") and "")
            suffix = f" [ref:{cite}]" if cite else ""
            lines.append(f"  {text}{suffix}")
        lines.append("")

    body = "\n".join(lines).strip()
    return body or _EMPTY_CTX
