# Slack Pipeline — Production Completion Plan

**Audience:** anyone finishing the Slack path from grouping → knowledge graph.
**Purpose:** a concrete, end-to-end, production-ready plan to redesign the Slack
half of the pipeline that was copied from Gmail, plus the rule for connecting
nodes **across** sources (Gmail / Slack / Jira) into one graph.

> Companion docs: [`SLACK_ACL_FOR_RBAC.md`](./SLACK_ACL_FOR_RBAC.md) (ACL),
> [`SLACK_PERSON_ID_RESOLUTION.md`](./SLACK_PERSON_ID_RESOLUTION.md) (identity).
> This doc covers **everything after fetch/filter/discovery** — which are done.

---

## 0. TL;DR

- Fetch, normalizer-level noise filtering, and ACL/identity **discovery** are
  done. Everything from **grouping → graph** is Gmail logic reused for Slack and
  must be redesigned.
- **8 work items**, in dependency order. Critical path: **W1 (sessionization)**,
  **W2 (body humanize)**, **W4 (identity wiring)** — these gate whether Slack
  produces *any* usable nodes. The rest refine quality and operations.
- **Cross-source rule (locked):** **MERGE the spine** (Person / Organization /
  Topic) across sources; **LINK content** (Decision / Task / Fact / Event) across
  sources with a soft edge — never merge content nodes, because each carries its
  own ACL scope and merging two scopes into one node is an RBAC leak.

---

## 0.1 Test-data reality — design for a real org, not the test workspace

The `techtest` ("Lore Test Workspace") corpus is **one person using two accounts
messaging themselves** across ~5 public channels. It is **not** an organization.
Concretely it has: 2 actors (one is the generator bot), **all-public channels**,
no org email domain, no private channels, and therefore **0 ACL principals by
design** (public content is workspace-wide — see `SLACK_ACL_FOR_RBAC.md` §5).

**Consequences — read before trusting any `techtest` result:**

- `techtest` validates **plumbing only**: does the pipeline run end-to-end without
  error, are session ids idempotent, does `scope_id` flow to `IN_SCOPE`, does an
  edit/delete delta apply. **Nothing about identity merging, private-channel ACL,
  cross-source connection, or extraction quality can be concluded from it.**
- **Do not let `techtest`'s data shape drive design.** "0 principals / no
  registry / 0 resolved persons" on `techtest` is a property of a degenerate toy,
  not a pipeline defect to design around.
- **Real acceptance (identity, ACL, quality) requires a real org** — e.g. EZO's
  Slack ingested alongside their Gmail, or any workspace with real named users and
  at least one private channel. Build the system for *that* shape; treat it as
  **untested-but-correct-by-design** until a real org is available.

This is why the acceptance gates (§6) are split into **Plumbing (testable on
`techtest` now)** and **Org-grade (requires real data)**.

---

## 0.2 ACL model — `scope_ids` array on the node (LOCKED 2026-06-06)

> **Supersedes the `AclScope`-node + `IN_SCOPE`-edge gate.** Older sections below
> still say "IN_SCOPE"; the **authoritative gate is now an array property**.
> Full contract for the backend: **`docs/RBAC/SCOPE_FILTER_HANDOFF.md`**.

First principles: the `AclScope` node + `IN_SCOPE` edge only earns its place if
**membership** also lives in the graph so visibility is a pattern-match. It does
not — the user→scope map lives in the **backend**. So the node was structure we
never traversed. We collapse it to two array properties on each knowledge node:

| Property | Bucket | Semantics | Filled by |
|----------|--------|-----------|-----------|
| `scope_ids` | OR (any-match) | Slack channels, Gmail recipients, Jira project | every session the node appears in, **unioned** |
| `scope_all_ids` | AND (all-match) | Jira stacked restriction (security level) | session `extra_scope_ids` |

- **Union, not first-write.** A node's scopes are unioned across **every** session
  it appears in (`unified_builder._collect_node_scope_sets`). So when dedup merges
  the same Project-X decision across `#sales` and `#devops`, the node ends up
  `scope_ids = ['slack:channel:sales','slack:channel:devops']` — a member of
  *either* channel sees it. This is exactly the array model:
  `scope: ['sales_channel','devops_channel']`.
- **Fail-closed:** empty/null `scope_ids` ⇒ matches nobody (the old guarantee).
- **Backend gate** = `any(scope_ids ∩ userScopes) AND all(scope_all_ids ⊆ userScopes)`.
- **Slack stores the container** (`slack:channel:C`) so membership stays **dynamic**
  (new hire sees history); **Gmail stores participants** (static — correct for mail).
  ⚠️ **Gmail `scope_ids` is not wired yet** → Gmail nodes are currently unscoped /
  invisible to scoped queries (flagged in the handoff doc).
