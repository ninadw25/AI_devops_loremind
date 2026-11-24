"""
pipeline/comm_graph.py
──────────────────────
Builds a person-to-person communication graph from ingested events
and the person registry.

The graph captures:
  - Directed edges: who emails whom (sender → recipient)
  - Edge weight: communication frequency
  - Edge metadata: topics, recency, first/last contact
  - Person nodes: enriched with registry data + centrality metrics
  - Community detection: identify teams/clusters

Works for any organization — uses person registry for identity resolution.

Usage:
  python -m common.comm_graph --input output/org_gmail_filtered.jsonl --org myorg
"""

import json
import time
from collections import Counter, defaultdict
from pathlib import Path
from common.logger import get_logger
from common.org_config import load_org_config
from common.stage_gate import check_nonempty
from common.stage_summary import log_summary

log = get_logger("pipeline.comm_graph")


# ══════════════════════════════════════════════════════════════════════
# GRAPH BUILDER
# ══════════════════════════════════════════════════════════════════════

def build_comm_graph(
    input_path: Path,
    email_to_person: dict[str, str],
    persons_by_id: dict[str, dict],
    org_domains: list[str],
    internal_only: bool = False,
) -> dict:
    """
    Build a communication graph from events + person registry.

    Args:
        input_path: Path to filtered JSONL events
        email_to_person: email → person_id mapping
        persons_by_id: person_id → person record
        org_domains: list of org domains
        internal_only: if True, only include edges where both sides are internal

    Returns:
        Graph dict with nodes, edges, communities, and stats
    """
    log.info("Building communication graph from %s", input_path)
    start = time.time()

    org_domain_set = set(d.lower() for d in org_domains)

    # ── Phase 1: Scan events and build edges ─────────────────────────
    # Edge key: (sender_person_id, recipient_person_id)
    edge_count: Counter = Counter()
    edge_subjects: dict[tuple, Counter] = defaultdict(Counter)
    edge_first_ts: dict[tuple, str] = {}
    edge_last_ts: dict[tuple, str] = {}

    event_count = 0
    skipped = 0

    with open(input_path, "r", encoding="utf-8") as f:
        for line in f:
            event = json.loads(line)
            event_count += 1

            # Skip thread-level events (already counted via messages)
            if event.get("event_type") == "gmail.thread":
                continue

            sender_raw = (event.get("primary_actor", "").replace("email:", "")).lower()
            sender_pid = email_to_person.get(sender_raw)
            if not sender_pid:
                skipped += 1
                continue

            subject = event.get("title", "")
            ts = event.get("timestamp", "")

            # Find all recipients
            actors = event.get("actors", [])
            for actor in actors:
                email = (actor.get("email", "") or actor.get("emailAddress", "") or "").lower()
                if not email or email == sender_raw:
                    continue

                recip_pid = email_to_person.get(email)
                if not recip_pid or recip_pid == sender_pid:
                    continue

                # Optionally filter to internal-only
                if internal_only:
                    sender_person = persons_by_id.get(sender_pid, {})
                    recip_person = persons_by_id.get(recip_pid, {})
                    if not (sender_person.get("is_internal") and recip_person.get("is_internal")):
                        continue

                edge_key = (sender_pid, recip_pid)
                edge_count[edge_key] += 1

                # Track subjects for topic analysis
                if subject:
                    edge_subjects[edge_key][subject] += 1

                # Track first/last timestamp
                if ts:
                    if edge_key not in edge_first_ts or ts < edge_first_ts[edge_key]:
                        edge_first_ts[edge_key] = ts
                    if edge_key not in edge_last_ts or ts > edge_last_ts[edge_key]:
                        edge_last_ts[edge_key] = ts

    log.info("Phase 1: Scanned %d events, built %d directed edges (%d skipped)",
             event_count, len(edge_count), skipped)

    # ── Phase 2: Build node set ──────────────────────────────────────
    node_pids = set()
    for sender, recip in edge_count:
        node_pids.add(sender)
        node_pids.add(recip)

    # Compute per-node metrics
    out_degree: Counter = Counter()  # distinct people this person emails
    in_degree: Counter = Counter()   # distinct people who email this person
    out_volume: Counter = Counter()  # total emails sent
    in_volume: Counter = Counter()   # total emails received

    for (sender, recip), count in edge_count.items():
        out_degree[sender] += 1
        in_degree[recip] += 1
        out_volume[sender] += count
        in_volume[recip] += count

    nodes = []
    for pid in sorted(node_pids):
        person = persons_by_id.get(pid, {})
        nodes.append({
            "person_id": pid,
            "canonical_name": person.get("canonical_name", pid),
            "primary_email": person.get("primary_email", ""),
            "is_internal": person.get("is_internal", False),
            "is_functional": person.get("is_functional", False),
            "out_degree": out_degree.get(pid, 0),
            "in_degree": in_degree.get(pid, 0),
            "out_volume": out_volume.get(pid, 0),
            "in_volume": in_volume.get(pid, 0),
            "total_volume": out_volume.get(pid, 0) + in_volume.get(pid, 0),
        })

    log.info("Phase 2: %d nodes in graph", len(nodes))

    # ── Phase 3: Build edge records ──────────────────────────────────
    edges = []
    for (sender, recip), count in edge_count.most_common():
        subjects = edge_subjects.get((sender, recip), Counter())
        top_subjects = [s for s, _ in subjects.most_common(5)]

        edges.append({
            "from": sender,
            "to": recip,
            "weight": count,
            "from_name": persons_by_id.get(sender, {}).get("canonical_name", sender),
            "to_name": persons_by_id.get(recip, {}).get("canonical_name", recip),
            "top_subjects": top_subjects,
            "first_contact": edge_first_ts.get((sender, recip), ""),
            "last_contact": edge_last_ts.get((sender, recip), ""),
        })

    log.info("Phase 3: %d edge records", len(edges))

    # ── Phase 4: Simple community detection (internal only) ──────────
    # Uses a simple approach: group by strongest bidirectional connections
    communities = _detect_communities(edge_count, persons_by_id, node_pids)
    log.info("Phase 4: Detected %d communities", len(communities))

    # ── Phase 5: Key connectors (bridge people) ─────────────────────
    connectors = _find_connectors(edge_count, persons_by_id, communities)

    elapsed = time.time() - start

    stats = {
        "events_processed": event_count,
        "total_nodes": len(nodes),
        "total_edges": len(edges),
        "internal_nodes": sum(1 for n in nodes if n["is_internal"]),
        "communities": len(communities),
        "duration_seconds": round(elapsed, 1),
    }

    return {
        "nodes": nodes,
        "edges": edges,
        "communities": communities,
        "connectors": connectors,
        "stats": stats,
    }


