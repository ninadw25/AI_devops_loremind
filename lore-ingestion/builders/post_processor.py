"""
builders/post_processor.py
───────────────────────────
Post-processing after both databases are built.

Runs across all threads to find cross-thread patterns:
  9A: Topic Clustering    — HDBSCAN + LLM labeling → canonical topic names
  9B: Person Expertise    — Aggregate EXPERT_IN edges from triple data
  9C: Process Synthesis   — Find recurring Decision/Task patterns → Process nodes
  9D: Cross-Thread Edges  — SUPERSEDES, PRECEDED_BY temporal chains

Outputs:
  - output/cypher/pass3_post_processing.cypher   (new nodes + edges)
  - output/knowledge/{org}_topic_taxonomy.json    (clustered taxonomy)
  - Updates to doc_index.json (topic_ids on existing docs)

Usage:
    python -m builders.post_processor --org ezobooks
    python -m builders.post_processor --org ezobooks --skip-clustering
"""

import json
import time
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

from builders.cypher_utils import cypher_escape as _esc
from common.config import get_settings, validate_for
from common.hashing import node_id_for
from common.logger import get_logger
from common.stage_summary import log_summary

# Module-level counter for cluster-label LLM fallbacks (F7 from audit).
# Reset at the start of each run_post_processing() invocation.
_label_stats: dict[str, int] = {"requested": 0, "fell_back": 0}

log = get_logger("builders.post_processor")


# ══════════════════════════════════════════════════════════════════════
# 9A: TOPIC CLUSTERING
# ══════════════════════════════════════════════════════════════════════

def collect_raw_topics(validated: dict) -> list[str]:
    """Collect all topic names from REGARDING triples."""
    topics: list[str] = []
    for thread in validated.values():
        for triple in thread.get("triples", []):
            if triple["predicate"] == "REGARDING":
                name = triple["object"].get("name", "")
                if name:
                    topics.append(name)
    return topics


_post_local_embedder = None


def embed_topics(topics: list[str]) -> np.ndarray:
    """Embed topic names via the canonical embedding provider.

    Falls back to a local sentence-transformers model if the remote
    provider signals EOL (HTTP 410) — same pattern as embed_knowledge.py.
    """
    from infra.embeddings import get_embedding_provider
    provider = get_embedding_provider()

    rows: list[list[float]] = []
    batch_size = 32
    for i in range(0, len(topics), batch_size):
        batch = topics[i: i + batch_size]
        try:
            rows.extend(provider.embed_batch(batch))
        except Exception as e:
            msg = str(e)
            if "410" in msg or "Gone" in msg or "end of life" in msg:
                import logging
                logging.getLogger(__name__).warning(
                    "Embedding provider EOL — falling back to local model for topic clustering"
                )
                global _post_local_embedder
                if _post_local_embedder is None:
                    from sentence_transformers import SentenceTransformer
                    _post_local_embedder = SentenceTransformer("all-mpnet-base-v2")
                local_embs = _post_local_embedder.encode(batch, show_progress_bar=False)
                rows.extend([list(map(float, v)) for v in local_embs])
            else:
                raise
    return np.array(rows, dtype=np.float32)


