"""
pipeline/embed_knowledge.py
───────────────────────────
Stage 6.5: Embed unique knowledge nodes for downstream semantic use.

Produces a shared embedding artifact used by:
  - deduplicator.py (cosine similarity clustering)
  - unified_builder.py (vector index — can reuse to avoid re-embedding)

Uses NVIDIA NeMo Retriever (nvidia/llama-3.2-nemoretriever-300m-embed-v1)
via the NVIDIA API (OpenAI-compatible). Checkpoints every CHECKPOINT_EVERY
batches so the run can be safely resumed after interruption.

Input:  output/knowledge/{org}_triples_validated.json
Output: output/knowledge/{org}_knowledge_embeddings.npy
        output/knowledge/{org}_knowledge_embedding_index.json

Checkpoint (auto-deleted on success):
        output/knowledge/{org}_knowledge_embeddings.ckpt.npy
        output/knowledge/{org}_knowledge_embeddings.ckpt.json

Usage:
    python -m pipeline.embed_knowledge --org ezobooks
    python -m pipeline.embed_knowledge --org ezobooks  # resume after crash
"""

import json
import time
from pathlib import Path

import numpy as np

# Local fallback embeddings
_LOCAL_EMBED_MODEL_NAME = "all-mpnet-base-v2"
_local_embedder = None

from common.config import get_settings, validate_for
from common.logger import get_logger
from common.stage_gate import CheckpointCorrupt, check_nonempty
from common.stage_summary import log_summary
from infra.embeddings import get_embedding_provider

log = get_logger("pipeline.embed_knowledge")

EMBED_BATCH_SIZE  = 32
CHECKPOINT_EVERY  = 50   # save checkpoint every N batches (~1600 nodes)
MAX_RETRIES       = 5

# Node types to embed.
# Decision/Task/Fact/Event: for dedup + Neo4j vector search.
# Organization: node_ids in triples match Neo4j (hashed from name).
# Person: collected separately from registry (uses person_id, not triple node_id).
_EMBED_TYPES = {"Decision", "Task", "Fact", "Event", "Organization", "Topic"}


# ══════════════════════════════════════════════════════════════════════
# NODE COLLECTION
# ══════════════════════════════════════════════════════════════════════

def collect_unique_nodes(validated: dict) -> list[dict]:
    """Collect one entry per unique node_id from triples (Decision/Task/Fact/Event/Organization).

    Knowledge nodes use 'description'; Organization nodes use 'name'.
    First occurrence wins.
    """
    seen: set[str] = set()
    nodes: list[dict] = []

    for tid, thread in validated.items():
        for triple in thread.get("triples", []):
            for node_key in ("subject", "object"):
                node = triple[node_key]
                ntype = node.get("type", "")
                nid   = node.get("node_id", "")
                # Knowledge nodes have 'description'; Person/Org nodes have 'name'
                desc  = node.get("description") or node.get("name") or ""

                if ntype not in _EMBED_TYPES:
                    continue
                if not nid or not desc:
                    continue
                if nid in seen:
                    continue

                seen.add(nid)
                nodes.append({
                    "node_id":     nid,
                    "type":        ntype,
                    "description": desc,
                    # Organizations are org-global; don't tie them to a single thread
                    "thread_id":   "" if ntype == "Organization" else tid,
                })

    return nodes


def collect_person_nodes(registry_path: Path) -> list[dict]:
    """Collect Person nodes from the person registry.

    Uses person_id (not the triple node_id) so embeddings can be matched
    directly to Neo4j Person nodes by their person_id property.
    Builds a short descriptive text: name + emails + internal/external.
    """
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    nodes: list[dict] = []

    for person in registry.get("persons", []):
        pid  = person.get("person_id", "")
        name = person.get("canonical_name", "")
        if not pid or not name:
            continue

        emails   = person.get("all_emails", [])
        internal = "internal" if person.get("is_internal") else "external"
        email_str = ", ".join(emails[:3])
        desc = f"{name} ({email_str}) [{internal}]" if email_str else f"{name} [{internal}]"

        nodes.append({
            "node_id":     pid,   # person_id — matches Neo4j Person.person_id
            "type":        "Person",
            "description": desc,
            "thread_id":   "",
        })

    return nodes


