"""
scripts/audit_scope_coverage.py
───────────────────────────────
Rollout step 0 for the v2 query pipeline (docs/QUERY_PIPELINE_V2.md §7).

Audits the live Neo4j graph for everything the unstructured-path retrieval
depends on, so we don't ship fail-closed scope gates against a graph that
isn't ready (unscoped nodes silently vanish → looks like a recall bug):

  1. Vector index `knowledge_embeddings` exists.
  2. Per content label (Decision/Task/Fact/Event): how many nodes are
     scope-bearing (scope_ids non-empty), embedded, time-stamped.
  3. Source breakdown inferred from scope_id prefixes — because content
     nodes carry no `source` property, this is how Gmail/Slack/Jira-derived
     knowledge is told apart. Confirms how much of the :KnowledgeNode space
     is Jira-derived (relevant to whether v2 must filter it out).
  4. Confirms the id model: type-specific id properties (decision_id, …)
     with unique constraints, and NO unified `node_id` property — frontier
     traversal matches per-type on the indexed id field.

Usage:
    ../.venv/bin/python -m scripts.audit_scope_coverage
    ../.venv/bin/python -m scripts.audit_scope_coverage --json

Reads NEO4J_* from core.settings (.env). Read-only; runs no writes.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter

from core.settings import get_settings

CONTENT_LABELS = ["Decision", "Task", "Fact", "Event"]
ID_FIELD = {
    "Decision": "decision_id", "Task": "task_id", "Fact": "fact_id",
    "Event": "event_id", "Issue": "issue_id",
}
GATE_TARGET = 0.99  # cutover gate: ≥99% of Gmail+Slack content nodes scoped


def _driver():
    s = get_settings()
    if not s.neo4j_uri:
        print("NEO4J_URI not set — cannot audit. Set it in .env.", file=sys.stderr)
        sys.exit(2)
    uri = s.neo4j_uri
    if "://" not in uri:
        uri = f"neo4j://{uri}"  # tolerate a schemeless host:port in .env
    from neo4j import GraphDatabase
    try:
        drv = GraphDatabase.driver(uri, auth=(s.neo4j_username, s.neo4j_password))
        drv.verify_connectivity()
    except Exception as exc:
        print(f"Could not connect to Neo4j at {uri!r}: {exc}\n"
              f"Run this against the live (Aura/prod) graph with NEO4J_* set in .env.",
              file=sys.stderr)
        sys.exit(2)
    return drv


def _scalar(session, cypher: str, **params):
    rec = session.run(cypher, **params).single()
    return rec[0] if rec else None


def _infer_source(scope_ids: list[str]) -> str:
    """Infer a node's source from its scope_id prefixes (no source property exists)."""
    if not scope_ids:
        return "unscoped"
    prefixes = {s.split(":", 1)[0] for s in scope_ids}
    if "jira" in prefixes:
        return "jira"
    if "slack" in prefixes:
        return "slack"
    # Gmail scopes are participant person ids (person:… or bare ids).
    return "gmail/other"


