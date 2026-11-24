"""
pipeline/deduplicator.py
────────────────────────
Stage 6.6: Post-validation entity deduplication via embedding similarity.

Merges Decision/Task/Fact/Event nodes with semantically-equivalent
descriptions across threads. The LLM often extracts the same event
with different wording ("Revised salary for Pooja to 24000" vs
"Approved Pooja's salary revision to Rs 24000") — these should be
a single graph node.

Approach:
  1. Load embeddings from embed_knowledge stage (2048-dim, L2-normalized)
  2. For each node type independently, compute cosine similarity matrix
     — avoids allocating the full cross-type N×N matrix (~5 GB for 35k nodes)
  3. Build sparse adjacency:
       - Same-thread pair  → lower threshold (paraphrases in one email thread)
       - Cross-thread pair → higher threshold (avoid false merges)
  4. Date-incompatibility blocking: nodes with explicit dates differing by
     more than DATE_BLOCK_DAYS are never merged regardless of similarity
  5. Connected-components clustering (scipy sparse)
  6. Canonical = most-central node (highest average cosine similarity to
     other cluster members — the "prototype" of the cluster)
  7. Remap node_ids in triples, drop structurally-duplicate triples

Calibration (--calibrate):
  Samples random same-thread and cross-thread pairs, prints cosine
  similarity distributions with percentile breakpoints and shows the
  top-5 most-similar cross-thread pairs for manual spot-checking.
  Run this first whenever the embedding model changes.

Input:  output/knowledge/triples/{org}_triples_validated.json
        output/knowledge/embeddings/{org}_knowledge_embeddings.npy
        output/knowledge/embeddings/{org}_knowledge_embedding_index.json
Output: output/knowledge/triples/{org}_triples_deduped.json

Usage:
    # Calibrate thresholds first:
    python -m pipeline.deduplicator --org ezobooks --calibrate

    # Deduplicate with defaults:
    python -m pipeline.deduplicator --org ezobooks

    # Custom thresholds (after reading calibration output):
    python -m pipeline.deduplicator --org ezobooks \\
        --same-thread-threshold 0.92 --cross-thread-threshold 0.96
"""

import json
import random
import time
from collections import defaultdict
from datetime import date as _date
from pathlib import Path

import numpy as np
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import connected_components

from common.logger import get_logger
from common.stage_gate import check_nonempty
from common.stage_summary import log_summary
from pipeline.embed_knowledge import load_embeddings

log = get_logger("pipeline.deduplicator")

# Two dated nodes this many days apart are never merged
DATE_BLOCK_DAYS = 60
# Pairs sampled per category during --calibrate
CALIBRATE_SAMPLE = 500


# ══════════════════════════════════════════════════════════════════════
# DATE HELPERS
# ══════════════════════════════════════════════════════════════════════

def _to_ordinal(date_str: str | None) -> float:
    """Parse date string to ordinal float. Returns NaN on failure/None."""
    if not date_str:
        return float("nan")
    try:
        return float(_date.fromisoformat(str(date_str)[:10]).toordinal())
    except (ValueError, TypeError):
        return float("nan")


def _extract_node_dates(validated: dict) -> dict[str, str | None]:
    """Build {node_id: date_str_or_None} from all validated triples."""
    dates: dict[str, str | None] = {}
    for thread in validated.values():
        for triple in thread.get("triples", []):
            for key in ("subject", "object"):
                node = triple[key]
                nid = node.get("node_id", "")
                if nid and nid not in dates:
                    dates[nid] = node.get("date")
    return dates


# ══════════════════════════════════════════════════════════════════════
# CALIBRATION
# ══════════════════════════════════════════════════════════════════════