# ══════════════════════════════════════════════════════════════════════
# COMMUNITY DETECTION (simple greedy approach)
# ══════════════════════════════════════════════════════════════════════

def _detect_communities(
    edge_count: Counter,
    persons_by_id: dict,
    all_pids: set,
) -> list[dict]:
    """
    Simple community detection for internal org members.

    Algorithm: greedy agglomeration based on bidirectional communication strength.
    Two people are in the same community if they have strong mutual communication.
    """
    # Only consider internal non-functional persons
    internal_pids = {
        pid for pid in all_pids
        if persons_by_id.get(pid, {}).get("is_internal", False)
        and not persons_by_id.get(pid, {}).get("is_functional", False)
    }

    if not internal_pids:
        return []

    # Build bidirectional weight matrix (sum of both directions)
    bidir_weight: dict[tuple, int] = {}
    for (a, b), count in edge_count.items():
        if a not in internal_pids or b not in internal_pids:
            continue
        key = tuple(sorted([a, b]))
        bidir_weight[key] = bidir_weight.get(key, 0) + count

    # Union-Find for clustering
    parent: dict[str, str] = {pid: pid for pid in internal_pids}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x, y):
        rx, ry = find(x), find(y)
        if rx != ry:
            parent[rx] = ry

    # Merge pairs with strong bidirectional communication
    # Threshold: at least 10 emails exchanged total (both directions)
    MERGE_THRESHOLD = 10
    for (a, b), weight in sorted(bidir_weight.items(), key=lambda x: -x[1]):
        if weight >= MERGE_THRESHOLD:
            union(a, b)

    # Build communities
    clusters: dict[str, list[str]] = defaultdict(list)
    for pid in internal_pids:
        root = find(pid)
        clusters[root].append(pid)

    # Format output — only communities with 2+ members
    communities = []
    for root, members in sorted(clusters.items(), key=lambda x: -len(x[1])):
        if len(members) < 2:
            continue

        member_info = []
        for pid in members:
            person = persons_by_id.get(pid, {})
            member_info.append({
                "person_id": pid,
                "canonical_name": person.get("canonical_name", pid),
                "primary_email": person.get("primary_email", ""),
            })

        # Sort members by name for consistent output
        member_info.sort(key=lambda m: m["canonical_name"])

        communities.append({
            "community_id": f"community_{len(communities)}",
            "size": len(members),
            "members": member_info,
        })

    return communities


