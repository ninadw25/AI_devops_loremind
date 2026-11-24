"""
scripts/smoke_continuity.py
───────────────────────────
Smoke-test the knowledge-continuity features against the LIVE Neo4j graph
(no fakes). Auto-discovers real persons/topics, then exercises:

    Expert Search      domain.retrieval.expertise.find_experts
    Continuity Radar   domain.analytics.bus_factor.{coverage_summary, topic_coverage}
    Offboarding        services.offboarding_service.build_report
    Onboarding         services.onboarding_service.build_learning_path

Run:  uv run python scripts/smoke_continuity.py

Uses super_admin scopes so results aren't hidden by the fail-closed gate.
Logging is configured so any Neo4jClient 'cypher_error' (run() swallows
exceptions → None) is printed loudly.
"""

from __future__ import annotations

from core.logging import configure_logging, get_logger
from core.settings import get_settings
from core.types import UserScopes
from infra.neo4j_client import Neo4jClient

from domain.retrieval.expertise import find_experts
from domain.analytics.bus_factor import coverage_summary, topic_coverage
from services.offboarding_service import build_report
from services.onboarding_service import build_learning_path

configure_logging()
log = get_logger("smoke")


def _hr(title: str) -> None:
    print(f"\n{'─' * 70}\n{title}\n{'─' * 70}")


def main() -> None:
    s = get_settings()
    g = Neo4jClient(s.neo4j_uri, s.neo4j_username, s.neo4j_password)
    if not g.available:
        print("❌ Neo4j UNAVAILABLE — check NEO4J_URI / credentials in .env")
        return
    print(f"✅ Neo4j connected: {s.neo4j_uri}")

    # See everything for the smoke test (bypasses the fail-closed gate).
    scopes = UserScopes(allowed=[], super_admin=True)

    # ── discover real data ────────────────────────────────────────────
    persons = g.run(
        "MATCH (p:Person)-[:DECIDED]->(d:Decision) "
        "RETURN p.person_id AS pid, p.name AS name, count(d) AS act "
        "ORDER BY act DESC LIMIT 5"
    ) or []
    topics = g.run(
        "MATCH (t:Topic) OPTIONAL MATCH (t)<-[:REGARDING]-(c) "
        "RETURN t.name AS name, count(c) AS deg ORDER BY deg DESC LIMIT 5"
    ) or []

    _hr("SAMPLE DATA")
    print("persons:", [(p["name"], p["act"]) for p in persons])
    print("topics :", [(t["name"], t["deg"]) for t in topics])
    if not persons or not topics:
        print("⚠️  graph has no persons/topics — cannot exercise features")
        g.close()
        return

    # ── Continuity Radar ──────────────────────────────────────────────
    _hr("CONTINUITY RADAR")
    print("coverage_summary:", coverage_summary(g, scopes))
    cov = topic_coverage(g, scopes, max_holders=2, limit=10)
    print(f"at-risk topics (≤2 holders): {len(cov)}")
    for r in cov[:5]:
        print(f"  • {r.get('topic_name')!r:40} holders={r.get('holder_count')} "
              f"sole={r.get('sole_holder')} evidence={r.get('total_evidence')}")

    # ── Expert Search ─────────────────────────────────────────────────
    topic = topics[0]["name"]
    _hr(f"EXPERT SEARCH — topic={topic!r}")
    experts = find_experts(g, topic, scopes, limit=5)
    print(f"experts found: {len(experts)}")
    for e in experts[:5]:
        print(f"  • {e.get('name')!r:30} evidence={e.get('evidence_count')} "
              f"expert_weight={e.get('expert_weight')} activity={e.get('total_activity')}")

    # ── Offboarding + Onboarding (top person) ─────────────────────────
    pid, pname = persons[0]["pid"], persons[0]["name"]

    _hr(f"OFFBOARDING REPORT — {pname} ({pid})")
    rep = build_report(g, pid, scopes)
    print("header:", rep["header"])
    o = rep["owned"]
    print(f"owned: decisions={len(o['decisions'])} tasks={len(o['tasks'])} "
          f"facts={len(o['facts'])} events={len(o['events'])}")
    print(f"at-risk worklist: {len(rep['worklist'])}")
    for w in rep["worklist"][:3]:
        print(f"  • {w.get('topic_name')!r:30} sole={w.get('sole_holder')} "
              f"inheritors={len(w.get('suggested_inheritors', []))}")
        print(f"      Q: {w.get('question')}")

    _hr(f"ONBOARDING PATH — learn {pname}'s role")
    path = build_learning_path(g, pid, scopes)
    print("focus_areas:", path["focus_areas"])
    print(f"key_decisions={len(path['key_decisions'])} people={len(path['people'])} "
          f"inherited_work={len(path['inherited_work'])}")
    for d in path["key_decisions"][:3]:
        print(f"  • decision: {str(d.get('description'))[:70]!r} "
              f"rationale={len(d.get('rationale', []))} topics={d.get('topics')}")

    g.close()
    print("\n✅ smoke test complete")


if __name__ == "__main__":
    main()
