"""
runners/backfill_gmail_scopes.py
────────────────────────────────
Give pre-ACL Gmail graphs the `scope_ids` the engine's fail-closed gate needs.

Graphs built before the scope model carry only `source_thread_id`, so every
node is invisible to lore-engine (even to super_admin, because the unstructured
source filter requires a non-Jira scope_id). This rebuilds the exact value a
fresh build writes (infra/gmail/normalizer.py): one `person:{email}` per actor,
unioned over every message in the thread, from the filtered Gmail JSONL.

Only fills nodes whose scope_ids is missing; never overwrites.

Usage (from lore-ingestion/):
    ../.venv/bin/python -m runners.backfill_gmail_scopes --org ezobooks \
        --events evaluation_dataset/ezobooks_gmail_filtered.jsonl
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict

from neo4j import GraphDatabase

from common.config import neo4j_credentials_for
from common.scopes import person_email


def thread_scopes(events_path: str) -> dict[str, list[str]]:
    scopes: dict[str, set[str]] = defaultdict(set)
    with open(events_path, encoding="utf-8") as f:
        for line in f:
            ev = json.loads(line)
            tid = (ev.get("raw") or {}).get("thread_id")
            if tid:
                scopes[tid].update(person_email(a["email"]) for a in ev.get("actors", []) if a.get("email"))
    return {t: sorted(s) for t, s in scopes.items() if s}


def backfill(org: str, events_path: str) -> None:
    by_thread = thread_scopes(events_path)
    uri, user, pw = neo4j_credentials_for(org)
    with GraphDatabase.driver(uri, auth=(user, pw)) as driver:
        rows = [{"t": t, "s": s} for t, s in by_thread.items()]
        updated = 0
        for i in range(0, len(rows), 1000):
            summary = driver.execute_query(
                "UNWIND $rows AS r MATCH (n:KnowledgeNode {source_thread_id: r.t}) "
                "WHERE n.scope_ids IS NULL SET n.scope_ids = r.s",
                rows=rows[i:i + 1000],
            ).summary
            updated += summary.counters.properties_set
        left = driver.execute_query(
            "MATCH (n:KnowledgeNode) WHERE n.scope_ids IS NULL RETURN count(n) AS c"
        ).records[0]["c"]
    print(f"threads with participants: {len(by_thread)} · properties set: {updated} · still unscoped (fail closed): {left}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--org", required=True)
    ap.add_argument("--events", required=True, help="filtered Gmail JSONL (pipeline.filter output)")
    a = ap.parse_args()
    backfill(a.org, a.events)


if __name__ == "__main__":
    main()
