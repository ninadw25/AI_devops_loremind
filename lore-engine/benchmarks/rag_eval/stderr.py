"""Bootstrap standard error (std of 5,000 resampled means, seed 0) for every number in the paper's tables.

    python -m benchmarks.rag_eval.stderr        # prints "mean ± se" per system/metric, writes results/stderr.json

Each configuration was run once; this is uncertainty over questions, not over repeated runs.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from .ablation import CELLS, FAMILIES, R


def se(xs, n: int = 5000, seed: int = 0) -> list[float]:
    x = np.asarray(xs, float)
    m = np.random.default_rng(seed).choice(x, (n, len(x))).mean(axis=1)
    return [round(float(x.mean()), 3), round(float(m.std(ddof=1)), 3)]


def graph(f: str, metric: str) -> dict:
    rows = json.loads((R / f).read_text())
    rows = rows["runs"] if isinstance(rows, dict) else rows
    out = {"all": se([r[metric] for r in rows])}
    for fam in FAMILIES:
        out[fam] = se([r[metric] for r in rows if r["family"] == fam])
    return out


def lookup(f: str, system: str) -> dict:
    ranks = [r["rank"] for r in json.loads((R / f).read_text())["runs"][system]]
    return {"hit@1": se([1.0 if k == 1 else 0.0 for k in ranks]),
            "hit@10": se([1.0 if k and k <= 10 else 0.0 for k in ranks]),
            "mrr@10": se([1.0 / k if k and k <= 10 else 0.0 for k in ranks])}


def main() -> None:
    out = {
        "test_a": {"raw_rag_thread": graph("graph_eval_raw_rag.json", "thread_recall"),
                   "node_rag_thread": graph("graph_eval_node_rag.json", "thread_recall"),
                   "lore_thread": graph("graph_eval_lore.json", "thread_recall"),
                   "node_rag_node": graph("graph_eval_node_rag.json", "node_recall"),
                   "lore_node": graph("graph_eval_lore.json", "node_recall")},
        "test_b": {"vector_rag": lookup("retrieval_eval.json", "baseline"),
                   "lore": lookup("retrieval_eval.json", "lore")},
        "ablation": {k: {"test_a": graph(gf, "node_recall"), "test_b": lookup(lf, "lore")}
                     for k, (gf, lf) in CELLS.items()},
    }
    (R / "stderr.json").write_text(json.dumps(out, indent=1))
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
