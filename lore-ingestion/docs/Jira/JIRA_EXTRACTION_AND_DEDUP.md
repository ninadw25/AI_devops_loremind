# Jira Extraction & Deduplication

> **Status:** DESIGN (P3 + P4). Companion to `JIRA_PIPELINE_REDESIGN.md` (master)
> and `JIRA_ONTOLOGY.md` (node/edge model). This doc is the **build spec**: it
> turns the ontology into two concrete stages — a deterministic field→edge
> builder and a rebuilt LLM prose pass — and the Jira-native dedup rules.

---

# Part A — Extraction

## A1. The split

For Jira, extraction is two stages with a clean boundary:

```
normalized issue (KnowledgeEvent + raw.fields + comment/changelog events)
        │
        ├──► (1) DETERMINISTIC BUILDER  — no LLM
        │        fields → structured triples  (Issue, edges in JIRA_ONTOLOGY §3a/3b)
        │        100% precision, free, idempotent
        │
        └──► (2) LLM PROSE PASS  — scalpel
                 description + comments → Decision / Fact / Topic  (+ evidence)
                 only what the discussion ADDS beyond the fields
        ▼
   merge → one triples record per issue  → validator → embed → builder
```

Both stages emit the **same triples JSON shape** the current extractor produces
(`{subject, predicate, object, evidence_sentence}` lists per unit), so the
validator, embed, and builder stages consume them unchanged.

## A2. Where this lives (routing)

Consistent with the repo rule (data-shape → `common/sources.py`; genuine
behaviour → named source-gated paths, like the existing Jira prompt-hint and
noise filter):

- Add a capability `structured: bool` to `SourceCaps` (`jira=True`,
  `gmail/slack=False`). It declares "this source has a deterministic skeleton."
- The extraction stage, when `caps.structured`, runs the **deterministic
  builder** first, then the **Jira prose pass** (different system prompt), and
  merges. When `False`, it runs the existing Gmail/Slack LLM path unchanged.
- No `if source == "jira"` scattered through the stages — one branch at the
  stage entry, selected by capability.

## A3. Stage 1 — the deterministic builder (no LLM)

**Input:** the normalized issue event + its comment/changelog children.
**Output:** structured triples, per `JIRA_ONTOLOGY.md` §3a/3b. Pure function of
fields — same input always yields the same triples (idempotent; safe to re-run).

Emits, per issue:

```
Issue(key){issue_type, status, priority?, story_points?, summary, created, resolved_at?, scope_id}
Issue -REPORTED_BY-> Person(reporter.accountId)
Issue -ASSIGNED_TO-> Person(assignee.accountId)            # if assigned
Issue -PART_OF_EPIC-> Issue(parent/epic)                   # if linked
Issue -SUBTASK_OF-> Issue(parent)                          # if subtask
Issue -BLOCKS|BLOCKED_BY|RELATES_TO|DUPLICATES-> Issue     # per issuelink
Issue -IN_SPRINT-> Sprint(name)                            # per sprint
Issue -HAS_COMPONENT-> Component ; Issue -LABELED-> Topic  # components/labels
Issue -RESOLVED_BY-> Person(resolution changelog author)   # if resolved
Issue -IN_SCOPE-> AclScope(scope_id [+ extra_scope_ids])   # see ACL doc
```

Rules:
- **Persons** are minted by `accountId` only (display name is a property). No
  name parsing. Identity merge happens later in the central resolver.
- **Issue-issue edges** (`PART_OF_EPIC`, `BLOCKS`, …) reference the target by
  Jira key. If the target issue wasn't fetched, still emit the edge to a
  **stub** `Issue(key)` node (key is authoritative) — it resolves if/when fetched.
- **No edge is invented.** Absent field → no edge (the ⛔ rows in the ontology
  simply produce nothing until data has them).
- Every triple carries the issue's `scope_id`/`extra_scope_ids` and
  `source_ref`/`source_url` for citations.

## A4. Stage 2 — the LLM prose pass (rebuilt prompt)

**The LLM reads only the unstructured prose** (description + comments, already
grouped by `truncate_body`). Its job is to surface knowledge **no field holds**.

