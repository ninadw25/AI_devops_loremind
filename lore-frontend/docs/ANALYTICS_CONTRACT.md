# Analytics Contract — LOR-84 (customer-centric rewrite)

> **Read this first.** This is a spec, not implemented yet. LOR-98–102 build against it.
> It replaces the earlier draft that described surfaces in terms of Neo4j predicates.
> Every surface here is anchored to a question a real leader asks before a real decision.

---

## 0. What this product is actually for

A CEO does not find out a customer is unhappy until the customer is halfway out the
door. Not because anyone hid it — because the knowledge of the problem is scattered
across the inboxes, Slack threads, and Jira tickets of the people doing the work, and
it never rolls up. The account lead knows. The engineer who keeps getting the bug
reports knows. The CEO is the last to know.

**Lore's job: pull the customer signals out of the work that is already happening, and
put them in front of the person who can act — with the receipts.**

Every surface runs in one direction:

```
CUSTOMER  →  SIGNAL (the data: emails, tickets, decisions, delays)  →  PEOPLE (who's involved / who knows)
```

You start from "which customer," see "what is actually happening" (facts from real
threads), then "who is on it." Not the other way around.

### Two hard rules (these are the product, not decoration)

1. **We present facts and structure. We never conclude.**
   We do not say "this account will churn (78%)." We do not score, rank-as-judgment,
   or predict. We show: *"4 escalations in 30 days, 1 commitment slipped twice, no
   reply to their last 2 emails in 11 days"* — and the manager decides what it means.
   The value is that they can **see** it, not that we judge it. We cannot do their job
   better than they can; we make sure they have every fact when they do it.

2. **Every number is clickable to its evidence.**
   Each figure traces back to the actual email, ticket, or decision it came from. A
   number with no receipt is fabricated and gets cut. This is what makes the facts
   trustworthy enough to act on.

---

## 1. The leadership questions, and where they're answered

| Persona | The question they actually ask | Surface |
|---|---|---|
| CEO / Founder | "What's going wrong with our customers that I have no visibility into?" | Customer Risk Radar |
| Account lead / CSM | "What's going to hit me in the next 15 days? Which accounts need me now?" | Risk Radar (forward view) |
| Delivery lead / EM | "Why was this customer's deliverable delayed?" | Delay Trace |
| Sales / upsell owner | "What's the real state of this account — is there room to expand, or is it slipping?" | Account View + Signal Feed |
| Any manager | "Who holds this customer relationship, and what's exposed if they leave?" | Account Coverage |

---

## 2. Customer signal model — feasible vs fabricated

The discipline from the original draft stays: nothing fabricated ships. But the lens is
now the customer, and the signals are the things a manager reads as risk or opportunity.

A **customer** is an external `Organization` node. Signals attach to it through the
people and content connected to that org.

| Signal | Feeds | Verdict | How it's derived (evidence trail) |
|---|---|---|---|
| Open vs resolved issues per customer | Radar, Account View | **Feasible** | Count Task/ticket nodes linked to the customer org by status |
| Commitment slippage (what we promised, what slipped) | Radar, Delay Trace | **Feasible** | Task/Decision with a due/target date now in the past, or a decision that changed a previously-committed date |
| Interaction recency ("days since last contact") | Radar, Account View | **Feasible** | Most recent Event/email timestamp on any thread involving the customer |
| Response latency to the customer | Radar | **Feasible** | Gap between inbound customer message and next outbound reply (comm graph timestamps) |
| Escalations / complaints raised | Radar, Signal Feed | **Feasible** | Events/Facts tagged as escalation or complaint by the extractor's `semantic_type` |
| Open commitments due in next 15 days | Risk Radar (forward) | **Feasible** | Task/Decision with target date in the next 15 days |
| Who owns / touches this customer | Account Coverage, Account View | **Feasible** | Person→Org edges from decisions, tasks, and threads on that customer |
| Customer-knowledge exposure (sole holder) | Account Coverage | **Feasible** | Customers/topics where exactly 1 person has evidence — if they leave, it walks |
| Decision & delay history for a customer | Delay Trace, Signal Feed | **Feasible** | Decision/Event nodes regarding the customer, ordered by time, with reasoning |
| Expansion / upsell asks from the customer | Signal Feed, Account View | **Feasible** | Facts/Decisions where the customer requested new scope/feature (semantic_type `partnership`/`client_communication`) |
| **Sentiment score / "health %"** | — | **Fabricated — drop** | No sentiment extraction exists today; a happiness % would be invented. Surface the hard signals instead (escalations, latency, slippage) |
| **Churn probability / churn score** | — | **Fabricated — drop** | This is a *conclusion*. We present the factors; the manager concludes. Never a score |
| **Upsell likelihood score** | — | **Fabricated — drop** | Same — present the expansion asks as facts, don't predict |
| **Per-rep activity counts / leaderboards** | — | **Fabricated — drop** | Vanity, surveillance-adjacent, drives volume not value |
| **Revenue / ARR / contract value** | — | **Out of scope** | Not in the ingested data; lives in the CRM/billing system, not Lore |

