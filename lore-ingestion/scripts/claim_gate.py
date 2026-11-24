"""
scripts/claim_gate.py
─────────────────────
Claim-extraction validation gate on the NEW substrate (build step 4 of
docs/revised_ingestion_pipeline/00_DESIGN.md §11; the risk gate before any
full re-extraction).

The prototype (scripts/claim_prototype.py) proved the prompt against the OLD
file pipeline and the OLD messy person registry. This re-runs the SAME brain
(its system prompt, schema, deterministic validators — imported, not copied)
but sources everything from the layers we just built:

    conversations  ← L0 events (signal only), grouped by conversation_id
    participants   ← L1 entity_person / entity_person_alias (the CLEAN registry)

It writes nothing to the DB and calls no old files. Output is content-free
quality metrics (evidence-verbatim %, entity-resolution %, schema validity) +
a readable sample, so we can decide go / no-go on productionizing extraction.

Usage (from lore-ingestion/):
    ../.venv/bin/python -m scripts.claim_gate --org ezobooks --limit 40
"""

from __future__ import annotations

import argparse
import asyncio
import json
from collections import defaultdict
from pathlib import Path

import sqlalchemy as sa
from dotenv import load_dotenv

from builders.l0_ledger import SCHEMA, Event, LedgerStore, _resolve_dsn
from infra.llm import get_chat_provider
from scripts.claim_prototype import ThreadExtraction, build_prompt, extract_one, validate

# Conversation sizes to sample (messages). Includes 1 — single-message
# conversations are 42k of ezobooks' signal and carry real knowledge (a salary
# email is one message), so the extractor must handle them, not just threads.
SIZE_BUCKETS = [(1, 1), (2, 3), (4, 6), (7, 12)]


def load_registry(store: LedgerStore, org: str) -> tuple[dict, dict]:
    """L1 → the two lookups build_prompt wants, shaped like the old registry.

    email_to_person : {email → person_id}
    persons_by_id   : {person_id → {canonical_name, is_internal, is_functional}}
    """
    email_to_person, persons_by_id = {}, {}
    with store._session() as s:
        for email, pid in s.execute(sa.text(
                f"SELECT email, person_id FROM {SCHEMA}.entity_person_alias WHERE org=:o"),
                {"o": org}):
            email_to_person[email.lower()] = pid
        for pid, name, internal, stype in s.execute(sa.text(
                f"SELECT person_id, display_name, is_internal, sender_type "
                f"FROM {SCHEMA}.entity_person WHERE org=:o"), {"o": org}):
            persons_by_id[pid] = {
                "canonical_name": name, "is_internal": internal,
                "is_functional": stype == "functional_mailbox",
            }
    return email_to_person, persons_by_id


