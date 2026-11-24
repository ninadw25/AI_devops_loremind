"""Build results/summary.md from the saved eval runs + live graph statistics (no hand-typed numbers)."""
from __future__ import annotations

import json
import random
import statistics as st
from pathlib import Path

from .common import neo4j_driver

R = Path(__file__).parent / "results"
SYS = ("raw_rag", "node_rag", "lore")
FAM = ("person_tasks", "decision_why", "person_topic")


def load(s): return json.loads((R / f"graph_eval_{s}.json").read_text())


def ci(a: list[float], b: list[float], n=5000) -> tuple[float, float, float]:
    """Paired bootstrap of mean(a-b); a,b aligned per question."""
    d, rng = [x - y for x, y in zip(a, b)], random.Random(0)
    m = sorted(st.mean(rng.choices(d, k=len(d))) for _ in range(n))
    return st.mean(d), m[int(.025 * n)], m[int(.975 * n)]


def main() -> None:
    runs, out = {s: load(s) for s in SYS}, []
    out += ["## Retrieval quality (top-10), by ontology pattern", "",
            "| System | " + " | ".join(FAM) + " | ALL | median ms |", "|---|" + "---|" * 5]
    for metric, label in (("thread_recall", "thread recall@10"), ("node_recall", "node recall@10")):
        for s in SYS:
            if metric == "node_recall" and s == "raw_rag":
                continue
            cells = []
            for f in (*FAM, None):
                v = [a[metric] for a in runs[s] if f is None or a["family"] == f]
                cells.append(f"{st.mean(v):.2f}")
            out.append(f"| {s} — {label} | " + " | ".join(cells) + f" | {st.median(a['ms'] for a in runs[s]):.0f} |")
    out += ["", "## Paired bootstrap (95% CI) of Lore minus baseline, node recall@10", "", "| Comparison | scope | mean diff | 95% CI |", "|---|---|---|---|"]
    for f in (*FAM, None):
        idx = [i for i, a in enumerate(runs["lore"]) if f is None or a["family"] == f]
        m, lo, hi = ci([runs["lore"][i]["node_recall"] for i in idx], [runs["node_rag"][i]["node_recall"] for i in idx])
        out.append(f"| lore − node_rag | {f or 'ALL'} (n={len(idx)}) | {m:+.2f} | [{lo:+.2f}, {hi:+.2f}] |")
    out += ["", "## How the mail data is connected in Neo4j (live counts)", ""]
    with neo4j_driver().session() as s:
        q = lambda c: s.run(c).single()[0]
        facts = {
            "email threads that produced knowledge": q("MATCH (n:KnowledgeNode) RETURN count(DISTINCT n.source_thread_id)"),
            "knowledge nodes (Decision/Task/Fact/Event)": q("MATCH (n:KnowledgeNode) RETURN count(n)"),
            "Person nodes (shared across threads)": q("MATCH (p:Person) RETURN count(p)"),
            "Topic nodes (shared across threads)": q("MATCH (t:Topic) RETURN count(t)"),
            "typed relationships": q("MATCH ()-[r]->() RETURN count(r)"),
            "Tasks with an ASSIGNED_TO person": q("MATCH (t:Task)-[:ASSIGNED_TO]->(:Person) RETURN count(DISTINCT t)"),
            "Decisions with a BECAUSE fact": q("MATCH (d:Decision)-[:BECAUSE]->(:Fact) RETURN count(DISTINCT d)"),
            "median threads touched per internal person": q("MATCH (p:Person {is_internal:true})<-[:ASSIGNED_TO|STATED_BY]-(n:KnowledgeNode) WITH p, count(DISTINCT n.source_thread_id) AS c RETURN percentileCont(c,0.5)"),
            "persons linked to ≥10 distinct threads": q("MATCH (p:Person)<-[:ASSIGNED_TO|STATED_BY]-(n:KnowledgeNode) WITH p, count(DISTINCT n.source_thread_id) AS c WHERE c>=10 RETURN count(p)"),
        }
    out += ["| Fact | Value |", "|---|---|"] + [f"| {k} | {v:,.0f} |" for k, v in facts.items()]
    (R / "summary.md").write_text("\n".join(out) + "\n")
    print("\n".join(out))


if __name__ == "__main__":
    main()