> **The churn/upsell line.** We never output "at risk" as a verdict. We output the
> **factor checklist** behind it — escalations, slipped commitments, contact gap,
> unresolved issues, relationship owner left — each with receipts. The manager reads
> the checklist and makes the call. That's rule #1 in practice.

---

## 3. Per-surface contracts

The four existing screens are repurposed. Same containers, customer-centric content.

| Existing screen | Becomes |
|---|---|
| `Dashboard.tsx` | **Customer Risk Radar** — forward-looking, "what needs me in the next 15 days" |
| `LorePulse.tsx` | **Customer Signal Feed + Delay Trace** — the stream of customer facts, decisions, and "why it was delayed" |
| `KnowledgeMap.tsx` | **Account View** — drill into one customer: signals + timeline + the people on it |
| `ExpertiseMatrix.tsx` | **Account Coverage** — who owns which customer, what's exposed if they leave |

Each section below is written so LOR-98–102 can build with no shape re-litigation.

---

### 3.1 Customer Risk Radar  (`Dashboard.tsx`)

**The question:** *"What is going to affect me in the next 15 days, and which customers need me right now?"*

This is the home screen. It is **forward-looking** — not "what happened" but "what's
about to." A lead opens this Monday morning and sees the accounts that need attention
before they become fires.

It does **not** tell anyone what to do. It lists each at-attention account with the
**factors** that put it there, each factor backed by evidence. The lead decides whether
to call the customer, escalate, or let it ride.

#### Shape

```ts
interface RiskRadar {
  windowDays: 15;
  accounts: AccountRisk[];          // sorted by signal density, NOT by a score
  upcoming: UpcomingCommitment[];   // commitments due in the window
}

interface AccountRisk {
  customerId:   string;
  customerName: string;
  // The factors — facts, not a verdict. Each is independently clickable.
  factors: RiskFactor[];
  daysSinceLastContact: number;
  openIssues:           number;
  slippedCommitments:   number;
  ownerIds:             string[];   // people who hold this account
}

interface RiskFactor {
  kind: 'escalation' | 'slipped_commitment' | 'contact_gap'
      | 'unresolved_issues' | 'owner_departing';
  label:        string;             // human sentence, e.g. "Replied to last email after 9 days"
  evidenceRefs: string[];           // ids of source threads/tickets/decisions
  observedAt:   string;             // ISO
}

interface UpcomingCommitment {
  customerId:   string;
  customerName: string;
  title:        string;
  dueDate:      string;             // ISO, within next 15 days
  status:       'on_track' | 'at_risk' | 'blocked';   // derived from task state, not guessed
  ownerIds:     string[];
  evidenceRefs: string[];
}
```

> **No score field anywhere.** Accounts are ordered by how many factors are firing and
> how recent — never by an invented risk number. The header reads *"6 accounts have
> signals worth a look,"* not *"churn risk: high."*

#### Engine → gateway

```
GET /internal/analytics/risk-radar?window=15   → RiskRadar (scope-filtered)
GET /api/risk-radar                             Permission: pulse.view   (manager/admin)
```

#### Removed from the old Dashboard

