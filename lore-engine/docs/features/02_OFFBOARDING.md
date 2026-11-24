# Offboarding — build doc (claims substrate)

**Build order: third** (after expert search + the shared views, which do most of its work).
**Product/UX reference:** workspace `docs/02-offboarding.md` (report-not-chatbot; section 2 "knowledge at risk" is the money — both stand).

---

## 1. The manager's actual moment — and why exit-time is already too late

The old spec starts at "a manager picks a person who's leaving." That's the *reactive* half. The notice period in Indian SMBs is 30–90 days; the knowledge-at-risk list is only actionable if there's runway to use it — and by resignation day, the runway is fixed. The structural edge Loremind has over Sensay/Exit Insights (their graph is built per-engagement, at exit) is that ours runs **continuously**. So the feature is two surfaces sharing one computation:

1. **The bus-factor radar (continuous, proactive).** A standing dashboard tile: topics where knowledge is dangerously concentrated, ranked by `risk × activity`. The manager sees "UPI reconciliation: 91% Rahul, no backup, 40 claims last quarter" *months before* anyone resigns — and can act with cross-training, not a panicked handover. This is the retention-side value nobody else can ship.
2. **The Knowledge Report (triggered, reactive).** Resignation arrives → manager generates the full report for that person → structured handover during notice.

## 2. The Knowledge Report, section by section (new substrate)

### §1 — What they did
`person_profile` view: claim counts by role (decided / owned / stated), topics ranked by their `expertise` rows, artifacts owned (L1 — Jira components, repos), top internal collaborators and **external counterparties** (L2, weights + recency). Everything expandable to claims with citations. SUPERSEDES-aware: shown claims are current-truth heads, with revision history available (the successor needs to know the process *as it is now*, and how it got there).

### §2 — Knowledge at risk (the money)
Five detectors, all SQL over the shared views — each row carries its evidence and its "why flagged" decomposition:

| Detector | Query | Why it matters to the manager |
|---|---|---|
| **Sole owner** | `expertise` where person's share > θ and 2nd-best < floor (literally `find_experts(topic)` returning ~one) | the classic bus-factor list |
| **Sole relationship** | L2: external counterparties where this person is the only internal above an interaction floor | the vendor/client relationship that walks out with them — invisible in any document, obvious in the comm graph |
| **Sole-stated knowledge** | claims where `corroborated_by` contains only their events (no other person ever restated it) | facts/processes that exist in exactly one head |
| **Unfinished commitments** | `open_commitments` for assignee = person, with due dates and age | the concrete handover checklist |
| **Uncaptured participation** | threads/containers with high L2 participation but few claims extracted | tacit-knowledge signal: they were deeply involved in things the written record barely captures — the exit interview should dig here |

Framing rule (kept from the old spec, now enforceable): every flag is phrased as **"at risk"**, never "here is what we don't know" — and because every detector row links to its evidence, the manager can judge the flag rather than trust it.

### §3 — Exit-interview question pack
One **offline** LLM call over the §2 gap list (narration, not computation — README rule): turns each risk row into 2–3 concrete questions ("Walk through the UPI reconciliation edge cases — the June revision changed the cutoff from 4 days to 48 hours; what else changed?"). Questions cite the claims they were generated from, so the interviewer has the context open while asking. Checklist UX: per-question done/answer state.

### §4 — Successor package (the bridge to onboarding)
One click: the report's claim set + people map + open commitments becomes a draft **onboarding package** (doc 01), pending manager approval and the scope-grant mint. This is the clean product loop: offboarding output *is* onboarding input.

## 3. Phase 2 — capture the answers back

The departing person answers §3 questions in-app (text first; voice later). The design insight the substrate makes trivial: **the interview is just another source.** Answers land as L0 events (source `interview`, scoped like the report), flow through the standard claim extractor, corroborate or supersede existing claims, and appear in the successor package automatically. No special storage, no separate "interview knowledge base" — the pipeline already knows how to do all of this. (Cross-source rule applies: interview claims CORROBORATE thread claims, never merge — INGEST 00 §5.)

## 4. Mechanics

- **Routes:** `POST /api/offboarding/{person_id}/generate` (async job — recomputes the subject's views fresh, assembles, persists) + `GET .../report`. Radar tile reads the standing `bus_factor` view directly (no job).
- **Access:** Plane A authority `feature.offboarding.generate` (manager/HR). Content: org-aggregate policy is effectively required here (a manager rarely shares every scope their report had) — the README's `analytics.view_org_aggregates` grant, audited; drill-down evidence stays scope-gated.
- **Storage:** Postgres `offboarding_reports` table (the old spec says Mongo — Mongo is removed from lore-engine; do not resurrect it). Report is a frozen snapshot (claim ids + rendered payload) so it stays stable while the graph moves on; regenerate is explicit.
- **Service:** `services/offboarding_service.py` orchestrating view refresh → detectors → narration call → persist. Nothing on the live query path.
- **The subject's dignity:** the report is about knowledge, not conduct. No sentiment analysis, no "productivity" framing, no surveillance-flavored stats (README rule). The departing employee may be shown their own §3 questions — never the risk framing about themselves.

## 5. Benchmarks (T1 makes this checkable)

The synthetic org generator knows, by construction, who is the sole owner of what. Gates: sole-owner detector precision/recall against latent truth ≥ 0.9/0.8; sole-relationship detector exact on seeded single-threaded vendors; zero scope leaks in drill-down; question-pack citations must reference real claim ids (same groundedness gate as V3 §6).

## 6. Build checklist

- [ ] `bus_factor` view + radar tile (ships with dashboards, doc 04).
- [ ] Five detectors as SQL over shared views, each with decomposition + evidence links.
- [ ] Report job + routes + Postgres persistence + freeze semantics.
- [ ] Offline question-pack narration with claim citations.
- [ ] Successor-package draft handoff (blocks onboarding, doc 01).
- [ ] Frontend report view (3 sections + radar tile + export PDF).
- [ ] (Phase 2) answer-capture → L0 `interview` source.

**Don't:** run on the query hot path; present flags as certainties; resurrect Mongo; let the report become a performance review.
