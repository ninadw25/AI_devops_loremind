from __future__ import annotations

import json
import os
from pathlib import Path

from neo4j import GraphDatabase


def _fail(message: str) -> None:
    print(f"FAIL: {message}")
    raise SystemExit(1)


def _load_discovery_channel_scopes(org: str) -> set[str]:
    scope_file = Path("output/slack") / f"{org}_scope_principals.json"
    if not scope_file.exists():
        _fail(f"missing discovery artifact at {scope_file}")
    payload = json.loads(scope_file.read_text(encoding="utf-8"))
    channel_scopes = {
        row.get("scope_id")
        for row in payload.get("scopes", [])
        if row.get("kind") == "channel" and row.get("scope_id")
    }
    if not channel_scopes:
        _fail("no discovery channel scopes found")
    return channel_scopes


def _connect_neo4j():
    uri = os.getenv("NEO4J_URI", "bolt://localhost:7687")
    user = os.getenv("NEO4J_USERNAME", "neo4j")
    pwd = os.getenv("NEO4J_PASSWORD", "lorepassword")
    return GraphDatabase.driver(uri, auth=(user, pwd))


def _stable_id(row: dict) -> str | None:
    for key in ("decision_id", "task_id", "fact_id", "event_id"):
        value = row.get(key)
        if value:
            return value
    return None


def _node_type(row: dict) -> str:
    labels = row.get("node_type") or []
    for label in labels:
        if label != "KnowledgeNode":
            return str(label)
    return "KnowledgeNode"


def main() -> None:
    org = os.getenv("ORG", "ezobooks")
    discovery_scopes = _load_discovery_channel_scopes(org)

    query = """
    MATCH (n:KnowledgeNode)
    OPTIONAL MATCH (n)-[:IN_SCOPE]->(s:AclScope)
    RETURN labels(n) AS node_type,
           n.description AS description,
           s.scope_id AS scope_id,
           n.decision_id AS decision_id,
           n.task_id AS task_id,
           n.fact_id AS fact_id,
           n.event_id AS event_id
    ORDER BY n.description
    """

    driver = _connect_neo4j()
    total_nodes = 0
    nodes_with_acl = 0
    nodes_missing_acl = 0
    nodes_missing_ids = 0
    invalid_scopes = 0

    try:
        with driver.session() as session:
            rows = list(session.run(query))

        print("=== Scope Plumbing Test ===")

        for row in rows:
            total_nodes += 1
            node = row.data()
            node_type = _node_type(node)
            description = node.get("description") or "(no description)"
            scope_id = node.get("scope_id")
            stable_id = _stable_id(node)

            if not stable_id:
                nodes_missing_ids += 1
                print(f"FAIL: {node_type} -> {description}")
                print("      id=NULL")
                if not scope_id:
                    print("      scope=NULL")
                elif scope_id not in discovery_scopes:
                    print(f"      scope={scope_id} (not in discovery)")
                continue

            if not scope_id:
                nodes_missing_acl += 1
                print(f"FAIL: {node_type} -> {description}")
                print(f"      id={stable_id}")
                print("      scope=NULL")
                continue

            if scope_id not in discovery_scopes:
                invalid_scopes += 1
                nodes_missing_acl += 1
                print(f"FAIL: {node_type} -> {description}")
                print(f"      id={stable_id}")
                print(f"      scope={scope_id} (not in discovery)")
                continue

            nodes_with_acl += 1
            print(f"PASS: {node_type} -> {description}")
            print(f"      scope={scope_id}")
            print(f"      id={stable_id}")

    finally:
        driver.close()

    print()
    print(f"KnowledgeNodes total: {total_nodes}")
    print(f"Nodes with ACL propagation: {nodes_with_acl}")
    print(f"Nodes missing ACL propagation: {nodes_missing_acl}")
    print(f"Nodes missing ids: {nodes_missing_ids}")
    print(f"Valid scopes loaded: {len(discovery_scopes)}")
    print()

    if nodes_missing_acl:
        _fail(f"{nodes_missing_acl} KnowledgeNode(s) are missing ACL propagation")
    if nodes_missing_ids:
        _fail(f"{nodes_missing_ids} KnowledgeNode(s) are missing stable ids")
    if invalid_scopes:
        _fail(f"{invalid_scopes} KnowledgeNode(s) reference scopes not found in discovery")

    print("PASS: every KnowledgeNode has an ACL scope")
    print("PASS: every scope exists in discovery artifact")
    print("PASS: every KnowledgeNode has a stable id")
    print("\nSTATUS: PASS")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:
        _fail(str(exc))
