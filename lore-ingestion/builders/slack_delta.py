"""
builders/slack_delta.py
───────────────────────
W6: apply a Slack reconcile delta (edits + deletes) to the knowledge graph,
incrementally — without rebuilding the whole graph.

Why session-granular: every Slack knowledge node carries `source_thread_id`
(= the session id) and session-level `source_ref`/`scope_id` — there is no
per-message provenance on a node. So an edited or deleted MESSAGE cannot be
mapped to individual nodes; the only correct move is to re-extract the affected
SESSION and diff its node set:

  • node_id present before, gone after  → SOFT-DELETE (tombstone `deleted_at` +
    empty `scope_ids` → the node fails closed out of every scoped query,
    but is kept for audit/rollback).
  • node_id present after              → re-MERGE (idempotent; deterministic ids
    mean re-applying the same delta is a no-op).

This module is the GRAPH side: pure planning + Cypher emit (+ optional apply).
Re-extraction of the changed sessions is the orchestrator's job
(runners/slack_apply_delta.py), which hands `plan_delta` the new triples.

V1 limitation: relationships among nodes that survive are not pruned — only
removed nodes are tombstoned (scope_ids emptied). Dangling edges point at
tombstoned nodes, which are already invisible to scoped queries. Pruning stale
edges on surviving nodes is a TODO.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from common.logger import get_logger
from builders.unified_builder import (
    _NODE_TYPE_TO_LABEL,
    _NODE_TYPE_TO_ID_FIELD,
    _esc,
    generate_knowledge_cypher,
)

log = get_logger("builders.slack_delta")

KNOWLEDGE_TYPES = set(_NODE_TYPE_TO_LABEL)  # {Decision, Task, Fact, Event}


# ── provenance helpers ──────────────────────────────────────────────────────
def session_node_ids(thread: dict) -> dict[str, str]:
    """{node_id → node_type} for every KnowledgeNode in a session's triples."""
    out: dict[str, str] = {}
    for triple in (thread or {}).get("triples", []):
        for key in ("subject", "object"):
            node = triple.get(key, {})
            nid, ntype = node.get("node_id", ""), node.get("type", "")
            if nid and ntype in KNOWLEDGE_TYPES:
                out[nid] = ntype
    return out


def affected_sessions_from_delta(delta_records: list[dict],
                                 sessions: dict[str, dict]) -> set[str]:
    """Session ids touched by the delta.

    A delta record carries `source_id` = "{channel}:{ts}". A session (from
    group_by_thread) holds events whose own `source_id` is the same shape, so we
    index ts→session once and look each delta source_id up.
    """
    sid_by_source_id: dict[str, str] = {}
    for sid, thread in sessions.items():
        for ev in thread.get("emails", []):
            src = ev.get("source_id") or (ev.get("raw", {}) or {}).get("source_id")
            if not src:
                raw = ev.get("raw", {}) or {}
                cid = (raw.get("slack_meta", {}) or {}).get("channel_id", "")
                ts = raw.get("ts", "")
                src = f"{cid}:{ts}" if cid and ts else ""
            if src:
                sid_by_source_id[src] = sid

    affected: set[str] = set()
    for rec in delta_records:
        sid = sid_by_source_id.get(rec.get("source_id", ""))
        if sid:
            affected.add(sid)
    return affected


# ── planning ────────────────────────────────────────────────────────────────
@dataclass
class DeltaPlan:
    # (node_type, node_id) for nodes that vanished from a re-extracted session.
    soft_delete: list[tuple[str, str]] = field(default_factory=list)
    # session ids whose new node set should be re-MERGEd (upsert).
    upsert_session_ids: list[str] = field(default_factory=list)

    def summary(self) -> dict:
        return {"soft_delete": len(self.soft_delete),
                "upsert_sessions": len(self.upsert_session_ids)}


def plan_delta(prior: dict, new: dict, affected: set[str]) -> DeltaPlan:
    """Diff old vs re-extracted nodes for each affected session.

    prior / new: {session_id → thread dict with triples[]}.
    affected:    session ids that were re-extracted (the bounded delta set).
    A session in `affected` but absent from `new` (wholly deleted) → all its old
    nodes are soft-deleted and nothing is upserted.
    """
    plan = DeltaPlan()
    for sid in sorted(affected):
        old_nodes = session_node_ids(prior.get(sid, {}))
        new_nodes = session_node_ids(new.get(sid, {}))
        for nid, ntype in old_nodes.items():
            if nid not in new_nodes:
                plan.soft_delete.append((ntype, nid))
        if new.get(sid, {}).get("triples"):
            plan.upsert_session_ids.append(sid)
    # Deterministic order (idempotent output).
    plan.soft_delete.sort(key=lambda t: (t[0], t[1]))
    return plan


# ── Cypher emit ─────────────────────────────────────────────────────────────
def emit_soft_delete_cypher(node_type: str, node_id: str) -> str:
    """Tombstone a node and empty its scope arrays (fail-closed, audit-kept).

    Clearing scope_ids/scope_all_ids makes the node invisible to every scoped
    query (empty OR-bucket matches nobody) without deleting it — the audit trail
    survives. See docs/RBAC/SCOPE_FILTER_HANDOFF.md."""
    label = _NODE_TYPE_TO_LABEL[node_type]
    id_field = _NODE_TYPE_TO_ID_FIELD[node_type]
    nid = _esc(node_id)
    return (
        f"MATCH (n:{label} {{{id_field}: '{nid}'}})\n"
        f"SET n.deleted_at = datetime(), n.deleted = true, "
        f"n.scope_ids = [], n.scope_all_ids = [];"
    )


def emit_delta_cypher(plan: DeltaPlan, new: dict,
                      name_to_pid: dict[str, str] | None = None,
                      org: str = "unknown") -> str:
    """Soft-delete statements + re-MERGE (upsert) Cypher for the new sessions.

    Upsert reuses `generate_knowledge_cypher` so delta nodes/edges/scope_ids are
    byte-for-byte the same shape the full builder produces."""
    parts: list[str] = ["// ── Slack reconcile delta ──", ""]

    parts.append(f"// Soft-delete {len(plan.soft_delete)} removed node(s)")
    for ntype, nid in plan.soft_delete:
        parts.append(emit_soft_delete_cypher(ntype, nid))
    parts.append("")

    upsert = {sid: new[sid] for sid in plan.upsert_session_ids if sid in new}
    if upsert:
        chunks = generate_knowledge_cypher(
            {"validated": upsert, "name_to_pid": name_to_pid or {}}, org=org)
        parts.append(f"// Re-MERGE nodes/edges for {len(upsert)} session(s)")
        for _name, cypher in chunks:
            parts.append(cypher)
    return "\n".join(parts).strip() + "\n"


def write_delta_cypher(org: str, data_dir: Path, cypher: str) -> Path:
    """Write the delta Cypher to output/cypher/{org}_slack_delta.cypher."""
    out = data_dir / "cypher" / f"{org}_slack_delta.cypher"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(cypher, encoding="utf-8")
    log.info("wrote delta cypher: %s", out)
    return out


def apply_delta_cypher(cypher_path: Path, uri: str, user: str, password: str) -> int:
    """Apply the delta Cypher to Neo4j (reuses the builder's applier)."""
    from neo4j import GraphDatabase
    from builders.unified_builder import _apply_cypher_file
    driver = GraphDatabase.driver(uri, auth=(user, password))
    try:
        return _apply_cypher_file(driver, cypher_path)
    finally:
        driver.close()
