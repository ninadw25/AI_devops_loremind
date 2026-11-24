# Onboarding — build doc (claims substrate)

**Build order: fourth** (consumes offboarding packages, the expertise view, and the elysia-rbac scope-grant mechanism).
**Product/UX reference:** workspace `docs/01-onboarding.md` — the golden rule stands: **structured curated content first, chat on the side.** A new joiner doesn't know what to ask yet.

---

## 1. The new joiner's actual first month

What a new hire at an SMB actually does: asks a teammate, waits, asks again, absorbs folklore, and repeatedly discovers that "how it's done" changed three months ago and nobody updated anything (there was nothing to update). The three questions that dominate their ramp:

1. **"How does this work here?"** — process/current-state knowledge.
2. **"Why is it like this?"** — the rationale behind non-obvious arrangements. This is the question documentation never answers and colleagues answer badly ("historical reasons").
3. **"Who do I ask?"** — per topic, who actually knows.

The claims substrate is unusually well-shaped for exactly these three — that's the pitch of this feature, and each maps to a card type below.

## 2. Why the new ontology changes what's buildable

- **Cards finally render.** The old graph's nodes were meaningless outside a traversal (INGEST 00 §F1) — "Vendor Quotation", "Scheduled meeting at 3 PM". A curated card built on that substrate would have been a list of context-free strings. Claims are self-contained: a card is literally a list of statements a newcomer can read cold. This feature is the F1 fix's showcase.
- **The newcomer never learns stale truth.** Every card serves SUPERSEDES chain heads (V3 §4), with "revised from X in June" history attached. On the old substrate, onboarding would confidently teach the March process that changed in June — arguably worse than no onboarding.
- **"Why is it like this?" is a query now.** Decision claims + BECAUSE edges → each key decision card shows the rationale chain. No competitor gets this from passive capture.
- **The glossary is free.** The topic registry (curated names + alias lists + anchor containers) *is* the org's vocabulary — "PO", "scalpel mode", project codenames. Rendered as a glossary card with each term's top corroborated claims.

## 3. The package model

A package is a **manager-approved, frozen bundle**: claim ids + people map + open commitments + topic set, minted with a scope grant and an expiry (default 60 days).

Two creation paths (the second is the one the old spec missed):

1. **From a predecessor** — the offboarding successor package (doc 02 §4), draft-generated, manager prunes and approves.
2. **From a role** — no predecessor (the common case in a *growing* SMB: the role is new). Manager picks topics from the registry + optionally "people like this role" (seed persons whose claim neighborhoods to draw from); the same package builder assembles it. Both paths converge on one format.

### Card sections (all precomputed at package build, all drill to evidence)

| Card | Contents | Source |
|---|---|---|
| **How things work** | current-truth Process/Fact claims per package topic, grouped, most-corroborated first | L3 + SUPERSEDES |
| **Key decisions & why** | Decision chain heads with BECAUSE rationale and revision history | L3 claims + edges |
| **People you should know** | per topic: top `ask`-facet experts + predecessor's top collaborators + external counterparties with context ("your vendor contact for X since 2024") | `expertise` + L2 |
| **Open threads / unfinished work** | open commitments inherited from the predecessor or attached to the role's topics | `open_commitments` |
| **Glossary** | registry topics + aliases + one defining claim each | topic registry |
| **Living FAQ** | the package topics' most *re-stated* claims (highest corroboration count = the things people repeatedly explain = the things newcomers ask) | L3 corroboration |

## 4. The hard part: scopes (kept hard, solved once)

A new hire has **no scopes** — they were on none of the threads. Fail-closed means their chat and cards see nothing. The old spec's answer stands and gets built properly here:

- Manager approval of a package **mints a grant** in elysia-rbac: `onboarding:<pkg_id>` enters the hire's allowed-scope set, time-boxed to the package expiry. The grant exposes **exactly the frozen claim set** — not the predecessor's scopes, not topic-wide access.
- **You cannot grant what you cannot see:** the package builder runs against the *approving manager's* visibility (plus their aggregate authority for scores, per README policy); claims outside the manager's view can't enter the package. This keeps the grant a delegation, never an escalation.
- No "onboarding bypass" of any kind. Expiry is enforced in elysia-rbac (Plane B), not in the frontend.
- Organic accretion continues in parallel: the hire joins channels and threads, gaining real scopes; by expiry, the training wheels are gone naturally.

## 5. Scoped chat + progress

- The chat box on the onboarding page is the normal V3 pipeline with a `package_focus=<pkg_id>` param: Lane A seeds constrained to the package claim set (a simple id-set predicate — no new engine, no extra LLM call). Same grounded citations; "ask about Priya's work" answers cite the same cards the hire is reading.
- Progress = per-card read/done state + week-by-week checklist (a small table + frontend; no pipeline impact). Manager sees progress **by section, not by reading-minutes** — ramp support, not monitoring (README rule).

## 6. Benchmarks

On the T1 synthetic org: package completeness (latent topics of the role covered ≥ 0.8), staleness = 0 (no superseded claim served as current — the E7 stale-answer gate applied here), scope containment exact (package grant exposes precisely the frozen set; adversarial probes leak nothing), FAQ sanity (top-corroborated claims match the generator's "frequently restated" set).

## 7. Build checklist

- [ ] Package format + builder (`services/onboarding_service.py`): both creation paths → one frozen bundle in Postgres.
- [ ] elysia-rbac coordination: `onboarding:<pkg_id>` grant type with expiry + the claim-set scope resolution (needs an elysia-rbac counterpart task).
- [ ] Card endpoints (`GET /api/onboarding/{pkg_id}/…`) reading the frozen bundle.
- [ ] `package_focus` param through gateway → QueryService → Lane A predicate.
- [ ] Frontend: landing dashboard (cards + drill-in + scoped chat + progress), per the old spec's wireframe.
- [ ] Package expiry job + "graduation" nudge (expiring package → suggest real channel/scope memberships to the manager).

**Don't:** build a second RAG engine; grant scopes wider than the frozen claim set; ship cards that serve superseded truth; turn progress into surveillance.