def run_calibration(
    embeddings: np.ndarray,
    nodes: list[dict],
    n_sample: int = CALIBRATE_SAMPLE,
) -> None:
    """Print cosine similarity distributions for same-thread and
    cross-thread pairs so you can choose appropriate thresholds.

    Shows:
      - Mean / std / min / max / percentiles
      - ASCII histogram
      - Top-5 most-similar cross-thread pairs for manual spot-checking
      - Suggested thresholds (conservative starting point)
    """
    rng = random.Random(42)

    log.info("=" * 60)
    log.info("CALIBRATION — cosine similarity distributions")
    log.info("  Nodes: %d  |  Dim: %d  |  Samples: %d per category",
             len(nodes), embeddings.shape[1], n_sample)
    log.info("=" * 60)

    # Group indices by type and by (type, thread_id)
    type_groups: dict[str, list[int]] = defaultdict(list)
    thread_type_groups: dict[tuple, list[int]] = defaultdict(list)
    for i, node in enumerate(nodes):
        type_groups[node["type"]].append(i)
        thread_type_groups[(node["type"], node["thread_id"])].append(i)

    # ── Sample same-thread pairs ──────────────────────────────────────
    same_pairs: list[tuple[int, int]] = []
    for (ntype, _), indices in thread_type_groups.items():
        if len(indices) < 2:
            continue
        # Up to ceil(C(k,2)) pairs per thread — avoid repeats for small threads
        k = len(indices)
        cap = min(10, k * (k - 1) // 2)
        sampled: set[tuple] = set()
        for _ in range(cap * 3):
            a, b = rng.sample(indices, 2)
            if a > b:
                a, b = b, a
            if (a, b) not in sampled:
                sampled.add((a, b))
                same_pairs.append((a, b))
            if len(sampled) >= cap:
                break
    rng.shuffle(same_pairs)
    same_pairs = same_pairs[:n_sample]

    # ── Sample cross-thread pairs ─────────────────────────────────────
    cross_pairs: list[tuple[int, int]] = []
    for ntype, indices in type_groups.items():
        if len(indices) < 2:
            continue
        thread_of = {i: nodes[i]["thread_id"] for i in indices}
        attempts = 0
        while len(cross_pairs) < n_sample and attempts < n_sample * 5:
            attempts += 1
            a, b = rng.sample(indices, 2)
            if thread_of[a] != thread_of[b]:
                cross_pairs.append((a, b))
    cross_pairs = cross_pairs[:n_sample]

    # ── Batch cosine similarity for sampled pairs ─────────────────────
    def _batch_sims(pairs: list[tuple[int, int]]) -> np.ndarray:
        if not pairs:
            return np.array([], dtype=np.float32)
        ai = np.array([p[0] for p in pairs])
        bi = np.array([p[1] for p in pairs])
        return (embeddings[ai] * embeddings[bi]).sum(axis=1)

    same_sims  = _batch_sims(same_pairs)
    cross_sims = _batch_sims(cross_pairs)

    # ── Print distribution ────────────────────────────────────────────
    def _print_dist(sims: np.ndarray, label: str) -> None:
        if len(sims) == 0:
            log.info("  %s: no pairs found", label)
            return
        pct_vals = np.percentile(sims, [50, 75, 90, 95, 99])
        log.info("  %s  (%d pairs):", label, len(sims))
        log.info("    mean=%.4f  std=%.4f  min=%.4f  max=%.4f",
                 sims.mean(), sims.std(), sims.min(), sims.max())
        log.info("    p50=%.4f  p75=%.4f  p90=%.4f  p95=%.4f  p99=%.4f",
                 *pct_vals)
        # ASCII histogram (12 buckets, range 0→1)
        hist, edges = np.histogram(sims, bins=12, range=(0.0, 1.0))
        max_cnt = int(hist.max()) or 1
        log.info("    Histogram:")
        for cnt, lo, hi in zip(hist, edges, edges[1:]):
            bar = "█" * int(40 * cnt / max_cnt)
            log.info("      [%.2f–%.2f] %-40s %d", lo, hi, bar, cnt)

    log.info("")
    _print_dist(same_sims,  "Same-thread pairs ")
    log.info("")
    _print_dist(cross_sims, "Cross-thread pairs")

    # ── Suggest thresholds ────────────────────────────────────────────
    if len(same_sims) > 0 and len(cross_sims) > 0:
        cross_p95 = float(np.percentile(cross_sims, 95))
        cross_p99 = float(np.percentile(cross_sims, 99))
        same_p75  = float(np.percentile(same_sims,  75))

        # same-thread: at least as strict as cross_p95 to avoid false merges
        sug_same  = round(max(same_p75, cross_p95) + 0.005, 2)
        sug_cross = round(max(sug_same + 0.02, cross_p99) + 0.005, 2)

        log.info("")
        log.info("  Suggested thresholds (conservative — tune up if over-merging):")
        log.info("    --same-thread-threshold  %.2f", sug_same)
        log.info("    --cross-thread-threshold %.2f", sug_cross)

    # ── Top-5 most-similar cross-thread pairs (spot-check) ───────────
    if len(cross_sims) > 0:
        log.info("")
        log.info("  Top-5 most-similar cross-thread pairs (spot-check for false merges):")
        top_k = min(5, len(cross_sims))
        for rank, idx in enumerate(np.argsort(cross_sims)[-top_k:][::-1]):
            a, b = cross_pairs[idx]
            sim = float(cross_sims[idx])
            log.info("    #%d  sim=%.4f  type=%s", rank + 1, sim, nodes[a]["type"])
            log.info("       A: %s", nodes[a]["description"][:90])
            log.info("       B: %s", nodes[b]["description"][:90])

    log.info("")
    log.info("  Run deduplication with chosen thresholds:")
    log.info("    python -m pipeline.deduplicator --org <org> "
             "--same-thread-threshold X --cross-thread-threshold Y")
    log.info("=" * 60)


# ══════════════════════════════════════════════════════════════════════
# CLUSTERING — per-type to avoid OOM
# ══════════════════════════════════════════════════════════════════════

def _thread_source(thread: dict) -> str:
    """Infer the source of a validated thread from its namespaced scope_id /
    thread_id. Slack and Jira always carry a namespaced scope (`slack:…` /
    `jira:…`); Gmail carries none, so it is the default.

    Used ONLY to keep dedup from merging across sources (see _node_source_map).
    """
    sid = str(thread.get("scope_id") or "")
    if sid.startswith("slack:"):
        return "slack"
    if sid.startswith("jira:"):
        return "jira"
    if sid.startswith("zoho:cliq:channel:"):
        return "zoho_cliq"
    tid = str(thread.get("thread_id") or "")
    if tid.startswith("slack:"):
        return "slack"
    if tid.startswith("jira:"):
        return "jira"
    if tid.startswith("zoho:cliq:channel:"):
        return "zoho_cliq"
    return "gmail"


def _node_source_map(validated: dict) -> dict[str, str]:
    """Map node_id → source ("slack"/"jira"/"gmail") from the validated triples.

    A node_id is minted per (thread, content), so it belongs to exactly one
    source; the same content in two sources gets two different node_ids. This
    map lets dedup block cross-source merges — merging would union two sources'
    ACL scopes into one node (RBAC leak). See docs/RBAC/SCOPE_FILTER_HANDOFF.md.
    """
    src_of: dict[str, str] = {}
    for thread in validated.values():
        source = _thread_source(thread)
        for triple in thread.get("triples", []):
            for node_key in ("subject", "object"):
                nid = triple.get(node_key, {}).get("node_id")
                if nid and nid not in src_of:
                    src_of[nid] = source
    return src_of


def cluster_nodes_by_type(
    embeddings: np.ndarray,
    nodes: list[dict],
    same_thread_threshold: float,
    cross_thread_threshold: float,
    date_by_node_id: dict[str, str | None] | None = None,
    source_by_node_id: dict[str, str] | None = None,
) -> tuple[list[list[int]], list[dict]]:
    """Cluster all nodes, processing each node type separately.

    Computes cosine similarity per type instead of across all 35k+ nodes
    at once. Peak memory: max(N_type)² × 4 bytes ≈ 1 GB for 16k Facts,
    vs ~5 GB for the full cross-type matrix.

    Returns (clusters, corroborates_pairs):
      - clusters: flat list of clusters (each a list of global node indices).
        Singleton clusters are included — only multi-node ones are meaningful.
      - corroborates_pairs: list of dicts for cross-source pairs that met the
        merge threshold but were blocked by the source guard. Each dict has
        node_id_a/b, similarity, type_a/b, source_a/b. These become
        CORROBORATES edges in Neo4j (soft link, ACL-isolated). Empty when
        source_by_node_id is None.
    """
    # Only deduplicate within the same semantic type.
    # Merging across types (e.g. a Decision into a Task) would corrupt the
    # graph structure — the predicate rules differ per type.
    _DEDUP_TYPES = {"Decision", "Task", "Fact", "Event"}

    type_to_indices: dict[str, list[int]] = defaultdict(list)
    for i, node in enumerate(nodes):
        type_to_indices[node["type"]].append(i)

    all_clusters: list[list[int]] = []
    all_corroborates: list[dict] = []

    for node_type, global_indices in type_to_indices.items():
        if node_type not in _DEDUP_TYPES:
            # Person/Organization/Topic nodes: no cross-node dedup —
            # they are name-keyed (same name = same hash already).
            all_clusters.extend([[i] for i in global_indices])
            continue
        n = len(global_indices)
        log.info("  Clustering %d %s nodes ...", n, node_type)
        t0 = time.time()

        type_nodes    = [nodes[i] for i in global_indices]
        type_embs     = embeddings[np.array(global_indices)]   # (n, D)

        # Full per-type cosine similarity matrix (n × n, float32)
        sim_matrix = (type_embs @ type_embs.T).astype(np.float32)  # (n, n)
        log.info("    sim_matrix: %.0f MB", sim_matrix.nbytes / 1e6)

        # Vectorised same-thread mask (bool n × n)
        thread_ids = np.array([node["thread_id"] for node in type_nodes])
        same_thread = thread_ids[:, None] == thread_ids[None, :]  # (n, n)

        # Apply per-pair thresholds in one vectorised step
        threshold_matrix = np.where(
            same_thread, same_thread_threshold, cross_thread_threshold
        )
        edge_matrix = np.triu(sim_matrix >= threshold_matrix, k=1)  # (n, n) bool

        # Date-incompatibility blocking (applies to both merges and CORROBORATES —
        # nodes 60+ days apart shouldn't be linked even if semantically similar).
        if date_by_node_id:
            node_ids  = [n["node_id"] for n in type_nodes]
            node_days = np.array(
                [_to_ordinal(date_by_node_id.get(nid)) for nid in node_ids]
            )  # NaN if no date

            # Get candidate edge pairs first, then check dates only for those
            # (avoids allocating a full n×n float matrix for date diffs)
            r_cands, c_cands = np.where(edge_matrix)
            if len(r_cands):
                days_i = node_days[r_cands]
                days_j = node_days[c_cands]
                both_dated = np.isfinite(days_i) & np.isfinite(days_j)
                date_blocked = both_dated & (np.abs(days_i - days_j) > DATE_BLOCK_DAYS)
                # Zero out blocked edges
                edge_matrix[r_cands[date_blocked], c_cands[date_blocked]] = False

        # Same-source guard (RBAC-critical): never merge nodes from different
        # sources — that would union two sources' ACL scopes into one node.
        # Cross-source pairs above threshold → captured as CORROBORATES links.
        if source_by_node_id is not None:
            sources = np.array(
                [source_by_node_id.get(node["node_id"], "") for node in type_nodes]
            )
            same_source = sources[:, None] == sources[None, :]  # (n, n)

            # Pairs that met the threshold but are from different sources
            # become CORROBORATES (soft link, ACL-isolated, not a merge).
            cross_source_mask = edge_matrix & ~same_source
            r_co, c_co = np.where(cross_source_mask)
            for ri, ci in zip(r_co, c_co):
                nid_a = type_nodes[ri]["node_id"]
                nid_b = type_nodes[ci]["node_id"]
                src_a = sources[ri]
                src_b = sources[ci]
                # Stable lexicographic order so MERGE is idempotent on re-runs
                if nid_a > nid_b:
                    nid_a, nid_b = nid_b, nid_a
                    src_a, src_b = src_b, src_a
                all_corroborates.append({
                    "node_id_a": nid_a,
                    "node_id_b": nid_b,
                    "similarity": round(float(sim_matrix[ri, ci]), 4),
                    "type_a": node_type,
                    "type_b": node_type,
                    "source_a": src_a,
                    "source_b": src_b,
                })

            edge_matrix &= same_source   # keep only same-source merges

        rows, cols = np.where(edge_matrix)

        if len(rows):
            graph = csr_matrix(
                (np.ones(len(rows), dtype=np.int8), (rows, cols)),
                shape=(n, n),
            )
            _, labels = connected_components(
                graph, directed=False, return_labels=True)
        else:
            labels = np.arange(n)

        # Map local indices → global indices in clusters
        local_clusters: dict[int, list[int]] = defaultdict(list)
        for local_idx, label in enumerate(labels):
            local_clusters[int(label)].append(global_indices[local_idx])

        n_multi = sum(1 for c in local_clusters.values() if len(c) > 1)
        log.info("    %d multi-node clusters, %d corroborates pairs in %.1fs",
                 n_multi, len(all_corroborates), time.time() - t0)
        all_clusters.extend(local_clusters.values())

    return all_clusters, all_corroborates


# ══════════════════════════════════════════════════════════════════════
# CANONICAL SELECTION
# ══════════════════════════════════════════════════════════════════════

def pick_canonical(cluster: list[int], embeddings: np.ndarray) -> int:
    """Return the index of the most-central node in the cluster.

    Most-central = highest mean cosine similarity to all other cluster
    members. This picks the most 'prototypical' description rather than
    the longest (which can be verbose or include noise).

    Singleton clusters trivially return their only element.
    """
    if len(cluster) == 1:
        return cluster[0]

    idxs      = np.array(cluster)
    clust_emb = embeddings[idxs]            # (k, D)
    sim_mat   = clust_emb @ clust_emb.T     # (k, k)

    k = len(cluster)
    # Mean similarity to all others (exclude self-similarity = 1.0)
    avg_sims = (sim_mat.sum(axis=1) - 1.0) / (k - 1)
    return int(idxs[int(np.argmax(avg_sims))])


# ══════════════════════════════════════════════════════════════════════
# APPLY MAPPING
# ══════════════════════════════════════════════════════════════════════

def apply_mapping(validated: dict, id_map: dict[str, str]) -> dict:
    """Rewrite validated triples using canonical node_ids.

    Drops triples that become structurally identical after remapping
    (same subject_id + predicate + object_id within a thread).
    """
    total_triples_in   = 0
    nodes_remapped     = 0
    duplicates_dropped = 0

    for thread in validated.values():
        new_triples: list[dict] = []
        seen_keys: set[tuple]   = set()

        for triple in thread.get("triples", []):
            total_triples_in += 1

            for node_key in ("subject", "object"):
                node   = triple[node_key]
                old_id = node.get("node_id", "")
                new_id = id_map.get(old_id, old_id)
                if new_id != old_id:
                    node["node_id"]      = new_id
                    node["_merged_from"] = old_id
                    nodes_remapped      += 1

            key = (
                triple["subject"].get("node_id", ""),
                triple.get("predicate", ""),
                triple["object"].get("node_id", ""),
            )
            if key in seen_keys:
                duplicates_dropped += 1
                continue
            seen_keys.add(key)
            new_triples.append(triple)

        thread["triples"] = new_triples

    return {
        "total_triples_in":         total_triples_in,
        "nodes_remapped":           nodes_remapped,
        "duplicate_triples_dropped": duplicates_dropped,
    }


# ══════════════════════════════════════════════════════════════════════
# MAIN PIPELINE
# ══════════════════════════════════════════════════════════════════════

def run_deduplication(
    validated_path: Path,
    embeddings_path: Path,
    index_path: Path,
    output_path: Path,
    same_thread_threshold: float = 0.92,
    cross_thread_threshold: float = 0.96,
    allow_empty: bool = False,
    corroborates_path: Path | None = None,
) -> dict:
    """Run the full deduplication pipeline."""
    start = time.time()

    log.info("=" * 60)
    log.info("ENTITY DEDUPLICATION — Stage 6.6")
    log.info("  Input:       %s", validated_path.name)
    log.info("  Embeddings:  %s", embeddings_path.name)
    log.info("  Thresholds:  same-thread=%.2f  cross-thread=%.2f",
             same_thread_threshold, cross_thread_threshold)
    log.info("=" * 60)

    if not embeddings_path.exists():
        log.error("Embeddings not found: %s — run embed_knowledge first", embeddings_path)
        return {}

    validated = json.loads(validated_path.read_text(encoding="utf-8"))
    embeddings, meta = load_embeddings(embeddings_path, index_path)
    nodes = meta["index"]["nodes"]

    log.info("Loaded %d node embeddings (dim=%d)", len(nodes), embeddings.shape[1])

    if len(nodes) == 0:
        log.info("No nodes to dedupe — writing input as output unchanged")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(validated, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return {"nodes": 0}

    # ── Date lookup (node_id → date string, may be None) ─────────────
    date_by_node_id = _extract_node_dates(validated)
    n_dated = sum(1 for v in date_by_node_id.values() if v)
    log.info("Date-blocking: %d / %d nodes have explicit dates", n_dated, len(nodes))

    # ── Cluster per type ──────────────────────────────────────────────
    # Source map blocks cross-source merges (RBAC: never union two sources'
    # ACL scopes into one node). Cross-source sameness → CORROBORATES link.
    source_by_node_id = _node_source_map(validated)
    n_by_source: dict[str, int] = defaultdict(int)
    for s in source_by_node_id.values():
        n_by_source[s] += 1
    log.info("Source guard: %s", dict(n_by_source))

    clusters, corroborates_pairs = cluster_nodes_by_type(
        embeddings, nodes,
        same_thread_threshold, cross_thread_threshold,
        date_by_node_id=date_by_node_id,
        source_by_node_id=source_by_node_id,
    )
    log.info("Cross-source CORROBORATES pairs: %d", len(corroborates_pairs))

    # ── Build id_map from merged clusters ─────────────────────────────
    id_map: dict[str, str] = {}
    multi_clusters   = 0
    largest_cluster  = 0
    sample_merges: list[list[str]] = []

    for cluster in clusters:
        if len(cluster) <= 1:
            continue
        multi_clusters  += 1
        largest_cluster  = max(largest_cluster, len(cluster))

        canonical_idx = pick_canonical(cluster, embeddings)
        canonical_id  = nodes[canonical_idx]["node_id"]
        for idx in cluster:
            id_map[nodes[idx]["node_id"]] = canonical_id

        if len(sample_merges) < 5:
            sample_merges.append([nodes[i]["description"] for i in cluster])

    log.info("Multi-node clusters: %d  (largest = %d nodes)", multi_clusters, largest_cluster)

    if sample_merges:
        log.info("Sample merges (first 5 clusters):")
        for i, descs in enumerate(sample_merges):
            log.info("  Cluster %d (%d nodes):", i + 1, len(descs))
            for d in descs[:4]:
                log.info("    – %s", d[:100])
            if len(descs) > 4:
                log.info("    … +%d more", len(descs) - 4)

    # ── Apply mapping + write output ──────────────────────────────────
    apply_stats = apply_mapping(validated, id_map)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(validated, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # ── Write CORROBORATES pairs (consumed by unified_builder) ────────
    if corroborates_path is not None and corroborates_pairs:
        corroborates_path.parent.mkdir(parents=True, exist_ok=True)
        corroborates_path.write_text(
            json.dumps(corroborates_pairs, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        log.info("Wrote %d CORROBORATES pairs → %s",
                 len(corroborates_pairs), corroborates_path.name)

    elapsed = time.time() - start
    stats = {
        "total_nodes":            len(nodes),
        "multi_node_clusters":    multi_clusters,
        "largest_cluster_size":   largest_cluster,
        "corroborates_pairs":     len(corroborates_pairs),
        "thresholds": {
            "same_thread":  same_thread_threshold,
            "cross_thread": cross_thread_threshold,
        },
        "duration_seconds": round(elapsed, 1),
        **apply_stats,
    }

    log.info("")
    log.info("=" * 60)
    log.info("DEDUPLICATION COMPLETE")
    log.info("  Total nodes:               %d", stats["total_nodes"])
    log.info("  Merged clusters:           %d", stats["multi_node_clusters"])
    log.info("  Nodes remapped:            %d", stats["nodes_remapped"])
    log.info("  CORROBORATES pairs:        %d", stats["corroborates_pairs"])
    log.info("  Triples in:                %d", stats["total_triples_in"])
    log.info("  Duplicate triples dropped: %d", stats["duplicate_triples_dropped"])
    log.info("  Output:                    %s", output_path)
    log.info("  Duration:                  %.1fs", elapsed)
    log.info("=" * 60)

    triples_out = (stats["total_triples_in"]
                   - stats["duplicate_triples_dropped"])
    log_summary(
        "deduplicator",
        nodes=stats["total_nodes"],
        clusters_merged=stats["multi_node_clusters"],
        nodes_remapped=stats["nodes_remapped"],
        corroborates_pairs=stats["corroborates_pairs"],
        triples_in=stats["total_triples_in"],
        triples_out=triples_out,
        duplicates_dropped=stats["duplicate_triples_dropped"],
        duration_s=round(elapsed, 1),
    )
    check_nonempty("deduplicator", count=triples_out,
                   what="triples after dedup", allow_empty=allow_empty)
    return stats


# ══════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════

def main():
    import argparse
    from dotenv import load_dotenv
    load_dotenv()

    parser = argparse.ArgumentParser(
        description="Stage 6.6: Deduplicate knowledge nodes via embedding similarity"
    )
    parser.add_argument("--org",      required=True)
    parser.add_argument("--data-dir", default="output")
    parser.add_argument("--source", choices=["gmail", "slack", "jira", "outlook_mail", "ms_teams", "zoho_mail", "zoho_cliq"], default=None,
                        help="Deduplicate only this source's triples "
                             "(slack/jira/outlook_mail/ms_teams get namespaced files; omit for Gmail/legacy)")
    parser.add_argument(
        "--calibrate", action="store_true",
        help="Print similarity distributions to help choose thresholds; "
             "does NOT write any output",
    )
    parser.add_argument(
        "--same-thread-threshold",  type=float, default=0.92,
        help="Cosine threshold for same-thread merges (default: 0.92)",
    )
    parser.add_argument(
        "--cross-thread-threshold", type=float, default=0.96,
        help="Cosine threshold for cross-thread merges (default: 0.96)",
    )
    parser.add_argument(
        "--allow-empty", action="store_true",
        help="Don't raise if 0 triples remain after dedup.",
    )
    args = parser.parse_args()

    data_dir = Path(args.data_dir)

    from common.logger import setup_logging
    (data_dir / "logs").mkdir(parents=True, exist_ok=True)
    setup_logging(org=args.org, log_dir=str(data_dir / "logs"),
                  source="deduplicator")

    from common.output_paths import KnowledgePaths
    src = args.source if args.source != "gmail" else None
    paths = KnowledgePaths(org=args.org, data_dir=data_dir, source=src)
    paths.makedirs()

    embeddings, meta = load_embeddings(paths.knowledge_embeddings,
                                       paths.knowledge_embedding_index)
    nodes = meta["index"]["nodes"]

    if args.calibrate:
        run_calibration(embeddings, nodes)
        return

    if not paths.triples_validated.exists():
        log.error("Validated triples not found: %s — run validator first",
                  paths.triples_validated)
        return

    run_deduplication(
        validated_path=paths.triples_validated,
        embeddings_path=paths.knowledge_embeddings,
        index_path=paths.knowledge_embedding_index,
        output_path=paths.triples_deduped,
        same_thread_threshold=args.same_thread_threshold,
        cross_thread_threshold=args.cross_thread_threshold,
        allow_empty=args.allow_empty,
        corroborates_path=paths.corroborates,
    )


if __name__ == "__main__":
    main()
