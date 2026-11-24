# Dashboards — build doc (claims substrate)

**Build order: second** (rides the same views as expert search; replaces the frontend's hardcoded demo data on `/dashboard`, `/pulse`, `/team`, `/map`).
**Positioning:** this is requirement (2) of the whole redesign — *queryable without AI* (INGEST 00 §1). Every widget below is SQL over L0–L4. If a widget needs an LLM to compute, it doesn't belong here.

---

## 1. Who opens a dashboard, and to do what

Dashboards die when they're "interesting"; they live when they answer a recurring question someone already has. Per persona, the recurring questions in an SMB:

- **CTO/founder:** "What is the company actually busy with? What did we decide lately? Where are we fragile?" — they can't read a thousand threads, and today their alternative is asking around.
- **Manager:** "What's stuck? Who's overloaded on knowledge (not tasks)? Which vendor relationships hang on one person?"
- **HR/ops:** "Who's ramping, who's leaving, and is knowledge transfer actually happening?"

Each widget below names its question, its layers, and its drill-down. **Every cell drills to claims → evidence quote → source link** — a dashboard number the user can't verify is a number they'll stop trusting (README rule).

## 2. The widgets

### 2.1 Org Pulse — "what is the company busy with?"
Topic-level activity over time from `topic_activity` (L0×L1×L3 star schema — this is literally the SQL-dashboard payoff of the event ledger). Stacked area by topic group, WoW deltas, filter by team/container.
The genuinely novel tile: **Emerging topics** — the topic-registry *staging table* (INGEST 00 §6 admission queue) is, for free, a feed of "things the org started talking about that don't fit existing categories." A new vendor, a new problem, a new project — surfaced before anyone tags anything.

### 2.2 Decision Log — "what did we decide, and why?"
Filterable feed of Decision claim chain heads (SUPERSEDES-aware — the *current* decision, with its revision trail expandable): who decided (role from the claim), when, BECAUSE rationale, affected people/topics, source. Filters: topic, decider, date, counterparty.
This is the widget SMB founders describe wanting verbatim ("what did we agree with vendor X?") and today reconstruct by scrolling email. It is also the audit trail they never had. Zero new computation — it's a scoped, ordered SELECT over claims.

### 2.3 Bus-Factor Heatmap — "where are we fragile?"
From `bus_factor` (topics × concentration): rows sorted by `risk × activity` — a risky-but-dormant topic can wait; a risky-and-hot topic is urgent. Cell drill-down: the expertise decomposition + the sole owner's claims. This is offboarding's radar (doc 02 §1) living where managers already look. Action affordance on each row: "suggest backup" → the next-ranked person from `expertise`, with what they'd need to learn (the gap between the two people's claim sets on the topic).

### 2.4 Commitment Tracker — "what's stuck?"
`open_commitments` grouped by **topic and age, not by person**: aging open Task claims with due dates, assignee shown on drill-down. Deliberate design: an "oldest open tasks" list is operationally useful; a "person with most open tasks" leaderboard is surveillance and will poison adoption (README rule). Superseded/completed tasks drop out automatically via the claim lifecycle — no manual state.

### 2.5 Relationship Coverage — "which external relationships hang on one thread?"
From L2: external counterparties (vendors, clients, banks — L1 Organizations) × internal contacts, interaction weight + recency. Flag: counterparties with exactly one active internal contact (the sole-relationship detector from doc 02, org-wide). For an SMB, a single-threaded *client* relationship is an existential risk no other tool surfaces passively.

### 2.6 Knowledge Map — the `/map` page, real data
The existing force-directed viz, re-pointed: registry topics as hubs (bounded ~150–300 — renderable, unlike 3,609 free-text nodes), sized by activity, colored by bus-factor risk, edges = L2 team↔topic involvement. Click-through to pulse/experts for that topic. The demo becomes the product.

### 2.7 Knowledge Freshness — "is what we know still true?"
Topics where activity continues but the newest *claim* is old (people are talking; knowledge isn't being restated — extraction gap or genuinely undocumented churn), and topics whose top claims are old *and* uncorroborated (stale single-source truth). This doubles as a content-free pipeline-health monitor the org admin sees (T3 metrics, INGEST 02 §4, rendered for the customer).

### 2.8 HR strip — ramp & transfer status
Onboarding packages in flight (progress by section), offboarding reports open (question-pack completion), grants nearing expiry. Pure feature-state tables; no substrate queries.

## 3. Mechanics

- **One route family:** `GET /api/dashboards/<widget>?filters…`, each a thin resolver over a shared view (README sandwich). No widget computes at request time beyond the final SELECT; views are scheduled jobs.
- **Scope policy per README:** default evidence-gated; org-wide numbers require the audited `analytics.view_org_aggregates` authority. The two-policy split is implemented in the view resolvers once — widgets inherit it.
- **Frontend contract:** replaces `app/src/data/` demo objects with these endpoints; reconcile with the existing `docs/ANALYTICS_CONTRACT.md` (predates the substrate — the shapes here win, that doc gets updated, not silently ignored).
- **Latency:** every widget is a sub-100ms SELECT (views are precomputed; A12 sizing says this is trivial at SMB scale).
- **Benchmarks:** T1 synthetic orgs make dashboard numbers *checkable* — the generator knows the latent activity, ownership, and relationship structure. Each widget ships with an assertion suite against latent truth (bus-factor exact, decision-log completeness, coverage flags exact) plus the standing zero-leak probes.

## 4. Deliberately not building (v1)

- **Sentiment/health-of-communication analytics** — surveillance-adjacent, LLM-dependent, off-thesis.
- **Individual productivity metrics** — see README; the fastest way to get connectors unplugged.
- **Real-time streams** — daily/hourly view refresh matches how these questions are asked; nobody watches an org-knowledge dashboard live.
- **Custom dashboard builder** — the eight widgets above answer the recurring questions; a query builder is a different product. Revisit only on repeated customer pull.

## 5. Build checklist

- [ ] Shared views land with expert search (README table); add `bus_factor` + relationship-coverage.
- [ ] Widget resolvers + `GET /api/dashboards/*` routes with the dual scope policy.
- [ ] Frontend: wire `/dashboard`, `/pulse`, `/map`, `/team` to real endpoints; delete demo data.
- [ ] Emerging-topics tile off the registry staging table.
- [ ] T1 assertion suite per widget + leak probes.
- [ ] Update `docs/ANALYTICS_CONTRACT.md` to these shapes.
