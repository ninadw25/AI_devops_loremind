# Claim Extraction Gate — Result (build step 4)

**Date:** 2026-07-22 · **Verdict: PASS — proceed to productionize (step 4→7).**
**Runner:** `scripts/claim_gate.py` (reuses the prototype's prompt/schema/validators,
sources conversations from L0 + participants from the L1 registries we just built).
**Model:** `moonshotai.kimi-k2.5` via Bedrock (the E1 winner). 40 signal
conversations, sizes 1–11, deterministic sample across size buckets.

## The gate question

§11 step 4: *"proceed only if quality clearly beats stage 5"* (the old triples
extractor). This gate re-validates against the **clean** L1 registry, since the
prototype's original 94% was measured on the old messy person registry.

## Headline metrics (40 conversations, 99 claims)

| Metric | Raw | Real | Note |
|---|---|---|---|
| Conversations extracted | 40/40 | 100% | zero API/schema failures |
| Valid kinds | 99/99 | 100% | |
| Valid entity roles | 98/99 | 99% | |
| Claims with attributes | 96/99 | 97% | discriminative dedup keys present |
| Topics linked (vs NEW:) | 99/0 | 100% | closed-world linking held; no topic invention |
| **Entity resolution** | 188/243 = 77% | **~98% of persons** | see below |
| **Evidence verbatim** | 81/99 = 82% | **100% grounded** | see below |

## Why the two "low" numbers are measurement artifacts, not quality problems

**Entity resolution 77% → really ~98%.** The metric counts an entity as
"resolved" only if its name matches a *thread participant*. Of the 55 misses,
**51 are organizations or externals named correctly** — `counterparty` (21,
e.g. "Knowlarity Communications"), `subject`/`mentioned`/`stated_by` (30, e.g.
"EZO Technologies Pvt. Ltd.", the GST portal). Those SHOULD be named and can't
be thread participants. Only **4 of 243 entities are genuine person misses**
(Sayali Kamble ×3, Arvind Tiwari — people named in a body but not in the actor
list). So person resolution is ~98%, and the clean registry did lift it (a
4-conversation smoke hit 100%).

**Evidence verbatim 82% → 100% grounded, zero fabrications.** Every one of the
18 "misses" is real text, failing only exact-substring for one of two reasons:
- **Cross-message stitches** (13/18): the bank-credit alerts match 25–28 words
  verbatim, then the transaction reference comes from a *sibling* message in the
  same conversation. Both halves are real; they live in different emails.
- **Reflowed tables** (5/18): pasted pricing tables ("Channels 30 · INR 37,500
  · incl 18% GST INR 44,250") the model linearized in a different column order
  than the raw whitespace. All numbers correct.

Manual read of the sample (`output/knowledge/prototype/ezobooks_claims_gate_sample.md`)
confirms: self-contained statements, pronouns resolved, canonical names, dates
and amounts inline, correct kinds. Clearly beats the old naked-triple output.

## Productionization note (found by the gate, not a blocker)

The evidence check should validate a quote against **per-message bodies** (match
any single source message), not the concatenated conversation. This both fixes
the cross-message-stitch false-negatives AND yields the exact `source_event_id`
each quote came from — which the claim's `provenance` needs for citations
anyway (design §4.2). Fold into the productionized extractor's validator.

## Decision

Extraction quality on the new substrate clearly clears the bar. **Go:** build
the production claim layer — tables (`claim`, `claim_entity`, `claim_topic`,
`claim_relation`), batching + checkpointing over ~50k signal conversations,
the per-message evidence validator above, then dedup (step 5) and the Neo4j +
vector projection (step 7). Parked, documented: topic staging/curation (step 6),
F-S personal-account linkage (step 9).
