"""
runners/reembed_graph.py
────────────────────────
Re-embed every node already in Neo4j with the currently configured
EMBEDDING_MODEL, in place. Needed when the provider retires a model: stored
vectors and query vectors must come from the same model or retrieval silently
returns nothing (NVIDIA retired llama-nemotron-embed-1b-v2 on 2026-08-25).

Texts mirror pipeline/embed_knowledge.py so a re-embed equals a fresh build:
  KnowledgeNode → description · Organization/Topic → name
  Person        → "name (email1, email2, email3) [internal|external]"

Resumable: each written node is stamped with `embedding_model`; reruns skip
nodes already on the configured model.

Usage (from lore-ingestion/):
    ../.venv/bin/python -m runners.reembed_graph --org ezobooks [--limit N]
"""

from __future__ import annotations

import argparse
import time

from neo4j import GraphDatabase

from common.config import get_settings, neo4j_credentials_for
from infra.embeddings.registry import get_embedding_provider

PAGE = 500

# label → Cypher expression producing the text to embed (same as embed_knowledge.py)
TEXT_EXPR = {
    "KnowledgeNode": "n.description",
    "Organization":  "n.name",
    "Topic":         "n.name",
    "Person": (
        "n.name + CASE WHEN size(coalesce(n.all_emails, [])) > 0 "
        "THEN ' (' + reduce(s = '', e IN n.all_emails[0..3] | "
        "s + CASE WHEN s = '' THEN '' ELSE ', ' END + e) + ')' ELSE '' END + "
        "CASE WHEN n.is_internal THEN ' [internal]' ELSE ' [external]' END"
    ),
}


def reembed(org: str, limit: int | None = None) -> None:
    s = get_settings()
    provider = get_embedding_provider()
    uri, user, pw = neo4j_credentials_for(org)
    driver = GraphDatabase.driver(uri, auth=(user, pw))
    done = 0
    t0 = time.time()
    try:
        for label, expr in TEXT_EXPR.items():
            while limit is None or done < limit:
                rows = driver.execute_query(
                    f"MATCH (n:{label}) WHERE coalesce(n.embedding_model, '') <> $m "
                    f"WITH n, {expr} AS text WHERE text IS NOT NULL AND text <> '' "
                    "RETURN elementId(n) AS id, text LIMIT $page",
                    m=s.embedding_model, page=PAGE,
                ).records
                if not rows:
                    break
                vecs = provider.embed_all([r["text"] for r in rows], batch_size=s.embed_batch_size)
                driver.execute_query(
                    "UNWIND $rows AS r MATCH (n) WHERE elementId(n) = r.id "
                    "SET n.embedding = r.v, n.embedding_model = $m",
                    rows=[{"id": r["id"], "v": v.tolist()} for r, v in zip(rows, vecs)],
                    m=s.embedding_model,
                )
                done += len(rows)
                print(f"{label}: +{len(rows)} (total {done}, {done / (time.time() - t0):.0f}/s)", flush=True)
    finally:
        driver.close()
    print(f"re-embedded {done} nodes with {s.embedding_model}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--org", required=True)
    ap.add_argument("--limit", type=int, default=None)
    a = ap.parse_args()
    reembed(a.org, a.limit)


if __name__ == "__main__":
    main()
