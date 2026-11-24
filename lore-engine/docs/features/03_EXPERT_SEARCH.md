# Expert Search — build doc (claims substrate)

**Build order: first.** Smallest surface, purely deterministic, and it materializes the `expertise` view every other feature reuses. It is also the end-to-end proof that the substrate delivers non-AI queryability (INGEST 00 §11 step 8).
**Product/UX reference:** workspace `docs/03-expert-search.md` (people cards with evidence, not prose — that golden rule stands).

---

## 1. The user's actual moment

- An employee is blocked: "who can I *ask* about GST invoice rejections?" They need someone who answers questions — a `stated_by` person.
- A manager is delegating: "who can *own* the vendor renegotiation?" They need someone who decides and executes — a `decider`/`assignee` person.
- A new joiner (via onboarding) needs both, ranked by approachability and recency.

These are different questions with different right answers, and the old design collapsed them into one `EXPERT_IN.count`. The claims substrate carries **roles**, so v2 of this feature can answer the question the user is actually asking. The card shows both facets; the API exposes them separately.

## 2. What the old spec can no longer assume

- `EXPERT_IN` as an ingested edge is **deleted** (INGEST 00 §4.3) — it was LLM-asserted, unexplainable, and unrecomputable. Expertise is now a *scored L4 view*.
- Topic resolution no longer means fuzzy-matching 3,609 free-text Topic nodes (where "payment" hits 40 of them). The query resolves against the **topic registry** — aliases included — so "UPI recon", "UPI reconciliation", and a Hinglish variant land on one topic id. This fixes the failure mode that would have made the old design return garbage rankings for most queries.
- `total_activity` tiebreaks and `COMMUNICATES_WITH` heuristics are replaced by L2 activity share, which is dense (it comes from headers/membership, not LLM extraction — INGEST 00 §F5 fixed).

## 3. The `expertise` view

One row per (person_id, topic_id), recomputed daily. Score is a sum of interpretable terms — **the decomposition is stored as columns**, so the card's "why" line is a SELECT, not an explanation generated after the fact:

```
authority   = Σ decider-role claims on topic        × 3.0
execution   = Σ assignee-role claims (completed)    × 2.0
knowledge   = Σ stated_by-role claims               × 1.5   (×1.5 again if corroborated)
involvement = L2 activity share in topic-linked containers × 10.0
recency     = exp(−age_of_latest_contribution / 90d)        (multiplier on the sum)

score       = (authority + execution + knowledge + involvement) × recency
```

Design notes, each answering a real failure mode:

- **Corroborated `stated_by` counts extra** — a fact one person stated and others independently confirmed is teaching, not chatter. This is the anti-"loudest person wins" term: raw message volume contributes only through the (capped) involvement term.
- **Recency is a multiplier, not a term** — someone who owned the topic two years ago and hasn't touched it since should rank below the person living in it now, but not vanish (they're still the right person for "why was it built this way", which offboarding/onboarding care about). Half-life is a Settings constant tuned on the T1 gold set.
- **Superseded claims still count** — expertise is about having *done* the work; a decision later revised was still their decision. (Contrast with the decision log, which serves current truth.)
- **Weights are falsifiable**: the T1 synthetic org has latent expertise by construction (the generator knows who decided what), so ranking accuracy (nDCG against latent truth) is a benchmark, not a vibe. Initial weights above are hypotheses; E-suite tunes them.
- Score bands (●●●●○) are per-topic percentile, not absolute — a small topic's top expert is still its top expert.

## 4. API + resolver (one implementation, two surfaces)

- `GET /api/experts?topic=<text>&facet=ask|own|all` → resolve text against the topic registry (alias match, embedding fallback; ambiguous → return the candidate topics for the user to pick, never guess silently) → read the view → people cards.
- The same resolver serves the `EXPERTISE_LOOKUP` intent in /ask (V3 Lane B) — chat narrates the identical ranking and links to the Experts page.
- Card payload: person (L1 registry — canonical name, role), score band, decomposition line ("8 decisions, 14 corroborated facts, active 3 days ago"), facet indicators, and the drill-down: the actual claims (scope-gated, evidence quotes, source links).
- Scope policy per the README: evidence-gated by default; `analytics.view_org_aggregates` shows org-wide rankings with gated drill-down.

## 5. Edge cases that will actually happen

| Case | Behavior |
|---|---|
| Topic with one contributor | Rank of one, flagged "no backup" — this *is* the offboarding sole-owner signal; don't hide it |
| Person who left the org | Included with a "departed" badge (L1 alias validity) — often the honest answer, and the pointer to their offboarding package |
| Query matches no registry topic | Offer nearest registry topics; never fabricate a ranking from raw embedding neighbors |
| Functional mailbox / bot in ranking | Excluded by construction — System actors never carry claim roles (INGEST 01 G3/G6) |
| Two same-name people | Distinct person_ids from the registry; cards disambiguate by role/containers (F-S identity keeps them apart) |

## 6. Build checklist

- [ ] `expertise` view job (SQL over claim-role + L2 tables) + decomposition columns + daily schedule.
- [ ] Topic-text → registry resolution helper (shared with V3 analysis anchoring — build once).
- [ ] `GET /api/experts` route + Lane B resolver, both reading the view; dual scope policy.
- [ ] People-card block in frontend (replaces the hardcoded `/expertise` demo data) + claims drill-down.
- [ ] Benchmark: nDCG vs T1 latent expertise; zero-leak check on drill-down.

**Don't:** rank on ungated evidence without the aggregate authority; return prose instead of cards; let the LLM anywhere near the score.
