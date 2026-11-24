"""Score retrieval systems on the ontology-derived questions.

Systems (all use the same embedding model):
  raw_rag    LangChain+FAISS over raw email threads      (what a normal RAG pipeline indexes)
  node_rag   LangChain+FAISS over extracted graph nodes  (isolates graph *structure* from extraction)
  lore       Lore engine: person resolution + vector seeds + graph expansion + rerank

Metrics per question (top-10 results):
  thread recall@10 = |returned threads ∩ GT threads| / min(|GT threads|, 10)     all systems
  node recall@10   = matched GT nodes / min(|GT nodes|, 10)                       node_rag, lore
  name-in-text     = share of GT node texts that literally contain the entity asked about
                     (evidence for WHY vector search struggles: the answer text doesn't name the person)

    python -m benchmarks.rag_eval.run_graph_eval --systems node_rag lore raw_rag
"""
from __future__ import annotations

import argparse
import json
import re
import statistics as st
import time
from pathlib import Path

import jwt
import numpy as np
import requests
from langchain_community.vectorstores import FAISS

from .common import env
from .run_eval import NvidiaQueryEmbeddings, build_faiss

DATA, RESULTS, K = Path(__file__).parent / "data", Path(__file__).parent / "results", 10
norm = lambda s: re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def node_match(gt: dict, thread: str, desc: str) -> bool:
    g, d = norm(gt["desc"])[:40], norm(re.sub(r"^\[[A-Za-z]+\]\s*", "", desc))
    return gt["thread"] == thread and bool(g) and (g in d or d[:40] in norm(gt["desc"]))


def raw_store() -> FAISS:
    ids = json.loads((DATA / "raw_thread_ids.json").read_text())
    texts = json.loads((DATA / "raw_thread_texts.json").read_text())
    vecs = np.load(DATA / "raw_vecs.npy")
    return FAISS.from_embeddings(list(zip(texts, vecs.tolist())), NvidiaQueryEmbeddings(),
                                 metadatas=[{"thread_id": t} for t in ids], normalize_L2=False)


def make_system(name: str):
    if name == "raw_rag":
        st_ = raw_store()
        def run(q):
            docs = st_.as_retriever(search_kwargs={"k": K}).invoke(q)
            return [(d.metadata["thread_id"], d.page_content[:80]) for d in docs]
    elif name == "node_rag":
        st_ = build_faiss()
        def run(q):
            docs = st_.as_retriever(search_kwargs={"k": K}).invoke(q)
            return [(d.metadata["thread_id"], d.page_content) for d in docs]
    else:
        secret = env("INTERNAL_AUTH_SECRET")
        tok = jwt.encode({"sub": "eval", "org": "ezobooks", "exp": int(time.time()) + 7200}, "x" * 32, algorithm="HS256")
        def run(q):
            r = requests.post(env("ENGINE_URL", "http://localhost:8000") + "/api/query", timeout=180,
                              headers={"X-Internal-Auth": secret, "Authorization": f"Bearer {tok}"},
                              json={"query": q, "k": K, "super_admin": True})
            r.raise_for_status()
            return [(s["thread_id"], s["description"]) for s in r.json()["sources"]][:K]
    return run


def evaluate(name: str, qs: list[dict]) -> list[dict]:
    run, rows = make_system(name), []
    for i, q in enumerate(qs, 1):
        t0 = time.time()
        try:
            got = run(q["question"])
        except Exception as e:
            print("ERR", name, e); got = []
        ms = (time.time() - t0) * 1000
        threads = list(dict.fromkeys(t for t, _ in got))[:K]
        tr = len(set(threads) & set(q["gt_threads"])) / min(len(q["gt_threads"]), K)
        nr = None
        if name != "raw_rag":
            hit = sum(1 for g in q["gt_nodes"] if any(node_match(g, t, d) for t, d in got))
            nr = hit / min(len(q["gt_nodes"]), K)
        rows.append({"family": q["family"], "question": q["question"], "thread_recall": tr, "node_recall": nr,
                     "ms": ms, "returned_threads": threads})
        print(name, i, q["family"], f"thread_recall={tr:.2f}", f"node_recall={nr}", flush=True)
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--systems", nargs="+", default=["node_rag", "lore", "raw_rag"])
    a = ap.parse_args()
    qs = json.loads((DATA / "graph_questions.json").read_text())
    RESULTS.mkdir(exist_ok=True)
    for name in a.systems:
        (RESULTS / f"graph_eval_{name}{env('EVAL_TAG', '')}.json").write_text(json.dumps(evaluate(name, qs), indent=1))


if __name__ == "__main__":
    main()
