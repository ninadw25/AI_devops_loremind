"""2x2 ablation of the two query-time components: cross-encoder rerank x LLM query analysis.

    python -m benchmarks.rag_eval.ablation          # prints the tables as markdown and writes results/ablation.json

Cells (same questions, same graph, same embeddings):
  rerank on,  LLM analysis      results/graph_eval_lore.json, retrieval_eval.json                (7 Oct)
  rerank off, LLM analysis      *_rerank_off.json            RERANK_PROVIDER=none
  rerank on,  keyword analysis  *_keyword_analysis.json      ANALYSIS_TIMEOUT_MS=1 (always the keyword fallback)
  rerank off, keyword analysis  prev/*.json                  6 Oct run: reranker not installed, 800 ms timeout
The last cell is approximate: with an 800 ms timeout the LLM analysis almost always fell back to keywords.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

R = Path(__file__).parent / "results"
CELLS = {
    "on_llm": ("graph_eval_lore.json", "retrieval_eval.json"),
    "off_llm": ("graph_eval_lore_rerank_off.json", "retrieval_eval_rerank_off.json"),
    "on_kw": ("graph_eval_lore_keyword_analysis.json", "retrieval_eval_keyword_analysis.json"),
    "off_kw": ("prev/graph_eval_lore.json", "prev/retrieval_eval.json"),
}
FAMILIES = ("person_tasks", "decision_why", "person_topic")


def _graph(f: str) -> list[dict]:
    d = json.loads((R / f).read_text())
    return d["runs"] if isinstance(d, dict) and "runs" in d else d


def _lookup(f: str) -> list[float]:
    runs = json.loads((R / f).read_text())["runs"]["lore"]
    return [1.0 if r["rank"] and r["rank"] <= 10 else 0.0 for r in runs]


def boot(a, b, n: int = 5000, seed: int = 0) -> tuple[float, float, float]:
    """Paired bootstrap of mean(a - b) over the same questions."""
    d = np.asarray(a, float) - np.asarray(b, float)
    rng = np.random.default_rng(seed)
    m = rng.choice(d, (n, len(d))).mean(axis=1)
    return float(d.mean()), float(np.percentile(m, 2.5)), float(np.percentile(m, 97.5))


def main() -> None:
    have = {k: v for k, v in CELLS.items() if all((R / f).exists() for f in v)}
    out: dict = {"cells": {}, "effects": {}}
    per_q: dict = {}
    for k, (gf, lf) in have.items():
        g, lk = _graph(gf), _lookup(lf)
        per_q[k] = {"all": [x["node_recall"] for x in g], "lookup": lk,
                    **{fam: [x["node_recall"] for x in g if x["family"] == fam] for fam in FAMILIES}}
        out["cells"][k] = {m: round(float(np.mean(v)), 3) for m, v in per_q[k].items()}
    for name, (x, y) in {"rerank (LLM analysis)": ("on_llm", "off_llm"),
                         "rerank (keyword analysis)": ("on_kw", "off_kw"),
                         "LLM analysis (rerank on)": ("on_llm", "on_kw"),
                         "LLM analysis (rerank off)": ("off_llm", "off_kw")}.items():
        if x in per_q and y in per_q:
            out["effects"][name] = {m: [round(v, 3) for v in boot(per_q[x][m], per_q[y][m])]
                                    for m in ("all", "lookup", *FAMILIES)}
    (R / "ablation.json").write_text(json.dumps(out, indent=1))
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