def cluster_topics(topics: list[str],
                   embeddings: np.ndarray) -> dict[str, list[str]]:
    """Cluster topics using HDBSCAN, fallback to KMeans.

    Returns dict[cluster_label → list of topic names].
    """
    unique_topics = list(set(topics))
    if len(unique_topics) < 5:
        return {"general": unique_topics}

    # Build unique topic embeddings
    topic_to_idx: dict[str, int] = {}
    for i, t in enumerate(topics):
        if t not in topic_to_idx:
            topic_to_idx[t] = i
    unique_embeddings = embeddings[
        [topic_to_idx[t] for t in unique_topics]]
    # L2-normalize so euclidean distance == cosine distance
    norms = np.linalg.norm(unique_embeddings, axis=1, keepdims=True)
    norms = np.where(norms == 0, 1, norms)
    unique_embeddings = unique_embeddings / norms

    try:
        import hdbscan
        clusterer = hdbscan.HDBSCAN(
            min_cluster_size=max(3, len(unique_topics) // 20),
            min_samples=2,
            metric="euclidean",
            algorithm="best",
        )
        labels = clusterer.fit_predict(unique_embeddings)
    except ImportError:
        log.warning("hdbscan not installed, falling back to KMeans")
        from sklearn.cluster import KMeans
        n_clusters = min(max(5, len(unique_topics) // 10), 50)
        km = KMeans(n_clusters=n_clusters, random_state=42, n_init=10)
        labels = km.fit_predict(unique_embeddings)

    clusters: dict[int, list[str]] = defaultdict(list)
    for topic, label in zip(unique_topics, labels):
        clusters[label].append(topic)

    # Label clusters by most common topic
    named_clusters: dict[str, list[str]] = {}
    for label, members in sorted(clusters.items()):
        if label == -1:
            named_clusters["uncategorized"] = members
        else:
            # Use most frequent member as cluster name
            counts = Counter(members)
            cluster_name = counts.most_common(1)[0][0]
            named_clusters[cluster_name] = members

    return named_clusters


def label_clusters_llm(clusters: dict[str, list[str]]) -> dict[str, str]:
    """Use LLM to generate canonical names for each cluster.

    Returns dict[original_label → canonical_label].
    Tracks fallback counts in `_label_stats` for the end-of-run summary.
    """
    _label_stats["requested"] += 1
    from infra.llm import get_chat_provider
    try:
        provider = get_chat_provider()  # uses LLM_PROVIDER setting
        client = provider.make_sync_client()
    except Exception:
        log.warning("No LLM provider available, skipping cluster labeling")
        _label_stats["fell_back"] += 1
        return {k: k for k in clusters}

    prompt_parts = []
    for i, (label, members) in enumerate(clusters.items()):
        sample = ", ".join(members[:10])
        prompt_parts.append(f"Cluster {i}: [{sample}]")

    prompt = (
        "Given these topic clusters from an organizational knowledge base, "
        "generate a single canonical name for each cluster (2-4 words, "
        "Title Case). Return ONLY a JSON object mapping cluster number "
        "to canonical name.\n\n"
        + "\n".join(prompt_parts)
    )

    try:
        model = get_settings().extraction_model or provider.default_model
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=1000,
        )
        text = resp.choices[0].message.content.strip()
        # Extract JSON
        import re
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            mapping = json.loads(m.group(0))
            result: dict[str, str] = {}
            for i, label in enumerate(clusters):
                canonical = mapping.get(str(i), mapping.get(i, label))
                result[label] = str(canonical)
            return result
    except Exception as e:
        log.warning("LLM labeling failed: %s", e)

    _label_stats["fell_back"] += 1
    return {k: k for k in clusters}


def build_taxonomy(
    validated: dict,
    skip_clustering: bool = False,
) -> dict:
    """Build topic taxonomy from validated triples.

    Returns taxonomy dict with clusters, mappings, and per-person expertise.
    """
    raw_topics = collect_raw_topics(validated)
    log.info("9A: Collected %d raw topics (%d unique)",
             len(raw_topics), len(set(raw_topics)))

    if not raw_topics or skip_clustering:
        return {
            "clusters": {},
            "topic_to_cluster": {},
            "person_expertise": {},
        }

    # Embed (NVIDIA NeMo) and cluster (HDBSCAN). LLM cluster labeling
    # in label_clusters_llm() is a separate, optional concern that
    # degrades gracefully if openrouter_api_key is missing.
    if get_settings().nvidia_api_key and len(set(raw_topics)) >= 5:
        unique = list(set(raw_topics))
        embeddings = embed_topics(unique)
        clusters = cluster_topics(unique, embeddings)
        labels = label_clusters_llm(clusters)
    else:
        # Simple frequency-based grouping
        counts = Counter(raw_topics)
        clusters = {"all_topics": [t for t, _ in counts.most_common()]}
        labels = {"all_topics": "All Topics"}

    # Build topic → cluster mapping
    topic_to_cluster: dict[str, str] = {}
    for original_label, members in clusters.items():
        canonical = labels.get(original_label, original_label)
        for topic in members:
            topic_to_cluster[topic] = canonical

    # Person expertise from REGARDING + DECIDED chains
    person_expertise: dict[str, dict[str, int]] = defaultdict(
        lambda: defaultdict(int))

    for thread in validated.values():
        # Find who made decisions in this thread
        deciders: set[str] = set()
        for triple in thread.get("triples", []):
            if triple["predicate"] == "DECIDED":
                name = triple["subject"].get("name", "")
                if name:
                    deciders.add(name)

        # Map topics to those deciders
        for triple in thread.get("triples", []):
            if triple["predicate"] == "REGARDING":
                topic = triple["object"].get("name", "")
                cluster = topic_to_cluster.get(topic, topic)
                for person in deciders:
                    person_expertise[person][cluster] += 1

    log.info("9A: %d clusters, %d topic mappings, %d persons with expertise",
             len(clusters), len(topic_to_cluster), len(person_expertise))

    return {
        "clusters": {labels.get(k, k): v for k, v in clusters.items()},
        "topic_to_cluster": topic_to_cluster,
        "person_expertise": {
            p: dict(sorted(topics.items(), key=lambda x: -x[1]))
            for p, topics in person_expertise.items()
        },
    }


# ══════════════════════════════════════════════════════════════════════
# 9B: PERSON EXPERTISE EDGES (EXPERT_IN)
# ══════════════════════════════════════════════════════════════════════

def generate_expertise_cypher(
    taxonomy: dict,
    name_to_pid: dict,
) -> list[str]:
    """Generate EXPERT_IN edges from person expertise data."""
    lines = ["// ── 9B: Person expertise edges ──", ""]

    for person_name, topics in taxonomy.get("person_expertise", {}).items():
        pid = name_to_pid.get(person_name.lower(), "")
        if not pid:
            continue

        for topic, count in topics.items():
            if count < 1:
                continue
            topic_id = node_id_for("Topic", topic)
            lines.extend([
                f"MERGE (t:Topic {{topic_id: '{_esc(topic_id)}'}})",
                f"ON CREATE SET t.name = '{_esc(topic)}', "
                f"t.created_at = datetime();",
                "",
                f"MATCH (p:Person {{person_id: '{_esc(pid)}'}}), "
                f"(t:Topic {{topic_id: '{_esc(topic_id)}'}})",
                f"MERGE (p)-[r:EXPERT_IN]->(t)",
                f"ON CREATE SET r.count = {count}, "
                f"r.created_at = datetime();",
                "",
            ])

    return lines


# ══════════════════════════════════════════════════════════════════════
# 9C: PROCESS SYNTHESIS
# ══════════════════════════════════════════════════════════════════════

def synthesize_processes(validated: dict) -> list[str]:
    """Find recurring Decision/Task patterns per topic → Process nodes.

    A "process" is detected when multiple threads in the same topic
    area have similar Task sequences.
    """
    lines = ["// ── 9C: Process synthesis ──", ""]

    # Group decisions by topic
    topic_decisions: dict[str, list[dict]] = defaultdict(list)

    for tid, thread in validated.items():
        topics = set()
        decisions = []
        tasks = []

        for triple in thread.get("triples", []):
            if triple["predicate"] == "REGARDING":
                topics.add(triple["object"].get("name", ""))
            elif triple["predicate"] == "DECIDED":
                decisions.append(triple["object"].get("description", ""))
            elif triple["predicate"] == "ASSIGNED_TO":
                tasks.append(triple["subject"].get("description", ""))

        for topic in topics:
            if decisions or tasks:
                topic_decisions[topic].append({
                    "thread_id": tid,
                    "decisions": decisions,
                    "tasks": tasks,
                })

    # Create Process nodes for topics with 3+ decision threads
    process_count = 0
    for topic, thread_data in topic_decisions.items():
        if len(thread_data) < 3:
            continue

        process_id = node_id_for("Process", topic)

        # Pair each task description with the thread it came from so we can
        # re-mint the Task node_id with the same scope the validator used
        # (scope=thread_id). Without this, the MATCH below misses the Task
        # row and the HAS_STEP edge is silently dropped.
        all_task_refs: list[tuple[str, str]] = []
        for td in thread_data:
            all_task_refs.extend((desc, td["thread_id"]) for desc in td["tasks"])

        # Deduplicate by description but keep the first thread_id we saw.
        seen: set[str] = set()
        unique_task_refs: list[tuple[str, str]] = []
        for desc, tid in all_task_refs:
            if desc in seen:
                continue
            seen.add(desc)
            unique_task_refs.append((desc, tid))
        unique_task_refs = unique_task_refs[:10]

        lines.extend([
            f"MERGE (proc:Process {{process_id: '{_esc(process_id)}'}})",
            f"ON CREATE SET proc.name = '{_esc(topic)} Process', "
            f"proc.thread_count = {len(thread_data)}, "
            f"proc.created_at = datetime();",
            "",
        ])

        # Link to topic
        topic_id = node_id_for("Topic", topic)
        lines.extend([
            f"MATCH (proc:Process {{process_id: '{_esc(process_id)}'}}), "
            f"(t:Topic {{topic_id: '{_esc(topic_id)}'}})",
            f"MERGE (proc)-[r:REGARDING]->(t)",
            f"ON CREATE SET r.created_at = datetime();",
            "",
        ])

        # HAS_STEP edges for common tasks
        for i, (task_desc, src_thread_id) in enumerate(unique_task_refs[:5]):
            task_id = node_id_for("Task", task_desc, scope=src_thread_id)
            lines.extend([
                f"MATCH (proc:Process {{process_id: '{_esc(process_id)}'}})",
                f"MATCH (tk:Task {{task_id: '{_esc(task_id)}'}})",
                f"MERGE (proc)-[r:HAS_STEP]->(tk)",
                f"ON CREATE SET r.step_order = {i + 1}, "
                f"r.created_at = datetime();",
                "",
            ])

        process_count += 1

    log.info("9C: %d processes synthesized from %d topics",
             process_count, len(topic_decisions))

    return lines


# ══════════════════════════════════════════════════════════════════════
# 9D: CROSS-THREAD EDGES
# ══════════════════════════════════════════════════════════════════════

def build_cross_thread_edges(validated: dict) -> list[str]:
    """Build temporal and semantic edges across threads.

    - SUPERSEDES: newer decision on same topic replaces older
    - PRECEDED_BY: temporal chain of related events
    """
    lines = ["// ── 9D: Cross-thread edges ──", ""]

    # Collect decisions with their topics and rough timestamps
    decisions_by_topic: dict[str, list[dict]] = defaultdict(list)

    for tid, thread in validated.items():
        topics = set()
        for triple in thread.get("triples", []):
            if triple["predicate"] == "REGARDING":
                topics.add(triple["object"].get("name", ""))

        # Use the earliest event timestamp in the thread as the sort key
        thread_ts = min(
            (e.get("timestamp", "") for e in thread.get("raw", {}).get("emails", []) if e.get("timestamp")),
            default=(thread.get("triples") or [{}])[0].get("evidence_sentence", "") if thread.get("triples") else tid,
        )

        for triple in thread.get("triples", []):
            if triple["predicate"] == "DECIDED":
                dec_id = triple["object"].get("node_id", "")
                for topic in topics:
                    decisions_by_topic[topic].append({
                        "node_id": dec_id,
                        "thread_id": tid,
                        "timestamp": thread_ts,
                        "description": triple["object"].get("description", ""),
                    })

    supersedes_count = 0
    for topic, decs in decisions_by_topic.items():
        if len(decs) < 2:
            continue

        # Sort by actual timestamp; fall back to thread_id if timestamps missing
        decs.sort(key=lambda d: (d.get("timestamp") or d["thread_id"]))

        for i in range(1, len(decs)):
            older = decs[i - 1]
            newer = decs[i]
            if older["node_id"] == newer["node_id"]:
                continue

            lines.extend([
                f"MATCH (newer:Decision "
                f"{{decision_id: '{_esc(newer['node_id'])}'}}),"
                f" (older:Decision "
                f"{{decision_id: '{_esc(older['node_id'])}'}})",
                f"MERGE (newer)-[r:SUPERSEDES]->(older)",
                f"ON CREATE SET r.topic = '{_esc(topic)}', "
                f"r.created_at = datetime();",
                "",
            ])
            supersedes_count += 1

    log.info("9D: %d SUPERSEDES edges across %d topics",
             supersedes_count, len(decisions_by_topic))

    return lines


# ══════════════════════════════════════════════════════════════════════
# MAIN POST-PROCESSOR
# ══════════════════════════════════════════════════════════════════════

def run_post_processing(
    org: str,
    data_dir: Path,
    skip_clustering: bool = False,
):
    """Run all post-processing stages."""
    start = time.time()
    _label_stats["requested"] = 0
    _label_stats["fell_back"] = 0

    log.info("=" * 55)
    log.info("POST-PROCESSING")
    log.info("  Org: %s", org)
    log.info("=" * 55)

    from common.output_paths import KnowledgePaths

    # Use KnowledgePaths to find the canonical locations for triples and registry
    paths = KnowledgePaths(org=org, data_dir=data_dir)
    paths.makedirs()

    knowledge_dir = paths.root

    cypher_dir = data_dir / "cypher"
    cypher_dir.mkdir(parents=True, exist_ok=True)

    # Load triples — prefer deduped (Stage 6.6), fall back to validated, so
    # post-processing and unified_builder operate on the SAME node set.
    source_path = paths.triples_deduped if paths.triples_deduped.exists() else paths.triples_validated
    log.info("Loading triples from: %s", source_path.name)
    validated = json.loads(source_path.read_text(encoding="utf-8"))

    # Load registry for name resolution — prefer centralized identity
    # (resolved_registry.json), fall back to the Gmail person_registry (W4).
    from pipeline.identity.registry_view import registry_source
    registry = json.loads(registry_source(org, data_dir).read_text(encoding="utf-8"))
    name_to_pid: dict[str, str] = {}
    for p in registry.get("persons", []):
        pid = p["person_id"]
        for name in [p.get("canonical_name", "")] + p.get("all_names", []):
            if name:
                name_to_pid[name.lower()] = pid

    all_cypher_lines: list[str] = [
        "// ── Pass 3: Post-processing ──",
        f"// Generated for org: {org}",
        "",
    ]

    # ── 9A: Topic Clustering ─────────────────────────────────────
    taxonomy = build_taxonomy(validated, skip_clustering=skip_clustering)
    taxonomy_path = knowledge_dir / f"{org}_topic_taxonomy.json"
    taxonomy_path.write_text(
        json.dumps(taxonomy, ensure_ascii=False, indent=2),
        encoding="utf-8")
    log.info("  Saved taxonomy: %s", taxonomy_path)

    # ── 9B: Person Expertise ─────────────────────────────────────
    expertise_lines = generate_expertise_cypher(taxonomy, name_to_pid)
    all_cypher_lines.extend(expertise_lines)

    # ── 9C: Process Synthesis ────────────────────────────────────
    process_lines = synthesize_processes(validated)
    all_cypher_lines.extend(process_lines)

    # ── 9D: Cross-Thread Edges ───────────────────────────────────
    cross_lines = build_cross_thread_edges(validated)
    all_cypher_lines.extend(cross_lines)

    # ── Save Cypher ──────────────────────────────────────────────
    cypher_path = cypher_dir / "pass3_post_processing.cypher"
    cypher_path.write_text("\n".join(all_cypher_lines), encoding="utf-8")

    # ── Update doc_index with topic_ids ──────────────────────────
    idx_path = data_dir / "index" / "doc_index.json"
    if idx_path.exists() and taxonomy.get("topic_to_cluster"):
        doc_index = json.loads(idx_path.read_text(encoding="utf-8"))
        topic_map = taxonomy["topic_to_cluster"]
        # doc_index may be a metadata dict {model, dim, nodes:[...]} or a list
        docs = doc_index if isinstance(doc_index, list) else doc_index.get("nodes", [])
        updated = 0
        for doc in docs:
            if not isinstance(doc, dict):
                continue
            dept_tags = doc.get("department_tags", doc.get("metadata", {}).get("department_tags", []))
            topic_ids = [topic_map[tag] for tag in dept_tags if tag in topic_map]
            if topic_ids:
                doc["topic_ids"] = topic_ids
                updated += 1
        idx_path.write_text(json.dumps(doc_index, ensure_ascii=False), encoding="utf-8")
        log.info("  Updated %d docs in doc_index with topic_ids", updated)

    elapsed = time.time() - start

    n_clusters = len(taxonomy.get("clusters", {}))
    log.info("")
    log.info("=" * 55)
    log.info("POST-PROCESSING COMPLETE")
    log.info("  Cypher:    %s", cypher_path)
    log.info("  Taxonomy:  %s", taxonomy_path)
    log.info("  Clusters:  %d (LLM labeled: %d, fallback: %d)",
             n_clusters,
             _label_stats["requested"] - _label_stats["fell_back"],
             _label_stats["fell_back"])
    log.info("  Duration:  %.1fs", elapsed)
    log.info("=" * 55)

    log_summary(
        "post_processor",
        org=org,
        clusters=n_clusters,
        label_calls=_label_stats["requested"],
        label_fallbacks=_label_stats["fell_back"],
        skip_clustering=skip_clustering,
        duration_s=round(elapsed, 1),
    )


# ══════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════

def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Post-processing (topics, expertise, "
                    "processes, cross-thread edges)")
    parser.add_argument("--org", required=True,
                        help="Organization name (e.g. ezobooks)")
    parser.add_argument("--data-dir", default="output",
                        help="Output directory root (default: output)")
    parser.add_argument("--skip-clustering", action="store_true",
                        help="Skip topic clustering (9A)")

    args = parser.parse_args()

    if not args.skip_clustering:
        validate_for("post_processor")

    run_post_processing(
        org=args.org,
        data_dir=Path(args.data_dir),
        skip_clustering=args.skip_clustering,
    )


if __name__ == "__main__":
    main()
