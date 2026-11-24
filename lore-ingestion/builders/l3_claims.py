"""
builders/l3_claims.py
─────────────────────
L3 claims — the knowledge layer (build step 7 of
docs/revised_ingestion_pipeline/00_DESIGN.md §4; gate passed in
docs/revised_ingestion_pipeline/CLAIM_GATE_RESULT.md).

Turns each signal conversation (L0 events) into self-contained CLAIMS —
statements of durable organizational knowledge, each with entities resolved to
the L1 registry, topics linked closed-world, verbatim evidence tied to the
exact source message, and scope_ids inherited fail-closed from the source
events. Plus one ThreadSummary DIGEST per conversation (the mid-level
retrieval unit, design §3).

This module owns the whole layer: the tables, the extraction "brain" (prompt +
schema — graduated here from the prototype so nothing production imports from
scripts/), the deterministic resolvers/validators, and the DB writes. The
async orchestration (batching, checkpoint/resume over ~60k conversations) is
`runners/extract_claims.py`.

Re-runnable and resumable: a conversation already carrying a digest row is
skipped. Claim ids are deterministic (kind + normalized statement), so a full
re-run is idempotent, not duplicative.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime
from typing import Optional

import sqlalchemy as sa
from pydantic import BaseModel, Field
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, insert as pg_insert
from sqlalchemy.orm import Mapped, mapped_column

from builders.l0_ledger import SCHEMA, Base
from common.hashing import make_id

EXTRACTOR_VERSION = "claim-1"

KINDS = {"Decision", "Fact", "Task", "Event", "Commitment", "Process"}
ROLES = {"decider", "assignee", "subject", "participant", "stated_by",
         "mentioned", "counterparty"}


# ── Tables ───────────────────────────────────────────────────────────────────

class Claim(Base):
    __tablename__ = "claim"
    __table_args__ = (
        sa.Index("claim_org_kind_idx", "org", "kind"),
        sa.Index("claim_org_conv_idx", "org", "conversation_id"),
        sa.Index("claim_scope_gin", "scope_ids", postgresql_using="gin"),
        {"schema": SCHEMA},
    )
    claim_id: Mapped[str] = mapped_column(sa.Text, primary_key=True)
    org: Mapped[str] = mapped_column(sa.Text, nullable=False)
    kind: Mapped[str] = mapped_column(sa.Text, nullable=False)
    statement: Mapped[str] = mapped_column(sa.Text, nullable=False)
    attributes: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    occurred_at: Mapped[str | None] = mapped_column(sa.Text)          # ISO, as claimed
    confidence: Mapped[float] = mapped_column(sa.Float, nullable=False, default=0.8)

    # Provenance — every claim traces to its source (design §4.2).
    conversation_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    evidence_quote: Mapped[str] = mapped_column(sa.Text, nullable=False, default="")
    evidence_event_id: Mapped[str | None] = mapped_column(sa.Text)    # which message
    evidence_grounded: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=False)
    source_event_ids: Mapped[list] = mapped_column(ARRAY(sa.Text), nullable=False, default=list)
    extractor_version: Mapped[str] = mapped_column(sa.Text, nullable=False)

    # ACL — inherited from source events, fail-closed (D-INGEST-001).
    scope_ids: Mapped[list] = mapped_column(ARRAY(sa.Text), nullable=False, default=list)
    scope_all_ids: Mapped[list] = mapped_column(ARRAY(sa.Text), nullable=False, default=list)

    # Temporal versioning (SUPERSEDES lives in claim_relation).
    superseded_by: Mapped[str | None] = mapped_column(sa.Text)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), server_default=sa.func.now())


class ClaimEntity(Base):
    __tablename__ = "claim_entity"
    __table_args__ = (
        sa.Index("claim_entity_claim_idx", "claim_id"),
        sa.Index("claim_entity_person_idx", "org", "person_id"),
        {"schema": SCHEMA},
    )
    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True, autoincrement=True)
    claim_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    org: Mapped[str] = mapped_column(sa.Text, nullable=False)
    entity_ref: Mapped[str] = mapped_column(sa.Text, nullable=False)   # name as stated
    role: Mapped[str] = mapped_column(sa.Text, nullable=False)
    person_id: Mapped[str | None] = mapped_column(sa.Text)             # resolved, or null
    resolved_kind: Mapped[str] = mapped_column(sa.Text, nullable=False, default="unresolved")


class ClaimTopic(Base):
    __tablename__ = "claim_topic"
    __table_args__ = (
        sa.Index("claim_topic_claim_idx", "claim_id"),
        sa.Index("claim_topic_org_topic_idx", "org", "topic"),
        {"schema": SCHEMA},
    )
    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True, autoincrement=True)
    claim_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    org: Mapped[str] = mapped_column(sa.Text, nullable=False)
    topic: Mapped[str] = mapped_column(sa.Text, nullable=False)
    is_proposal: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=False)


class ClaimRelation(Base):
    """Typed edges between claims (design §4.2): BECAUSE, SUPERSEDES,
    RELATES_TO, CORROBORATES, INSTANCE_OF. Populated by dedup (step 5) and
    later enrichment; the table ships now so the projection can read it."""
    __tablename__ = "claim_relation"
    __table_args__ = (
        sa.Index("claim_relation_src_idx", "src_claim_id"),
        sa.Index("claim_relation_dst_idx", "dst_claim_id"),
        sa.UniqueConstraint("org", "src_claim_id", "dst_claim_id", "rel_type",
                            name="claim_relation_edge_uq"),
        {"schema": SCHEMA},
    )
    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True, autoincrement=True)
    org: Mapped[str] = mapped_column(sa.Text, nullable=False)
    src_claim_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    dst_claim_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    rel_type: Mapped[str] = mapped_column(sa.Text, nullable=False)


class ClaimDigest(Base):
    """One contextualized summary per conversation — the mid-level retrieval
    unit (design §3). Also the checkpoint: a conversation with a digest row is
    'extracted' and skipped on resume."""
    __tablename__ = "claim_digest"
    __table_args__ = (
        sa.Index("claim_digest_org_idx", "org"),
        {"schema": SCHEMA},
    )
    conversation_id: Mapped[str] = mapped_column(sa.Text, primary_key=True)
    org: Mapped[str] = mapped_column(sa.Text, nullable=False)
    digest: Mapped[str] = mapped_column(sa.Text, nullable=False, default="")
    department_tags: Mapped[list] = mapped_column(ARRAY(sa.Text), nullable=False, default=list)
    sensitivity: Mapped[str] = mapped_column(sa.Text, nullable=False, default="internal")
    claim_count: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    source_event_ids: Mapped[list] = mapped_column(ARRAY(sa.Text), nullable=False, default=list)
    scope_ids: Mapped[list] = mapped_column(ARRAY(sa.Text), nullable=False, default=list)
    extractor_version: Mapped[str] = mapped_column(sa.Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), server_default=sa.func.now())


# ── Extraction schema (the LLM's output contract) ────────────────────────────

class EntityRef(BaseModel):
    name: str
    role: str

    def ok(self) -> bool:
        return self.role in ROLES


# Quality floors are enforced in build_rows (drop the claim), NOT as Pydantic
# constraints — one short evidence quote ("Ok" on a leave approval) must not
# fail-validate an entire conversation's worth of good claims. Parse leniently,
# filter per-claim.
_MIN_STATEMENT = 30
_MIN_EVIDENCE = 2


class LLMClaim(BaseModel):
    kind: str
    statement: str = ""
    entities: list[EntityRef] = []
    topics: list[str] = []
    attributes: dict = {}
    occurred_at: Optional[str] = None
    evidence_quote: str = ""
    confidence: float = 0.8

    def passes(self) -> bool:
        return (self.kind in KINDS
                and len(self.statement.strip()) >= _MIN_STATEMENT
                and len(self.evidence_quote.strip()) >= _MIN_EVIDENCE)


class ThreadExtraction(BaseModel):
    digest: str = ""
    claims: list[LLMClaim] = []
    department_tags: list[str] = []
    sensitivity: str = "internal"


# Seed topics — prototype stand-in for the topic registry (step 6). An admin
# curates the real one; until then the extractor links closed-world to these.
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

evidence_quote — an EXACT VERBATIM substring copied from ONE email in the thread
  (not the digest, not stitched across emails). Copy it character-for-character.
  It will be checked mechanically against each source message.

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


# ── Prompt building ──────────────────────────────────────────────────────────

def strip_quoted_replies(body: str) -> str:
    """Drop quoted-reply tails so the model sees each message's own content.

    Kept local (the old pipeline.extraction.prompts version) so this module has
    no dependency on the retired file path.
    """
    lines = []
    for ln in (body or "").splitlines():
        s = ln.strip()
        if s.startswith(">"):
            continue
        if re.match(r"^On .+ wrote:$", s) or re.match(r"^-{2,}\s*Original Message", s, re.I):
            break
        if re.match(r"^From:\s.+@", s) and lines:
            break
        lines.append(ln)
    return "\n".join(lines).strip()


# Prompt-size ceiling. A 100-message notification thread is ~300k tokens and
# blows the model's context window. Mirror the old pipeline's thread-grouping:
# keep the first and last few messages in full (that's where decisions land),
# collapse the middle to subjects, and cap any single giant body. Bounds the
# prompt to well under the context limit while keeping what carries knowledge.
_HEAD_TAIL = 4          # full-body messages kept at each end
_MAX_BODY = 8000        # chars per message body
_FULL_IF_UNDER = 10     # conversations at/under this many messages: all full


def _cap_emails(emails: list[dict]) -> list[dict]:
    """Bound a conversation to a sendable size. Returns email dicts where
    middle messages (in a long thread) carry title only, body ''."""
    if len(emails) <= _FULL_IF_UNDER:
        return [{**e, "body": (e.get("body") or "")[:_MAX_BODY]} for e in emails]
    head, tail = emails[:_HEAD_TAIL], emails[-_HEAD_TAIL:]
    middle = [{**e, "body": ""} for e in emails[_HEAD_TAIL:-_HEAD_TAIL]]
    kept = ([{**e, "body": (e.get("body") or "")[:_MAX_BODY]} for e in head]
            + middle
            + [{**e, "body": (e.get("body") or "")[:_MAX_BODY]} for e in tail])
    return kept


def build_prompt(conv: dict, email_to_person: dict, persons_by_id: dict) -> str:
    """conv = {conversation_id, emails:[{primary_actor, actors, body, title, timestamp}]}."""
    emails = _cap_emails(conv["emails"])
    participants: set[str] = set()
    for e in emails:
        sender = (e.get("primary_actor") or "").replace("email:", "").lower()
        if sender:
            participants.add(sender)
        for a in e.get("actors", []):
            addr = (a.get("email") or a.get("id", "").replace("email:", "")).lower()
            if addr:
                participants.add(addr)

    plines = []
    for addr in sorted(participants):
        pid = email_to_person.get(addr)
        p = persons_by_id.get(pid) if pid else None
        if p:
            label = "INTERNAL" if p.get("is_internal") else "EXTERNAL"
            if p.get("is_functional"):
                label += ", FUNCTIONAL MAILBOX"
            plines.append(f"  {p.get('canonical_name', addr)} ({addr}) — {label}")
        else:
            plines.append(f"  {addr} — EXTERNAL, UNKNOWN")

    blocks = []
    for i, e in enumerate(emails):
        sender = (e.get("primary_actor") or "").replace("email:", "").lower()
        pid = email_to_person.get(sender)
        name = persons_by_id.get(pid, {}).get("canonical_name", sender) if pid else sender
        body = strip_quoted_replies(e.get("body", "") or "")
        blocks.append(
            f"--- Email {i+1} of {len(emails)} ---\n"
            f"From: {name}\nDate: {(e.get('timestamp') or '')[:16]}\n"
            f"Subject: {e.get('title', 'No subject')}\n\n{body}")

    topics = "\n".join(f"  - {t}" for t in SEED_TOPICS)
    return (
        "THREAD PARTICIPANTS (use these exact canonical names):\n"
        + "\n".join(plines)
        + f"\n\nCANDIDATE TOPICS (link to these; propose NEW: only if none fit):\n{topics}"
        + f"\n\nTHREAD ({len(emails)} emails):\n\n"
        + "\n\n".join(blocks)
        + "\n\nProduce the digest and claims. Return valid JSON only."
    )


# ── Deterministic resolution / validation ────────────────────────────────────

def norm_text(s: str) -> str:
    """NFKC + punctuation-strip + whitespace-collapse. Evidence and statement
    keys both run through this (see CLAIM_GATE_RESULT: leniency toward FORMATTING
    is correct for an evidence-recall check)."""
    s = unicodedata.normalize("NFKC", s or "")
    s = re.sub(r"[^\w\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip().lower()


def claim_id(org: str, kind: str, statement: str) -> str:
    """Deterministic id: same (org, kind, statement) → same id (idempotent
    re-runs). Near-duplicate collapsing is dedup's job (step 5), not this."""
    return "claim:" + make_id("claim", f"{org}:{kind}:{norm_text(statement)}")


