# Claim Dedup — Status (build step 5)

**Date:** 2026-07-23 · **Built:** `builders/dedup_claims.py` · **Scope: two
deterministic rules only**, per `00_DESIGN.md` §5. Everything else in the
funnel design is intentionally parked until there's enough real corpus to
tune it against.

## What's built

Populates `claim_relation` with `CORROBORATES` edges (never merges or deletes
a claim — "corroborate, don't discard"). Re-runnable: recomputed from the
current claim table each run, `ON CONFLICT DO NOTHING` on `(org,
src_claim_id, dst_claim_id, rel_type)`.

**Rule 1 — shared external id.** Same `kind` + same `attributes.external_id`,
within 30 days of each other, and the id isn't a bare digit string (phone
numbers / employee ids recur across someone's whole history and are not a
one-off transaction — this is what `_looks_like_transaction_id` guards
against) and the id's group isn't bigger than 3 claims (a real transaction
is mentioned a couple of times; an account/client id is mentioned constantly
— `CN33451` showed up across 6 unrelated claims spanning a year in the first
live run and was the reason both guards exist).

**Rule 2 — shared subject + date.** Same `kind` + same `attributes.
subject_person` + same date (day granularity) + compatible amounts (if both
claims state one, they must match — a genuine conflict blocks the merge
rather than silently corroborating it).

**Live result on the partial ezobooks corpus (1,607 claims so far, extraction
still running):** 1 corroboration found, after two rounds of tightening
caught by spot-checking actual output (344 → 4 → 1, the drops were real false
merges, not over-caution — see below). Expected to grow substantially once
the full ~17k-conversation extraction lands and more invoices/tickets/
transactions get referenced from multiple threads.

## Known false-merge classes found and fixed during build

1. **Recurring account/client id treated as a transaction id.** `CN33451`
   (a Servetel client id) matched 6 unrelated claims — onboarding, billing,
   a support ticket, a license change — across a year. Fixed with the
   group-size cap + date window.
2. **Bare-digit ids (employee id / phone number).** `10746` (an employee id)
   matched two different absence records for the same person 6 days apart.
   Fixed by requiring the id contain at least one letter — real transaction/
   ticket refs in this data are alphanumeric (`AA090925085565J`,
   `A1009733941763249`); pure-digit strings are consistently phone numbers or
   employee ids.

**Known recall cost of the alpha-guard:** a genuine pure-numeric id (e.g. a
Naukri job requirement id, `300125023146`) now never matches, even when it's
legitimately a one-off reference repeated in two threads. Accepted trade-off
— a missed corroboration is cheap (the claim still exists, just unlinked); a
false merge silently conflates two different facts. Revisit if recall turns
out to matter more than expected.

## Deliberately parked — not built, not scheduled

| Piece | Why parked | What it needs |
|---|---|---|
| **Cosine-similarity blocking** (design §5.2 Stage A) | No claim embedding pass exists yet — `pipeline/embed_knowledge.py` embeds the old triple format, not claims | A claim embedding step (reuse the same NVIDIA NeMo model) before this can recall paraphrase-level duplicates that share no structured attribute |
| **SUPERSEDES** (temporal restatement, e.g. "salary is 19k" → "revised to 24k") | Riskier to get right without eyeballing real conflicting pairs first; the two Naukri applicant-count claims found during testing (6→4, 29→14 on different report dates) are exactly this case, currently tagged CORROBORATES instead of SUPERSEDES | Real conflicting-value examples to validate the rule against, then wire `Claim.superseded_by` (column already exists, unused) |
| **INSTANCE_OF** (recurring event series, e.g. weekly standups) | No `series_key` population in the extractor's `attributes` yet for most Event claims | Extractor prompt tweak to populate `series_key` reliably, then a grouping pass |
| **Stage C — LLM adjudication of residuals** (SAME/UPDATE/RELATED/DIFFERENT) | Needs real residual-pair examples (survived blocking, no rule decided) to write a useful constrained prompt against; premature with today's small corpus | Run Stage A+B at full corpus scale, sample the residuals, then design the prompt against real cases |
| **RELATES_TO** (similar semantics, disjoint participants) | Same as above — needs either embeddings or Stage C | |

## Next

Full-corpus rerun once claim extraction finishes (`runners/extract_claims.py`
— currently in progress). Then proceed per the build order to the Neo4j +
vector projection (step 7), which does not depend on any of the parked items
above — `claim_relation` just gets richer over time as dedup keeps running.