# ══════════════════════════════════════════════════════════════════════
# CHECKPOINT
# ══════════════════════════════════════════════════════════════════════

def _ckpt_paths(base: Path) -> tuple[Path, Path]:
    """Return (embeddings_ckpt.npy, meta_ckpt.json) paths.

    Checkpoint lives alongside the embeddings file (same directory).
    """
    stem = base.stem          # e.g. "ezobooks_knowledge_embeddings"
    parent = base.parent
    return (
        parent / f"{stem}.ckpt.npy",
        parent / f"{stem}.ckpt.json",
    )


def load_checkpoint(
    embeddings_path: Path,
    *,
    force_restart: bool = False,
) -> tuple[list[list[float]], int]:
    """Load partial embeddings and the number of nodes already done.

    Returns (partial_rows, done_count). partial_rows is a list of
    1-D float lists (not yet stacked). Returns ([], 0) if no checkpoint
    file exists.

    Raises `CheckpointCorrupt` if a checkpoint exists but cannot be
    read. Pass `force_restart=True` to delete the broken checkpoint
    and start fresh (mirrors `--force-restart` on the CLI).
    """
    npy_ckpt, meta_ckpt = _ckpt_paths(embeddings_path)
    if not npy_ckpt.exists() or not meta_ckpt.exists():
        return [], 0

    try:
        arr  = np.load(npy_ckpt)
        meta = json.loads(meta_ckpt.read_text())
        done = int(meta.get("done", len(arr)))
        log.info("Checkpoint found — resuming from node %d/%d",
                 done, meta.get("total", "?"))
        return arr.tolist(), done
    except Exception as e:
        if force_restart:
            log.warning(
                "Corrupt checkpoint (%s) — deleting and starting fresh "
                "because --force-restart was set", e,
            )
            for p in (npy_ckpt, meta_ckpt):
                p.unlink(missing_ok=True)
            return [], 0
        raise CheckpointCorrupt(
            f"Embedding checkpoint at {npy_ckpt} / {meta_ckpt} could not "
            f"be parsed ({type(e).__name__}: {e}). Re-run with "
            f"--force-restart to discard and start fresh."
        ) from e


def save_checkpoint(
    embeddings_path: Path,
    partial: list[list[float]],
    done: int,
    total: int,
    model_name: str,
) -> None:
    """Atomically save partial embedding array + metadata."""
    npy_ckpt, meta_ckpt = _ckpt_paths(embeddings_path)
    tmp_npy  = npy_ckpt.with_suffix(".tmp.npy")
    tmp_meta = meta_ckpt.with_suffix(".tmp.json")

    arr = np.array(partial, dtype=np.float32)
    np.save(tmp_npy, arr)
    tmp_meta.write_text(
        json.dumps({"done": done, "total": total, "model": model_name}),
        encoding="utf-8",
    )
    tmp_npy.rename(npy_ckpt)
    tmp_meta.rename(meta_ckpt)


def delete_checkpoint(embeddings_path: Path) -> None:
    for p in _ckpt_paths(embeddings_path):
        p.unlink(missing_ok=True)


# ══════════════════════════════════════════════════════════════════════
# EMBEDDING
# ══════════════════════════════════════════════════════════════════════