# ══════════════════════════════════════════════════════════════════════
# KEY CONNECTORS (bridge people)
# ══════════════════════════════════════════════════════════════════════

def _find_connectors(
    edge_count: Counter,
    persons_by_id: dict,
    communities: list[dict],
) -> list[dict]:
    """
    Find bridge people who connect different communities.

    A connector is someone who has significant communication
    with members of multiple communities.
    """
    if len(communities) < 2:
        return []

    # Build person → community map
    person_to_community: dict[str, str] = {}
    for comm in communities:
        for member in comm["members"]:
            person_to_community[member["person_id"]] = comm["community_id"]

    # For each person, count how many communities they connect to
    person_cross_comm: dict[str, set] = defaultdict(set)
    person_cross_volume: Counter = Counter()

    for (sender, recip), count in edge_count.items():
        sender_comm = person_to_community.get(sender)
        recip_comm = person_to_community.get(recip)

        if sender_comm and recip_comm and sender_comm != recip_comm:
            person_cross_comm[sender].add(recip_comm)
            person_cross_volume[sender] += count

    # Rank connectors
    connectors = []
    for pid, communities_connected in sorted(person_cross_comm.items(), key=lambda x: -len(x[1])):
        if len(communities_connected) < 2:
            continue
        person = persons_by_id.get(pid, {})
        connectors.append({
            "person_id": pid,
            "canonical_name": person.get("canonical_name", pid),
            "communities_connected": len(communities_connected),
            "cross_community_volume": person_cross_volume.get(pid, 0),
        })

    connectors.sort(key=lambda c: (-c["communities_connected"], -c["cross_community_volume"]))
    return connectors[:20]


# ══════════════════════════════════════════════════════════════════════
# OUTPUT
# ══════════════════════════════════════════════════════════════════════

def save_graph(graph: dict, output_dir: Path, org: str):
    """Save the communication graph to disk."""
    graph_path = output_dir / f"{org}_comm_graph.json"
    graph_path.write_text(json.dumps(graph, indent=2, ensure_ascii=False), encoding="utf-8")
    log.info("Saved communication graph → %s", graph_path)

    # Also save a compact internal-only summary
    internal_nodes = [n for n in graph["nodes"] if n["is_internal"]]
    internal_edges = []
    internal_pids = {n["person_id"] for n in internal_nodes}
    for e in graph["edges"]:
        if e["from"] in internal_pids and e["to"] in internal_pids:
            internal_edges.append(e)

    summary = {
        "org": org,
        "internal_nodes": len(internal_nodes),
        "internal_edges": len(internal_edges),
        "communities": graph["communities"],
        "connectors": graph["connectors"],
        "top_internal_by_volume": sorted(internal_nodes, key=lambda n: -n["total_volume"])[:20],
        "top_internal_edges": internal_edges[:50],
    }
    summary_path = output_dir / f"{org}_comm_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    log.info("Saved internal summary → %s", summary_path)