- **Dedup is source-scoped** (W5): only merge within the same source, so a union
  never mixes a `slack:channel:` scope with a Gmail/Jira scope. Cross-source
  sameness → `CORROBORATES` **link**, never a merge (§4). The source guard is what
  keeps the scope-union safe.
- `AclScope` nodes from the Jira Pass-0 are now **vestigial reference data** (no
  `IN_SCOPE` edges point at knowledge nodes); removal is a later cleanup.

Implemented in `builders/unified_builder.py` (union + `scope_ids`/`scope_all_ids`),
W6 soft-delete empties both arrays (`builders/slack_delta.py`). Tests:
`infra/slack/tests/test_builder_scope_plumbing.py`,
`infra/jira/tests/test_stacked_scope.py`.

---

## 1. Modularity guidelines (follow these for every change)

The repo's layering is enforced (`pipeline/tests/test_layering.py`) and must hold:

1. **No `if source == "slack"` scattered in `pipeline/`.** Per-source behavior is
   a **policy object** in `common/` (the existing pattern: `common/sources.py`
   `SOURCE_CAPS` / `caps_for`). Grouping strategy, prompt variant, and dedup
   thresholds become per-source policy, selected by table lookup — not inline
   branching. One new field on the policy, one new value per source.
2. **`infra/slack/` imports only `common.*` + its own package** — never
   `pipeline/` or `builders/`. Normalizer/humanize logic that needs no pipeline
   state stays in `infra/slack/`.
3. **IDs and scope strings stay centralized.** Node IDs through
   `common/hashing.py:node_id_for`; ACL strings through `common/scopes.py`. No
   new ID/scope formula anywhere else (locked by `test_scopes.py`).
4. **One artifact registry.** New outputs go through `common/output_paths.py`
   (`KnowledgePaths` / `SlackPaths`) — no hardcoded paths.
5. **Each stage fails loudly on empty** (`common/stage_gate.py:check_nonempty`)
   and emits one `STAGE_SUMMARY` line (`common/stage_summary.py`).
6. **Simple first.** Prefer a per-source policy field + a small pure function over
   a new subsystem. Every item below is a localized change, not a rewrite.

---

## 2. Target data flow (Slack)

```
infra/slack (DONE)
  fetch → normalize (noise filter, ACL scope_id stamped) → discovery (scope_principals)
    → output/filtered/{org}_slack_*.jsonl
    → output/slack/{org}_scope_principals.json

pipeline (REDESIGN for Slack)
  W1 group_by_session  (channel + time-window, not thread_ts)
  W2 humanize body     (<@U…>/<#C…> → names, in normalizer)
  W4 identity wiring   (slack:U… → person_id; registry includes Slack people)
  → triple_extractor  (W3 Slack prompt variant)
  → validator          (passes once W4 lands)
  → embed_knowledge
  → deduplicator       (W5 Slack-calibrated thresholds, session-aware)
  → triples_deduped.json

builders
  → unified_builder    (IN_SCOPE edges from scope_principals; W7 input fix)
  → post_processor     (global topic clustering — already cross-source)
  W6 incremental edit/delete application (soft-delete + re-extract sessions)

cross-source (W-X)
  combined corpus → shared spine merge + content LINK edges
```

---

## 3. Work items

Each item: **what**, **where** (real symbols), **contract**, **why**, **done when**.

### W1 — Slack-native grouping (channel + time-window sessionization)  ⛔ critical

- **What:** Replace `thread_ts`-keyed grouping for Slack with **conversation
  sessions**: bucket a channel's messages by idle gap, then fold each
  `thread_ts` reply-tree into the session it belongs to. One session = one
  extraction unit (the Slack analogue of a Gmail thread).
- **Where:** `pipeline/extraction/grouping.py:group_by_thread`. Today it keys
  Slack on `raw.thread_ts → ts → source_id` (via `common/sources.py`
  `SOURCE_CAPS["slack"].thread_key_raw_fields`). Add a per-source **grouping
  policy** to `common/sources.py` (e.g. `group_strategy: "thread" | "session"`,
  `session_idle_gap_minutes`, `session_max_messages`). `group_by_thread`
  dispatches on the policy — no inline `if source==`.
- **Contract:** session id is deterministic and stable across re-runs:
  `slack:session:{channel_id}:{first_ts}`. Sessions carry `scope_id`,
  `source_ref`, `source_url` (already hoisted for Slack via `scope_in_raw`).
  Reply-trees never split across sessions. Cap very long sessions
  (`session_max_messages`, default ~40) by splitting on the largest idle gap so
  prompt size stays bounded (important now that the model is deepseek-v3.2 with a
  far smaller window than the old Gemini path).
