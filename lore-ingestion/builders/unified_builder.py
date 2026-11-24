"""
builders/unified_builder.py
────────────────────────────
Build Neo4j Cypher files and upload NVIDIA NeMo embeddings to Neo4j nodes.

Reads validated (+ optionally deduped) triples, person registry, and comm graph:

  Pass 0 — output/cypher/{org}_pass0_schema.cypher      (constraints + vector indexes)
  Pass 1 — output/cypher/{org}_pass1_persons.cypher     (Person, Organization, Topic nodes)
  Pass 2 — output/cypher/{org}_pass2_chunk_*.cypher     (Knowledge nodes + edges)
  Pass 3 — uploads NVIDIA NeMo embeddings to Neo4j node properties via Python driver

Embeddings must be pre-computed by pipeline/embed_knowledge.py before running
with --upload. Nodes receive an `embedding` property; Neo4j vector indexes
enable semantic search directly in Cypher (GraphRAG).

Usage:
    python -m builders.unified_builder --org ezobooks
    python -m builders.unified_builder --org ezobooks --skip-upload
"""

import json
import time
from collections import defaultdict
from pathlib import Path

import numpy as np

from builders.cypher_utils import cypher_escape as _esc
from common.config import (
    MissingConfigError,
    get_settings,
    neo4j_credentials_for,
    validate_for,
)
from common.logger import get_logger, setup_logging
from common.stage_gate import check_nonempty, check_scoped
from common.stage_summary import log_summary
from pipeline.input_selection import latest_nonempty_path

log = get_logger("builders.unified_builder")

CYPHER_CHUNK_SIZE = 500  # triples per Cypher file
NEO4J_UPLOAD_BATCH = 500  # nodes per UNWIND batch when uploading embeddings
# Read actual embedding dimension from settings so vector indexes stay in sync
_EMBED_DIM = get_settings().embedding_dim


def _split_cypher_statements(cypher_text: str) -> list[str]:
    """Split a generated Cypher file into executable statements.

    The generated artifacts are line-oriented and terminate each statement with
    a semicolon. We ignore comment-only / blank lines and execute each completed
    statement in order.
    """
    statements: list[str] = []
    current: list[str] = []

    for line in cypher_text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("//"):
            continue

        current.append(line)
        if stripped.endswith(";"):
            statement = "\n".join(current).strip()
            if statement:
                statements.append(statement)
            current = []

    trailing = "\n".join(current).strip()
    if trailing:
        statements.append(trailing)

    return statements


def _apply_cypher_file(driver, cypher_path: Path) -> int:
    """Apply a single Cypher file to Neo4j and return statement count."""
    cypher_text = cypher_path.read_text(encoding="utf-8")
    statements = _split_cypher_statements(cypher_text)
    if not statements:
        log.info("  Skipping empty Cypher file: %s", cypher_path.name)
        return 0

    log.info("  Applying %s (%d statements)", cypher_path.name, len(statements))
    try:
        with driver.session() as session:
            for statement in statements:
                session.run(statement).consume()
    except Exception as exc:
        raise RuntimeError(f"Failed while applying Cypher file {cypher_path.name}") from exc

    log.info("  Applied %s", cypher_path.name)
    return len(statements)


def _apply_generated_cypher(cypher_dir: Path, org: str,
                            neo4j_uri: str, neo4j_user: str, neo4j_pass: str) -> dict:
    """Apply generated Cypher passes to Neo4j in the required order."""
    try:
        from neo4j import GraphDatabase
    except ImportError:
        raise ImportError(
            "neo4j driver not installed. Run: pip install neo4j  (from lore-ingestion/)"
        )

    ordered_files: list[Path] = []
    pass0 = cypher_dir / f"{org}_pass0_schema.cypher"
    pass1 = cypher_dir / f"{org}_pass1_persons.cypher"
    ordered_files.extend([pass0, pass1])
    # Optional Jira ACL pass (also org-prefixed)
    pass0_jira = cypher_dir / f"{org}_pass0_jira_acl.cypher"
    if pass0_jira.exists():
        ordered_files.insert(1, pass0_jira)  # between pass0 and pass1
    ordered_files.extend(sorted(cypher_dir.glob(f"{org}_pass2_chunk_*.cypher")))
    # CORROBORATES edges reference nodes created in pass2 chunks, so they go last in pass2
    pass2_corr = cypher_dir / f"{org}_pass2_corroborates.cypher"
    if pass2_corr.exists():
        ordered_files.append(pass2_corr)
    ordered_files.extend(sorted(cypher_dir.glob(f"{org}_pass3*.cypher")))

    driver = GraphDatabase.driver(neo4j_uri, auth=(neo4j_user, neo4j_pass))
    try:
        driver.verify_connectivity()
        total_files = 0
        total_statements = 0
        for cypher_path in ordered_files:
            if not cypher_path.exists():
                continue
            total_statements += _apply_cypher_file(driver, cypher_path)
            total_files += 1
    except Exception as exc:
        raise RuntimeError("Neo4j Cypher application failed") from exc
    finally:
        driver.close()

    return {"files": total_files, "statements": total_statements}


def _collect_graph_summary(neo4j_uri: str, neo4j_user: str, neo4j_pass: str) -> dict:
    """Collect a small graph summary after apply/upload."""
    try:
        from neo4j import GraphDatabase
    except ImportError:
        raise ImportError(
            "neo4j driver not installed. Run: pip install neo4j  (from lore-ingestion/)"
        )

    driver = GraphDatabase.driver(neo4j_uri, auth=(neo4j_user, neo4j_pass))
    try:
        driver.verify_connectivity()
        queries = {
            "knowledge_nodes": "MATCH (n:KnowledgeNode) RETURN count(n) AS c, count(n.embedding) AS e",
            "persons": "MATCH (p:Person) RETURN count(p) AS c, count(p.embedding) AS e",
            "scoped": "MATCH (n:KnowledgeNode) WHERE n.scope_ids IS NOT NULL AND size(n.scope_ids) > 0 RETURN count(n) AS c",
            "unscoped": "MATCH (n:KnowledgeNode) WHERE n.scope_ids IS NULL OR size(n.scope_ids) = 0 RETURN count(n) AS c",
        }
        with driver.session() as session:
            knowledge = session.run(queries["knowledge_nodes"]).single()
            persons = session.run(queries["persons"]).single()
            scoped = session.run(queries["scoped"]).single()
            unscoped = session.run(queries["unscoped"]).single()
        return {
            "knowledge_nodes": knowledge["c"],
            "knowledge_embeddings": knowledge["e"],
            "persons": persons["c"],
            "person_embeddings": persons["e"],
            "scoped": scoped["c"],
            "unscoped": unscoped["c"],
        }
    finally:
        driver.close()


# ══════════════════════════════════════════════════════════════════════
# DATA LOADING
# ══════════════════════════════════════════════════════════════════════

