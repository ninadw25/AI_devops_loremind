"""
scripts/claim_prototype.py
──────────────────────────
Prototype of the CLAIM extractor from docs/revised_ingestion_pipeline/00_DESIGN.md §4.

Standalone experiment — touches nothing in pipeline/. Runs the new
contextualize+extract prompt on N real threads, validates deterministically,
and writes a side-by-side comparison against the current validated triples
for the same threads so old-vs-new quality can be judged directly.

Usage (from lore-ingestion/):
    ../.venv/bin/python -m scripts.claim_prototype --org ezobooks --limit 10

Output:
    output/knowledge/prototype/{org}_claims_prototype.json   claims + digests
    output/knowledge/prototype/{org}_claims_compare.md       old vs new, per thread
    stdout: content-free metrics (schema pass, evidence-verbatim, entity resolution)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import unicodedata
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from openai import AsyncOpenAI
from pydantic import BaseModel, Field, ValidationError

from common.config import get_settings
from infra.llm import get_chat_provider
from pipeline.extraction.grouping import group_by_thread, load_person_registry
from pipeline.extraction.prompts import strip_quoted_replies

# ── Claim schema (prototype-local; graduates to common/ if the gate passes) ──

KINDS = {"Decision", "Fact", "Task", "Event", "Commitment", "Process"}
ROLES = {"decider", "assignee", "subject", "participant", "stated_by",
         "mentioned", "counterparty"}


class EntityRef(BaseModel):
    name: str
    role: str

    def ok(self) -> bool:
        return self.role in ROLES


class Claim(BaseModel):
    kind: str
    statement: str = Field(min_length=30)   # self-contained ⇒ can't be tiny
    entities: list[EntityRef] = []
    topics: list[str] = []
    attributes: dict = {}
    occurred_at: Optional[str] = None
    evidence_quote: str = Field(min_length=10)
    confidence: float = 0.8


class ThreadExtraction(BaseModel):
    digest: str = Field(min_length=20)
    claims: list[Claim] = []
    department_tags: list[str] = []
    sensitivity: str = "internal"


# ── Seed topic registry (prototype stand-in for §6; an admin would seed this) ──
# ponytail: hand-seeded from the org's observed domains; real registry comes later
SEED_TOPICS = [
    "Hiring & Recruitment", "Employee Compensation", "Employee Leave & Attendance",
    "Employee Performance & Discipline", "Employee Onboarding & Offboarding",
    "Training & Development", "Sales Leads & Follow-ups", "Sales Incentives",
    "Partner & Reseller Management", "Customer Support & Complaints",
    "Billing Machine Product", "Software Product & App Development",
    "Payments & Transactions", "Invoicing & Accounts", "Vendor Management & Procurement",
    "Banking & Finance Operations", "Compliance & Legal", "GST & Taxation",
    "Marketing & Campaigns", "Logistics & Shipping", "IT & Infrastructure",
    "Company Governance & Board", "Fundraising & Investors", "Office Administration",
]


SYSTEM_PROMPT = """You are a knowledge extraction engine for an organizational \
memory system. You read a communication thread and produce TWO things:

1. A DIGEST — a fully contextualized rewrite of what happened in the thread.
2. A list of CLAIMS — self-contained statements of organizational knowledge.

## THE DIGEST

3-8 sentences. Resolve every pronoun to a canonical participant name. Name every
person using EXACTLY the canonical names from THREAD PARTICIPANTS. Include
concrete dates, amounts, and identifiers inline. A reader with no access to the
thread must fully understand what happened. Skip pleasantries and boilerplate.

## CLAIMS

Each claim is ONE self-contained statement of durable organizational knowledge.
The statement must make complete sense alone — a reader who sees ONLY the
statement (no thread, no other claims) must understand who did what, when,
and to whom. Resolve all pronouns. Use canonical participant names. Include
dates and amounts inline.

kind — one of:
  Decision    a deliberate choice/approval/rejection/revision that changed something
  Task        a specific action someone must do
  Fact        verifiable institutional knowledge (rules, numbers, IDs, structures)
  Event       a meeting/interview/incident that occurred, with time and participants
  Commitment  a promise with specific terms (pricing, timeline, deliverable)
  Process     how something is done in this org (a repeatable procedure)

entities — every person/organization involved, with a role:
  decider | assignee | subject | participant | stated_by | mentioned | counterparty
  Use EXACTLY the canonical names from THREAD PARTICIPANTS. For someone not in
  the list, use the name from the signature/body. Never use email addresses.

topics — 1-2 entries. STRONGLY prefer linking to the CANDIDATE TOPICS list
  (use the exact candidate string). Only if nothing fits, propose "NEW: <name>".

