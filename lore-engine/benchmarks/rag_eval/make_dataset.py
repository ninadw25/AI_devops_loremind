"""Build the retrieval eval set: one LLM-written question per sampled email thread.

Ground truth = the thread the question was written from. Questions are written
from the RAW email text (not from graph nodes), so the graph's extraction quality
is part of what is being tested rather than baked into the labels.

    python -m benchmarks.rag_eval.make_dataset --events <filtered_gmail.jsonl> --n 60
"""
from __future__ import annotations

import argparse
import json
import random
import re
from collections import defaultdict
from pathlib import Path

from .common import LLM_MODEL, neo4j_driver, nvidia

OUT = Path(__file__).parent / "data" / "questions.json"
PROMPT = """Below is an internal company email thread. Write ONE specific question a colleague \
might ask whose answer is found in this thread. Rules: do not mention the word "thread" or "email"; \
do not copy long phrases verbatim; do not include IDs; the question must stand alone without the thread.
Reply with only the question.

THREAD:
{text}"""


def load_threads(events_path: str, wanted: set[str]) -> dict[str, str]:
    texts: dict[str, list[str]] = defaultdict(list)
    with open(events_path, encoding="utf-8") as f:
        for line in f:
            ev = json.loads(line)
            tid = (ev.get("raw") or {}).get("thread_id")
            if tid in wanted and len(texts[tid]) < 4:
                texts[tid].append(f"Subject: {ev.get('title','')}\n{ev.get('body','')[:1500]}")
    return {t: "\n---\n".join(v) for t, v in texts.items()}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--events", required=True)
    ap.add_argument("--n", type=int, default=60)
    a = ap.parse_args()

    with neo4j_driver().session() as s:
        eligible = [r["t"] for r in s.run(
            "MATCH (n:KnowledgeNode) WHERE n.scope_ids IS NOT NULL "
            "WITH n.source_thread_id AS t, count(*) AS c WHERE c >= 3 RETURN t")]
    texts = {t: x for t, x in load_threads(a.events, set(eligible)).items() if len(x) >= 500}
    rng = random.Random(42)
    picked = rng.sample(sorted(texts), min(a.n, len(texts)))

    client, items = nvidia(), []
    for t in picked:
        q = client.chat.completions.create(
            model=LLM_MODEL, temperature=0.3, max_tokens=1500,
            messages=[{"role": "user", "content": PROMPT.format(text=texts[t][:3500])}],
        ).choices[0].message.content or ""
        q = re.sub(r"<think>.*?</think>", "", q, flags=re.S).strip().strip('"')
        if 15 < len(q) < 300:
            items.append({"question": q, "thread_id": t})
            print(len(items), q[:110], flush=True)
    OUT.write_text(json.dumps(items, indent=1))
    print(f"wrote {len(items)} questions -> {OUT}")


if __name__ == "__main__":
    main()