def resolve_entity(name: str, name_to_person: dict) -> tuple[str | None, str]:
    """Name → (person_id, kind). Exact (normalized) match against registry
    display names — the model was GIVEN the canonical names, so exact match is
    right; fuzzy person-merging is an ACL risk and lives in step 9, not here."""
    pid = name_to_person.get(norm_text(name))
    return (pid, "person") if pid else (None, "unresolved")


def ground_evidence(quote: str, messages: list[dict]) -> tuple[str | None, bool]:
    """Match a quote to the exact source message it came from (the gate's
    productionization fix). Returns (event_id, grounded).

    Grounded means citable to ONE message — the honest bar for a citation.
    A quote that only matches across messages (cross-message stitch) or a
    reflowed table returns (None, False): real content, kept with the claim's
    full source_event_ids, but flagged so a citation never points at a message
    that doesn't actually contain the words."""
    q = norm_text(quote)
    if not q:
        return None, False
    for m in messages:
        if q in norm_text((m.get("title", "") + " " + m.get("body", ""))):
            return m.get("event_id"), True
    return None, False


def inherit_scopes(messages: list[dict]) -> tuple[list[str], list[str]]:
    """Claim scope = union of source events' scope_ids (OR-set). Fail-closed:
    empty union → empty → the claim is invisible to everyone (D-INGEST-001).
    A conversation's messages share participants, so the union is the set of
    people who were on the thread."""
    scope: set[str] = set()
    for m in messages:
        scope |= set(m.get("scope_ids") or ())
    return sorted(scope), []