attributes — typed slots when present in the text (discriminative keys for
  deduplication). Use null-free JSON: omit absent keys.
  amount (with currency), date (ISO), external_id (ticket/transaction/invoice id),
  counterparty (org name), subject_person, due_date, metric, url

occurred_at — ISO date the claimed thing happened (not the email date, if different).

evidence_quote — an EXACT VERBATIM substring copied from the thread (not the
  digest). Copy it character-for-character. It will be checked mechanically.

confidence — 0.0-1.0, your certainty that the claim is correct and durable.

## QUALITY RULES

- Do NOT extract: pleasantries, signatures, disclaimers, HTML artifacts,
  auto-notifications, newsletter/marketing content, calendar-share boilerplate.
- If a spreadsheet/report is pasted, extract ONE claim summarizing its insight,
  never row-level claims.
- "Noted", "ok", "approved" with no terms is NOT a Decision. A Decision needs
  what changed. A request to do something is a Task, not a Decision.
- If one email asks N people the same thing, that is ONE Task (assignee = the
  group/team), not N tasks.
- A thread with no durable organizational knowledge → "claims": [].
- Claims must be about THIS organization's work. Generic content from vendors,
  newsletters, or marketing is not organizational knowledge.

## OUTPUT — valid JSON only, no markdown fences

{
  "digest": "...",
  "claims": [
    {
      "kind": "Decision",
      "statement": "On 2025-11-01 Mukesh Sethia (Director) revised Tanvi Chaughule's salary by 7% to Rs. 20,330/month based on her prior-year performance, effective November 2025.",
      "entities": [
        {"name": "Mukesh Sethia", "role": "decider"},
        {"name": "Tanvi Chaughule", "role": "subject"}
      ],
      "topics": ["Employee Compensation"],
      "attributes": {"amount": "INR 20330/month", "date": "2025-11-01", "subject_person": "Tanvi Chaughule"},
      "occurred_at": "2025-11-01",
      "evidence_quote": "Your salary has been increased by 7% based on your performance last year. Your revised salary will be Rs. 20330/- per month w.e.f. November 2025.",
      "confidence": 0.95
    }
  ],
  "department_tags": ["hr"],
  "sensitivity": "confidential"
}
"""


# ── Prompt building (reuses the proven participant-context pattern) ──────────

def build_prompt(thread: dict, email_to_person: dict, persons_by_id: dict) -> str:
    emails = thread["emails"]

    participants: set[str] = set()
    for e in emails:
        sender = e.get("primary_actor", "").replace("email:", "").lower()
        if sender:
            participants.add(sender)
        for a in e.get("actors", []):
            addr = (a.get("email") or a.get("id", "").replace("email:", "")).lower()
            if addr:
                participants.add(addr)

    plines = []
    for addr in sorted(participants):
        pid = email_to_person.get(addr)
        if pid and pid in persons_by_id:
            p = persons_by_id[pid]
            label = "INTERNAL" if p.get("is_internal") else "EXTERNAL"
            if p.get("is_functional"):
                label += ", FUNCTIONAL MAILBOX"
            plines.append(f"  {p.get('canonical_name', addr)} ({addr}) — {label}")
        else:
            plines.append(f"  {addr} — EXTERNAL, UNKNOWN")

    blocks = []
    for i, e in enumerate(emails):
        sender = e.get("primary_actor", "").replace("email:", "").lower()
        pid = email_to_person.get(sender)
        name = persons_by_id.get(pid, {}).get("canonical_name", sender) if pid else sender
        body = strip_quoted_replies(e.get("body", "") or "")
        blocks.append(
            f"--- Email {i+1} of {len(emails)} ---\n"
            f"From: {name}\nDate: {e.get('timestamp','')[:16]}\n"
            f"Subject: {e.get('title','No subject')}\n\n{body}")

    topics = "\n".join(f"  - {t}" for t in SEED_TOPICS)
    return (
        f"THREAD PARTICIPANTS (use these exact canonical names):\n"
        + "\n".join(plines)
        + f"\n\nCANDIDATE TOPICS (link to these; propose NEW: only if none fit):\n{topics}"
        + f"\n\nTHREAD ({len(emails)} emails):\n\n"
        + "\n\n".join(blocks)
        + "\n\nProduce the digest and claims. Return valid JSON only."
    )


# ── Deterministic validation (the §9 verifiable checks) ──────────────────────

def _norm(s: str) -> str:
    # G5 evidence-verbatim normalizer. Real threads carry WhatsApp *bold*,
    # smart quotes, ₹/unicode and bullet punctuation the model reflows — so a
    # naive whitespace/case fold undercounts true verbatim quotes as misses.
    # ponytail: leniency toward FORMATTING is correct for an evidence-recall
    # check; drops punctuation entirely — ceiling is a fabricated quote that
    # coincidentally matches, negligible for the long quotes we admit.
    s = unicodedata.normalize("NFKC", s)
    s = re.sub(r"[^\w\s]", " ", s)          # strip *, `, quotes, punctuation
    return re.sub(r"\s+", " ", s).strip().lower()


def validate(ext: ThreadExtraction, thread: dict,
             participant_names: set[str]) -> dict:
    """Content-free quality metrics for one thread's extraction."""
    full_text = _norm(" ".join(
        (e.get("title", "") or "") + " " + (e.get("body", "") or "")
        for e in thread["emails"]))

    n = len(ext.claims)
    verbatim = sum(1 for c in ext.claims if _norm(c.evidence_quote) in full_text)
    kinds_ok = sum(1 for c in ext.claims if c.kind in KINDS)
    roles_ok = sum(1 for c in ext.claims
                   if c.entities and all(e.ok() for e in c.entities))
    topic_linked = sum(1 for c in ext.claims
                       if c.topics and all(not t.startswith("NEW:") for t in c.topics))
    topic_new = sum(1 for c in ext.claims
                    if any(t.startswith("NEW:") for t in c.topics))
    lowered = {p.lower() for p in participant_names}
    ent_total = sum(len(c.entities) for c in ext.claims)
    ent_resolved = sum(
        1 for c in ext.claims for e in c.entities if e.name.lower() in lowered)
    has_attrs = sum(1 for c in ext.claims if c.attributes)

    return {
        "claims": n,
        "evidence_verbatim": verbatim,
        "kinds_valid": kinds_ok,
        "roles_valid": roles_ok,
        "entities_total": ent_total,
        "entities_resolved_to_participants": ent_resolved,
        "topic_linked": topic_linked,
        "topic_new_proposals": topic_new,
        "claims_with_attributes": has_attrs,
    }