def _discover_triples(org: str, data_dir: Path, source: str | None) -> dict:
    """Load and merge triples from one or all sources.

    With source=None (multi-source build): discovers every per-source deduped
    file (ezobooks_slack_triples_deduped.json, ezobooks_jira_triples_deduped.json,
    …) plus the legacy unsuffixed file (Gmail / pre-namespacing runs).
    Thread IDs are source-namespaced so there are no key collisions.

    With an explicit source: loads only that source's file (fails loudly if
    missing).
    """
    from common.output_paths import KnowledgePaths
    triples_dir = data_dir / "knowledge" / "triples"

    if source is not None:
        src_key = source if source != "gmail" else None
        paths = KnowledgePaths(org=org, data_dir=data_dir, source=src_key)
        candidate = paths.triples_deduped if paths.triples_deduped.exists() else paths.triples_validated
        log.info("Loading triples (%s) from: %s", source, candidate.name)
        return json.loads(candidate.read_text(encoding="utf-8"))

    # Multi-source: merge all per-source deduped files + legacy
    merged: dict = {}

    # Named-source files first  (e.g. ezobooks_slack_triples_deduped.json)
    for named_deduped in sorted(triples_dir.glob(f"{org}_*_triples_deduped.json")):
        threads = json.loads(named_deduped.read_text(encoding="utf-8"))
        log.info("  Merged %d threads from %s", len(threads), named_deduped.name)
        merged.update(threads)

    # Fall back to per-source validated if deduped doesn't exist
    for named_validated in sorted(triples_dir.glob(f"{org}_*_triples_validated.json")):
        deduped_equiv = named_validated.parent / named_validated.name.replace(
            "_triples_validated", "_triples_deduped")
        if deduped_equiv.exists():
            continue  # already loaded above
        threads = json.loads(named_validated.read_text(encoding="utf-8"))
        log.info("  Merged %d threads from %s (no deduped)", len(threads), named_validated.name)
        merged.update(threads)

    # Legacy unsuffixed file (Gmail / pre-namespacing artifacts)
    legacy_paths = KnowledgePaths(org=org, data_dir=data_dir)  # source=None
    legacy = (legacy_paths.triples_deduped if legacy_paths.triples_deduped.exists()
              else legacy_paths.triples_validated)
    if legacy.exists():
        threads = json.loads(legacy.read_text(encoding="utf-8"))
        log.info("  Merged %d threads from legacy %s", len(threads), legacy.name)
        merged.update(threads)

    if not merged:
        log.warning("No triples found for org '%s' — run extraction first", org)
    log.info("Total threads loaded: %d", len(merged))
    return merged


def load_inputs(data_dir: Path, org: str, source: str | None = None,
                allow_unscoped: bool = False) -> dict:
    """Load all input files for building."""
    from common.output_paths import KnowledgePaths
    from pipeline.identity.registry_view import registry_source

    validated = _discover_triples(org, data_dir, source)
    paths = KnowledgePaths(org=org, data_dir=data_dir)

    # Prefer the centralized resolver's resolved_registry (W4), fall back to the
    # legacy Gmail person_registry — same chooser the validator/extractor use.
    registry_path = registry_source(org, data_dir)
    log.info("Registry source: %s", registry_path.name)
    registry = json.loads(registry_path.read_text(encoding="utf-8"))

    comm_graph = (json.loads(paths.comm_graph.read_text(encoding="utf-8"))
                  if paths.comm_graph.exists() else None)

    email_to_pid = registry.get("email_to_person_id", {})
    persons_by_id = {p["person_id"]: p for p in registry.get("persons", [])}

    # Build name → person_id for resolving triple names to registry
    name_to_pid: dict[str, str] = {}
    for p in registry.get("persons", []):
        pid = p["person_id"]
        for name in [p.get("canonical_name", "")] + p.get("all_names", []):
            if name:
                name_to_pid[name.lower()] = pid

    # ACL safety gate (fail-loud). scope_id is stamped by the Slack/Jira
    # normalizers and carried through grouping → extraction → validation;
    # Gmail threads instead carry a scope_ids list populated at extraction.
    # A thread with source_ref set (Slack/Jira/any ACL-bearing source) but no
    # scope info means a broken capability flag (common/sources.py) or a
    # normalizer bug — its nodes would be written with an empty scope_ids
    # array and be invisible to EVERY user. Refuse to build a silently
    # invisible graph; --allow-unscoped downgrades this to a warning.
    def _has_scope(thread: dict) -> bool:
        if thread.get("scope_id"):
            return True
        sids = thread.get("scope_ids")
        return isinstance(sids, list) and len(sids) > 0

    unscoped_acl = [
        tid for tid, thread in validated.items()
        if thread.get("source_ref") and not _has_scope(thread)
    ]
    check_scoped("unified_builder", unscoped=unscoped_acl,
                 total=len(validated), allow_unscoped=allow_unscoped)

    # Gmail/legacy threads (no source_ref) without scope info also fail
    # closed, but legacy pre-scope artifacts exist, so warn instead of raise.
    unscoped_legacy = [
        tid for tid, thread in validated.items()
        if not thread.get("source_ref") and not _has_scope(thread)
    ]
    if unscoped_legacy:
        log.warning(
            "%d Gmail/legacy thread(s) have no scope info — their nodes get an "
            "empty scope_ids array and stay invisible to scoped queries "
            "(fail-closed). First few: %s",
            len(unscoped_legacy), unscoped_legacy[:5],
        )

    log.info("Loaded: %d validated threads, %d persons, comm_graph=%s",
             len(validated), len(persons_by_id),
             "yes" if comm_graph else "no")

    return {
        "validated": validated,
        "registry": registry,
        "comm_graph": comm_graph,
        "persons_by_id": persons_by_id,
        "email_to_pid": email_to_pid,
        "name_to_pid": name_to_pid,
    }


# ══════════════════════════════════════════════════════════════════════
# PASS 0: SCHEMA (constraints + indexes)
# ══════════════════════════════════════════════════════════════════════