- **Why:** Fixes both the **cost/rate blowup** (one LLM call per loose message →
  ~1 call per session; matters under Slack's 1 req/min non-Marketplace cap) and
  the **semantic gap** (a decision and its rationale in separate top-level
  messages now land in the same call).
- **Done when:** on techtest's 213 events, session count ≪ message count; a known
  multi-message decision appears in a single session; re-running yields identical
  session ids (idempotent).
- **Implementation status (2026-06-05): ✅ DONE.** Per-source policy on
  `common/sources.py:SourceCaps` (`group_strategy="session"`,
  `session_idle_gap_minutes=60`, `session_max_messages=40` — confirmed defaults; no
  `if source==` in the pipeline). `pipeline/extraction/grouping.py` dispatches: Slack
  events bucket by channel → `_sessionize_channel()` (gap-split roots, max-cap,
  reply-trees fold into their root's session, orphan reply acts as a root, scope +
  citation hoisted from the first event). Deterministic id
  `slack:session:{channel}:{first_root_ts}`. **Verified on real data: 213 events →
  6 sessions** (sizes 69/59/55/27/2/1); 8 unit tests
  (`pipeline/tests/test_sessionize.py`) + updated `test_grouping_source_caps.py`;
  full sweep 133 green.
  ⚠️ **V1 edge:** the max cap is on *root* messages — a single hot `thread_ts` with
  many replies folds in and can exceed 40 (keeps the reply-tree whole); sub-splitting
  a giant thread is a TODO. Gmail/Jira grouping unchanged.

### W2 — Humanize the message body before extraction  ⛔ critical

- **What:** Rewrite Slack markup so the LLM reads names, not IDs:
  `<@U08…>` → display name, `<#C…|general>` → `#general`, `<https://…|label>`
  → `label (url)`. Drop reaction-only / emoji-only fragments.
- **Where:** `infra/slack/normalizer.py:compose_message_body`. A `_MENTION_RE`
  already exists (`normalizer.py:27`) but is only used to *extract* mentioned IDs
  for actor linkage (`findall`, line 180) — it does **not** rewrite the body.
  Add a pure `humanize_text(text, user_cache, channel_cache) -> str`. `user_cache`
  is already threaded through `normalize_message`.
- **Contract:** pure function, no network; unresolved IDs fall back to the raw
  token (never crash). Keep the raw text in `raw` for provenance.
- **Why:** Without it the model sees `<@U08KEP3SKS>` and cannot attribute
  decisions/mentions → empty or wrong extractions.
- **Done when:** a message mentioning a user renders the canonical name in the
  extractor prompt; unit test covers user/channel/link/unknown-id cases.
- **Implementation status (2026-06-05): ✅ DONE.** `humanize_slack_text()` +
  `_user_display()` in `infra/slack/normalizer.py`; applied in
  `build_knowledge_event` right after `compose_message_body` (raw text preserved in
  `raw`). Rewrites `<@U…>`→@Name, `<#C…|name>`→#name, `<!here>`→@here,
  `<!subteam^…|@team>`→@team, `<…|label>` links→label; decodes `&amp;/&lt;/&gt;`;
  unresolved ids degrade to `@U…`/`#C…` (never raises). 9 unit tests
  (`infra/slack/tests/test_humanize.py`) + layering green; verified on real
  techtest bodies.

### W3 — Slack-specific extraction prompt variant

- **What:** A Slack framing + few-shots, parallel to the existing `jira_hint`.
- **Where:** `pipeline/extraction/prompts.py`. `build_thread_prompt` already
  branches `is_jira = source == "jira"` and prepends `jira_hint`. Add
  `is_slack` + `slack_hint`. **Keep the ontology and `SYSTEM_PROMPT` body
  unchanged** — only the framing block and examples differ. To avoid a third
  inline branch later, lift the hint selection into a tiny per-source map keyed by
  `source` (policy, not `if/elif` chains).
- **Slack hint must convey:** this is a **channel conversation**, not an email
  chain (no subject/From/Date semantics); a decision may be spread across short
  messages and its answer may be in a thread reply; **short standalone messages
  usually contain no extractable knowledge → return empty triples**; suppress
  reactions/"+1"/"lol"/"👍" as non-knowledge. Provide 2 Slack few-shots: (a) a
  decision reached across several messages, (b) a noise-only session → empty.
- **Why:** Current prompt says "email threads" and every few-shot is an email;
  Slack reuse causes both over-extraction of trivia and missed decisions.
- **Done when:** on a hand-labeled set of **real-org** sessions, decisions are
  caught and noise sessions yield empty — measured, not eyeballed (feed to the
  `evaluation/` rounds). `techtest` can only confirm the Slack prompt is selected
  and produces well-formed output, not that extraction is *good* (§0.1).
- **Implementation status (2026-06-05): ✅ DONE (plumbing).** `SLACK_HINT` +
  `JIRA_HINT` constants + `SOURCE_HINTS` map in `prompts.py` (the only place a
  source attaches a hint — no inline `if/elif`). `build_thread_prompt` is now
  source-aware: Slack → channel-session framing (`CHANNEL SESSION` header,
  `Message` units, no Subject line, "return empty if no knowledge" closer) +
  `SLACK_HINT` (channel-conversation framing, ignore reactions/chit-chat, 1 decision
  few-shot + 1 empty few-shot). Gmail/Jira framing unchanged. 4 lock tests
  (`pipeline/tests/test_prompt_framing.py`); full sweep 137 green.
  🚩 **UNTESTED:** extraction *quality* (precision/recall) — needs a real-org labeled
  set + LLM creds; only framing/selection is verified.

### W4 — Identity wiring so the validator accepts Slack people  ⛔ critical / BLOCKER

- **What:** Make `slack:U…` actors resolve to a `person_id` with a canonical
  name, and ensure Slack people are present in the registry the validator checks.
- **Where:** the in-progress, **uncommitted** `pipeline/identity/`
  (`resolver.py`, `source_identity.py`, `resolve.py`). Three concrete seams:
  1. **Person source = the Slack directory + event actors, NOT
     `scope_principals`.** `scope_principals.principals[]` only lists members of
     **private** channels; a public-only or DM-light org yields few/zero
     principals (verified: `techtest` has 0). The authoritative list of *who the
     people are* is the **workspace directory** — `users.list` (bot) /
     `admin.users.list` (Grid) — plus the **actors already on every event** (each
     Slack event carries `actor.id = slack:U…`, `display_name`, often `email`).
     Persist the directory during discovery (e.g. `SlackPaths.directory`) and
     feed people from there. `scope_principals` remains the source of **ACL
     grants** (private-channel membership), not the person registry.
  2. `pipeline/extraction/grouping.py:load_person_registry` already augments the
     registry from **Jira** discovery principals — add the Slack equivalent:
     ingest the Slack directory/actors → register `slack:U… → person` (map both
     the full `slack:U…` and bare `U…`), mint a fallback person when an actor has
     no email (mirror the Jira fallback block; the generator bot is the canonical
     no-email case).
  3. `pipeline/extraction/prompts.py:build_thread_prompt` / `_get_sender_name`
     resolve participants via `email_to_person.get(addr)` where
     `addr = primary_actor.replace("email:","")`. Generalize so a `slack:U…`
     primary actor resolves through the same map (don't assume `email:`).
- **Contract:** every internal Slack participant shows in the roster as a named
  INTERNAL principal; the validator's "Person must exist in registry" check
  passes. Org membership is keyed off the **org's Slack team/email domain**, not a
  hardcoded assumption; external `#EXT#`/foreign-team users stay external (already
  handled in discovery `_is_external_principal`).
- **Why:** Today Slack actors don't match the Gmail-built, email-keyed registry →
  roster shows `EXTERNAL, UNKNOWN` → the validator **rejects every Slack Person
  triple**. Empirically the whole chain is currently dead: no person registry,
  0 principals, resolver outputs `persons: []` — purely a wiring gap (the names
  are present on 213/213 events). Nothing usable survives without this.
- **Done when (org-grade, real data):** running the validator on Slack-extracted
  triples rejects ~0 triples for `unresolved_person`; the centralized resolver
  returns one `person_id` for a human seen on both Gmail and Slack. **On
  `techtest` this only proves the wiring runs** (2 self-accounts is not an
  identity test) — see §0.1.

- **Implementation status (2026-06-05):**
  - ✅ **Gap B DONE — consumer cutover wired.** New bridge
    `pipeline/identity/registry_view.py`:
    `identity_to_registry()` converts `identity.json` → the legacy registry shape;
    `write_resolved_registry()` (called at the end of `pipeline.identity.resolve`)
    emits `output/identity/{org}_resolved_registry.json`; `registry_source()`
    makes the **validator** (`validation/cli.py`) and **extractor**
    (`extraction/grouping.py`) prefer it, falling back to the Gmail
    `person_registry.json` when no identity run exists. Verified: converter unit
    smoke (email + `slack:U…` lookups, incl. the extractor's lowercasing), techtest
    end-to-end (resolve → resolved_registry → chooser picks it), **ezobooks still
    falls back to the legacy registry (Gmail path untouched)**, layering + scopes
    locks green.
  - ⏭️ **Gap A SKIPPED (by decision) — build for Grid.** The Grid path already
    persists the full `admin.users.list` directory as `principals[]`
    (`discovery.py:260-268`), so the identity adapter has its fuel on the paid
    plan. The bot-fallback directory-persistence fix (the ~1% poor-client path) is
    intentionally **not** done — see §0.1 / "Fallback testability".
  - 🚩 **UNTESTED:** the Grid path end-to-end (no Grid token). `from_slack` /
    `admin.py` / `discovery_api.py` field shapes remain `# ASSUMED` — verify
    against the first real Grid client. On `techtest` the resolver yields 0 persons
    (all-public, fallback skipped), so identity output there is empty *by design*.
  - ⚠️ **Known degradation:** `identity.json` carries no per-message activity
    counts, so `build_org_context` (top-internal-by-volume) produces an empty block
    on the resolved-registry path. Acceptable for Grid; TODO if richer org context
    is wanted.
  - **Pipeline order:** `person_registry` (Gmail) + `discovery` (Slack/Jira) →
    **`pipeline.identity.resolve`** → extraction → validation. The identity stage
    must run before extraction for the cutover to take effect.

### W5 — Dedup calibrated and session-aware for Slack

- **What:** Make the same/cross threshold switch use **session** (not
  `thread_ts`) as the "same context" signal, and set Slack-specific thresholds.
- **Where:** `pipeline/deduplicator.py`. The same-thread (0.92) vs cross-thread
  (0.96) switch keys on `node["thread_id"]`; with W1 that becomes the session id,
  which is the correct "same conversation" signal for Slack. Run `--calibrate` on
  real Slack embeddings and record Slack thresholds (Slack text is shorter →
  higher baseline cosine → thresholds usually need to go **up**, not down).
- **Same-source guard (LOCKED, RBAC-critical):** dedup must **only merge nodes
  within the same source.** Today `cluster_nodes_by_type` blocks by node *type*
  only, so it could merge a Slack node with a Gmail/Jira one — which would union
  their scopes (§0.2) across sources and collapse two ACL domains into one node =
  RBAC leak. Add a source dimension to the blocking (derive source from the node's
  `thread_id`/`source_thread_id` prefix: `slack:session:` vs Gmail thread vs
  `jira:`). Cross-source sameness is handled by the `CORROBORATES` **link** (§4),
  never a merge. This guard is what makes the scope-union safe.
- **Contract:** thresholds are config/CLI, not hardcoded; calibration output is
  saved alongside the run for auditability. Merge only within `source`. Keep the
  date-block and single-linkage caveats from the main dedup plan.
- **Why:** With loose messages each their own `thread_ts`, today *everything* is
  "cross-thread" (stricter) → Slack paraphrases under-merge. Session keys fix the
  signal; calibration fixes the numbers; the source guard keeps the merge inside
  one ACL domain.
- **Done when:** dedup never merges across sources (test); calibration table for
  Slack recorded; spot-check top-5 cross-session pairs shows no false merges at the
  chosen threshold.
- **Implementation status (2026-06-06):**
  - ✅ **Same-source guard DONE + tested.** `pipeline/deduplicator.py`:
    `_thread_source` (scope_id/thread_id prefix → slack/jira/gmail), `_node_source_map`
    (node_id→source from validated), and a `same_source` mask AND-ed into the edge
    matrix in `cluster_nodes_by_type` (new `source_by_node_id` param, passed from
    `run_deduplication`). Cross-source pairs can never form a merge edge. Tests:
    `pipeline/tests/test_dedup_source_guard.py` (5) — incl. a control proving the
    guard, not the threshold, is what separates identical cross-source content.
    Full sweep 190 green.
  - ✅ **Session-aware** — free from W1: `thread_id` is now the session id, so the
    same/cross-thread threshold switch already keys on "same conversation".
  - ⏳ **Threshold calibration — PENDING (data-dependent).** Needs real Slack
    embeddings; `techtest` is too degenerate (2 actors → 6 sessions) to yield
    meaningful numbers. Run `pipeline.deduplicator --calibrate` on a real org's
    Slack (EZO) and record the table. Until then dedup uses the Gmail-tuned
    0.92/0.96 — likely **over-merges** short Slack text, so this is **untested for
    Slack quality**. Per-source threshold config is a follow-up once numbers exist.

### W6 — Incremental edit/delete application to the graph

- **What:** Apply the reconcile delta to the graph without a full re-run.
- **Where:** `runners/slack_reconcile.py` already emits a delta JSONL (edited →
  new body; deleted → tombstone keyed by `source_id`). Add a builder pass that:
  **deletes** → soft-delete the affected nodes/edges in Neo4j (mark
  `deleted_at`, empty `scope_ids` so they fail-closed out of results — see §0.2);
  **edits** → re-extract only the affected **session**, re-mint nodes, orphan the
  superseded ones (reuse the dedup remap machinery).
- **Contract:** idempotent (re-applying the same delta is a no-op); soft-delete
  not hard-delete (audit + reversible); bounded to changed sessions (never O(history)).
- **Why:** Closes the loop built in the reconcile work; keeps the graph fresh
  without re-ingesting. On Grid this is the Discovery change-stream instead of
  snapshot-diff (see `capabilities.editdelete_pathway`) — same builder pass
  consumes either delta.
- **Done when:** edit one message + delete one message on techtest → graph shows
  the edited node's new content and the deleted node gone from query results,
  re-running the delta changes nothing.
- **Implementation status (2026-06-06): ✅ DONE (logic), live e2e pending.** Unit
  of update = the **session** (`source_thread_id`), since nodes are session-granular.
  - `builders/slack_delta.py` (graph side, pure + tested): `affected_sessions_from_delta`,
    `plan_delta` (diff old vs re-extracted session nodes → soft-delete removed,
    upsert present), `emit_soft_delete_cypher` (tombstone `deleted_at` + DROP
    `IN_SCOPE` → fail-closed, node kept for audit), `emit_delta_cypher` (reuses
    `generate_knowledge_cypher` for upsert — identical shape to the full builder),
    `write_delta_cypher`, `apply_delta_cypher`.
  - `runners/slack_apply_delta.py` (orchestrator): reads latest
    `reconcile_delta_*.jsonl` → affected sessions → **re-extract only those**
    (pluggable `reextract_fn`; real LLM path isolated in `_default_reextract`) →
    `plan_delta` → write `{org}_slack_delta.cypher` → optional `--apply` to Neo4j.
    Bounded to changed sessions (never O(history)); idempotent (deterministic ids).
  - Tests: `pipeline/tests/test_slack_delta.py` (7) + `test_apply_delta_orchestrator.py`
    (2, fake re-extractor) — full sweep 146 green.
  - ✅ **LIVE e2e VERIFIED (2026-06-06)** on techtest with Bedrock + Neo4j: delta
    (2 records) → 1 affected session → re-extract (8 nodes/9 edges) → 17 statements
    applied, summary `{affected:1, soft_delete:0, upsert_sessions:1}`. Caught + fixed
    a bug: `_default_reextract` kept `NodeType` str-enums in memory, so the delta
    Cypher emitted `MATCH (s:NodeType.DECISION …)`. Fix = JSON-launder the
    re-extracted dict (`_jsonable`), mirroring the full pipeline's file write.
    Regression test in `test_apply_delta_orchestrator.py`.
  - ⚠️ **V1 limits:** assumes the latest Slack events file reflects current state
    (post-fetch); stale edges among surviving nodes aren't pruned (removed nodes are
    tombstoned + `scope_ids` emptied, so they're invisible); a deleted message in a
    multi-message session is handled by re-extracting the whole session.

### W7 — Fix two consistency bugs (small, do early)

- **What:**
  1. `builders/post_processor.py:516` reads `triples_validated` while
     `builders/unified_builder.py:171` reads `triples_deduped` (else validated).
     Align both on the deduped artifact so post-processing and the graph agree.
  2. Stale comment in `prompts.py` ("Gemini's 1M context window") — model is now
     `deepseek-ai/deepseek-v3.2` (smaller window). Fix the comment; it informs
     W1's `session_max_messages` cap.
- **Why:** Otherwise topic clustering / expertise / temporal edges run on
  un-deduped nodes — duplicates leak into the final graph.
- **Done when:** both builders read the same source; grep shows no "Gemini" / "1M"
  context claims.
- **Implementation status (2026-06-05): ✅ DONE.** `post_processor.py` now reads
  `triples_deduped` (else validated), matching `unified_builder`, and uses the W4
  `registry_source()` chooser for the registry. Stale Gemini/1M comment in
  `prompts.py` replaced with the deepseek-v3.2 + bounded-prompt note. Imports +
  layering green.

### W8 — End-to-end validation (two beds: plumbing now, org-grade later)

- **W8a — Plumbing smoke test on `techtest`. ✅ RAN e2e 2026-06-06** (Bedrock
  `moonshotai.kimi-k2.5` + NVIDIA NeMo embeds + local Neo4j). Full chain on 213
  events: **6 sessions → 92 triples (0 errors) → 84 validated → 75 embedded →
  dedup (source guard `{slack:75}`, 0 cross-source merges) → 49 knowledge nodes +
  84 edges built & applied → 75 embeddings uploaded (100% matched).** Verified the
  **scope_ids gate in Neo4j**: a member of `slack:channel:C0B7MG7DQS1` sees 24
  nodes, an outsider sees 0, and the 27,005 pre-existing unscoped EZO nodes leak to
  0 users (fail-closed). W6 delta applied live (above). **Does not measure
  extraction quality or identity** — `techtest` is two self-accounts (§0.1).
  - **Bugs caught + fixed by running it:** (1) `unified_builder.load_inputs` still
    read the legacy `person_registry` directly — missed the W4 `registry_source()`
    cutover; now fixed. (2) W6 delta enum leak (see W6). 
  - 🐞 **Latent issues filed (not blockers):** pass2 Cypher chunks are **not
    org-namespaced** (`pass2_chunk_NNN_knowledge.cypher`) and the builder doesn't
    clean the cypher dir → a prior run's stale chunks get re-applied (worked around
    here by moving 278 stale files to `output/_cypher_stale_backup/`). And
    `.env`'s `NEO4J_URI` lacks the `bolt://` scheme (overrode at the CLI). Both
    worth a proper fix before multi-org production.
- **W8b — Org-grade validation (requires a real org).** The real acceptance bed:
  a workspace with real named users and ≥1 private channel — ideally EZO's Slack
  ingested **alongside** their Gmail so cross-source spine-merge is exercised.
  Asserts: decisions caught (precision/recall on a labeled set via `evaluation/`),
  ~0 `unresolved_person` rejects, private-channel ACL principals resolved and
  fail-closed, one `person_id` for a human across Slack+Gmail, `CORROBORATES`
  links (not merges) for cross-source content.
- **Done when:** W8a checklist passes and numbers are recorded for regression;
  W8b is scheduled against the first real org and its gates (§6 Org-grade) are
  green there. Treat the system as **untested-but-correct-by-design** between W8a
  and W8b.

---

## 4. Cross-source connection (the rule, locked)

**Your instinct ("dedup happens within source") is half-right.** In code,
`deduplicator.py` is **not** source-scoped — it clusters by node *type* only and
would merge across sources if nodes coexisted in one run. What actually keeps
sources apart today is that each source writes the **same**
`{org}_triples_validated.json`, so separate runs overwrite instead of combine.

**The rule we adopt (RBAC-safe):**

| Node class | Across sources | Mechanism |
|------------|----------------|-----------|
| **Person** | **MERGE** | centralized identity → one `person_id` for `email:`/`slack:U…`/`jira:U…` |
| **Organization** | **MERGE** | canonicalization (see gap below) |
| **Topic** | **MERGE** | `post_processor` HDBSCAN+LLM clustering — already global ✅ |
| **Decision / Task / Fact / Event** | **LINK, never merge** | soft `CORROBORATES` edge when cross-source similarity is high |

**Why content is LINK-not-MERGE:** RBAC gates **content** nodes and never the
spine (per `SLACK_ACL_FOR_RBAC.md`). A Slack Decision carries `slack:channel:C…`;
the "same" decision in email carries a Gmail scope. Merging them collapses two
ACL scopes into one node → a user with email access could see Slack-scoped
content (or vice versa). Keeping them separate but **linked** preserves both each
node's scope/citation **and** the cross-source connection.

**Why the spine is the real connective tissue:** once Ravi is one Person,
"Razorpay Integration" is one Topic, and "EZO" is one Org, a Slack decision and an
email decision that both `DECIDED→Ravi` + `REGARDING→Razorpay Integration` are
**already connected through shared nodes** — no content merge, no ACL risk.

**W-X status: CORROBORATES content edge ✅ DONE 2026-06-06.**

Item 4 (the CORROBORATES content edge) is implemented and tested:

- **`pipeline/deduplicator.py`** — `cluster_nodes_by_type` now returns
  `(clusters, corroborates_pairs)`. When the source guard blocks a cross-source
  merge (high similarity but different `source_a`/`source_b`), the pair is
  captured as a CORROBORATES candidate instead of being silently dropped. Pairs
  are sorted lexicographically by `node_id` for idempotency. Only
  `_DEDUP_TYPES` (Decision/Task/Fact/Event) — not Person/Org/Topic (spine nodes).
  `run_deduplication` accepts an optional `corroborates_path` and writes
  `{org}_corroborates.json`.
- **`builders/unified_builder.py`** — `generate_corroborates_cypher(pairs)` emits
  MERGE Cypher for `CORROBORATES` edges. `run_builder` loads the pairs file (if
  present) and writes `pass2_corroborates.cypher`. Applied after all pass2
  knowledge chunks (so both endpoints exist). Edge carries `similarity`,
  `source_a`, `source_b`.
- **`common/output_paths.py`** — `KnowledgePaths.corroborates` property added.
- **Tests** — `pipeline/tests/test_corroborates.py` (10 tests): high-sim
  cross-source captured, below-threshold not, same-source → merge not
  CORROBORATES, spine types skipped, 3-source all-pairs, lexicographic order,
  Cypher shape, id-field per type. `test_dedup_source_guard.py` updated to unpack
  tuple + assert corroborates populated. **201 tests pass.**

**What still needs a real combined corpus (open questions):**

1. **Combined per-org corpus.** Today the single `triples_validated` path makes
   separate per-source runs overwrite each other. Extract all sources into one run,
   or write per-source-namespaced triples files and add an explicit merge step
   before `embed_knowledge` — so dedup/embeddings see every source together and the
   CORROBORATES pass actually fires on real cross-source pairs.
2. **Centralized identity** (W4) — the #1 cross-source connector.
3. **Organization canonicalization** — the #1 cross-source **gap** (none today;
   name-keyed hash merges only *exact* strings, so "EZO"/"EZO Technologies"
   fragment). Add an Org-canon step analogous to topic clustering.
4. **`CORROBORATES` threshold calibration** — currently inherits dedup's
   `cross_thread_threshold` (default 0.96). May need to be stricter or looser for
   cross-source assertion once a real combined corpus is available.

---

## 5. Sequencing & dependencies

```
W7 (quick fixes)            ─┐
W2 (humanize body)          ─┼─ no deps, do first
W1 (sessionization)         ─┘   ← unlocks W3, W5
W4 (identity wiring)        ───── BLOCKER for any usable Slack output
W3 (Slack prompt)           ── needs W1, W2
W5 (dedup calibration)      ── needs W1 (+ real embeddings)
W6 (edit/delete apply)      ── needs graph build working
W8a (plumbing smoke)        ── needs W1–W6; runs on techtest now
W8b (org-grade validation)  ── needs a real org; the true production bar
W-X (cross-source)          ── ✅ CORROBORATES edge DONE 2026-06-06 (see §4); combined-corpus wiring pending real org
```

Recommended order: **W7 → W2 → W1 → W4 → W3 → W5 → W6 → W8a → W-X**, with **W8b**
run against the first real org as soon as one is available (it gates production).

---

## 6. Acceptance gates

Split by what each test bed can actually prove (§0.1). **Plumbing** is testable on
`techtest` now; **Org-grade** requires a real org and is the true production bar.

### Plumbing (testable on `techtest` now)
- [ ] **Grouping:** sessions ≪ messages; reply-trees intact; idempotent session ids.
- [ ] **Humanize:** no raw `<@U…>`/`<#C…>` reaches the LLM; unit-tested.
- [ ] **Run-clean:** extractor → validator → embed → dedup → builder runs without error.
- [ ] **ACL plumbing:** every content node gets a non-empty `scope_ids` array (§0.2); fail-closed (empty array) on unresolved scope.
- [ ] **Edit/delete:** delta applies idempotently; re-apply is a no-op; deletes fail-closed out of results.
- [ ] **Consistency:** both builders read `triples_deduped`; no stale model comments.
- [ ] **Layering:** `test_layering.py` + `test_scopes.py` pass; no `if source==` in `pipeline/`.

### Org-grade (requires a real org — the production bar)
- [ ] **Identity:** ~0 `unresolved_person` rejects; one `person_id` for a human across Slack+Gmail.
- [ ] **Prompt quality:** measured precision/recall on a labeled real-org session set (via `evaluation/`).
- [ ] **Dedup:** Slack thresholds calibrated on real embeddings + recorded; no false merges in spot-check.
- [ ] **Private ACL:** private-channel principals resolved (membership/Discovery); fail-closed when unresolved.
- [ ] **Cross-source:** spine merges, content links via `CORROBORATES`; no scope collapse.

---

## 7. Risks & rollback

- **Session over-merge** (one session swallows unrelated talk) → bound by
  `session_idle_gap_minutes` + `session_max_messages`; calibrate the gap on real
  channels; split on largest idle gap when capped.
- **Identity over-merge** (two humans → one `person_id`) → keep the resolver's
  tiered matching conservative; fail-open to separate people, never guess-merge.
- **Cross-source content merge by accident** → enforced by the LINK-not-MERGE
  rule: dedup remap only within same `source`+scope; different source ⇒ edge only.
- **Edits re-mint storms** → bound re-extraction to changed sessions; soft-delete
  is reversible (drop `deleted_at`).
- Each work item is a localized, separately-revertable change; no big-bang.

---

## 8. Open questions (remaining after W-X implementation)

- **Combined corpus wiring:** single extraction run over all sources, or per-source
  files + explicit merge step before `embed_knowledge`? (Leaning per-source files +
  merge — keeps source runs independent and resumable.) Until this is wired, the
  CORROBORATES pass never fires in production (single-source runs have no
  cross-source pairs).
- **Org canonicalization:** reuse the `post_processor` HDBSCAN+LLM machinery, or a
  lighter alias/normalization table seeded from discovery directories?
- **`CORROBORATES` threshold calibration:** currently inherits `cross_thread_threshold`
  (default 0.96). The question of stricter vs same is deferred to real combined-corpus
  data — run `deduplicator --calibrate` on a combined Gmail+Slack corpus to decide.
