"""Ontology-derived question set. Each question is a template over one ontology pattern;
the ground truth is the Cypher result for that pattern (stored with the question), so the
evidence for every label is a traversal of the graph, not an LLM opinion.

  F1 person_tasks      Task -[ASSIGNED_TO]-> Person                      "What tasks were assigned to X?"
  F2 decision_why      Decision -[BECAUSE]-> Fact                        "Why was it decided to <decision>?"
  F3 person_topic      Person -[DECIDED]-> Decision -[REGARDING]-> Topic "What did X decide about T?"
"""
from __future__ import annotations

import json
import random
from pathlib import Path

from .common import neo4j_driver

OUT = Path(__file__).parent / "data" / "graph_questions.json"
N = 20
GATE = "n.scope_ids IS NOT NULL"   # only nodes the engine can serve; keeps the comparison about retrieval

F1 = f"""
MATCH (p:Person)<-[:ASSIGNED_TO]-(n:Task) WHERE {GATE} AND p.is_internal = true AND coalesce(p.is_functional,false) = false
  AND p.name CONTAINS ' ' AND size(p.name) < 40
WITH p, collect(n) AS ns WHERE size(ns) >= 3 AND size(ns) <= 20
RETURN p.name AS name, [x IN ns | {{thread: x.source_thread_id, desc: x.description}}] AS gt"""

F2 = f"""
MATCH (n:Decision)-[:BECAUSE]->(f:Fact) WHERE {GATE} AND f.source_thread_id IS NOT NULL AND size(n.description) > 30 AND size(n.description) < 160
WITH n, collect(f) AS fs WHERE size(fs) >= 1 AND size(fs) <= 4
RETURN n.description AS name, [x IN fs | {{thread: x.source_thread_id, desc: x.description}}] AS gt"""

F3 = f"""
MATCH (p:Person)-[:DECIDED]->(n:Decision)-[:REGARDING]->(t:Topic) WHERE {GATE} AND p.is_internal = true
  AND coalesce(p.is_functional,false) = false AND p.name CONTAINS ' ' AND size(p.name) < 40
WITH p, t, collect(DISTINCT n) AS ns WHERE size(ns) >= 3 AND size(ns) <= 15
RETURN p.name AS name, t.name AS topic, [x IN ns | {{thread: x.source_thread_id, desc: x.description}}] AS gt"""

TEMPLATES = {
    "person_tasks": (F1, lambda r: f"What tasks were assigned to {r['name']}?"),
    "decision_why": (F2, lambda r: f"Why was it decided to: {r['name']}?"),
    "person_topic": (F3, lambda r: f"What did {r['name']} decide regarding {r['topic']}?"),
}


def main() -> None:
    rng, items = random.Random(7), []
    with neo4j_driver().session() as s:
        dup_names = {r["n"] for r in s.run("MATCH (p:Person) WITH p.name AS n, count(*) AS c WHERE c > 1 RETURN n")}
        for fam, (cypher, verbalise) in TEMPLATES.items():
            rows = [dict(r) for r in s.run(cypher)]
            # ambiguity filters done here: a named person must be unique, and answers must span threads
            rows = [r for r in rows if (fam == "decision_why" or r["name"] not in dup_names)
                    and len({g["thread"] for g in r["gt"]}) >= (1 if fam == "decision_why" else 2 if fam == "person_tasks" else 2)]
            print(fam, "candidates:", len(rows))
            for r in rng.sample(rows, min(N, len(rows))):
                gt = [{"thread": g["thread"], "desc": (g["desc"] or "").strip()} for g in r["gt"]]
                items.append({"family": fam, "question": verbalise(r), "gt_nodes": gt,
                              "gt_threads": sorted({g["thread"] for g in gt}), "cypher": cypher.strip()})
    OUT.write_text(json.dumps(items, indent=1))
    print(f"wrote {len(items)} questions -> {OUT}")


if __name__ == "__main__":
    main()