def generate_schema_cypher() -> str:
    """Generate Cypher for constraints and indexes."""
    lines = [
        "// ── Pass 0: Schema constraints + indexes ──",
        "",
        "CREATE CONSTRAINT person_id IF NOT EXISTS "
        "FOR (p:Person) REQUIRE p.person_id IS UNIQUE;",
        "",
        "CREATE CONSTRAINT decision_id IF NOT EXISTS "
        "FOR (d:Decision) REQUIRE d.decision_id IS UNIQUE;",
        "",
        "CREATE CONSTRAINT task_id IF NOT EXISTS "
        "FOR (t:Task) REQUIRE t.task_id IS UNIQUE;",
        "",
        "CREATE CONSTRAINT fact_id IF NOT EXISTS "
        "FOR (f:Fact) REQUIRE f.fact_id IS UNIQUE;",
        "",
        "CREATE CONSTRAINT topic_id IF NOT EXISTS "
        "FOR (t:Topic) REQUIRE t.topic_id IS UNIQUE;",
        "",
        "CREATE CONSTRAINT org_id IF NOT EXISTS "
        "FOR (o:Organization) REQUIRE o.org_id IS UNIQUE;",
        "",
        "CREATE CONSTRAINT event_id IF NOT EXISTS "
        "FOR (e:Event) REQUIRE e.event_id IS UNIQUE;",
        "",
        "// ── Jira structured node constraints ──",
        "CREATE CONSTRAINT issue_id IF NOT EXISTS "
        "FOR (i:Issue) REQUIRE i.issue_id IS UNIQUE;",
        "",
        "CREATE CONSTRAINT sprint_id IF NOT EXISTS "
        "FOR (s:Sprint) REQUIRE s.sprint_id IS UNIQUE;",
        "",
        "CREATE CONSTRAINT component_id IF NOT EXISTS "
        "FOR (c:Component) REQUIRE c.component_id IS UNIQUE;",
        "",
        "CREATE CONSTRAINT version_id IF NOT EXISTS "
        "FOR (v:Version) REQUIRE v.version_id IS UNIQUE;",
        "",
        "// Indexes for common lookups",
        "CREATE INDEX person_name IF NOT EXISTS "
        "FOR (p:Person) ON (p.name);",
        "",
        "CREATE INDEX decision_date IF NOT EXISTS "
        "FOR (d:Decision) ON (d.date);",
        "",
        "CREATE INDEX fact_domain IF NOT EXISTS "
        "FOR (f:Fact) ON (f.domain);",
        "",
        f"// ── Vector indexes ({_EMBED_DIM}-dim embeddings) ──",
        "// KnowledgeNode is a secondary label on Decision/Task/Fact/Event",
        "// for single-call cross-type vector search.",
        "CREATE VECTOR INDEX knowledge_embeddings IF NOT EXISTS",
        "FOR (n:KnowledgeNode) ON (n.embedding)",
        f"OPTIONS {{indexConfig: {{`vector.dimensions`: {_EMBED_DIM}, "
        "`vector.similarity_function`: 'cosine'}};",
        "",
        "CREATE VECTOR INDEX person_embeddings IF NOT EXISTS",
        "FOR (n:Person) ON (n.embedding)",
        f"OPTIONS {{indexConfig: {{`vector.dimensions`: {_EMBED_DIM}, "
        "`vector.similarity_function`: 'cosine'}};",
        "",
        "CREATE VECTOR INDEX org_embeddings IF NOT EXISTS",
        "FOR (n:Organization) ON (n.embedding)",
        f"OPTIONS {{indexConfig: {{`vector.dimensions`: {_EMBED_DIM}, "
        "`vector.similarity_function`: 'cosine'}};",
        "",
    ]
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════
# PASS 0 (JIRA): AclScope nodes
# ══════════════════════════════════════════════════════════════════════

def generate_jira_acl_cypher(org: str, data_dir: Path) -> str | None:
    """Read the Jira discovery artifact and emit MERGE Cypher for :AclScope nodes.

    Returns None when the artifact is absent (Jira not configured for this org).
    No participants property is written — that is an explicit hard limit.
    """
    from common.output_paths import JiraPaths
    artifact_path = JiraPaths(org, data_dir).scope_principals
    if not artifact_path.exists():
        return None

    try:
        artifact = json.loads(artifact_path.read_text(encoding="utf-8"))
    except Exception as exc:
        log.warning("Could not read Jira artifact %s: %s", artifact_path, exc)
        return None

    scopes = artifact.get("scopes", [])
    if not scopes:
        return "// No Jira AclScope nodes to create\n"

    lines = [
        "// ── Pass 0 (Jira): AclScope nodes ──",
        f"// Source: {artifact_path.name}",
        "",
        "CREATE CONSTRAINT acl_scope_id IF NOT EXISTS",
        "FOR (s:AclScope) REQUIRE s.scope_id IS UNIQUE;",
        "",
    ]

    for scope in scopes:
        sid = _esc(scope.get("scope_id", ""))
        kind = _esc(scope.get("kind", ""))
        name = _esc(scope.get("name", ""))
        is_org_wide = str(scope.get("is_org_wide", False)).lower()
        tenant_id = _esc(scope.get("tenant_id", org))

        lines += [
            f"MERGE (s:AclScope {{scope_id: '{sid}'}})",
            "ON CREATE SET",
            f"  s.source = 'jira',",
            f"  s.kind = '{kind}',",
            f"  s.name = '{name}',",
            f"  s.is_org_wide = {is_org_wide},",
            f"  s.tenant_id = '{tenant_id}',",
            "  s.created_at = datetime()",
            "ON MATCH SET s.updated_at = datetime();",
            "",
        ]

    log.info("Jira ACL: %d AclScope nodes", len(scopes))
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════
# PASS 1: PERSONS + ORGANIZATIONS + TOPICS
# ══════════════════════════════════════════════════════════════════════

def generate_persons_cypher(data: dict) -> str:
    """Generate Cypher for Person, Organization, and Topic nodes."""
    lines = ["// ── Pass 1: Persons, Organizations, Topics ──", ""]
    registry = data["registry"]
    comm_graph = data["comm_graph"]
    validated = data["validated"]

    # ── Person nodes from registry ────────────────────────────────
    for person in registry.get("persons", []):
        pid = person["person_id"]
        name = _esc(person.get("canonical_name", ""))
        primary_email = _esc(person.get("primary_email", ""))
        is_internal = person.get("is_internal", False)
        is_functional = person.get("is_functional", False)
        all_emails = person.get("all_emails", [])
        stats = person.get("stats", {})

        lines.append(f"MERGE (p:Person {{person_id: '{_esc(pid)}'}})")
        lines.append(f"ON CREATE SET")
        lines.append(f"  p.name = '{name}',")
        lines.append(f"  p.primary_email = '{primary_email}',")
        lines.append(f"  p.all_emails = {json.dumps(all_emails)},")
        lines.append(f"  p.is_internal = {str(is_internal).lower()},")
        lines.append(f"  p.is_functional = {str(is_functional).lower()},")
        lines.append(f"  p.emails_sent = {stats.get('emails_sent', 0)},")
        lines.append(f"  p.emails_received = {stats.get('emails_received', 0)},")
        lines.append(f"  p.total_activity = {stats.get('total_activity', 0)},")
        lines.append(f"  p.created_at = datetime()")
        lines.append(f"ON MATCH SET")
        lines.append(f"  p.name = '{name}',")
        lines.append(f"  p.primary_email = '{primary_email}',")
        lines.append(f"  p.all_emails = {json.dumps(all_emails)},")
        lines.append(f"  p.is_internal = {str(is_internal).lower()},")
        lines.append(f"  p.is_functional = {str(is_functional).lower()},")
        lines.append(f"  p.emails_sent = {stats.get('emails_sent', 0)},")
        lines.append(f"  p.emails_received = {stats.get('emails_received', 0)},")
        lines.append(f"  p.total_activity = {stats.get('total_activity', 0)},")
        lines.append(f"  p.updated_at = datetime();")
        lines.append("")

    # ── Comm graph edges (COMMUNICATES_WITH) ──────────────────────
    if comm_graph:
        lines.append("// Communication graph edges")
        for edge in comm_graph.get("edges", []):
            from_pid = _esc(edge.get("from", ""))
            to_pid = _esc(edge.get("to", ""))
            weight = edge.get("weight", 1)
            if from_pid and to_pid:
                lines.append(
                    f"MATCH (a:Person {{person_id: '{from_pid}'}}), "
                    f"(b:Person {{person_id: '{to_pid}'}})")
                lines.append(
                    f"MERGE (a)-[r:COMMUNICATES_WITH]->(b)")
                lines.append(
                    f"ON CREATE SET r.weight = {weight}, "
                    f"r.created_at = datetime();")
                lines.append("")

    # ── Collect unique Organizations and Topics from triples ──────
    orgs_seen: dict[str, str] = {}   # node_id → name
    topics_seen: dict[str, str] = {}  # node_id → name

    for thread in validated.values():
        for triple in thread.get("triples", []):
            for node in (triple.get("subject", {}), triple.get("object", {})):
                ntype = node.get("type", "")
                name = node.get("name") or node.get("description") or ""
                nid = node.get("node_id", "")
                if ntype == "Organization" and nid:
                    orgs_seen[nid] = name
                elif ntype == "Topic" and nid:
                    topics_seen[nid] = name

    # Organization nodes
    if orgs_seen:
        lines.append("// Organization nodes")
        for nid, name in sorted(orgs_seen.items()):
            lines.append(
                f"MERGE (o:Organization {{org_id: '{_esc(nid)}'}})")
            lines.append(
                f"ON CREATE SET o.name = '{_esc(name)}', "
                f"o.created_at = datetime();")
            lines.append("")

    # Topic nodes
    if topics_seen:
        lines.append("// Topic nodes")
        for nid, name in sorted(topics_seen.items()):
            lines.append(
                f"MERGE (t:Topic {{topic_id: '{_esc(nid)}'}})")
            lines.append(
                f"ON CREATE SET t.name = '{_esc(name)}', "
                f"t.created_at = datetime();")
            lines.append("")

    # ── Jira structural nodes + Jira Person nodes (from triples) ──────────────
    jira_nodes = _collect_jira_structural_nodes(validated)
    if jira_nodes:
        lines.extend(jira_nodes)

    log.info("Pass 1: %d persons, %d organizations, %d topics",
             len(registry.get("persons", [])),
             len(orgs_seen), len(topics_seen))

    return "\n".join(lines)