def build_rows(ext: ThreadExtraction, conv: dict, org: str,
               name_to_person: dict) -> dict:
    """One extraction → DB rows (claims, entities, topics, digest). Pure."""
    messages = conv["emails"]
    event_ids = [m.get("event_id") for m in messages if m.get("event_id")]
    scope_ids, scope_all = inherit_scopes(messages)
    conv_id = conv["conversation_id"]

    claims, entities, topics = [], [], []
    seen: set[str] = set()
    for c in ext.claims:
        if not c.passes():        # quality floor — drop the claim, keep the rest
            continue
        cid = claim_id(org, c.kind, c.statement)
        if cid in seen:                       # collapse exact dups within a conv
            continue
        seen.add(cid)
        ev_id, grounded = ground_evidence(c.evidence_quote, messages)
        claims.append({
            "claim_id": cid, "org": org, "kind": c.kind, "statement": c.statement,
            "attributes": c.attributes or {}, "occurred_at": c.occurred_at,
            "confidence": c.confidence, "conversation_id": conv_id,
            "evidence_quote": c.evidence_quote, "evidence_event_id": ev_id,
            "evidence_grounded": grounded, "source_event_ids": event_ids,
            "extractor_version": EXTRACTOR_VERSION,
            "scope_ids": scope_ids, "scope_all_ids": scope_all,
        })
        for e in c.entities:
            if not e.ok():
                continue
            pid, kind = resolve_entity(e.name, name_to_person)
            entities.append({"claim_id": cid, "org": org, "entity_ref": e.name,
                             "role": e.role, "person_id": pid, "resolved_kind": kind})
        for t in c.topics:
            topics.append({"claim_id": cid, "org": org,
                           "topic": t[4:].strip() if t.startswith("NEW:") else t,
                           "is_proposal": t.startswith("NEW:")})

    digest = {
        "conversation_id": conv_id, "org": org, "digest": ext.digest,
        "department_tags": ext.department_tags or [], "sensitivity": ext.sensitivity,
        "claim_count": len(claims), "source_event_ids": event_ids,
        "scope_ids": scope_ids, "extractor_version": EXTRACTOR_VERSION,
    }
    return {"claims": claims, "entities": entities, "topics": topics, "digest": digest}