# ── LLM call ─────────────────────────────────────────────────────────────────

async def extract_one(client: AsyncOpenAI, model: str, thread: dict,
                      email_to_person: dict, persons_by_id: dict,
                      sem: asyncio.Semaphore) -> tuple[str, ThreadExtraction | None, str]:
    prompt = build_prompt(thread, email_to_person, persons_by_id)
    async with sem:
        for attempt in range(3):
            try:
                resp = await client.chat.completions.create(
                    model=model,
                    messages=[{"role": "system", "content": SYSTEM_PROMPT},
                              {"role": "user", "content": prompt}],
                    temperature=0.1,
                    max_tokens=8000,
                )
                text = resp.choices[0].message.content or ""
                text = re.sub(r"^```(json)?|```$", "", text.strip(),
                              flags=re.MULTILINE).strip()
                # deepseek-v3.2 may emit reasoning before the JSON object
                start = text.find("{")
                if start > 0:
                    text = text[start:]
                return thread["thread_id"], ThreadExtraction.model_validate_json(text), ""
            except (ValidationError, json.JSONDecodeError) as exc:
                err = f"schema: {exc}"
            except Exception as exc:  # rate limits, transient API errors
                err = f"api: {exc}"
                await asyncio.sleep(5 * (attempt + 1))
    return thread["thread_id"], None, err


# ── Thread selection: same threads the old pipeline extracted, mixed sizes ───