def _collect_jira_structural_nodes(validated: dict) -> list[str]:
    """Scan triples and emit MERGE Cypher for Jira structural nodes.

    Covers:
      • Sprint / Component / Version  — taxonomy nodes, no KnowledgeNode label.
      • Jira Person nodes             — accountId-keyed, get :Person:JiraPerson label
                                        so the central resolver can distinguish them.

    These must be created in pass 1 (before the edges in pass 2 try to MATCH them).
    """
    sprints_seen: dict[str, dict] = {}
    components_seen: dict[str, dict] = {}
    versions_seen: dict[str, dict] = {}
    jira_persons_seen: dict[str, dict] = {}   # node_id → node dict

    for thread in validated.values():
        for triple in thread.get("triples", []):
            if triple.get("derived") != "field":
                continue  # structural nodes only come from deterministic triples
            for node_key in ("subject", "object"):
                node = triple.get(node_key, {})
                ntype = node.get("type", "")
                nid = node.get("node_id", "")
                if not nid:
                    continue
                if ntype == "Sprint":
                    sprints_seen.setdefault(nid, node)
                elif ntype == "Component":
                    components_seen.setdefault(nid, node)
                elif ntype == "Version":
                    versions_seen.setdefault(nid, node)
                elif ntype == "Person" and node.get("_account_type"):
                    # Jira person (has _account_type from deterministic builder)
                    jira_persons_seen.setdefault(nid, node)

    lines: list[str] = []

    # Sprint nodes
    if sprints_seen:
        lines.append("// Jira Sprint nodes")
        for nid, node in sorted(sprints_seen.items()):
            nid_e = _esc(nid)
            name = _esc(node.get("_sprint_name") or node.get("name") or "")
            lines += [
                f"MERGE (s:Sprint {{sprint_id: '{nid_e}'}})",
                f"ON CREATE SET s.name = '{name}', s.created_at = datetime();",
                "",
            ]

    # Component nodes
    if components_seen:
        lines.append("// Jira Component nodes")
        for nid, node in sorted(components_seen.items()):
            nid_e = _esc(nid)
            name = _esc(node.get("_component_name") or node.get("name") or "")
            lines += [
                f"MERGE (c:Component {{component_id: '{nid_e}'}})",
                f"ON CREATE SET c.name = '{name}', c.created_at = datetime();",
                "",
            ]

    # Version nodes
    if versions_seen:
        lines.append("// Jira Version nodes")
        for nid, node in sorted(versions_seen.items()):
            nid_e = _esc(nid)
            name = _esc(node.get("_version_name") or node.get("name") or "")
            lines += [
                f"MERGE (v:Version {{version_id: '{nid_e}'}})",
                f"ON CREATE SET v.name = '{name}', v.created_at = datetime();",
                "",
            ]

    # Jira Person nodes (accountId-keyed, :Person:JiraPerson so resolver can link them)
    if jira_persons_seen:
        lines.append("// Jira Person nodes (accountId-keyed)")
        for nid, node in sorted(jira_persons_seen.items()):
            nid_e = _esc(nid)
            account_id = _esc(node.get("name") or "")  # name = accountId in deterministic
            display_name = _esc(node.get("_display_name") or account_id)
            account_type = _esc(node.get("_account_type") or "atlassian")
            email = _esc(node.get("_email") or "")
            lines += [
                f"MERGE (p:Person:JiraPerson {{person_id: '{nid_e}'}})",
                f"ON CREATE SET",
                f"  p.account_id = '{account_id}',",
                f"  p.name = '{display_name}',",
                f"  p.account_type = '{account_type}',",
                f"  p.primary_email = '{email}',",
                f"  p.is_internal = true,",
                f"  p.source = 'jira',",
                f"  p.created_at = datetime()",
                f"ON MATCH SET p.name = '{display_name}', p.updated_at = datetime();",
                "",
            ]

    if sprints_seen or components_seen or versions_seen or jira_persons_seen:
        log.info("Pass 1 (Jira): %d sprints, %d components, %d versions, %d persons",
                 len(sprints_seen), len(components_seen), len(versions_seen),
                 len(jira_persons_seen))

    return lines


# ══════════════════════════════════════════════════════════════════════
# PASS 2: KNOWLEDGE NODES + EDGES (from validated triples)
# ══════════════════════════════════════════════════════════════════════

_NODE_TYPE_TO_LABEL = {
    # Email / Slack / shared knowledge nodes — get :KnowledgeNode secondary label.
    "Decision": "Decision",
    "Task": "Task",
    "Fact": "Fact",
    "Event": "Event",
    # Jira Issue — first-class structured node + KnowledgeNode (semantic search on summary).
    "Issue": "Issue",
}

# Jira structural nodes that become nodes in the graph but are NOT KnowledgeNodes
# (they're metadata / project taxonomy, not knowledge items to embed).
_JIRA_STRUCTURAL_LABELS = {"Sprint", "Component", "Version"}

