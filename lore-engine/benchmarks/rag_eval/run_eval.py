"""Compare Lore's graph-hybrid retrieval against a plain LangChain + FAISS vector RAG.

Both systems use the SAME embeddings (NVIDIA nemotron-3-embed-1b, already stored on
the graph nodes) over the SAME 27k knowledge nodes, so the only variable is the
retrieval method. Retrieval is scored at thread level: a hit means the source
thread the question was written from appears in the top-k distinct threads.

    python -m benchmarks.rag_eval.run_eval            # needs engine on :8000
"""
from __future__ import annotations

import json
import statistics as st
import time
from pathlib import Path

import jwt
import numpy as np
import requests
from langchain_community.vectorstores import FAISS
from langchain_core.embeddings import Embeddings

from .common import EMBED_MODEL, env, neo4j_driver, nvidia

DATA = Path(__file__).parent / "data"
RESULTS = Path(__file__).parent / "results"
K = 10


class NvidiaQueryEmbeddings(Embeddings):
    """LangChain wrapper; input_type=query matches how the stored vectors were built."""

    def __init__(self) -> None:
        self.client = nvidia()

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self.embed_query(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        r = self.client.embeddings.create(
            input=[text], model=EMBED_MODEL, encoding_format="float",
            extra_body={"input_type": "query", "truncate": "NONE"})
        v = np.array(r.data[0].embedding, dtype=np.float32)
        return (v / np.linalg.norm(v)).tolist()


def build_faiss() -> FAISS:
    with neo4j_driver().session() as s:
        rows = list(s.run(
            "MATCH (n:KnowledgeNode) WHERE n.embedding IS NOT NULL AND n.scope_ids IS NOT NULL "
            "RETURN n.description AS d, n.source_thread_id AS t, n.embedding AS e"))
    vecs = np.array([r["e"] for r in rows], dtype=np.float32)
    vecs /= np.linalg.norm(vecs, axis=1, keepdims=True)
    pairs = [(r["d"] or "", v.tolist()) for r, v in zip(rows, vecs)]
    return FAISS.from_embeddings(pairs, NvidiaQueryEmbeddings(),
                                 metadatas=[{"thread_id": r["t"]} for r in rows],
                                 normalize_L2=False)


def baseline_threads(store: FAISS, q: str) -> list[str]:
    docs = store.as_retriever(search_kwargs={"k": 50}).invoke(q)   # top-50 nodes → distinct threads
    return list(dict.fromkeys(d.metadata["thread_id"] for d in docs))[:K]


def lore_threads(q: str, token: str, secret: str) -> list[str]:
    r = requests.post(env("ENGINE_URL", "http://localhost:8000") + "/api/query", timeout=180,
                      headers={"X-Internal-Auth": secret, "Authorization": f"Bearer {token}"},
                      json={"query": q, "k": K, "super_admin": True})
    r.raise_for_status()
    return list(dict.fromkeys(s["thread_id"] for s in r.json()["sources"]))[:K]


def score(ranks: list[int | None]) -> dict:
    n = len(ranks)
    hit = lambda k: sum(1 for r in ranks if r and r <= k) / n
    return {"n": n, "hit@1": hit(1), "hit@5": hit(5), "hit@10": hit(10),
            "mrr@10": sum(1 / r for r in ranks if r) / n}


def main() -> None:
    qs = json.loads((DATA / "questions.json").read_text())
    secret = env("INTERNAL_AUTH_SECRET")
    token = jwt.encode({"sub": "eval", "org": "ezobooks", "exp": int(time.time()) + 7200}, "x" * 32, algorithm="HS256")
    store = build_faiss()
    out = {"baseline": [], "lore": []}
    for i, item in enumerate(qs, 1):
        for name, fn in (("baseline", lambda q: baseline_threads(store, q)),
                         ("lore", lambda q: lore_threads(q, token, secret))):
            t0 = time.time()
            try:
                got = fn(item["question"])
            except Exception as e:                       # a failed call counts as a miss, kept for analysis
                got = []; print("ERR", name, e)
            ms = (time.time() - t0) * 1000
            rank = got.index(item["thread_id"]) + 1 if item["thread_id"] in got else None
            out[name].append({**item, "rank": rank, "ms": ms, "returned": got})
        print(i, "baseline", out["baseline"][-1]["rank"], "lore", out["lore"][-1]["rank"], flush=True)

    summary = {}
    for name, rows in out.items():
        summary[name] = {**score([r["rank"] for r in rows]),
                         "latency_ms_median": st.median(r["ms"] for r in rows),
                         "latency_ms_p95": float(np.percentile([r["ms"] for r in rows], 95))}
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / f"retrieval_eval{env('EVAL_TAG', '')}.json").write_text(json.dumps({"summary": summary, "runs": out}, indent=1))
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main()
