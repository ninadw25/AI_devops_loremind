# Jira Pipeline Redesign — First Principles

> **Status:** DESIGN (master). This is the controlling design doc for the Jira
> knowledge pipeline. It supersedes the email-shaped assumptions baked into the
> shared pipeline for Jira's lane. Detailed specs live in companion docs:
> `JIRA_ONTOLOGY.md` (node/edge model), `JIRA_EXTRACTION_AND_DEDUP.md`
> (field-map + system prompt + dedup rules), `JIRA_PERSON_ID_RESOLUTION.md`
> (identity emission), `JIRA_ACL_FOR_RBAC.md` (scope/RBAC).
>
> **Decisions locked (2026-06-05):** (1) Architecture = *shared spine, Jira-native
> stages* — keep the staged pipeline and `KnowledgeEvent` as the cross-source
> spine, but route Jira through its own deterministic-extraction and key-based
> dedup stages selected by `source`. (2) Docs before code.

---

## 1. Why Jira needs its own thinking

The existing pipeline was built for **Gmail** — unstructured conversation. In
email you do **not** know who decided what, what the tasks are, or who owns
them; the entire `Decision / Task / Fact` ontology and the LLM triple-extractor
exist to **recover** structure that was never explicit. For email, the LLM does
*everything*.

**Jira is the inverse.** The structure is already machine-readable and
authoritative:

| Jira gives you, as a field | Email makes you infer |
|----------------------------|------------------------|
| `issuetype` (Task/Story/Bug/Epic/Sub-task) | what kind of thing this is |
| `assignee` — **who owns it** | who's responsible |
| `reporter` — who raised it | who's speaking |
| `status` + full `changelog` — the lifecycle, with who/when | whether anything happened |
| `priority`, `story points`, `labels`, `components`, `fix version`, `sprint` | importance, grouping, timing |
| `parent` / Epic Link / `issuelinks` (blocks, relates, duplicates, subtask) | how things relate |

### The reframe (the one idea this doc exists to state)

> **For Jira, ~80% of the graph is built *deterministically from fields — no
> LLM*. The LLM only reads the genuinely unstructured part (descriptions +
> comments) to extract the *latent* knowledge that no field captures: decisions
> reached in discussion, rationale, blockers/risks raised, commitments made.**

This is not more complexity — it is **less**. It aligns with the project's
north star ("simple yet modular and robust"):

- **Cheaper & exact:** field→edge mapping is deterministic, free, not rate-limited,
  and never hallucinates. The 40 RPM NVIDIA cap stops being the bottleneck.
- **Dedup mostly disappears:** every issue has a globally unique stable key
  (`ENG-123`). There is no cross-thread duplication of issues to chase, so the
  expensive, risky embedding-clustering dedup is **not needed** for Jira's
  structured core. The key *is* the dedup.
- **Correct by construction:** the assignee edge comes from the assignee field,
  not from an LLM guessing at a sentence.

The LLM stops being the engine and becomes a **scalpel** for prose.

---

## 2. Current state — what's built, what survives, what changes

What actually exists today (verified against code + the real `loremind` fetch of
158 issues / 449 events / 5 projects):

| Component | State | Verdict |
|-----------|-------|---------|
| `infra/jira/` fetcher / discovery / run | Built, works | **Keep** |
| ACL discovery (3-phase, stacked scopes, `scope_principals.json`) | Built, works | **Keep** (+ narrow fixes, §8) |
| `infra/jira/normalizer.py` → `KnowledgeEvent` | Built; **preserves** reporter+assignee `actors`, `project`, `tags` (labels+components), `parent_id`, `related_ids` (epic), `sprints`, `scope_id`, `extra_scope_ids`, attachments, **full `raw`**; emits separate comment + changelog events | **Keep** — the structure is here, intact |
| **Deterministic field → triple stage** | **Does not exist** | **BUILD (new)** — this is the core gap |
| Triple extractor (email `SYSTEM_PROMPT` + 1-line `jira_hint`) | Reused from Gmail; reads only `body`, ignores every structured field | **Rebuild** for the prose-only role |
| Ontology (7 nodes / 9 predicates) | Email-native; cannot express issue links, epics, sprints, status lifecycle | **Extend** |
| Dedup (embedding cosine + connected components) | Reused from Gmail | **Retire for Jira structured nodes; key-based instead** |
| Identity emission for central resolver | Partial (`scope_principals.json` has principals) | **Finish** (small) |
| Cross-source linking | Not built | **New (last)** |