_NODE_TYPE_TO_ID_FIELD = {
    "Decision": "decision_id",
    "Task": "task_id",
    "Fact": "fact_id",
    "Event": "event_id",
    "Person": "person_id",
    "Organization": "org_id",
    "Topic": "topic_id",
    "Issue": "issue_id",
    "Sprint": "sprint_id",
    "Component": "component_id",
    "Version": "version_id",
}

_NODE_TYPE_TO_ID_PREFIX = {
    "Person": "person_id",
    "Organization": "org_id",
    "Topic": "topic_id",
}


def _collect_node_scope_sets(validated: dict) -> tuple[dict, dict]:
    """Union the scopes a knowledge node inherits across EVERY session it appears in.

    Scope lives at the session/thread level (`thread.scope_id` + `extra_scope_ids`);
    a node inherits the scope of every session it is part of. After dedup merges the
    same node across channels, it appears in multiple sessions, so we union here.

    Returns (or_scopes, and_scopes) keyed by node_id:
      - or_scopes:  any-match gate (Slack channels, Gmail recipients, Jira project)
      - and_scopes: all-match gate (Jira stacked restriction, e.g. security level)
    A node with empty or_scopes gets an empty scope_ids array → fails closed.
    See docs/RBAC/SCOPE_FILTER_HANDOFF.md.
    """
    or_scopes: dict[str, set] = defaultdict(set)
    and_scopes: dict[str, set] = defaultdict(set)
    for thread in validated.values():
        sid = thread.get("scope_id")
        # Gmail threads carry scope_ids (list of participant person_ids).
        # Jira/Slack use the single scope_id field instead.
        or_ids = thread.get("scope_ids")
        or_ids = or_ids if isinstance(or_ids, list) else []
        extra = thread.get("extra_scope_ids")
        extra = extra if isinstance(extra, list) else []
        for triple in thread.get("triples", []):
            for node_key in ("subject", "object"):
                node = triple.get(node_key, {})
                if node.get("type") not in _NODE_TYPE_TO_LABEL:
                    continue
                nid = node.get("node_id", "")
                if not nid:
                    continue
                if or_ids:
                    for s in or_ids:
                        or_scopes[nid].add(s)
                elif sid:
                    or_scopes[nid].add(sid)
                for e in extra:
                    and_scopes[nid].add(e)
    return or_scopes, and_scopes


def _cypher_for_node(node: dict, thread: dict,
                     scope_ids: set, scope_all_ids: set) -> list[str]:
    """Generate MERGE + SET for a knowledge node (Decision/Task/Fact/Event/Issue).

    scope_ids / scope_all_ids are the unioned ACL buckets for this node
    (from _collect_node_scope_sets), not just this one thread's scope.
    """
    ntype = node.get("type", "")
    if ntype not in _NODE_TYPE_TO_LABEL:
        return []

    label = _NODE_TYPE_TO_LABEL[ntype]
    id_field = _NODE_TYPE_TO_ID_FIELD[ntype]
    nid = _esc(node.get("node_id", ""))
    # Issues: use the human summary as the searchable description, not the key.
    if ntype == "Issue":
        desc = _esc(node.get("_summary") or node.get("name") or "")
    else:
        desc = _esc(node.get("name") or node.get("description") or "")
    tid = _esc(thread.get("thread_id", ""))

    dept_tags = thread.get("department_tags", ["general"])
    access_groups = thread.get("access_groups", [])
    sensitivity = thread.get("sensitivity", "internal")
    sens_score = thread.get("sensitivity_score", 0.3)
    source_ref = thread.get("source_ref", "")
    source_url = thread.get("source_url", "")

    var = label[0].lower()  # d, t, f, e

    # ACL gate (see docs/RBAC/SCOPE_FILTER_HANDOFF.md): scope_ids is the OR-bucket
    # (any channel/recipient/project the node was seen in — unioned across every
    # session via _collect_node_scope_sets), scope_all_ids the AND-bucket (Jira
    # stacked restriction). Empty scope_ids → node fails closed at query time.
    scope_ids_sorted = sorted(scope_ids)
    scope_all_sorted = sorted(scope_all_ids)

    # Jira Issue: include structured field properties (key, type, status, etc.) in
    # the same SET block — a second standalone SET would be out of scope.
    extra_set_lines: list[str] = []
    if ntype == "Issue":
        issue_key = _esc(node.get("name", ""))
        issue_type = _esc(node.get("_issue_type", ""))
        status = _esc(node.get("_status", ""))
        priority = _esc(node.get("_priority") or "")
        created = _esc(node.get("_created") or "")
        extra_set_lines.append(f"  {var}.key = '{issue_key}',")
        extra_set_lines.append(f"  {var}.issue_type = '{issue_type}',")
        extra_set_lines.append(f"  {var}.status = '{status}',")
        if priority:
            extra_set_lines.append(f"  {var}.priority = '{priority}',")
        if created:
            extra_set_lines.append(f"  {var}.created_at_jira = '{created}',")

    lines = [
        # :KnowledgeNode secondary label enables single-index cross-type vector search
        f"MERGE ({var}:{label}:KnowledgeNode {{{id_field}: '{nid}'}})",
        f"ON CREATE SET",
        f"  {var}.created_at = datetime()",
        f"SET",
        f"  {var}.description = '{desc}',",
        f"  {var}.scope_ids = {json.dumps(scope_ids_sorted)},",
        (f"  {var}.scope_all_ids = {json.dumps(scope_all_sorted)},") if scope_all_sorted else "",
        f"  {var}.source_thread_id = '{tid}',",
        f"  {var}.source_ref = '{_esc(source_ref)}',",
        f"  {var}.source_url = '{_esc(source_url)}',",
        f"  {var}.department_tags = {json.dumps(dept_tags)},",
        f"  {var}.access_groups = {json.dumps(access_groups)},",
        f"  {var}.sensitivity = {sens_score},",
        f"  {var}.sensitivity_label = '{sensitivity}',",
        *extra_set_lines,
        f"  {var}.updated_at = datetime();",
        "",
    ]
    return lines


def _cypher_for_edge(triple: dict, thread: dict,
                     name_to_pid: dict) -> list[str]:
    """Generate MATCH + MERGE for a relationship edge."""
    subject = triple.get("subject", {})
    predicate = triple.get("predicate", "")
    obj = triple.get("object", {})
    evidence = _esc(triple.get("evidence_sentence", "")[:200])
    tid = _esc(thread.get("thread_id", ""))

    s_type = subject.get("type", "")
    o_type = obj.get("type", "")

    def _match_clause(node: dict) -> str:
        ntype = node.get("type", "")
        nid = _esc(node.get("node_id", ""))
        id_field = _NODE_TYPE_TO_ID_FIELD.get(ntype, "node_id")

        # Person nodes: try registry person_id first; fall back to node_id
        # (Jira persons are keyed by accountId hash, not a registry person_id)
        if ntype == "Person":
            name = (node.get("name") or "").lower()
            pid = name_to_pid.get(name, "")
            if pid:
                return f"Person {{person_id: '{_esc(pid)}'}}"
            return f"Person {{person_id: '{nid}'}}"

        # Org/Topic: use their id field
        if ntype == "Organization":
            return f"Organization {{org_id: '{nid}'}}"
        if ntype == "Topic":
            return f"Topic {{topic_id: '{nid}'}}"

        # Jira structural nodes (Issue/Sprint/Component/Version): use their id field
        if ntype in _NODE_TYPE_TO_ID_FIELD:
            return f"{ntype} {{{id_field}: '{nid}'}}"

        return f"{ntype} {{{id_field}: '{nid}'}}"

    s_match = _match_clause(subject)
    o_match = _match_clause(obj)

    lines = [
        f"MATCH (s:{s_match}), (o:{o_match})",
        f"MERGE (s)-[r:{predicate}]->(o)",
        f"ON CREATE SET r.source_id = '{tid}', "
        f"r.evidence = '{evidence}', "
        f"r.created_at = datetime();",
        "",
    ]
    return lines