`decisionsThisWeek`, `teamVelocity`, `onboardingReadiness`, `executionLag`,
`VelocityDocScatter`, `CollaborationChord` — all gone. They counted internal activity;
this screen is about customers.

---

### 3.2 Customer Signal Feed + Delay Trace  (`LorePulse.tsx`)

Two tabs, both customer-first.

#### Tab A — Signal Feed

**The question:** *"What's actually being said and decided about our customers, that I'd otherwise never see?"*

A reverse-chronological stream of customer-relevant facts, decisions, and escalations
pulled from real threads. This is the CEO's window into the ground truth — the things
employees know but never report upward.

```ts
interface SignalFeedItem {
  id:           string;
  customerId:   string;
  customerName: string;
  kind: 'decision' | 'escalation' | 'commitment' | 'expansion_ask' | 'fact';
  summary:      string;            // extracted, factual — no editorializing
  peopleIds:    string[];          // who was involved
  occurredAt:   string;            // ISO
  source:       'gmail' | 'slack' | 'jira';
  evidenceRefs: string[];          // click → the real thread/ticket
}
```

We surface the item and the receipt. We do **not** label it "good" or "bad" — an
`expansion_ask` next to an `escalation` for the same account is exactly the kind of
mixed picture a human needs to weigh themselves.

#### Tab B — Delay Trace

**The question:** *"Why was this delayed?"*

Pick a deliverable/commitment; get the reconstructed timeline of how it slipped —
when it was committed, what blocked it, which decisions changed it, who was involved.
Built entirely from timestamps and predicates already in the graph. We lay out the
chain of events; the manager draws the conclusion about accountability or process.

```ts
interface DelayTrace {
  commitmentId:   string;
  customerId:     string;
  customerName:   string;
  title:          string;
  committedDate:  string;          // original target
  currentDate:    string;          // latest target or actual
  slipDays:       number;          // currentDate - committedDate (a fact, not a fault)
  timeline:       DelayEvent[];    // ordered
}

interface DelayEvent {
  occurredAt:   string;            // ISO
  kind: 'committed' | 'blocked' | 'rescoped' | 'reassigned' | 'resolved';
  description:  string;            // what happened, factually
  peopleIds:    string[];
  evidenceRefs: string[];
}
```

> Delay Trace presents the sequence. It never names a culprit or says "this was
> avoidable" — the timeline plus the people involved is enough for a lead to run the
> conversation themselves.

#### Engine → gateway

```
GET /internal/analytics/signal-feed?customer=<id?>   → SignalFeedItem[]
GET /internal/analytics/delay-trace?commitment=<id>  → DelayTrace
GET /api/signal-feed     Permission: pulse.view   (manager/admin)
GET /api/delay-trace     Permission: pulse.view   (manager/admin)
```

#### Removed

Department headcount distribution, top-contributor leaderboard — vanity, not customer
signal. Gone.

---

### 3.3 Account View  (`KnowledgeMap.tsx`)

**The question:** *"Show me everything on this one customer — what's happening and who's on it."*

The old KnowledgeMap was a force-directed graph of the whole org — pretty, unusable.
Repurposed into a **single-account drill-in**: pick a customer, see the full picture.
This is the `customer → signal → people` chain made concrete for one account.

```ts
interface AccountView {
  customer:     CustomerSummary;
  signals:      SignalFeedItem[];   // recent activity on this account (reuses 3.2 shape)
  commitments:  UpcomingCommitment[];
  people:       AccountPerson[];    // who touches this account, and how
  timeline:     DelayEvent[];       // full interaction timeline
}

interface CustomerSummary {
  customerId:           string;
  customerName:         string;
  daysSinceLastContact: number;
  openIssues:           number;
  status:               string;     // factual lifecycle state if present, else omitted
}

interface AccountPerson {
  personId:     string;
  name:         string;
  role:         string;
  relationship: 'owner' | 'contributor';   // derived from volume/recency of involvement
  lastTouch:    string;             // ISO
  evidenceRefs: string[];
}
```

The graph view, if kept at all, is demoted to an optional "explore connections" toggle
inside this account view — not a standalone screen. Nobody navigates the business from
a node cloud.