### A4.1 Cost gate — skip the call when there's nothing to read

Many Jira issues are a title + assignee with no real description and no comments
(loremind has plenty). **Do not call the LLM** when, after stripping, the
description and all comments together have < N characters of substance (start
N≈80). These issues are fully covered by the deterministic skeleton. This keeps
the 40 RPM budget on issues that actually contain discussion.

### A4.2 The structured context block (given to the model as KNOWN facts)

The prompt **tells the model what's already known** so it doesn't re-extract it:

```
KNOWN STRUCTURED FACTS (do NOT re-extract these — they are already in the graph):
  Issue: ENG-123  ·  Type: Task  ·  Status: In Progress
  Reporter: Reia Baid       Assignee: Arnav Gupta
  Project: ENG   Epic: ENG-100   Blocks: ENG-130
```

### A4.3 The new system prompt

```
You are a Jira knowledge extractor. You are given an issue's STRUCTURED FACTS
(type, status, reporter, assignee, links) and its PROSE (description + comments).

The structured facts are ALREADY in the knowledge graph. Your ONLY job is to
extract knowledge that the prose ADDS and the fields do NOT already capture:

  • DECISIONS reached in discussion — an approval, choice, agreement, rejection,
    or change of direction stated in the description or a comment.
  • RATIONALE — the reason or justification behind a decision (BECAUSE → Fact).
  • BLOCKERS / RISKS raised in discussion that aren't an issue-link.
  • CONCRETE FACTS the prose states — deadlines, dates, numbers, named
    quantities, external systems — when they carry institutional knowledge.

DO NOT extract:
  • The assignee as a task — ASSIGNED_TO is already a field.
  • The status, reporter, priority, or issue links — already fields.
  • A restatement of the issue summary with no added information.
  • Pleasantries, "+1", "thanks", status pings, or bot/automation comments.

NODE TYPES you may emit: Decision, Fact, Topic, Person, Issue (for cross-issue
mentions only). PREDICATES: DECIDED (Person→Decision), BECAUSE (Decision→Fact),
REGARDING (Decision→Topic), MENTIONS (Decision/Fact→Person|Issue).

Attribute DECIDED/STATED knowledge to the person the PROSE names as deciding or
stating it — NOT to the issue reporter by default.

Every triple needs an evidence_sentence: an exact quote from the prose.

If the prose adds NOTHING beyond the structured facts, return an empty triple
list. This is common and correct — do not invent knowledge to fill the output.

Return ONLY valid JSON: {"triples":[...], "issue_summary":"…",
"department_tags":[...], "sensitivity":"internal|confidential|public"}
```

Key differences from the email prompt:
- **Anti-duplication is the central instruction**, not a footnote.
- **Empty output is explicitly endorsed** (email prompt pushed hard to always
  find Decisions/Tasks; for Jira that causes hallucinated restatements).
- **No `Task` node** in the allowed set (the Issue is the task).
- **No collective-mailbox / functional-sender logic** (Jira has named actors).

### A4.4 Anchoring (added by the builder, not the LLM)

The LLM does **not** emit `DERIVED_FROM`. Because the pass runs per-issue, the
merge step attaches `DERIVED_FROM -> Issue(key)` to every LLM-produced node
automatically. Same for `scope_id`/citations — inherited from the issue. This
keeps the LLM output minimal and the provenance reliable.

## A5. Validator implications

The existing validator's email rules don't all apply:
- "Every Decision MUST have a DECIDED" — keep (still true).
- "Every Task MUST have ASSIGNED_TO" — N/A for Jira (no Task nodes); the
  deterministic `ASSIGNED_TO` is always present and correct.
- "Persons must exist in registry (no phantom names)" — keep, but Jira persons
  are accountId-keyed; validate against the Jira principal list, not email names.
- Structured triples from Stage 1 are **trusted** (deterministic) — the validator
  should pass them through without LLM-style content checks, and only gate the
  prose triples. Gate by triple provenance (a `derived: "field"|"llm"` tag).

---

# Part B — Deduplication

## B1. Why Jira dedup is different

