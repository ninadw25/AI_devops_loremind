"""Baseline corpus: embed the RAW email threads (what a normal RAG pipeline would index).

One vector per thread = subject + first 3 messages, truncated. Resumable via cache files.
    python -m benchmarks.rag_eval.build_raw_index --events <filtered_gmail.jsonl>
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

import numpy as np

from .common import EMBED_MODEL, nvidia

DATA = Path(__file__).parent / "data"
MAX_CHARS, BATCH = 2500, 32


def embed_batch(client, batch: list[str]) -> list[np.ndarray]:
    """One call; if the provider rejects the batch (e.g. an email with an inline image makes it
    demand VLM serving) retry per text, and for a text that still fails embed its subject line only."""
    def call(xs):
        r = client.embeddings.create(input=xs, model=EMBED_MODEL, encoding_format="float",
                                     extra_body={"input_type": "passage", "truncate": "END"})
        return [np.array(d.embedding, dtype=np.float32) for d in r.data]
    try:
        return call(batch)
    except Exception:
        out = []
        for t in batch:
            try:
                out.append(call([t])[0])
            except Exception:
                out.append(call([t.split("\n", 1)[0][:200] or "empty"])[0])
        return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--events", required=True)
    a = ap.parse_args()
    threads: dict[str, list[str]] = defaultdict(list)
    with open(a.events, encoding="utf-8") as f:
        for line in f:
            ev = json.loads(line)
            tid = (ev.get("raw") or {}).get("thread_id")
            if tid and len(threads[tid]) < 3:
                threads[tid].append(f"{ev.get('title','')}\n{(ev.get('body') or '')[:1200]}")
    ids = sorted(threads)
    texts = ["\n---\n".join(threads[t])[:MAX_CHARS] for t in ids]
    (DATA / "raw_thread_ids.json").write_text(json.dumps(ids))
    (DATA / "raw_thread_texts.json").write_text(json.dumps(texts))

    client, out = nvidia(), []
    ckpt = DATA / "raw_vecs.ckpt.npy"
    if ckpt.exists():
        out = list(np.load(ckpt))
    for i in range(len(out), len(texts), BATCH):
        out.extend(embed_batch(client, texts[i:i + BATCH]))
        if (i // BATCH) % 50 == 0:
            np.save(ckpt, np.array(out)); print(len(out), "/", len(texts), flush=True)
    arr = np.array(out); arr /= np.linalg.norm(arr, axis=1, keepdims=True)
    np.save(DATA / "raw_vecs.npy", arr)
    print("done", arr.shape)


if __name__ == "__main__":
    main()