def generate_corroborates_cypher(corroborates_pairs: list[dict]) -> str:
    """Generate Cypher for CORROBORATES edges between cross-source content nodes.

    A CORROBORATES edge means two KnowledgeNodes from different sources
    (e.g. Slack + Gmail) describe the same real-world item but live in
    separate nodes — merging them would union two ACL scopes into one node
    (RBAC leak). The edge preserves discoverability without leaking scope.

    Direction is lexicographic on node_id for idempotency. The backend
    should query (a)-[:CORROBORATES]-(b) without direction.
    """
    if not corroborates_pairs:
        return "// No cross-source CORROBORATES edges\n"

    lines = [
        "// ── Pass 2 (cross-source): CORROBORATES edges ──",
        "// Links content nodes from different sources that describe the same",
        "// real-world item. Kept as separate nodes to preserve ACL isolation.",
        "// Query without direction: (a)-[:CORROBORATES]-(b)",
        "",
    ]

    for pair in corroborates_pairs:
        id_a = _esc(pair["node_id_a"])
        id_b = _esc(pair["node_id_b"])
        type_a = pair.get("type_a", "KnowledgeNode")
        type_b = pair.get("type_b", "KnowledgeNode")
        id_field_a = _NODE_TYPE_TO_ID_FIELD.get(type_a, "node_id")
        id_field_b = _NODE_TYPE_TO_ID_FIELD.get(type_b, "node_id")
        sim = pair.get("similarity", 0.0)
        src_a = _esc(pair.get("source_a", ""))
        src_b = _esc(pair.get("source_b", ""))

        lines += [
            f"MATCH (a:{type_a} {{{id_field_a}: '{id_a}'}}), "
            f"(b:{type_b} {{{id_field_b}: '{id_b}'}})",
            f"MERGE (a)-[r:CORROBORATES]->(b)",
            f"ON CREATE SET r.similarity = {sim}, "
            f"r.source_a = '{src_a}', r.source_b = '{src_b}', "
            f"r.created_at = datetime();",
            "",
        ]

    log.info("Corroborates: %d cross-source CORROBORATES edges", len(corroborates_pairs))
    return "\n".join(lines)


def generate_knowledge_cypher(data: dict, org: str) -> list[tuple[str, str]]:
    """Generate Pass 2 Cypher for knowledge nodes + edges.

    Returns list of (filename, cypher_text) tuples, chunked to avoid
    huge single files. Filenames are org-prefixed so runs for different
    orgs never overwrite each other in the shared cypher/ directory.
    """
    validated = data["validated"]
    name_to_pid = data["name_to_pid"]

    # Union each node's ACL scopes across every session it appears in (the merge
    # of the same node across channels accumulates all their scopes here).
    or_scopes, and_scopes = _collect_node_scope_sets(validated)

    # Collect complete statements as atomic units (never split mid-statement)
    all_stmts: list[str] = []
    nodes_created: set[str] = set()
    total_nodes = 0
    total_edges = 0

    for tid, thread in validated.items():
        for triple in thread.get("triples", []):
            # Create knowledge nodes (Decision, Task, Fact, Event)
            for node_key in ("subject", "object"):
                node = triple[node_key]
                nid = node.get("node_id", "")
                ntype = node.get("type", "")
                if ntype in _NODE_TYPE_TO_LABEL and nid not in nodes_created:
                    node_lines = _cypher_for_node(
                        node, thread,
                        or_scopes.get(nid, set()), and_scopes.get(nid, set()))
                    all_stmts.append("\n".join(node_lines))
                    nodes_created.add(nid)
                    total_nodes += 1

            # Create edge
            edge_lines = _cypher_for_edge(triple, thread, name_to_pid)
            all_stmts.append("\n".join(edge_lines))
            total_edges += 1

    log.info("Pass 2: %d knowledge nodes, %d edges", total_nodes, total_edges)

    # Chunk by statement count so boundaries always fall between complete statements
    chunks: list[tuple[str, str]] = []
    for i in range(0, max(1, len(all_stmts)), CYPHER_CHUNK_SIZE):
        chunk_num = i // CYPHER_CHUNK_SIZE
        chunk_stmts = all_stmts[i:i + CYPHER_CHUNK_SIZE]
        header = (f"// ── Pass 2: Knowledge chunk {chunk_num} ──\n"
                  f"// Nodes + edges from validated triples\n\n")
        filename = f"{org}_pass2_chunk_{chunk_num:03d}_knowledge.cypher"
        chunks.append((filename, header + "\n".join(chunk_stmts)))

    if not chunks:
        chunks.append((f"{org}_pass2_chunk_000_knowledge.cypher",
                        "// No knowledge triples to load\n"))

    return chunks


# ══════════════════════════════════════════════════════════════════════
# PASS 3: UPLOAD EMBEDDINGS TO NEO4J
# ══════════════════════════════════════════════════════════════════════

# Maps node type → Neo4j ID property (must match Cypher MERGE in passes 1 + 2)
_NEO4J_ID_FIELD = {
    "Decision":     "decision_id",
    "Task":         "task_id",
    "Fact":         "fact_id",
    "Event":        "event_id",
    "Person":       "person_id",
    "Organization": "org_id",
    "Topic":        "topic_id",
    "Issue":        "issue_id",      # Jira — gets :KnowledgeNode, receives embeddings
}