def pick_threads(threads: dict, old: dict, limit: int) -> list[dict]:
    candidates = [t for tid, t in threads.items()
                  if tid in old and old[tid].get("triples")
                  and 2 <= len(t["emails"]) <= 12]
    # deterministic spread: sort by id, stride across the list for size variety
    candidates.sort(key=lambda t: t["thread_id"])
    stride = max(1, len(candidates) // limit)
    return candidates[::stride][:limit]


# ── Comparison report ────────────────────────────────────────────────────────

def write_compare(path: Path, results: list[tuple[str, ThreadExtraction]],
                  old: dict) -> None:
    lines = ["# Claim prototype vs current triples\n"]
    for tid, ext in results:
        lines.append(f"\n## Thread `{tid}`\n")
        lines.append(f"**Digest (new):** {ext.digest}\n")
        lines.append("### New claims\n")
        for c in ext.claims:
            ents = ", ".join(f"{e.name}({e.role})" for e in c.entities)
            lines.append(f"- **[{c.kind}]** {c.statement}")
            lines.append(f"  - entities: {ents or '—'} · topics: {', '.join(c.topics) or '—'}"
                         f" · attrs: {json.dumps(c.attributes) if c.attributes else '—'}")
            lines.append(f"  - evidence: “{c.evidence_quote[:140]}”")
        lines.append("\n### Old triples (validated)\n")
        for tr in old.get(tid, {}).get("triples", []):
            s, o = tr.get("subject", {}), tr.get("object", {})
            lines.append(f"- ({s.get('type')}: {s.get('name') or s.get('description','')[:80]}) "
                         f"-[{tr.get('predicate')}]-> "
                         f"({o.get('type')}: {o.get('name') or o.get('description','')[:80]})")
        old_sum = old.get(tid, {}).get("thread_summary", "")
        if old_sum:
            lines.append(f"\n**Old thread_summary:** {old_sum}\n")
    path.write_text("\n".join(lines), encoding="utf-8")


# ── Main ─────────────────────────────────────────────────────────────────────

async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--org", default="ezobooks")
    ap.add_argument("--limit", type=int, default=10)
    ap.add_argument("--concurrency", type=int, default=5)
    args = ap.parse_args()

    load_dotenv()
    settings = get_settings()
    data_dir = Path("output")

    filtered = data_dir / "filtered" / f"{args.org}_gmail_filtered.jsonl"
    old_path = data_dir / "knowledge" / "triples" / f"{args.org}_triples_validated.json"
    out_dir = data_dir / "knowledge" / "prototype"
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"Loading threads from {filtered.name} …")
    threads = group_by_thread(filtered)
    old = json.loads(old_path.read_text(encoding="utf-8"))
    email_to_person, persons_by_id = load_person_registry(args.org, data_dir)
    print(f"{len(threads)} threads · {len(old)} old extractions · "
          f"{len(persons_by_id)} persons")

    picked = pick_threads(threads, old, args.limit)
    print(f"Selected {len(picked)} threads "
          f"(sizes: {[len(t['emails']) for t in picked]})")

    provider = get_chat_provider()            # settings.llm_provider (bedrock)
    client = provider.make_async_client()
    model = provider.default_model            # bedrock → deepseek.v3.2
    print(f"Provider: {provider.name} · model: {model}")
    sem = asyncio.Semaphore(args.concurrency)
    tasks = [extract_one(client, model, t,
                         email_to_person, persons_by_id, sem) for t in picked]
    results = await asyncio.gather(*tasks)

    ok: list[tuple[str, ThreadExtraction]] = []
    failures: list[tuple[str, str]] = []
    all_metrics: list[dict] = []
    thread_by_id = {t["thread_id"]: t for t in picked}

    for tid, ext, err in results:
        if ext is None:
            failures.append((tid, err))
            continue
        t = thread_by_id[tid]
        pnames = set()
        for e in t["emails"]:
            for a in e.get("actors", []):
                addr = (a.get("email") or a.get("id", "").replace("email:", "")).lower()
                pid = email_to_person.get(addr)
                if pid and pid in persons_by_id:
                    pnames.add(persons_by_id[pid].get("canonical_name", ""))
                if a.get("display_name"):
                    pnames.add(a["display_name"])
        all_metrics.append(validate(ext, t, pnames))
        ok.append((tid, ext))

    # ── Aggregate content-free metrics ──
    def agg(key: str) -> int:
        return sum(m[key] for m in all_metrics)

    total = agg("claims")
    print("\n════════ PROTOTYPE METRICS (content-free) ════════")
    print(f"threads extracted:        {len(ok)}/{len(picked)}"
          + (f"   FAILED: {[f[0] for f in failures]}" if failures else ""))
    for tid, err in failures:
        print(f"  fail {tid}: {err[:200]}")
    if total:
        print(f"claims total:             {total} "
              f"(per thread: {total/len(ok):.1f})")
        print(f"evidence verbatim:        {agg('evidence_verbatim')}/{total} "
              f"({100*agg('evidence_verbatim')/total:.0f}%)")
        print(f"valid kinds:              {agg('kinds_valid')}/{total}")
        print(f"claims w/ valid roles:    {agg('roles_valid')}/{total}")
        er, et = agg("entities_resolved_to_participants"), agg("entities_total")
        print(f"entities resolved:        {er}/{et} ({100*er/max(et,1):.0f}%)")
        print(f"topics linked/NEW:        {agg('topic_linked')}/{agg('topic_new_proposals')}")
        print(f"claims with attributes:   {agg('claims_with_attributes')}/{total}")

    out = {tid: json.loads(ext.model_dump_json()) for tid, ext in ok}
    (out_dir / f"{args.org}_claims_prototype.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    write_compare(out_dir / f"{args.org}_claims_compare.md", ok, old)
    print(f"\nWrote {out_dir / f'{args.org}_claims_prototype.json'}")
    print(f"Wrote {out_dir / f'{args.org}_claims_compare.md'}")


if __name__ == "__main__":
    asyncio.run(main())