**The precise diagnosis:** structure is **not** lost at normalization — it is
lost **downstream**, because nothing turns the preserved structured fields into
graph edges. The extractor LLM-processes only `body` (description + truncated
comments). So today, an issue's assignee, status, epic, and links reach the
graph *only if the LLM happens to re-infer them from prose* — lossy and
redundant when the fields were right there.

### Corrected facts (things earlier analysis got wrong — recorded so we don't relitigate)

- **Scope coverage is 100%, not 65%.** All 449 events (issues, comments,
  changelog) carry `scope_id`; comments/changelog inherit the issue's project
  scope in the normalizer. They lack only the cosmetic `project` convenience
  field. **There is no project-level ACL gap.**
- The real ACL gaps are narrower (§8): `extra_scope_ids` last-writer-wins under
  node merge; comment-visibility (Layer 4) captured in `raw` but not enforced;
  SCRUM permission-scheme fetch returned 403 → must fail-closed.

---

## 3. Target architecture — shared spine, Jira-native stages

Keep `KnowledgeEvent` as the **cross-source spine** (it is what makes Gmail +
Slack + Jira land in one graph and one identity space). Route Jira through
**source-selected stages** for the two places where email logic is wrong:
extraction and dedup. No second pipeline; no fork of the orchestrator.

```
            ┌───────────────── shared spine (KnowledgeEvent) ─────────────────┐
Jira API ─► fetch ─► normalize ─► filter ─►        (NEW)            ─► validate ─► embed* ─► build ─► Neo4j
            (infra/jira)          (noise)   ┌─ deterministic field→triple ─┐                  *prose
                                            │  (rules, no LLM)             │                   nodes
                                            └─ LLM prose extractor ────────┘                   only
                                                  (description + comments,
                                                   Jira system prompt)
                                                          │
                                                   dedup (Jira mode):
                                                   • structured nodes → key-based (exact)
                                                   • prose nodes → semantic, scoped to one issue
                                                   • NEVER across scope_id / extra_scope_ids
```

Routing principle (unchanged repo rule): per-source behavior is selected via
`common/sources.py` capability flags, **not** inline `if source == "jira"`
scattered across stages. The deterministic builder and the Jira dedup mode are
distinct, testable units gated by source capability.

---

## 4. The seven problems

Mapped to the three areas called out by the product owner — ① cross-source
connection, ② identity, ③ ontology + dedup — plus the structural problems that
fall out of the reframe.

### P1 — Data model / source contract  *(foundational)*
**Question:** does Jira keep collapsing into `KnowledgeEvent`, or carry a richer
structured form?
**Direction:** keep `KnowledgeEvent` as the spine (decided). The structured
fields already ride along in typed fields + `raw`. The deterministic stage (P3)
reads them from there. No new top-level schema; if anything, formalize a small
typed `jira` sidecar view over `raw.fields` so the builder isn't reaching into
raw JSON ad hoc.

### P2 — Ontology redesign  *(③ part 1)* → `JIRA_ONTOLOGY.md`
**Question:** what are the nodes and edges for structured Jira?
**Direction (preview):**
- **Nodes:** `Issue` (first-class, keyed by `ENG-123`, with type/status/priority/
  points), `Epic`, `Sprint`, `Component`, `Version/Release`, plus the existing
  `Person`, and LLM-derived `Decision` / `Topic`.
- **Deterministic edges (from fields):** `ASSIGNED_TO`, `REPORTED_BY`,
  `HAS_STATUS`/`TRANSITIONED_TO` (+ timestamp), `BLOCKS` / `BLOCKED_BY` /
  `RELATES_TO` / `DUPLICATES` (issuelinks), `SUBTASK_OF`, `PART_OF_EPIC`,
  `IN_SPRINT`, `HAS_COMPONENT`, `FIX_IN_VERSION`, `LABELED`.
- **LLM-derived edges (from prose):** `DECIDED`, `BECAUSE`, `REGARDING`, plus a
  Jira-specific `RAISED_BLOCKER` / `CONCERN` if warranted.
- Status transitions are **events with timestamps** — they give a free temporal
  spine the email pipeline never had.

### P3 — Extraction split: deterministic vs LLM  *(③ part 2 — the system prompt)* → `JIRA_EXTRACTION_AND_DEDUP.md`
**Question:** what does each path own, and what does the new prompt say?
**Direction:**
- **Deterministic builder (new stage, no LLM):** consumes the normalized issue +
  changelog + links → emits the structured edges in P2. 100% of these come from
  fields. This is the skeleton.
- **LLM prose extractor (rebuilt prompt):** runs on description + comments only.
  New system prompt's core instruction: *"The structured facts (type, assignee,
  status, links) are already known — do **not** re-extract them. Extract only
  what the discussion adds: decisions reached, rationale/justification, risks or
  blockers raised, commitments made, with an evidence quote."* This kills task
  explosion and field-restating, and focuses the 40 RPM budget on signal.