def upload_embeddings_to_neo4j(
    embeddings_path: Path,
    index_path: Path,
    neo4j_uri: str,
    neo4j_user: str,
    neo4j_pass: str,
) -> dict:
    """Upload pre-computed NVIDIA NeMo embeddings to Neo4j nodes.

    Matches each node by its ID field, sets the `embedding` property.
    Processes each node type in separate batched UNWIND queries.
    Requires the `neo4j` Python driver (uv add neo4j).
    """
    try:
        from neo4j import GraphDatabase
    except ImportError:
        raise ImportError(
            "neo4j driver not installed. Run: uv add neo4j  (from lore-ingestion/)"
        )

    embeddings = np.load(embeddings_path)
    index      = json.loads(index_path.read_text(encoding="utf-8"))
    nodes      = index["nodes"]

    log.info("Uploading %d node embeddings to Neo4j (%s)...", len(nodes), neo4j_uri)

    # Group nodes by type
    by_type: dict[str, list[dict]] = {}
    for node in nodes:
        by_type.setdefault(node["type"], []).append(node)

    driver = GraphDatabase.driver(neo4j_uri, auth=(neo4j_user, neo4j_pass))
    driver.verify_connectivity()

    stats: dict[str, int] = {}
    total_updated = 0

    for ntype, type_nodes in sorted(by_type.items()):
        id_field = _NEO4J_ID_FIELD.get(ntype)
        if not id_field:
            log.warning("  No ID field mapping for type '%s', skipping", ntype)
            continue

        type_updated = 0
        for i in range(0, len(type_nodes), NEO4J_UPLOAD_BATCH):
            batch = type_nodes[i : i + NEO4J_UPLOAD_BATCH]
            batch_data = [
                {"id": n["node_id"], "vec": embeddings[n["row"]].tolist()}
                for n in batch
            ]
            cypher = (
                f"UNWIND $batch AS item "
                f"MATCH (n:{ntype} {{{id_field}: item.id}}) "
                f"SET n.embedding = item.vec "
                f"RETURN count(n) AS updated"
            )
            with driver.session() as session:
                result  = session.run(cypher, batch=batch_data)
                updated = result.single()["updated"]
                type_updated += updated

        stats[ntype] = type_updated
        total_updated += type_updated
        log.info("  %-14s %d / %d nodes updated", ntype, type_updated, len(type_nodes))

    driver.close()

    match_rate = total_updated / len(nodes) if nodes else 1.0
    log.info("  Matched %d / %d nodes (%.1f%%)", total_updated, len(nodes), match_rate * 100)
    if total_updated == 0:
        log.warning("No nodes received embeddings — run passes 0-2 first so nodes exist in Neo4j.")
    elif match_rate < 0.5:
        log.warning(
            "Only %d / %d nodes matched (%.0f%%). "
            "Re-run embed_knowledge.py then retry upload.",
            total_updated, len(nodes), match_rate * 100,
        )

    return {"total_updated": total_updated, "by_type": stats}


# ── Dead-code graveyard starts here — intentionally removed ─────────────────
# build_documents(), _openrouter_embed(), embed_and_save() were the numpy
# serving-index builders. They are replaced by upload_embeddings_to_neo4j()
# above. Neo4j native vector search (GraphRAG) is the new serving path.
# ────────────────────────────────────────────────────────────────────────────


# ══════════════════════════════════════════════════════════════════════
# MAIN BUILDER
# ══════════════════════════════════════════════════════════════════════

def run_builder(
    org: str,
    data_dir: Path,
    neo4j_uri: str = "",
    neo4j_user: str = "",
    neo4j_pass: str = "",
    skip_upload: bool = False,
    upload_only: bool = False,
    allow_empty: bool = False,
    source: str | None = None,
    allow_unscoped: bool = False,
):
    """Run the full unified build pipeline.

    Passes 0–2 generate Cypher files for Neo4j graph structure.
    Pass 3 uploads NVIDIA NeMo embeddings (from embed_knowledge.py) to nodes.

    --skip-upload  Skip pass 3 (generate Cypher only).
    --upload-only  Skip passes 0–2, only upload embeddings to existing Neo4j nodes.
    """
    start = time.time()

    log.info("=" * 55)
    log.info("UNIFIED BUILDER")
    log.info("  Org: %s", org)
    log.info("=" * 55)

    from common.output_paths import KnowledgePaths
    # source=None for embeddings in multi-source build: the builder embeds nodes
    # from all merged sources together into a single embedding index. Per-source
    # builds use source-namespaced embedding files.
    src_key = source if source and source != "gmail" else None
    paths = KnowledgePaths(org=org, data_dir=data_dir, source=src_key)

    jira_acl_written = False
    knowledge_chunks: list[tuple[str, str]] = []
    cypher_dir = data_dir / "cypher"

    if not upload_only:
        data = load_inputs(data_dir, org, source=source,
                           allow_unscoped=allow_unscoped)

        # ── Cypher generation (passes 0–2) ────────────────────────────
        cypher_dir.mkdir(parents=True, exist_ok=True)

        # Migration: remove any stale non-org-prefixed pass2 chunks from a
        # prior run that used the old (unnamespaced) filename convention.
        # These would otherwise be re-applied on the next Neo4j run for a
        # different org, silently contaminating the graph.
        for stale in cypher_dir.glob("pass2_chunk_*.cypher"):
            stale.unlink()
            log.info("  Removed stale unnamespaced chunk: %s", stale.name)

        schema_cypher = generate_schema_cypher()
        (cypher_dir / f"{org}_pass0_schema.cypher").write_text(schema_cypher, encoding="utf-8")
        log.info("  Wrote %s_pass0_schema.cypher", org)

        jira_acl_cypher = generate_jira_acl_cypher(org, data_dir)
        if jira_acl_cypher is not None:
            (cypher_dir / f"{org}_pass0_jira_acl.cypher").write_text(jira_acl_cypher, encoding="utf-8")
            log.info("  Wrote %s_pass0_jira_acl.cypher", org)
            jira_acl_written = True

        persons_cypher = generate_persons_cypher(data)
        (cypher_dir / f"{org}_pass1_persons.cypher").write_text(persons_cypher, encoding="utf-8")
        log.info("  Wrote %s_pass1_persons.cypher", org)

        knowledge_chunks = generate_knowledge_cypher(data, org=org)
        for filename, cypher_text in knowledge_chunks:
            (cypher_dir / filename).write_text(cypher_text, encoding="utf-8")
        log.info("  Wrote %d pass2 knowledge chunk(s)", len(knowledge_chunks))

        # CORROBORATES edges (cross-source; written by the deduplicator)
        corr_pairs: list[dict] = []
        if paths.corroborates.exists():
            corr_pairs = json.loads(paths.corroborates.read_text(encoding="utf-8"))
            log.info("  Loaded %d CORROBORATES pairs from %s",
                     len(corr_pairs), paths.corroborates.name)
        corr_cypher = generate_corroborates_cypher(corr_pairs)
        (cypher_dir / f"{org}_pass2_corroborates.cypher").write_text(corr_cypher, encoding="utf-8")
        log.info("  Wrote %s_pass2_corroborates.cypher (%d edges)", org, len(corr_pairs))

    apply_status: str
    if skip_upload:
        log.info("  Neo4j Cypher application skipped (--skip-upload)")
        apply_status = "skipped_flag"
    elif upload_only:
        log.info("  Neo4j Cypher application skipped (--upload-only)")
        apply_status = "skipped_upload_only"
    else:
        apply_stats = _apply_generated_cypher(
            cypher_dir=cypher_dir,
            org=org,
            neo4j_uri=neo4j_uri,
            neo4j_user=neo4j_user,
            neo4j_pass=neo4j_pass,
        )
        apply_status = "ok"
        log.info(
            "  Neo4j application complete: %d file(s), %d statement(s)",
            apply_stats["files"],
            apply_stats["statements"],
        )

    # ── Pass 3: Upload embeddings to Neo4j ────────────────────────
    upload_status: str
    nodes_uploaded = 0
    if skip_upload:
        log.info("  Pass 3 skipped (--skip-upload)")
        upload_status = "skipped_flag"
    elif upload_only and not (neo4j_uri and neo4j_pass):
        log.warning("  Pass 3 skipped — NEO4J_URI/NEO4J_PASSWORD not set in .env")
        upload_status = "skipped_no_credentials"
    elif not (neo4j_uri and neo4j_pass):
        log.warning("  Pass 3 skipped — NEO4J_URI/NEO4J_PASSWORD not set in .env")
        upload_status = "skipped_no_credentials"
    elif not paths.knowledge_embeddings.exists():
        log.warning(
            "  Pass 3 skipped — embeddings not found at %s. "
            "Run pipeline/embed_knowledge.py first.",
            paths.knowledge_embeddings,
        )
        upload_status = "skipped_no_embeddings"
    else:
        upload_stats = upload_embeddings_to_neo4j(
            embeddings_path=paths.knowledge_embeddings,
            index_path=paths.knowledge_embedding_index,
            neo4j_uri=neo4j_uri,
            neo4j_user=neo4j_user,
            neo4j_pass=neo4j_pass,
        )
        nodes_uploaded = upload_stats["total_updated"]
        upload_status = "ok"
        log.info("  Pass 3: %d nodes received embeddings", nodes_uploaded)

    elapsed = time.time() - start

    log.info("")
    log.info("=" * 55)
    log.info("BUILD COMPLETE")
    n_cypher_files = 0 if upload_only else 3 + len(knowledge_chunks) + (1 if jira_acl_written else 0)
    # 3 = pass0_schema + pass1_persons + pass2_corroborates
    if not upload_only:
        log.info("  Cypher:  %s (%d files)", cypher_dir, n_cypher_files)
    log.info("  Neo4j application: %s", apply_status)
    if not skip_upload and not upload_only:
        summary = _collect_graph_summary(neo4j_uri, neo4j_user, neo4j_pass)
        log.info(
            "  Graph: KnowledgeNodes=%d (embedded=%d), Persons=%d (embedded=%d), scoped=%d, unscoped(fail-closed)=%d",
            summary["knowledge_nodes"],
            summary["knowledge_embeddings"],
            summary["persons"],
            summary["person_embeddings"],
            summary["scoped"],
            summary["unscoped"],
        )
        log.info("  Embeddings uploaded: %d", nodes_uploaded)
    log.info("  Duration: %.1fs", elapsed)
    log.info("=" * 55)
    log_summary(
        "unified_builder",
        org=org,
        cypher_files=n_cypher_files,
        cypher_apply=apply_status,
        upload=upload_status,
        nodes_uploaded=nodes_uploaded,
        duration_s=round(elapsed, 1),
    )
    # Gate: at least one of (Cypher files generated, embeddings uploaded)
    # must be non-zero — otherwise this run did literally nothing.
    productive = n_cypher_files + nodes_uploaded
    check_nonempty(
        "unified_builder", count=productive,
        what="Cypher files generated or embeddings uploaded",
        allow_empty=allow_empty,
    )