def embed_texts(
    texts: list[str],
    api_key: str,
    embeddings_path: Path,
    model_name: str,
    *,
    start_from: int = 0,
    partial_rows: list[list[float]] | None = None,
) -> np.ndarray:
    """Embed texts[start_from:] and return full L2-normalized [N, D] array.

    Checkpoints every CHECKPOINT_EVERY batches. If start_from > 0 and
    partial_rows is provided, those rows are prepended to the result.

    `api_key` is retained in the signature for callers that pass it
    explicitly; the active embedding provider reads its own credential
    from Settings, so the argument is now informational.
    """
    del api_key  # provider reads credentials from Settings

    provider = get_embedding_provider()

    all_rows: list[list[float]] = list(partial_rows or [])
    total = len(texts)
    remaining = texts[start_from:]

    log.info("  Embedding %d nodes (skipping %d already done) ...",
             len(remaining), start_from)

    batch_num = 0
    for i in range(0, len(remaining), EMBED_BATCH_SIZE):
        batch     = remaining[i: i + EMBED_BATCH_SIZE]
        abs_start = start_from + i        # absolute node index
        retries   = 0

        while True:
            try:
                all_rows.extend(provider.embed_batch(batch))
                break
            except Exception as e:
                # If the remote provider signals model EOL (HTTP 410 'Gone'),
                # fall back to a local sentence-transformers model to avoid
                # blocking the pipeline. Otherwise, retry transient errors.
                msg = str(e)
                if "410" in msg or "Gone" in msg or "reached its end of life" in msg:
                    # Fail loud rather than silently emit wrong-dimension vectors:
                    # the local fallback model must match the configured
                    # embedding_dim, or the resulting .npy is incompatible with
                    # the vector index, deduplicator, and backend (silent corruption).
                    expected_dim = get_settings().embedding_dim
                    log.warning("Embedding provider EOL or unavailable (%s). Falling back to local model %s", msg[:120], _LOCAL_EMBED_MODEL_NAME)
                    try:
                        global _local_embedder
                        if _local_embedder is None:
                            from sentence_transformers import SentenceTransformer
                            _local_embedder = SentenceTransformer(_LOCAL_EMBED_MODEL_NAME)
                        local_embs = _local_embedder.encode(batch, show_progress_bar=False)
                        local_dim = len(local_embs[0]) if len(local_embs) else 0
                        if local_dim != expected_dim:
                            raise RuntimeError(
                                f"Local fallback model {_LOCAL_EMBED_MODEL_NAME} produces "
                                f"{local_dim}-dim vectors but embedding_dim is configured as "
                                f"{expected_dim}. Refusing to write mismatched embeddings into a "
                                f"{expected_dim}-dim index. Restore the remote embedding provider, "
                                f"or set EMBEDDING_MODEL/EMBEDDING_DIM to a {local_dim}-dim local model."
                            )
                        # convert to list-of-lists
                        all_rows.extend([list(map(float, v)) for v in local_embs])
                        break
                    except Exception as local_e:
                        log.error("Local embedder failed: %s", str(local_e)[:160])
                        raise
                retries += 1
                if retries > MAX_RETRIES:
                    raise
                wait = 60 if "429" in msg or "rate" in msg.lower() else 10
                log.warning(
                    "Batch %d–%d retry %d/%d (%s) — sleeping %ds",
                    abs_start, abs_start + len(batch),
                    retries, MAX_RETRIES, msg[:80], wait,
                )
                time.sleep(wait)

        batch_num += 1
        done_abs = start_from + i + len(batch)

        # Progress log every 10 batches
        if batch_num % 10 == 0:
            log.info("  Embedded %d / %d nodes ...", done_abs, total)

        # Checkpoint every CHECKPOINT_EVERY batches
        if batch_num % CHECKPOINT_EVERY == 0:
            save_checkpoint(
                embeddings_path,
                all_rows,
                done_abs,
                total,
                model_name,
            )
            log.info("  Checkpoint saved at node %d/%d", done_abs, total)

    arr   = np.array(all_rows, dtype=np.float32)
    norms = np.linalg.norm(arr, axis=1, keepdims=True)
    norms = np.where(norms == 0, 1, norms)
    return arr / norms


# ══════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════