def pick_conversations(store: LedgerStore, org: str, limit: int) -> list[dict]:
    """Sample signal conversations spread across size buckets, deterministically.

    Returns thread dicts shaped exactly like the prototype expects:
    {thread_id, emails:[{primary_actor, actors, body, title, timestamp}]}.
    """
    per_bucket = max(1, limit // len(SIZE_BUCKETS))
    chosen: list[str] = []
    with store._session() as s:
        for lo, hi in SIZE_BUCKETS:
            ids = s.execute(sa.text(f"""
                WITH conv AS (
                  SELECT conversation_id, count(*) n FROM {SCHEMA}.events
                  WHERE org=:o AND noise_class='signal' AND conversation_id IS NOT NULL
                  GROUP BY 1)
                SELECT conversation_id FROM conv
                WHERE n BETWEEN :lo AND :hi
                ORDER BY conversation_id            -- deterministic
                LIMIT :k"""),
                {"o": org, "lo": lo, "hi": hi, "k": per_bucket}).scalars().all()
            chosen.extend(ids)

        # Pull the events for the chosen conversations, oldest-first per conv.
        rows = s.execute(
            sa.select(Event.conversation_id, Event.primary_actor, Event.actors,
                      Event.body, Event.title, Event.occurred_at)
            .where(Event.org == org, Event.noise_class == "signal",
                   Event.conversation_id.in_(chosen))
            .order_by(Event.conversation_id, Event.occurred_at.asc().nullslast())
        ).all()

    by_conv: dict[str, list] = defaultdict(list)
    for r in rows:
        by_conv[r.conversation_id].append({
            "primary_actor": r.primary_actor or "",
            "actors": r.actors or [],
            "body": r.body or "",
            "title": r.title or "",
            "timestamp": r.occurred_at.isoformat() if r.occurred_at else "",
        })
    return [{"thread_id": cid, "emails": emails} for cid, emails in by_conv.items()]


def participant_names(thread: dict, email_to_person: dict, persons_by_id: dict) -> set[str]:
    names: set[str] = set()
    for e in thread["emails"]:
        for a in e.get("actors", []):
            addr = (a.get("email") or a.get("id", "").replace("email:", "")).lower()
            pid = email_to_person.get(addr)
            if pid and pid in persons_by_id:
                names.add(persons_by_id[pid]["canonical_name"])
            if a.get("display_name"):
                names.add(a["display_name"])
    return {n for n in names if n}


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--org", default="ezobooks")
    ap.add_argument("--limit", type=int, default=40)
    ap.add_argument("--concurrency", type=int, default=5)
    args = ap.parse_args()

    load_dotenv()
    store = LedgerStore(_resolve_dsn())
    if not store.connect():
        raise RuntimeError("claim_gate: could not connect to Postgres.")

    email_to_person, persons_by_id = load_registry(store, args.org)
    convs = pick_conversations(store, args.org, args.limit)
    sizes = sorted(len(c["emails"]) for c in convs)
    print(f"registry: {len(persons_by_id)} persons, {len(email_to_person)} aliases")
    print(f"selected {len(convs)} conversations (sizes: {sizes})")

    provider = get_chat_provider()
    client = provider.make_async_client()
    model = provider.default_model
    print(f"provider: {provider.name} · model: {model}\n")

    sem = asyncio.Semaphore(args.concurrency)
    results = await asyncio.gather(*[
        extract_one(client, model, t, email_to_person, persons_by_id, sem)
        for t in convs])

    by_id = {t["thread_id"]: t for t in convs}
    ok, failures, metrics = [], [], []
    for tid, ext, err in results:
        if ext is None:
            failures.append((tid, err))
            continue
        t = by_id[tid]
        metrics.append(validate(ext, t, participant_names(t, email_to_person, persons_by_id)))
        ok.append((tid, ext))

    def agg(k: str) -> int:
        return sum(m[k] for m in metrics)

    total = agg("claims")
    print("════════ CLAIM GATE — content-free metrics ════════")
    print(f"conversations extracted:  {len(ok)}/{len(convs)}"
          + (f"   FAILED {len(failures)}" if failures else ""))
    for tid, err in failures[:5]:
        print(f"  fail {tid}: {err[:160]}")
    if total:
        ev, et = agg("evidence_verbatim"), agg("entities_total")
        er = agg("entities_resolved_to_participants")
        print(f"claims total:             {total}  (per conv: {total/len(ok):.1f})")
        print(f"evidence verbatim:        {ev}/{total} ({100*ev/total:.0f}%)   ← gate: >=90%")
        print(f"valid kinds:              {agg('kinds_valid')}/{total}")
        print(f"claims w/ valid roles:    {agg('roles_valid')}/{total}")
        print(f"entities resolved:        {er}/{et} ({100*er/max(et,1):.0f}%)   ← gate: clean reg should lift this")
        print(f"topics linked/NEW:        {agg('topic_linked')}/{agg('topic_new_proposals')}")
        print(f"claims with attributes:   {agg('claims_with_attributes')}/{total}")

    out_dir = Path("output/knowledge/prototype")
    out_dir.mkdir(parents=True, exist_ok=True)
    out = {tid: json.loads(ext.model_dump_json()) for tid, ext in ok}
    (out_dir / f"{args.org}_claims_gate.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    # A readable sample: the first few conversations' digest + claims.
    sample = ["# Claim gate — sample output (new substrate)\n"]
    for tid, ext in ok[:8]:
        sample.append(f"\n## `{tid}`  ({len(by_id[tid]['emails'])} msgs)\n")
        sample.append(f"**Digest:** {ext.digest}\n")
        for c in ext.claims:
            ents = ", ".join(f"{e.name}({e.role})" for e in c.entities)
            sample.append(f"- **[{c.kind}]** {c.statement}")
            sample.append(f"  - entities: {ents or '—'} · topics: {', '.join(c.topics) or '—'}")
            sample.append(f"  - evidence: “{c.evidence_quote[:160]}”")
    (out_dir / f"{args.org}_claims_gate_sample.md").write_text(
        "\n".join(sample), encoding="utf-8")
    print(f"\nwrote {out_dir}/{args.org}_claims_gate.json")
    print(f"wrote {out_dir}/{args.org}_claims_gate_sample.md")
    store.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
