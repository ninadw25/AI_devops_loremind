"""
builders/dedup_claims.py
─────────────────────────
Claim dedup — build step 5 of `docs/revised_ingestion_pipeline/00_DESIGN.md` §5.

Populates `claim_relation` with CORROBORATES edges between claims that are the
SAME real-world fact told in different conversations (e.g. a salary revision
mentioned in the HR thread and again in a payroll confirmation). Never
deletes or merges rows — "corroborate, don't discard" (§5.2): every claim
survives, the edge is the merge.

Scope for this pass — the two UNAMBIGUOUS deterministic rules only:

    same kind + same attributes.external_id                 -> CORROBORATES
    same kind + same subject_person + same date (+ same
        amount, when both claims state one)                  -> CORROBORATES

`external_id` is populated inconsistently by the extractor — sometimes a true
one-off transaction/ticket id, sometimes a recurring client/account id (e.g.
"CN33451" showing up across a year of unrelated claims). Two guards keep the
rule honest: a same-key group larger than `_MAX_EXTERNAL_ID_GROUP` is treated
as a recurring identifier and skipped entirely, and any surviving match must
also fall within `_EXTERNAL_ID_WINDOW_DAYS` of each other (a real shared
transaction is discussed close in time; a shared account id is not).

Deliberately parked (documented, not built) — see
docs/revised_ingestion_pipeline/DEDUP_STATUS.md:
  - cosine-similarity blocking (needs a claim embedding pass, not built yet)
  - SUPERSEDES (temporal restatement — riskier without eyeballing real
    conflicting pairs first)
  - INSTANCE_OF (recurring-event series)
  - Stage C LLM adjudication of residuals (low volume, needs real candidate
    data to write a useful prompt against)

Re-runnable: candidate pairs are recomputed from the current claim table each
run and edges are inserted with ON CONFLICT DO NOTHING (an edge is either
there or not — no partial state to reconcile).

Usage (from lore-ingestion/):
    ../.venv/bin/python -m builders.dedup_claims --org ezobooks
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import date

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert

from builders.l0_ledger import LedgerStore, _resolve_dsn
from builders.l3_claims import Claim, ClaimRelation, norm_text
from common.logger import get_logger

log = get_logger("builders.dedup_claims")

# A real shared transaction/ticket id is discussed close in time by a handful
# of claims. A recurring client/account id shows up across many claims spread
# over months — these two guards tell them apart (see module docstring).
_MAX_EXTERNAL_ID_GROUP = 3
_EXTERNAL_ID_WINDOW_DAYS = 30


# ── Pure core (no DB — unit-tested directly) ────────────────────────────────

def _bucket_date(s: str | None) -> str | None:
    """Day-granularity date key, or None if unusable."""
    return (s or "")[:10] or None


def _looks_like_transaction_id(ext: str) -> bool:
    """Pure-digit strings in this data are phone numbers / employee ids —
    persistent identifiers that recur across someone's whole history, not a
    one-off transaction (the Israr Khan / EMP-ID false-merge this guards).
    Real transaction/ticket refs observed here are alphanumeric."""
    return any(ch.isalpha() for ch in ext)


def _external_key(c: dict) -> tuple | None:
    ext = (c.get("attributes") or {}).get("external_id")
    if not ext or not _looks_like_transaction_id(str(ext)):
        return None
    return (c["kind"], norm_text(str(ext)))


def _subject_date_key(c: dict) -> tuple | None:
    attrs = c.get("attributes") or {}
    subj = attrs.get("subject_person")
    date = _bucket_date(attrs.get("date") or c.get("occurred_at"))
    return (c["kind"], norm_text(str(subj)), date) if subj and date else None


def _amounts_compatible(a: dict, b: dict) -> bool:
    aa, ab = (a.get("attributes") or {}).get("amount"), (b.get("attributes") or {}).get("amount")
    return not aa or not ab or norm_text(str(aa)) == norm_text(str(ab))


def _claim_date(c: dict) -> date | None:
    d = _bucket_date((c.get("attributes") or {}).get("date") or c.get("occurred_at"))
    try:
        return date.fromisoformat(d) if d else None
    except ValueError:
        return None


def find_candidates(claims: list[dict]) -> list[tuple[dict, dict]]:
    """Stage A — blocking. Groups claims sharing a key; only within-group pairs
    are ever compared (never O(n^2) across the whole corpus). External-id
    groups bigger than _MAX_EXTERNAL_ID_GROUP are dropped outright — that
    many claims sharing one id means it's a recurring account/client
    identifier, not a one-off transaction (see module docstring)."""
    ext_groups: dict[tuple, list[dict]] = defaultdict(list)
    subj_groups: dict[tuple, list[dict]] = defaultdict(list)
    for c in claims:
        ek, sk = _external_key(c), _subject_date_key(c)
        if ek:
            ext_groups[ek].append(c)
        if sk:
            subj_groups[sk].append(c)

    pairs, seen = [], set()
    def _add(a, b):
        if a["conversation_id"] == b["conversation_id"]:
            return   # same-thread near-dups are build_rows's job, not dedup's
        pair_key = tuple(sorted((a["claim_id"], b["claim_id"])))
        if pair_key not in seen:
            seen.add(pair_key)
            pairs.append((a, b))

    for group in ext_groups.values():
        if 2 <= len(group) <= _MAX_EXTERNAL_ID_GROUP:
            for i in range(len(group)):
                for j in range(i + 1, len(group)):
                    _add(group[i], group[j])
    for group in subj_groups.values():
        if len(group) >= 2:
            for i in range(len(group)):
                for j in range(i + 1, len(group)):
                    _add(group[i], group[j])
    return pairs


def adjudicate(a: dict, b: dict) -> str | None:
    """Stage B — deterministic rules only (Stage C LLM adjudication is
    parked). Returns 'CORROBORATES' or None."""
    if a["kind"] != b["kind"]:
        return None
    ek_a, ek_b = _external_key(a), _external_key(b)
    if ek_a and ek_a == ek_b:
        da, db = _claim_date(a), _claim_date(b)
        if da and db and abs((da - db).days) <= _EXTERNAL_ID_WINDOW_DAYS:
            return "CORROBORATES"
        if not da or not db:   # no date to check against — trust the shared id
            return "CORROBORATES"
        return None
    sk_a, sk_b = _subject_date_key(a), _subject_date_key(b)
    if sk_a and sk_a == sk_b and _amounts_compatible(a, b):
        return "CORROBORATES"
    return None


def resolve(claims: list[dict], org: str) -> list[dict]:
    """Candidates -> relation rows. Deterministic; one row per corroborating
    pair (undirected relationship, stored once with claim_id order fixed)."""
    rows = []
    for a, b in find_candidates(claims):
        verdict = adjudicate(a, b)
        if verdict:
            src, dst = sorted((a["claim_id"], b["claim_id"]))
            rows.append({"org": org, "src_claim_id": src, "dst_claim_id": dst,
                        "rel_type": verdict})
    return rows


# ── DB pass ─────────────────────────────────────────────────────────────────

def build(org: str) -> dict:
    store = LedgerStore(_resolve_dsn())
    if not store.connect():
        raise RuntimeError("dedup_claims: could not connect to Postgres.")

    with store._session() as s:
        claims = [dict(r._mapping) for r in s.execute(sa.select(
            Claim.claim_id, Claim.kind, Claim.attributes, Claim.occurred_at,
            Claim.conversation_id).where(Claim.org == org))]
    log.info("[dedup] %d claims loaded — blocking + adjudicating", len(claims))

    rows = resolve(claims, org)
    if rows:
        with store._session() as s:
            ins = pg_insert(ClaimRelation).values(rows)
            s.execute(ins.on_conflict_do_nothing(constraint="claim_relation_edge_uq"))

    stats = {"claims": len(claims), "relations": len(rows)}
    log.info("[dedup] STAGE_SUMMARY stage=dedup_claims org=%s claims=%d relations=%d",
             org, stats["claims"], stats["relations"])
    log.info("[dedup] DONE %s", stats)
    store.disconnect()
    return stats


def main() -> None:
    ap = argparse.ArgumentParser(description="Dedup funnel: claim_relation CORROBORATES edges (step 5)")
    ap.add_argument("--org", required=True)
    args = ap.parse_args()
    build(args.org)


if __name__ == "__main__":
    main()