#### Engine → gateway

```
GET /internal/analytics/account?customer=<id>   → AccountView (scope-filtered)
GET /api/account                                 Permission: query.ask
```

---

### 3.4 Account Coverage  (`ExpertiseMatrix.tsx`)

**The question:** *"Who holds this customer relationship — and what's exposed if they leave or go on leave?"*

This is the customer-flavored bus factor. The old expertise matrix scored people 0–100
on topics — fabricated. This instead answers a concrete risk question: which accounts
ride on a single person's head.

```ts
interface AccountCoverage {
  accounts: CoverageRow[];          // sorted by exposure (fewest holders first)
}

interface CoverageRow {
  customerId:    string;
  customerName:  string;
  holders:       CoverageHolder[];  // people with real evidence on this account
  soleHolder:    boolean;           // exactly one person — the exposure flag
  openItems:     number;            // what's in flight if they vanish
}

interface CoverageHolder {
  personId:     string;
  name:         string;
  evidenceCount: number;            // count of decisions/facts/threads — a fact, with receipts
  lastTouch:    string;             // ISO
  departing?:   boolean;            // true if known to be leaving (drives the alert)
}
```

We show "Account X: 1 holder (Priya), 7 open items." We do **not** say "high risk" —
the sole-holder fact and the open-item count are the alert; the manager decides whether
to cross-train, document, or accept it.

`evidenceCount` is a count of real content nodes (decisions/facts/threads) for that
person on that account — never a hand-tuned score. It exists to rank by *grounding*,
not to grade the person.

#### Engine → gateway

```
GET /internal/analytics/coverage   → AccountCoverage (scope-filtered)
GET /api/coverage                  Permission: expertise.view   (manager/admin)
```

---

## 4. Engine endpoints summary

All under `/internal/analytics/*`, internal-only, called by the gateway, read-only Neo4j.

| Endpoint | Returns | Surface |
|---|---|---|
| `GET /internal/analytics/risk-radar` | `RiskRadar` | Customer Risk Radar |
| `GET /internal/analytics/signal-feed` | `SignalFeedItem[]` | Signal Feed |
| `GET /internal/analytics/delay-trace` | `DelayTrace` | Delay Trace |
| `GET /internal/analytics/account` | `AccountView` | Account View |
| `GET /internal/analytics/coverage` | `AccountCoverage` | Account Coverage |

All receive scope params from the gateway (LOR-78). All are read-only.

---

## 5. Access posture

> Enforcement depends on LOR-78 (scope gate) and LOR-87 (gateway authority). Defined here.

| Surface | Permission | Who |
|---|---|---|
| Account View | `query.ask` | All authenticated users (scoped to accounts they touch) |
| Customer Risk Radar | `pulse.view` | Manager / admin |
| Signal Feed + Delay Trace | `pulse.view` | Manager / admin |
| Account Coverage | `expertise.view` | Manager / admin |

Customer signals about people's performance and account exposure are management-gated —
a viewer-role user shouldn't browse who's failing which account. All surfaces are
scope-filtered: a user only sees customers and content within their `allowedScopes`.

---

## 6. Explicitly out of scope (no data source — not faked)

- Churn probability / churn score  →  we show the **factors**, not a verdict
- Upsell likelihood score  →  we show the **expansion asks**, not a prediction
- Customer health % / sentiment score  →  needs sentiment extraction we don't have; use hard signals
- Revenue / ARR / contract value  →  lives in CRM/billing, not in ingested comms
- Per-rep activity counts, leaderboards, velocity, commit/doc counts  →  vanity
- Whole-org graph visualization as a standalone screen  →  demoted to per-account explore

If a future ticket wants a churn or health *metric*, it writes a new contract section
defining the data source first — it does not retrofit a number onto these surfaces.

---

## 7. The one rule that governs all of it

**Customer in, facts out, manager decides.** We trace every signal from the customer,
through the real data, to the people involved — and we stop there. We hand over the
evidence and the structure to read it. We never make the business call, because the
person in the chair will always make it better than we can. Our edge is that, for the
first time, they can see everything before they make it.