# ══════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════

def main():
    import argparse

    parser = argparse.ArgumentParser(description="Build communication graph from events + person registry")
    parser.add_argument("--input", required=True, help="Path to filtered JSONL file")
    parser.add_argument("--org", required=True, help="Organization name")
    parser.add_argument("--output-dir", default=None, help="Output directory (default: same as input)")
    parser.add_argument("--internal-only", action="store_true",
                        help="Only include edges where both sides are internal")
    parser.add_argument("--allow-empty", action="store_true",
                        help="Don't raise if the comm graph has 0 nodes.")

    args = parser.parse_args()
    input_path = Path(args.input)
    output_dir = Path(args.output_dir) if args.output_dir else input_path.parent.parent  # parent of filtered/

    from common.output_paths import KnowledgePaths
    paths = KnowledgePaths(org=args.org, data_dir=output_dir)
    paths.makedirs()
    config_dir = output_dir / "config"
    logs_dir = output_dir / "logs"
    for d in (config_dir, logs_dir):
        d.mkdir(parents=True, exist_ok=True)

    from common.logger import setup_logging
    setup_logging(org=args.org, log_dir=str(logs_dir), source="comm_graph")

    # Load person registry
    registry_path = paths.person_registry
    if not registry_path.exists():
        log.error("Person registry not found at %s. Run person_registry first.", registry_path)
        return

    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    email_to_person = registry["email_to_person_id"]
    persons_by_id = {p["person_id"]: p for p in registry["persons"]}

    # Load org config
    org_config = load_org_config(args.org, config_dir)
    org_domains = org_config.get("org_domains", [])

    graph = build_comm_graph(
        input_path=input_path,
        email_to_person=email_to_person,
        persons_by_id=persons_by_id,
        org_domains=org_domains,
        internal_only=args.internal_only,
    )

    save_graph(graph, paths.registry_dir, args.org)

    # Print summary
    stats = graph["stats"]
    log.info("")
    log.info("=" * 55)
    log.info("COMMUNICATION GRAPH COMPLETE — %s", args.org)
    log.info("  Nodes            : %d (%d internal)", stats["total_nodes"], stats["internal_nodes"])
    log.info("  Edges            : %d", stats["total_edges"])
    log.info("  Communities      : %d", stats["communities"])
    log.info("  Duration         : %.1fs", stats["duration_seconds"])

    if graph["communities"]:
        log.info("")
        log.info("  Communities:")
        for comm in graph["communities"]:
            members = ", ".join(m["canonical_name"] for m in comm["members"][:5])
            more = f" +{comm['size'] - 5} more" if comm["size"] > 5 else ""
            log.info("    %s (size=%d): %s%s",
                     comm["community_id"], comm["size"], members, more)

    if graph["connectors"]:
        log.info("")
        log.info("  Key connectors (bridge people):")
        for c in graph["connectors"][:10]:
            log.info("    %-25s connects %d communities (%d cross-comm emails)",
                     c["canonical_name"], c["communities_connected"],
                     c["cross_community_volume"])

    log.info("=" * 55)

    log_summary(
        "comm_graph",
        org=args.org,
        nodes=stats["total_nodes"],
        internal_nodes=stats["internal_nodes"],
        edges=stats["total_edges"],
        communities=stats["communities"],
        duration_s=stats["duration_seconds"],
    )
    check_nonempty("comm_graph", count=stats["total_nodes"],
                   what="nodes", allow_empty=args.allow_empty)


if __name__ == "__main__":
    main()