def audit(session) -> dict:
    report: dict = {"labels": {}, "source_breakdown": {}, "vector_index": None,
                    "node_id_property_present": None, "id_constraints": []}

    # 1. Vector index
    idx = session.run(
        "SHOW INDEXES YIELD name, type, properties "
        "WHERE name = 'knowledge_embeddings' RETURN name, type, properties"
    ).data()
    report["vector_index"] = idx[0] if idx else None

    # 4. id model: constraints + absence of unified node_id
    constraints = session.run(
        "SHOW CONSTRAINTS YIELD name, properties RETURN name, properties"
    ).data()
    report["id_constraints"] = [
        c for c in constraints
        if any(p.endswith("_id") for p in (c.get("properties") or []))
    ]
    report["node_id_property_present"] = bool(_scalar(
        session,
        "MATCH (n:KnowledgeNode) WHERE n.node_id IS NOT NULL RETURN count(n) > 0 LIMIT 1"
    ))

    # 2. per-label coverage
    for label in CONTENT_LABELS:
        total = _scalar(session, f"MATCH (n:{label}) RETURN count(n)") or 0
        if total == 0:
            report["labels"][label] = {"total": 0}
            continue
        scoped = _scalar(
            session,
            f"MATCH (n:{label}) WHERE n.scope_ids IS NOT NULL AND size(n.scope_ids) > 0 "
            f"RETURN count(n)",
        ) or 0
        embedded = _scalar(
            session, f"MATCH (n:{label}) WHERE n.embedding IS NOT NULL RETURN count(n)"
        ) or 0
        dated = _scalar(
            session, f"MATCH (n:{label}) WHERE n.created_at IS NOT NULL RETURN count(n)"
        ) or 0
        report["labels"][label] = {
            "total": total,
            "scoped": scoped,
            "scoped_pct": round(scoped / total, 4),
            "embedded": embedded,
            "embedded_pct": round(embedded / total, 4),
            "created_at": dated,
        }

    # 3. source breakdown via scope_id prefixes (sampled across all content labels)
    src = Counter()
    rows = session.run(
        "MATCH (n:KnowledgeNode) RETURN n.scope_ids AS scope_ids LIMIT 50000"
    )
    for r in rows:
        src[_infer_source(r["scope_ids"] or [])] += 1
    report["source_breakdown"] = dict(src)

    return report


def _print(report: dict) -> None:
    print("\n" + "=" * 70)
    print("  v2 QUERY PIPELINE — GRAPH READINESS AUDIT")
    print("=" * 70)

    vi = report["vector_index"]
    print(f"\nVector index 'knowledge_embeddings': {'PRESENT' if vi else 'MISSING ✗'}")
    if vi:
        print(f"  type={vi.get('type')} properties={vi.get('properties')}")

    print(f"\nUnified node_id property present: {report['node_id_property_present']} "
          f"(expected False — traversal uses per-type indexed id fields)")
    print(f"Type-id unique constraints found: {len(report['id_constraints'])}")

    print("\nPer-label coverage (scoped = retrievable under fail-closed gate):")
    print(f"  {'label':<10} {'total':>8} {'scoped':>8} {'scoped%':>8} {'embed%':>8} {'dated':>8}")
    gate_fail = []
    for label in CONTENT_LABELS:
        d = report["labels"].get(label, {})
        if not d.get("total"):
            print(f"  {label:<10} {'0':>8}  (none)")
            continue
        print(f"  {label:<10} {d['total']:>8} {d['scoped']:>8} "
              f"{d['scoped_pct']*100:>7.1f}% {d['embedded_pct']*100:>7.1f}% {d['created_at']:>8}")

    print("\nSource breakdown (inferred from scope_id prefixes):")
    total_src = sum(report["source_breakdown"].values()) or 1
    for src, n in sorted(report["source_breakdown"].items(), key=lambda x: -x[1]):
        print(f"  {src:<14} {n:>8}  ({n/total_src*100:.1f}%)")

    # Cutover gate verdict on Gmail+Slack scoped coverage
    print("\nCutover gate (≥%.0f%% of Gmail+Slack content nodes scoped):" % (GATE_TARGET * 100))
    unscoped = report["source_breakdown"].get("unscoped", 0)
    gmail_slack = (report["source_breakdown"].get("gmail/other", 0)
                   + report["source_breakdown"].get("slack", 0))
    denom = gmail_slack + unscoped
    if denom:
        cov = gmail_slack / denom
        verdict = "PASS ✓" if cov >= GATE_TARGET else "FAIL ✗ — unscoped nodes will vanish"
        print(f"  scoped {gmail_slack}/{denom} = {cov*100:.2f}%  →  {verdict}")
    else:
        print("  no Gmail/Slack content nodes found")
    print("=" * 70 + "\n")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true", help="emit raw JSON instead of a report")
    args = ap.parse_args()

    drv = _driver()
    try:
        with drv.session() as session:
            report = audit(session)
    finally:
        drv.close()

    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        _print(report)


if __name__ == "__main__":
    main()