# ══════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════

def main():
    import argparse
    from dotenv import load_dotenv
    load_dotenv()
    import os

    parser = argparse.ArgumentParser(
        description="Build Neo4j Cypher files + upload NVIDIA NeMo embeddings")
    parser.add_argument("--org", required=True,
                        help="Organization name (e.g. ezobooks)")
    parser.add_argument("--data-dir", default="output",
                        help="Output directory root (default: output)")
    parser.add_argument("--source", choices=["gmail", "slack", "jira", "outlook_mail", "ms_teams", "zoho_mail", "zoho_cliq"], default=None,
                        help="Build from a single source's triples "
                             "(slack/jira/outlook_mail/ms_teams use namespaced files). "
                             "Omit to build from ALL sources merged (default).")
    parser.add_argument("--skip-upload", action="store_true",
                        help="Skip embedding upload to Neo4j (generate Cypher only)")
    parser.add_argument("--upload-only", action="store_true",
                        help="Skip Cypher generation, only upload embeddings to existing Neo4j nodes")
    parser.add_argument("--allow-empty", action="store_true",
                        help="Don't raise if no Cypher was generated and no embeddings were uploaded.")
    parser.add_argument("--allow-unscoped", action="store_true",
                        help="Don't raise when ACL-bearing threads (Slack/Jira) lack scope info; "
                             "warn and build anyway (their nodes stay invisible to scoped queries).")

    args = parser.parse_args()

    logs_dir = Path(args.data_dir) / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    setup_logging(org=args.org, log_dir=str(logs_dir), source="unified_builder")

    # Prefer Neo4j Aura env vars when present. Map them to the legacy
    # NEO4J_* names so existing validation and settings continue to work.
    # Expected style in .env:
    #   NEO4J_AURA_URI=...
    #   NEO4J_AURA_USER=...
    #   NEO4J_AURA_PASSWORD=...
    # and optionally the backwards-compatible aliases below:
    #   NEO4J_URI=${NEO4J_AURA_URI}
    #   NEO4J_USERNAME=${NEO4J_AURA_USER}
    #   NEO4J_PASSWORD=${NEO4J_AURA_PASSWORD}
    # Map Aura vars into the legacy names only when the legacy names
    # are not already defined in the environment.
    aura_uri = os.getenv("NEO4J_AURA_URI")
    aura_user = os.getenv("NEO4J_AURA_USER") or os.getenv("NEO4J_AURA_USERNAME")
    aura_pass = os.getenv("NEO4J_AURA_PASSWORD")
    if aura_uri and not os.getenv("NEO4J_URI"):
        os.environ["NEO4J_URI"] = aura_uri
    if aura_user and not os.getenv("NEO4J_USERNAME"):
        os.environ["NEO4J_USERNAME"] = aura_user
    if aura_pass and not os.getenv("NEO4J_PASSWORD"):
        os.environ["NEO4J_PASSWORD"] = aura_pass

    # Skip env validation when only emitting Cypher (no Neo4j needed).
    # For runbook robustness, automatically degrade to cypher-only mode
    # if Neo4j credentials are absent.
    if not args.skip_upload:
        try:
            validate_for("unified_builder")
        except MissingConfigError as exc:
            if args.upload_only:
                raise
            log.warning("%s", exc)
            log.warning("Neo4j credentials missing; continuing with --skip-upload (cypher-only mode).")
            args.skip_upload = True

    # Cypher-only runs touch no client database, so they skip the routing guard.
    if args.skip_upload:
        s = get_settings()
        neo4j_uri, neo4j_user, neo4j_pass = s.neo4j_uri, s.neo4j_username, s.neo4j_password
    else:
        neo4j_uri, neo4j_user, neo4j_pass = neo4j_credentials_for(args.org)

    run_builder(
        org=args.org,
        data_dir=Path(args.data_dir),
        neo4j_uri=neo4j_uri,
        neo4j_user=neo4j_user,
        neo4j_pass=neo4j_pass,
        skip_upload=args.skip_upload,
        upload_only=args.upload_only,
        allow_empty=args.allow_empty,
        source=args.source,
        allow_unscoped=args.allow_unscoped,
    )


if __name__ == "__main__":
    main()
