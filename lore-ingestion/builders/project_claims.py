"""
builders/project_claims.py
────────────────────────────
Neo4j projection of the claim substrate — build step 7 of
`docs/revised_ingestion_pipeline/00_DESIGN.md` §11. This is the "knowledge in
the graph" deliverable: makes L1 registries + L2 edges + L3 claims traversable
for lore-engine and queryable for expert search (step 8), without needing any
LLM at query time.

Writes to a SEPARATE Neo4j instance from `builders/unified_builder.py`'s
target (`common/config.py: claims_neo4j_*` / `neo4j/docker-compose.claims.yml`)
— the old naked-triple ontology (:Person/:Decision/:Task/:Fact/:Event/:Topic/
:Organization) and this projection never share a database (§10: "dies" list).

Graph shape:

    (:Person {person_id, org, primary_email, display_name, sender_type, is_internal})
    (:Organization {org_id, org, domain, kind})
    (:Container {container_id, org, source, kind, source_key, display_name})
    (:Artifact {artifact_id, org, source, kind, title, message_count, first_at, last_at})
    (:Claim {claim_id, org, kind, statement, attributes (JSON string),
             occurred_at, confidence, evidence_quote, evidence_grounded,
             scope_ids, scope_all_ids})
    (:Topic {name, org, status})   status = 'linked' once ANY claim links to it
                                    non-proposal, else 'proposal' (closed-world
                                    linking already decided this at extraction —
                                    projection just carries it, never invents)

    (:Claim)-[:FROM_CONVERSATION]->(:Artifact)
    (:Claim)-[:ABOUT_TOPIC {is_proposal}]->(:Topic)
    (:Claim)-[:ENTITY {role}]->(:Person)              only resolved entities —
                                                        unresolved names stay
                                                        text-only in Postgres
                                                        (meaning lives there;
                                                        graph is for traversal)
    (:Claim)-[:CORROBORATES]->(:Claim)                 from claim_relation
    (:Person)-[:AUTHORED {weight, first_at, last_at}]->(:Artifact)
    (:Person)-[:PARTICIPATED_IN {weight, first_at, last_at}]->(:Artifact)
    (:Person)-[:COMMUNICATES_WITH {weight, first_at, last_at}]->(:Person)
    (:Person)-[:MEMBER_OF]->(:Container)

Idempotent: every write is a MERGE on the deterministic id, so reruns update
in place. Meaning always lives in Postgres — this graph can be dropped and
rebuilt from L1/L2/L3 at any time with zero loss (§3 "why layers").

Usage (from lore-ingestion/):
    ../.venv/bin/python -m builders.project_claims --org ezobooks
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict

import sqlalchemy as sa

from builders.l0_ledger import LedgerStore, _resolve_dsn
from builders.l1_registry import (EntityArtifact, EntityContainer,
                                  EntityOrganization, EntityPerson)
from builders.l2_edges import (EdgeAuthored, EdgeCommunicatesWith,
                               EdgeMemberOf, EdgeParticipatedIn)
from builders.l3_claims import Claim, ClaimEntity, ClaimRelation, ClaimTopic
from common.config import claims_neo4j_credentials_for
from common.logger import get_logger

log = get_logger("builders.project_claims")

BATCH = 1000
RELATION_TYPES = ("CORROBORATES", "SUPERSEDES", "RELATES_TO", "INSTANCE_OF", "BECAUSE")

# Every MERGE key needs a uniqueness constraint or Neo4j label-scans the whole
# store per row — the difference between this finishing in minutes and never
# finishing at hundreds of thousands of edges.
CONSTRAINTS = [
    "CREATE CONSTRAINT person_id IF NOT EXISTS FOR (p:Person) REQUIRE p.person_id IS UNIQUE",
    "CREATE CONSTRAINT org_id IF NOT EXISTS FOR (o:Organization) REQUIRE o.org_id IS UNIQUE",
    "CREATE CONSTRAINT container_id IF NOT EXISTS FOR (c:Container) REQUIRE c.container_id IS UNIQUE",
    "CREATE CONSTRAINT artifact_id IF NOT EXISTS FOR (a:Artifact) REQUIRE a.artifact_id IS UNIQUE",
    "CREATE CONSTRAINT claim_id IF NOT EXISTS FOR (c:Claim) REQUIRE c.claim_id IS UNIQUE",
    "CREATE CONSTRAINT topic_org_name IF NOT EXISTS FOR (t:Topic) REQUIRE (t.org, t.name) IS UNIQUE",
]


# ── Pure shaping (no DB, no driver — unit-tested directly) ──────────────────

def shape_topics(claim_topic_rows: list[dict]) -> list[dict]:
    """Distinct topics with a status: 'linked' if ANY claim linked to it
    closed-world (is_proposal=False) anywhere in the corpus, else 'proposal'.
    A name proposed in one claim and properly linked in another is linked —
    the registry (once step 6 lands) would have promoted it; don't understate
    that here."""
    linked: set[str] = set()
    seen: set[str] = set()
    for r in claim_topic_rows:
        seen.add(r["topic"])
        if not r["is_proposal"]:
            linked.add(r["topic"])
    return [{"name": t, "status": "linked" if t in linked else "proposal"}
            for t in sorted(seen)]


def shape_relations(claim_relation_rows: list[dict]) -> dict[str, list[dict]]:
    """Group relation rows by rel_type — Cypher relationship types can't be
    parameterized, so each type gets its own batched query."""
    by_type: dict[str, list[dict]] = defaultdict(list)
    for r in claim_relation_rows:
        by_type[r["rel_type"]].append({"src": r["src_claim_id"], "dst": r["dst_claim_id"]})
    return dict(by_type)


# ── Neo4j writes ─────────────────────────────────────────────────────────────

def _run_batched(driver, cypher: str, rows: list[dict], key: str = "batch") -> int:
    total = 0
    for i in range(0, len(rows), BATCH):
        chunk = rows[i:i + BATCH]
        with driver.session() as session:
            session.run(cypher, **{key: chunk})
        total += len(chunk)
    return total


def write_nodes(driver, org: str, store, claims_only: bool = False) -> dict:
    stats = {}
    with store._session() as s:
        if not claims_only:
            persons = [dict(r._mapping) for r in s.execute(sa.select(
                EntityPerson.person_id, EntityPerson.org, EntityPerson.primary_email,
                EntityPerson.display_name, EntityPerson.sender_type, EntityPerson.is_internal)
                .where(EntityPerson.org == org))]
            orgs = [dict(r._mapping) for r in s.execute(sa.select(
                EntityOrganization.org_id, EntityOrganization.org, EntityOrganization.domain,
                EntityOrganization.kind).where(EntityOrganization.org == org))]
            containers = [dict(r._mapping) for r in s.execute(sa.select(
                EntityContainer.container_id, EntityContainer.org, EntityContainer.source,
                EntityContainer.kind, EntityContainer.source_key, EntityContainer.display_name)
                .where(EntityContainer.org == org))]
            artifacts = [dict(r._mapping) for r in s.execute(sa.select(
                EntityArtifact.artifact_id, EntityArtifact.org, EntityArtifact.source,
                EntityArtifact.kind, EntityArtifact.title, EntityArtifact.message_count,
                EntityArtifact.first_at, EntityArtifact.last_at).where(EntityArtifact.org == org))]
        claims = [dict(r._mapping) for r in s.execute(sa.select(
            Claim.claim_id, Claim.org, Claim.kind, Claim.statement, Claim.attributes,
            Claim.occurred_at, Claim.confidence, Claim.evidence_quote,
            Claim.evidence_grounded, Claim.scope_ids, Claim.scope_all_ids,
            Claim.conversation_id).where(Claim.org == org))]
        topic_rows = [dict(r._mapping) for r in s.execute(sa.select(
            ClaimTopic.topic, ClaimTopic.is_proposal).where(ClaimTopic.org == org))]

    for c in claims:
        c["attributes"] = json.dumps(c["attributes"] or {})

    if not claims_only:
        stats["persons"] = _run_batched(driver, """
            UNWIND $batch AS item
            MERGE (p:Person {person_id: item.person_id})
            SET p += item""", persons)
        stats["organizations"] = _run_batched(driver, """
            UNWIND $batch AS item
            MERGE (o:Organization {org_id: item.org_id})
            SET o += item""", orgs)
        stats["containers"] = _run_batched(driver, """
            UNWIND $batch AS item
            MERGE (c:Container {container_id: item.container_id})
            SET c += item""", containers)
        stats["artifacts"] = _run_batched(driver, """
            UNWIND $batch AS item
            MERGE (a:Artifact {artifact_id: item.artifact_id})
            SET a += item""", artifacts)
    stats["claims"] = _run_batched(driver, """
        UNWIND $batch AS item
        MERGE (c:Claim {claim_id: item.claim_id})
        SET c += item""", claims)
    topics = [{**t, "org": org} for t in shape_topics(topic_rows)]
    stats["topics"] = _run_batched(driver, """
        UNWIND $batch AS item
        MERGE (t:Topic {org: item.org, name: item.name})
        SET t.status = item.status""", topics)
    return stats


def write_claim_edges(driver, org: str, store) -> dict:
    stats = {}
    with store._session() as s:
        conv_ids = [dict(r._mapping) for r in s.execute(sa.select(
            Claim.claim_id, Claim.conversation_id).where(Claim.org == org))]
        entities = [dict(r._mapping) for r in s.execute(sa.select(
            ClaimEntity.claim_id, ClaimEntity.role, ClaimEntity.person_id)
            .where(ClaimEntity.org == org, ClaimEntity.person_id.isnot(None)))]
        topics = [dict(r._mapping) for r in s.execute(sa.select(
            ClaimTopic.claim_id, ClaimTopic.topic, ClaimTopic.is_proposal)
            .where(ClaimTopic.org == org))]
        relations = [dict(r._mapping) for r in s.execute(sa.select(
            ClaimRelation.src_claim_id, ClaimRelation.dst_claim_id, ClaimRelation.rel_type)
            .where(ClaimRelation.org == org))]

    stats["claim_artifact"] = _run_batched(driver, """
        UNWIND $batch AS item
        MATCH (c:Claim {claim_id: item.claim_id})
        MATCH (a:Artifact {artifact_id: item.conversation_id})
        MERGE (c)-[:FROM_CONVERSATION]->(a)""", conv_ids)

    stats["entity"] = _run_batched(driver, """
        UNWIND $batch AS item
        MATCH (c:Claim {claim_id: item.claim_id})
        MATCH (p:Person {person_id: item.person_id})
        MERGE (c)-[r:ENTITY {role: item.role}]->(p)""", entities)

    stats["about_topic"] = _run_batched(driver, """
        UNWIND $batch AS item
        MATCH (c:Claim {claim_id: item.claim_id})
        MATCH (t:Topic {org: item.org, name: item.topic})
        MERGE (c)-[r:ABOUT_TOPIC]->(t)
        SET r.is_proposal = item.is_proposal""",
        [{**t, "org": org} for t in topics])

    by_type = shape_relations(relations)
    rel_total = 0
    for rel_type, pairs in by_type.items():
        if rel_type not in RELATION_TYPES:
            log.warning("[project] unknown relation type %s, skipping %d edges",
                       rel_type, len(pairs))
            continue
        rel_total += _run_batched(driver, f"""
            UNWIND $batch AS item
            MATCH (a:Claim {{claim_id: item.src}})
            MATCH (b:Claim {{claim_id: item.dst}})
            MERGE (a)-[:{rel_type}]->(b)""", pairs)
    stats["claim_relations"] = rel_total
    return stats


def write_l2_edges(driver, org: str, store) -> dict:
    stats = {}
    with store._session() as s:
        authored = [dict(r._mapping) for r in s.execute(sa.select(
            EdgeAuthored.person_id, EdgeAuthored.artifact_id, EdgeAuthored.weight,
            EdgeAuthored.first_at, EdgeAuthored.last_at).where(EdgeAuthored.org == org))]
        participated = [dict(r._mapping) for r in s.execute(sa.select(
            EdgeParticipatedIn.person_id, EdgeParticipatedIn.artifact_id,
            EdgeParticipatedIn.weight, EdgeParticipatedIn.first_at, EdgeParticipatedIn.last_at)
            .where(EdgeParticipatedIn.org == org))]
        comm = [dict(r._mapping) for r in s.execute(sa.select(
            EdgeCommunicatesWith.person_id, EdgeCommunicatesWith.other_person_id,
            EdgeCommunicatesWith.weight, EdgeCommunicatesWith.first_at,
            EdgeCommunicatesWith.last_at).where(EdgeCommunicatesWith.org == org))]
        member_of = [dict(r._mapping) for r in s.execute(sa.select(
            EdgeMemberOf.person_id, EdgeMemberOf.container_id)
            .where(EdgeMemberOf.org == org))]

    stats["authored"] = _run_batched(driver, """
        UNWIND $batch AS item
        MATCH (p:Person {person_id: item.person_id})
        MATCH (a:Artifact {artifact_id: item.artifact_id})
        MERGE (p)-[r:AUTHORED]->(a)
        SET r.weight = item.weight, r.first_at = item.first_at, r.last_at = item.last_at""",
        authored)
    stats["participated_in"] = _run_batched(driver, """
        UNWIND $batch AS item
        MATCH (p:Person {person_id: item.person_id})
        MATCH (a:Artifact {artifact_id: item.artifact_id})
        MERGE (p)-[r:PARTICIPATED_IN]->(a)
        SET r.weight = item.weight, r.first_at = item.first_at, r.last_at = item.last_at""",
        participated)
    stats["communicates_with"] = _run_batched(driver, """
        UNWIND $batch AS item
        MATCH (a:Person {person_id: item.person_id})
        MATCH (b:Person {person_id: item.other_person_id})
        MERGE (a)-[r:COMMUNICATES_WITH]->(b)
        SET r.weight = item.weight, r.first_at = item.first_at, r.last_at = item.last_at""",
        comm)
    stats["member_of"] = _run_batched(driver, """
        UNWIND $batch AS item
        MATCH (p:Person {person_id: item.person_id})
        MATCH (c:Container {container_id: item.container_id})
        MERGE (p)-[:MEMBER_OF]->(c)""", member_of)
    return stats


# ── Entry point ───────────────────────────────────────────────────────────

def build(org: str, claims_only: bool = False) -> dict:
    """claims_only=True skips the static L1 nodes (persons/orgs/containers/
    artifacts) and the L2 edge pass — those only change when new EVENTS land,
    not when new claims land. The flow daemon uses it to refresh the graph
    every few minutes while extraction runs, at seconds of cost."""
    from neo4j import GraphDatabase

    store = LedgerStore(_resolve_dsn())
    if not store.connect():
        raise RuntimeError("project_claims: could not connect to Postgres.")

    uri, user, pw = claims_neo4j_credentials_for(org)
    if not uri or not pw:
        raise RuntimeError("project_claims: CLAIMS_NEO4J_URI/CLAIMS_NEO4J_PASSWORD not set.")
    driver = GraphDatabase.driver(uri, auth=(user, pw))
    driver.verify_connectivity()
    log.info("[project] connected to %s%s", uri, " (claims-only)" if claims_only else "")

    try:
        with driver.session() as session:
            for stmt in CONSTRAINTS:
                session.run(stmt)
        node_stats = write_nodes(driver, org, store, claims_only)
        log.info("[project] nodes: %s", node_stats)
        claim_edge_stats = write_claim_edges(driver, org, store)
        log.info("[project] claim edges: %s", claim_edge_stats)
        l2_stats = {}
        if not claims_only:
            l2_stats = write_l2_edges(driver, org, store)
            log.info("[project] L2 edges: %s", l2_stats)
    finally:
        driver.close()
        store.disconnect()

    stats = {**node_stats, **claim_edge_stats, **l2_stats}
    log.info("[project] STAGE_SUMMARY stage=project_claims org=%s %s", org, stats)
    log.info("[project] DONE %s", stats)
    return stats


def main() -> None:
    ap = argparse.ArgumentParser(description="Project L1+L2+L3 into the claim-substrate Neo4j (step 7)")
    ap.add_argument("--org", required=True)
    ap.add_argument("--claims-only", action="store_true",
                    help="Refresh claims/topics/claim-edges only (skip static L1 nodes + L2 edges)")
    args = ap.parse_args()
    build(args.org, args.claims_only)


if __name__ == "__main__":
    main()