### P4 — Deduplication (Jira-native)  *(③ part 3)* → `JIRA_EXTRACTION_AND_DEDUP.md`
**Question:** what actually needs deduping in Jira?
**Direction:**
- **Structured nodes:** dedup by **stable key** (`Issue ENG-123`, `Epic`,
  `Sprint`, `Component`, `Person accountId`) — exact, free, no embeddings.
- **Prose nodes** (LLM `Decision`/`Fact` from comments): light semantic dedup
  **scoped within a single issue** (same issue's comments often restate one
  decision). No cross-issue embedding clustering.
- **Hard rule:** never merge two nodes with differing `scope_id` or
  `extra_scope_ids`. Scope set is part of any dedup key.
- The Gmail `pipeline/deduplicator.py` cross-thread embedding machinery is
  **not in the Jira path**.

### P5 — Identity emission  *(②)* → `JIRA_PERSON_ID_RESOLUTION.md`
**Question:** what does Jira owe the central identity resolver?
**Direction (the simple one):** emit a clean
`{ accountId → display_name, email? }` list per run for the central resolver to
key on. Jira Cloud hides email (GDPR) by default, so `email` is often absent —
the resolver links on display name + cross-source signals. The data is already
in `actors` / `scope_principals.json`; the work is confirming the exact output
shape the resolver consumes and wiring the connector to write it. **No
resolution logic lives in the Jira pipeline** — it only supplies the list.

### P6 — ACL / scope correctness  *(gates Neo4j load)* → `JIRA_ACL_FOR_RBAC.md`
**Question:** what must be true before Jira data is queryable under RBAC?
**Direction (narrow, specific fixes):**
1. `extra_scope_ids` must not be a last-writer-wins scalar under node merge —
   the query gate should rely on `IN_SCOPE` **edges**, not a single node
   property. (Tied to P4's no-cross-scope-merge rule.)
2. Comment-visibility (Layer 4): captured in `raw.comment_visibility` but not
   enforced — knowledge from a role/group-restricted comment currently inherits
   the (wider) issue project scope. Decide: tighten to comment scope, or store
   and defer. Minimum: don't widen.
3. ACL-incomplete projects (SCRUM, HTTP 403) must **fail-closed** — fetched but
   not exposed until discovery can read the scheme.

### P7 — Cross-source connection  *(①, last)*
**Question:** how does Jira knowledge join the Gmail/Slack graph?
**Direction:** primarily via **resolved identity** (P5) — the same human's email
decisions and Jira issues collapse onto one `Person`. Then shared `Topic`s, then
issue-key mentions (`ENG-123`) detected in email/Slack bodies → `MENTIONS Issue`.
Deliberately last: Jira must stand alone correctly before we bridge it.

---

## 5. Sequencing & doc set

```
P1 ─► P2 ─► P3 ─┐
            P4 ─┼─► implement on real loremind data ─► P7
P5 ─────────────┤
P6 ─────────────┘   (P5, P6 parallel; both gate the Neo4j load)
```

Docs (reconciled with the seven that already exist — update, don't duplicate):

1. **`JIRA_PIPELINE_REDESIGN.md`** — this file (master). ✅
2. **`JIRA_ONTOLOGY.md`** — rewrite for the node/edge model (P2).
3. **`JIRA_EXTRACTION_AND_DEDUP.md`** — new: field-map, the deterministic→edge
   table, the new LLM system prompt, the dedup rules (P3+P4).
4. **`JIRA_PERSON_ID_RESOLUTION.md`** — delta for the emission contract (P5).
5. **`JIRA_ACL_FOR_RBAC.md`** — delta for the three fixes (P6).

Then implement P3/P4 against the **449 real events already in
`output/raw/loremind_jira_2026-06-02.jsonl`** — no mock.

---

## 6. Open questions (resolve during P2/P3)

- **Issue as node vs. issue as the thread container?** Leaning: `Issue` is a
  first-class node *and* the grouping key — its description/comments are the
  prose the LLM reads. Confirm in `JIRA_ONTOLOGY.md`.
- **How much changelog to graph?** Every transition, or just status→Done /
  reassignments / resolution? Leaning: model resolution + reassignment + final
  status; keep intermediate churn in `raw`.
- **Topics in Jira:** derive from components/labels (deterministic) vs LLM
  `REGARDING` from prose vs both, then reconcile in dedup. Leaning: both, with
  components/labels as anchors.
- **Filter stage for Jira:** how much noise is there really? (loremind: 93/449
  dropped.) Confirm Jira noise signals differ from email's.