Gmail dedup exists because the same decision is described in different words
across threads with **no stable ID**, so it uses embedding similarity +
connected components. **Jira has stable keys.** An issue is an issue; a person is
an `accountId`. There is no cross-thread duplication of structured entities to
chase. So:

> **The expensive, risky embedding-clustering dedup
> (`pipeline/deduplicator.py`) is NOT in the Jira path.** The key is the dedup.

## B2. Dedup rules by node type

| Node | Dedup mechanism | Notes |
|------|-----------------|-------|
| `Issue` | **exact by Jira key** (`ENG-123`) | globally unique; stub + real merge on key |
| `Person` | **exact by `accountId`** | identity resolver merges cross-source later |
| `Sprint`/`Component`/`Version` | **exact by (project, name)** | |
| `Topic` | **exact by normalized name** | reconcile component-Topics vs LLM-Topics (B4) |
| `Decision`/`Fact` (LLM) | **light semantic, scoped to ONE issue** | see B3 |

All of these are deterministic key matches except the LLM prose nodes.

## B3. Prose-node dedup (the only semantic step)

Within a **single issue**, the description and multiple comments often restate
one decision ("Let's go with Postgres" … later … "Confirmed, Postgres it is").
These should collapse to one `Decision`.

- Embed only the LLM-derived `Decision`/`Fact` nodes **of that issue** and merge
  near-duplicates (cosine ≥ threshold, calibrated like the Gmail dedup's
  `--calibrate`, but the candidate set is tiny — one issue's prose nodes).
- **Never merge across issues.** Cross-issue "similar decisions" are a
  post-processing / topic-clustering concern, not entity identity. Two issues
  deciding similar things are two decisions.
- Canonical = the most complete statement (latest comment usually supersedes the
  description).

## B4. Topic reconciliation

Components/labels produce deterministic `Topic`s; the LLM produces `REGARDING`
`Topic`s from prose. After both, fold by normalized name so "infrastructure"
(label) and "Infrastructure / Server Audit" (LLM) don't fragment. Prefer the
deterministic (component/label) name as canonical when they match; otherwise
keep both and let post-processing's topic clustering relate them.

## B5. The hard ACL rule (applies to every merge)

> **Two nodes are never merged if their `scope_id` or `extra_scope_ids` differ.**
> Scope set is part of every dedup key.

For Jira this matters because it's the only source with stacked scopes
(`jira:project:*` + `jira:seclevel:*`). Since structured dedup is exact-by-key
and prose dedup is scoped-to-one-issue (one scope), this rule is naturally
satisfied — but it is asserted explicitly and tested, because it's the
difference between correct RBAC and a security-level leak. (See
`JIRA_ACL_FOR_RBAC.md` for the `extra_scope_ids`-as-edges fix that makes the
builder side safe too.)

## B6. What is retired for Jira

- Cross-thread embedding clustering (`cluster_nodes_by_type`, connected
  components over all nodes) — **not run** for Jira.
- Date-incompatibility blocking — irrelevant (no cross-issue merges).
- The 0.92/0.96 same/cross-thread thresholds — replaced by a single per-issue
  prose threshold.

---

# Part C — Build order & tests

1. **Deterministic builder** first — it needs no LLM, runs on the 449 real
   loremind events immediately, and produces a verifiable skeleton (assert
   158 Issues, 158 `REPORTED_BY`, 124 `ASSIGNED_TO`, 100% `IN_SCOPE`).
2. **Cost gate + prose pass** second — verify it returns empty for the bare
   issues and real Decisions for the ones with discussion; assert no
   `ASSIGNED_TO`/status restatement appears in LLM output.
3. **Merge + anchoring** — assert every LLM node has exactly one `DERIVED_FROM`.
4. **Dedup** — assert exact-key collapse for Issues/Persons; assert no
   cross-issue prose merge; assert the scope rule with a synthetic
   restricted+public pair.

Tests assert only what the loremind data exercises; ⛔ edges get a tiny
synthetic fixture (one epic link, one blocks link, one sprint) so the code path
is covered without waiting for richer real data.