def run_embed_knowledge(
    validated_path: Path,
    embeddings_path: Path,
    index_path: Path,
    api_key: str,
    registry_path: Path | None = None,
    allow_empty: bool = False,
    force_restart: bool = False,
) -> dict:
    """Embed all unique knowledge nodes + persons and save embeddings + index.

    Incremental mode: if a completed embeddings file already exists, only new
    nodes (those whose node_id is not in the existing index) are embedded and
    appended. This avoids re-embedding 35k nodes when just adding Person/Org.
    """
    start = time.time()
    s = get_settings()
    embed_model = s.embedding_model

    log.info("=" * 55)
    log.info("EMBED KNOWLEDGE NODES — Stage 6.5")
    log.info("  Input:  %s", validated_path.name)
    log.info("  Provider: %s", s.embedding_provider)
    log.info("  Model:    %s", embed_model)
    log.info("=" * 55)

    validated = json.loads(validated_path.read_text(encoding="utf-8"))

    nodes = collect_unique_nodes(validated)
    log.info("Collected %d unique knowledge + org nodes from triples", len(nodes))

    # Collect Person nodes from registry (separate, uses person_id)
    if registry_path and registry_path.exists():
        person_nodes = collect_person_nodes(registry_path)
        log.info("Collected %d person nodes from registry", len(person_nodes))
        nodes = nodes + person_nodes
    else:
        log.warning("No registry_path provided — Person nodes will not be embedded")

    if not nodes:
        log.info("No knowledge nodes to embed.")
        return {"nodes": 0}

    # ── Incremental mode: reuse existing completed embeddings ─────────
    # If a completed index exists (no checkpoint), load it and only embed
    # nodes whose node_id is not already present.
    existing_embeddings: np.ndarray | None = None
    existing_nodes: list[dict] = []

    partial_rows, start_from = load_checkpoint(
        embeddings_path, force_restart=force_restart)
    if start_from == 0 and embeddings_path.exists() and index_path.exists():
        # No in-progress checkpoint, but a completed file exists — incremental
        existing_embeddings = np.load(embeddings_path)
        existing_index = json.loads(index_path.read_text(encoding="utf-8"))
        existing_nodes = existing_index.get("nodes", [])
        existing_ids = {n["node_id"] for n in existing_nodes}

        new_nodes = [n for n in nodes if n["node_id"] not in existing_ids]
        if not new_nodes:
            log.info("All %d nodes already embedded — nothing to do.", len(nodes))
            elapsed = time.time() - start
            return {"nodes": len(nodes), "new": 0, "duration_seconds": round(elapsed, 1)}

        log.info("Incremental: %d existing nodes + %d new nodes to embed",
                 len(existing_nodes), len(new_nodes))
        nodes_to_embed = new_nodes
    else:
        if start_from > 0:
            log.info("Checkpoint found — resuming from node %d / %d", start_from, len(nodes))
        nodes_to_embed = nodes

    type_counts: dict[str, int] = {}
    for node in nodes_to_embed:
        type_counts[node["type"]] = type_counts.get(node["type"], 0) + 1
    for t, c in sorted(type_counts.items(), key=lambda x: -x[1]):
        log.info("  %-12s %d", t, c)

    # ── Embed ─────────────────────────────────────────────────────────
    if existing_embeddings is not None:
        # Only embed the new nodes; prepend existing rows
        texts      = [n["description"] for n in nodes_to_embed]
        new_embs   = embed_texts(
            texts, api_key, embeddings_path,
            model_name=embed_model,
            start_from=0, partial_rows=[],
        )
        # Reassign to full combined set
        embeddings = np.concatenate([existing_embeddings, new_embs], axis=0)
        nodes      = existing_nodes + nodes_to_embed
    elif start_from >= len(nodes):
        log.info("Checkpoint complete — rebuilding index only.")
        embeddings = np.load(_ckpt_paths(embeddings_path)[0])
        norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
        norms = np.where(norms == 0, 1, norms)
        embeddings = embeddings / norms
    else:
        texts      = [n["description"] for n in nodes]
        embeddings = embed_texts(
            texts, api_key, embeddings_path,
            model_name=embed_model,
            start_from=start_from, partial_rows=partial_rows,
        )

    # ── Save final artifacts ─────────────────────────────────────────
    embeddings_path.parent.mkdir(parents=True, exist_ok=True)
    np.save(embeddings_path, embeddings)

    index = {
        "model":      embed_model,
        "dim":        int(embeddings.shape[1]),
        "normalized": True,
        "nodes": [
            {
                "node_id":     n["node_id"],
                "type":        n["type"],
                "description": n["description"],
                "thread_id":   n["thread_id"],
                "row":         i,
            }
            for i, n in enumerate(nodes)
        ],
    }
    index_path.write_text(
        json.dumps(index, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    delete_checkpoint(embeddings_path)

    elapsed = time.time() - start
    log.info("")
    log.info("=" * 55)
    log.info("EMBEDDING COMPLETE")
    log.info("  Nodes:       %d", len(nodes))
    log.info("  Dimensions:  %d", embeddings.shape[1])
    log.info("  Size:        %.1f MB", embeddings.nbytes / 1_000_000)
    log.info("  Output:      %s", embeddings_path)
    log.info("  Index:       %s", index_path)
    log.info("  Duration:    %.1fs", elapsed)
    log.info("=" * 55)

    log_summary(
        "embed_knowledge",
        nodes=len(nodes),
        dim=int(embeddings.shape[1]),
        size_mb=round(embeddings.nbytes / 1_000_000, 1),
        model=embed_model,
        duration_s=round(elapsed, 1),
    )
    check_nonempty("embed_knowledge", count=len(nodes),
                   what="embedded nodes", allow_empty=allow_empty)

    return {
        "nodes":            len(nodes),
        "dim":              int(embeddings.shape[1]),
        "types":            type_counts,
        "duration_seconds": round(elapsed, 1),
    }


# ══════════════════════════════════════════════════════════════════════
# LOADER (used by downstream stages)
# ══════════════════════════════════════════════════════════════════════

def load_embeddings(
    embeddings_path: Path,
    index_path: Path,
) -> tuple[np.ndarray, dict]:
    """Load embeddings + index.

    Returns (embeddings_array, {"index": ..., "node_id_to_row": ...}).
    """
    embeddings      = np.load(embeddings_path)
    index           = json.loads(index_path.read_text(encoding="utf-8"))
    node_id_to_row  = {n["node_id"]: n["row"] for n in index["nodes"]}
    return embeddings, {"index": index, "node_id_to_row": node_id_to_row}


# ══════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════

def main():
    import argparse
    from dotenv import load_dotenv
    load_dotenv()

    parser = argparse.ArgumentParser(
        description="Stage 6.5: Embed unique knowledge nodes (NVIDIA NeMo Retriever)")
    parser.add_argument("--org",      required=True,
                        help="Organization name (e.g. ezobooks)")
    parser.add_argument("--data-dir", default="output",
                        help="Output directory root (default: output)")
    parser.add_argument("--source", choices=["gmail", "slack", "jira", "outlook_mail", "ms_teams", "zoho_mail", "zoho_cliq"], default=None,
                        help="Source to embed (slack/jira/outlook_mail/ms_teams get namespaced files; "
                             "omit or 'gmail' for legacy unsuffixed files)")
    parser.add_argument("--allow-empty", action="store_true",
                        help="Don't raise if there are 0 nodes to embed.")
    parser.add_argument(
        "--force-restart", action="store_true",
        help="If the checkpoint is corrupt, delete it and start over "
             "instead of aborting (default: abort).",
    )
    args = parser.parse_args()

    data_dir = Path(args.data_dir)

    from common.logger import setup_logging
    logs_dir = data_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    setup_logging(org=args.org, log_dir=str(logs_dir),
                  source="embed_knowledge")

    validate_for("embed_knowledge")
    api_key = get_settings().nvidia_api_key

    from common.output_paths import KnowledgePaths
    src = args.source if args.source != "gmail" else None
    paths = KnowledgePaths(org=args.org, data_dir=data_dir, source=src)
    paths.makedirs()
    # Prefer deduped file if it exists (same source as unified_builder)
    validated_path  = (paths.triples_deduped if paths.triples_deduped.exists()
                       else paths.triples_validated)
    embeddings_path = paths.knowledge_embeddings
    index_path      = paths.knowledge_embedding_index
    registry_path   = paths.person_registry

    if not validated_path.exists():
        log.error("Triples not found: %s", validated_path)
        return

    run_embed_knowledge(validated_path, embeddings_path, index_path, api_key,
                        registry_path=registry_path,
                        allow_empty=args.allow_empty,
                        force_restart=args.force_restart)


if __name__ == "__main__":
    main()
