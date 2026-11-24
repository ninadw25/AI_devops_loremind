# Feature Build Docs — on the Claims Substrate

**Status:** Proposed — supersedes the intern build notes at workspace `docs/01–03` (kept as product/UX reference; their backend sections are written against the old ontology and are stale)
**Date:** 2026-07-13
**Note:** the onboarding/offboarding/experts/continuity features that actually shipped (`services/onboarding_service.py`, `services/offboarding_service.py`, `api/routes/{experts,continuity,onboarding,offboarding,people}.py`) read the Neo4j graph, not the claims substrate or endpoints proposed here. The workspace `docs/01–03` copies previously duplicated in this repo were removed; the workspace copies remain.
**Depends on:** `../../../docs/revised_ingestion_pipeline/` (the L0–L4 substrate — `INGEST 00`–`04`) and `../QUERY_PIPELINE_V3.md` (lanes, resolvers, grounded synthesis)

| Doc | Feature | For whom | Build order |
|---|---|---|---|
| [01_ONBOARDING.md](01_ONBOARDING.md) | Guided ramp-up space for new joiners | new employee + their manager | 4th (needs offboarding packages + scope grants) |
| [02_OFFBOARDING.md](02_OFFBOARDING.md) | Knowledge-risk report + continuous bus-factor radar | manager, HR | 3rd |
| [03_EXPERT_SEARCH.md](03_EXPERT_SEARCH.md) | "Who knows about X" with evidence | everyone | **1st — the primitive the others reuse** |
| [04_DASHBOARDS.md](04_DASHBOARDS.md) | Org pulse, decision log, commitments, silo map, freshness | manager, CTO, HR | 2nd (same views as expert search) |

---

## The value thesis (why these features, for this market)

Loremind sells to SMBs, and SMBs have a specific shape of the knowledge problem: **no documentation culture, no time to build one, and hero employees who carry whole functions in their heads.** Nobody writes the wiki. The knowledge exists only in Gmail threads, Slack scrollback, and Jira comments — which is exactly what Lore passively captures. The features below are the four moments where that captured knowledge converts into money the buyer can feel:

1. **Someone joins** → weeks of "ask around and wait" become a structured package. (Onboarding)
2. **Someone leaves** → the ₹-value moment. What walks out the door, made visible while there's still a notice period to act in. (Offboarding)
3. **Someone is blocked on "who do I ask?"** → the daily-friction moment, and the habit-forming one. (Expert search)
4. **A manager needs to see the org** → decisions, commitments, risk concentration — without reading a thousand threads. (Dashboards)

Competitors (Sensay, Exit Insights, Clarive) build their graph *per engagement, at exit time*, from interviews. Our structural edge is that the substrate is **already running continuously** — so "what does Rahul know" is a query, not a project, and risk is visible *before* the resignation letter. Every feature doc below leans on that edge.

---

## Shared architecture: every feature is the same four-layer sandwich

```
L2/L3/L4 substrate            (ingestion-owned: claims, deterministic edges, registries)
      │
      ▼
MATERIALIZED VIEWS            (feature-owned L4 jobs: expertise scores, bus-factor,
  (SQL, scheduled, versioned)  open-commitments, topic-activity, person-profile)
      │
      ▼
RESOLVERS + ROUTES            (thin, deterministic, scope-gated; the same resolvers
  (lore-engine Lane B)         serve /ask intents AND the feature UIs — V3 §3.2)
      │
      ▼
UI + optional NARRATION       (cards/reports/dashboards; offline LLM may *narrate*
                               a report — it never computes a number)
```

Rules that hold across all four docs:

- **Deterministic numbers, LLM prose.** Every score, count, rank, and flag is SQL over the views — explainable, recomputable, testable against the synthetic gold set (INGEST 02 T1, where the latent org truth makes "correct bus factor" a checkable fact). The LLM appears only in offline narration (report text, interview questions) and in the scoped chat that accompanies a feature.
- **Every number drills to evidence.** A score without a "why" is an assertion; a score with claims behind it is knowledge. Every card/cell/flag expands to the claims that produced it, each with evidence quote + source link (the claim provenance chain). This is the product's trust story and it is non-negotiable.
- **Chat and features cannot disagree.** The feature UIs and the /ask Lane B resolvers read the *same views* (V3 Q5). "Who knows UPI reconciliation" in chat and the Experts page return the same ranking.
- **Current truth by default.** Everything claim-derived is SUPERSEDES-aware (V3 §4): onboarding never teaches a stale process; the decision log shows the latest decision with its revision history attached.
- **Access = Plane A authority for the feature, Plane B scopes for the content.** Who may open the offboarding report is an elysia-rbac authority (`feature.offboarding.generate`, manager/HR roles). What appears *inside* is fail-closed scope-gated to the requester. See "the aggregation question" below.
- **Not surveillance.** These features measure *knowledge risk and knowledge flow*, not employee performance. No individual message-count leaderboards, no response-time metrics, no "least active person" views. This is a design constraint, not a tone note: an SMB where employees learn the tool watches them is an SMB where the connectors get quietly sabotaged. Aggregate up (topics, teams, risks), never down (person-as-metric) — except where the person themselves or an explicit HR workflow (offboarding) is the subject.

### The aggregation question (read before building any of these)

Views aggregate over claims the *viewer* may not have scopes for. Two policies, chosen per surface:

1. **Evidence-gated (default):** compute against only the requester's visible claims. Correct-by-construction, but a manager with narrow scopes sees a misleadingly small picture.
2. **Org-aggregate authority (explicit opt-in):** a Plane A authority (`analytics.view_org_aggregates`) lets designated roles see scores computed over *all* org data, while drill-down evidence stays scope-gated (you can see *that* Rahul is the sole owner of UPI reconciliation; you cannot open claims you lack scopes for). This is a deliberate, audited grant made by the org's own admin — it never happens implicitly.

Never a third option: silently mixing the two, or ranking on invisible evidence without the authority. The dual policy is implemented once, in the view resolvers, not per feature.

### Shared views (build once, in this order)

| View | Feeds | Source layers |
|---|---|---|
| `expertise(person, topic, score, decomposition…)` | expert search, offboarding sole-owner, onboarding people-map, bus-factor heatmap | L3 claim roles + L2 activity + recency |
| `topic_activity(topic, period, claims, events, participants)` | pulse, freshness, bus-factor weighting | L0×L1×L3 |
| `open_commitments(task_claim, assignee, due, age, status)` | commitment tracker, offboarding unfinished-work | L3 Task claims (SUPERSEDES-aware) |
| `person_profile(person → roles, containers, top counterparts, claim counts)` | onboarding, offboarding "what they did", people cards | L1+L2+L3 |
| `bus_factor(topic, top1_share, backup_gap, risk)` | offboarding radar, dashboard heatmap | derived from `expertise` × `topic_activity` |

All are plain SQL over the Postgres substrate, recomputed on schedule (daily is enough; offboarding triggers a fresh recompute for its subject), versioned like every other derived artifact, and benchmarked against the T1 synthetic org where the ground-truth expertise/ownership is known by construction.