def write_conversation(store, rows: dict) -> None:
    """Persist one conversation's rows. Idempotent: claims upsert on id, the
    digest upsert marks the conversation done. Entity/topic rows for a claim
    are replaced (delete-by-claim then insert) so a re-extraction is clean."""
    claims, entities, topics, digest = (rows["claims"], rows["entities"],
                                        rows["topics"], rows["digest"])
    with store._session() as s:
        if claims:
            ins = pg_insert(Claim).values(sorted(claims, key=lambda r: r["claim_id"]))
            s.execute(ins.on_conflict_do_update(
                index_elements=["claim_id"],
                set_={"statement": ins.excluded.statement,
                      "attributes": ins.excluded.attributes,
                      "confidence": ins.excluded.confidence,
                      "evidence_quote": ins.excluded.evidence_quote,
                      "evidence_event_id": ins.excluded.evidence_event_id,
                      "evidence_grounded": ins.excluded.evidence_grounded,
                      "scope_ids": ins.excluded.scope_ids,
                      "extractor_version": ins.excluded.extractor_version}))
            cids = [c["claim_id"] for c in claims]
            s.execute(sa.delete(ClaimEntity).where(ClaimEntity.claim_id.in_(cids)))
            s.execute(sa.delete(ClaimTopic).where(ClaimTopic.claim_id.in_(cids)))
            if entities:
                s.execute(sa.insert(ClaimEntity), entities)
            if topics:
                s.execute(sa.insert(ClaimTopic), topics)
        d = pg_insert(ClaimDigest).values([digest])
        s.execute(d.on_conflict_do_update(
            index_elements=["conversation_id"],
            set_={"digest": d.excluded.digest, "claim_count": d.excluded.claim_count,
                  "department_tags": d.excluded.department_tags,
                  "sensitivity": d.excluded.sensitivity,
                  "scope_ids": d.excluded.scope_ids,
                  "extractor_version": d.excluded.extractor_version}))
