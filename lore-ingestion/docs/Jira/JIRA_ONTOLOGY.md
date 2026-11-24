# Jira Ontology → Knowledge Graph

> **Status:** DESIGN (P2). Companion to `JIRA_PIPELINE_REDESIGN.md` (master).
> Defines the node and edge model for Jira. The extraction split that *produces*
> these (deterministic field→edge builder + LLM prose pass) and the dedup rules
> are specified in `JIRA_EXTRACTION_AND_DEDUP.md` (P3/P4).
>
> **This rewrites the previous ontology**, which mapped issues into the email
> ontology (`Epic→Decision`, `Story→Task`, `Bug→Fact`). That approach threw away
> the structure Jira hands us. The model below makes the **Issue a first-class
> node** and derives most edges deterministically from fields — see the master
> doc's reframe.

---

## 1. Design principles

1. **The Issue is the anchor.** Every Jira issue is a first-class, durable node
   keyed by its stable Jira key (`ENG-123`). It is *not* mapped to `Task` or
   `Decision`. Email's `Task` was an *inferred* action item; a Jira issue is the
   authoritative work item itself.
2. **Skeleton from fields, soft tissue from prose.** Structured edges
   (assignee, reporter, status, links, epic, sprint) are built **deterministically
   — no LLM**. The LLM only adds knowledge latent in description + comments
   (`Decision`, rationale, blockers) and **anchors it back to the Issue** via a
   provenance edge.
3. **Reuse shared labels to unify across sources.** LLM-derived nodes use the
   **same** `Person` / `Decision` / `Topic` / `Fact` labels the Gmail/Slack
   pipeline emits, so "what did Ravi decide?" surfaces email *and* Jira
   decisions in one graph. Only the *structured* Jira concepts add new labels.
4. **Identity key = `accountId`.** Stable across renames and email changes. The
   central identity resolver later merges Jira `Person`s with email `Person`s;
   the ontology does not do that itself.
5. **ACL is orthogonal.** Every node carries `scope_id` (+ `extra_scope_ids`)
   and is wired to `AclScope` via `IN_SCOPE` edges by the builder. See
   `JIRA_ACL_FOR_RBAC.md`. Dedup never merges across differing scope.

---

## 2. Node types

### 2a. Structured nodes — NEW labels, built deterministically

| Node | Key | Key properties | Source |
|------|-----|----------------|--------|
| **`Issue`** | Jira key (`ENG-123`) | `issue_type` (Task/Story/Bug/Sub-task/Epic), `status`, `priority?`, `story_points?`, `summary`, `created`, `resolved_at?`, `scope_id` | `raw.fields` |
| **`Sprint`** | (project, name) | `name`, `state?`, `start?`, `end?` | `sprints` |
| **`Component`** | (project, name) | `name` | `fields.components` |
| **`Version`** | (project, name) | `name`, `released?` | `fields.fixVersions` |

`Epic` is **not** a separate label — it is an `Issue` with `issue_type = "Epic"`
that other issues point to via `PART_OF_EPIC`. One uniform `Issue` label keeps
queries and the builder simple.

`status` is a **property** on `Issue` (To Do / In Progress / Done / …), not a
node — current state is queryable without a join.

### 2b. Shared nodes — reused so Jira unifies with email/Slack

| Node | Key | Produced by | Notes |
|------|-----|-------------|-------|
| **`Person`** | `accountId` | deterministic (reporter/assignee/comment author) | identity resolver merges later |
| **`Topic`** | normalized name | deterministic (components/labels) **and** LLM (`REGARDING`) | reconciled in dedup; components/labels are anchors |
| **`Decision`** | content hash | **LLM only** (from prose) | unifies with email Decisions |
| **`Fact`** | content hash | **LLM only**, sparingly | rationale/observations from prose |

### 2c. What Jira does **NOT** emit (deliberate)

- **No `Task` nodes.** The `Issue` *is* the task. Emitting `Task` would duplicate
  the issue and re-introduce the email "task explosion" problem. (Exception is
  resisted, not embraced: a side-action in a comment — "also update the docs" —
  is better captured as a `Decision`/commitment than a free-floating Task. Lean
  toward not extracting it.)
- **No `Event` nodes.** Jira has no meetings. The lifecycle lives as Issue
  properties + the `RESOLVED_BY` edge (§3b).
- **No `Organization` / `WORKS_AT`.** Jira gives no employer; email does that.

---

## 3. Edge types

### 3a. Deterministic edges — from fields, no LLM

| Edge | From → To | Source field | In loremind data? |
|------|-----------|--------------|:--:|
| `REPORTED_BY` | Issue → Person | `fields.reporter` | ✅ 158/158 |
| `ASSIGNED_TO` | Issue → Person | `fields.assignee` | ✅ 124/158 |
| `PART_OF_EPIC` | Issue → Issue(Epic) | `fields.parent` / Epic Link / `related_ids` | ⛔ (epics exist, no links populated) |
| `SUBTASK_OF` | Issue → Issue | `fields.parent` (when subtask) | ⚠️ 1 sub-task |
| `BLOCKS` / `BLOCKED_BY` | Issue → Issue | `fields.issuelinks` | ⛔ none in dataset |
| `RELATES_TO` | Issue → Issue | `fields.issuelinks` | ⛔ |
| `DUPLICATES` | Issue → Issue | `fields.issuelinks` | ⛔ |
| `IN_SPRINT` | Issue → Sprint | `sprints` | ⛔ |
| `HAS_COMPONENT` | Issue → Component | `fields.components` | ⛔ |
| `FIX_IN_VERSION` | Issue → Version | `fields.fixVersions` | ⛔ |
| `LABELED` | Issue → Topic | `fields.labels` | ⛔ |
| `IN_SCOPE` | Issue/node → AclScope | `scope_id` + `extra_scope_ids` | ✅ 100% |

> **⛔ = supported but absent in the current `loremind` dataset.** The dataset is
> a young Jira (only issuetype/status/assignee/reporter/description/comments +
> changelog are populated). We **design for the full model, build the edges
> behind capability checks, and only assert in tests what the data exercises** —
> adding a richer fixture later turns the ⛔ rows on without code change.

`ASSIGNED_TO` and `REPORTED_BY` deliberately reuse the **shared** predicate
direction the graph already understands (`ASSIGNED_TO` exists in the email
ontology) so cross-source queries work.

### 3b. Lifecycle / changelog — kept minimal

The changelog is a rich audit trail (loremind: 40 reassignments, 20 transitions,
16 resolutions). Modeling every churn event would bloat the graph. Rule:

- **Current state as Issue properties:** `status`, `created`, `resolved_at`.
- **`RESOLVED_BY`** (Issue → Person): the changelog author of the `resolution`/
  `status→Done` entry — answers "who closes things." Worth a node-edge.
- **Everything else (intermediate transitions, reassignment history) stays in
  `raw`** for audit, not as graph edges. *(Deliberate scoping choice — revisit
  if "assignment churn" becomes a product question.)*

### 3c. LLM-derived edges — from prose only

| Edge | From → To | Meaning |
|------|-----------|---------|
| `DECIDED` | Person → Decision | a decision reached in discussion (not a field) |
| `BECAUSE` | Decision → Fact | rationale / justification |
| `REGARDING` | Decision/Issue → Topic | subject area (reconciled with component/label Topics) |
| `MENTIONS` | Decision/Fact → Person/Issue | references another person or issue key |
| **`DERIVED_FROM`** | Decision/Fact/Topic → Issue | **provenance** — anchors every LLM node to the issue whose prose produced it |

`DERIVED_FROM` is the keystone of the skeleton-plus-soft-tissue model: the
deterministic builder attaches **every** LLM-derived node from issue *X* to
`Issue X` automatically (the extraction is per-issue, so provenance is known for
free). This keeps prose knowledge tethered to the structured spine and gives
query-time citations.

### 3d. The anti-duplication rule (the whole point)

The LLM system prompt (spec in `JIRA_EXTRACTION_AND_DEDUP.md`) must **forbid
re-extracting anything already in a field**: do not emit a Decision/Task for the
assignee, the status, the reporter, or the issue links — those are deterministic
edges. The LLM only earns its cost on what the *discussion* adds.

---

## 4. Field → graph mapping (concrete)

From the normalized `KnowledgeEvent` + `raw.fields`:

```
event.source_id / raw.key      → Issue.key  (ENG-123)        [anchor node]
raw.fields.issuetype.name      → Issue.issue_type
raw.fields.status.name         → Issue.status                [property]
raw.fields.priority.name       → Issue.priority?
raw.fields.created             → Issue.created
event.scope_id                 → Issue.scope_id + IN_SCOPE
event.extra_scope_ids          → extra IN_SCOPE edges (NOT a scalar prop)

raw.fields.reporter.accountId  → Person ; Issue -REPORTED_BY-> Person
raw.fields.assignee.accountId  → Person ; Issue -ASSIGNED_TO-> Person
event.related_ids / parent     → Issue  -PART_OF_EPIC/SUBTASK_OF-> Issue
raw.fields.issuelinks[]        → Issue  -BLOCKS/RELATES_TO/DUPLICATES-> Issue
event.sprints[]                → Sprint ; Issue -IN_SPRINT-> Sprint
raw.fields.components[]         → Component/Topic ; Issue -HAS_COMPONENT/LABELED->
raw.fields.labels[]            → Topic ; Issue -LABELED-> Topic
changelog (resolution/Done)    → Issue.resolved_at + Issue -RESOLVED_BY-> Person

description + comments (prose)  → LLM pass → Decision/Fact/Topic
                                  + DERIVED_FROM -> Issue (provenance)
```

Node IDs route through `common/hashing.py` (repo rule — no source defines its own
ID formula). `Issue` gets a new prefix (e.g. `issue:`) hashed from
`(org, key)`, and carries the human `key` as a property. Dedup keys are
previewed in §6; full rules in `JIRA_EXTRACTION_AND_DEDUP.md`.

---

## 5. Worked example (real loremind issue)

**Input** — issue in project OPS:
> *Summary:* "Server infrastructure audit"  ·  *type:* Task  ·  *status:* To Do
> *reporter:* Reia Baid  ·  *assignee:* Arnav Gupta
> *description:* "Arnav Gupta assigned to audit all production servers by May 30.
> Reia Baid approved the scope."

**Deterministic (no LLM):**
```
Issue(OPS-x){issue_type:Task, status:"To Do", created:…}
Issue(OPS-x) -REPORTED_BY-> Person(accountId=Reia Baid)
Issue(OPS-x) -ASSIGNED_TO-> Person(accountId=Arnav Gupta)
Issue(OPS-x) -IN_SCOPE->   AclScope(jira:project:OPS)
```

**LLM (from prose) — note it does NOT re-emit the assignment (that's a field):**
```
Person(Reia Baid) -DECIDED-> Decision("Approved scope of the production-server audit")
Decision           -DERIVED_FROM-> Issue(OPS-x)
Decision           -REGARDING->    Topic("Infrastructure / Server Audit")
Fact("Audit deadline is May 30") -DERIVED_FROM-> Issue(OPS-x)
```

The deadline and the approval are *latent* knowledge the fields don't hold; the
assignment is *not* re-extracted because `ASSIGNED_TO` already captured it.

---

## 6. Node-ID & dedup keys (preview)

| Node | Dedup key | Mechanism |
|------|-----------|-----------|
| `Issue` | Jira key (`ENG-123`) | exact — globally unique, free |
| `Person` | `accountId` | exact |
| `Sprint`/`Component`/`Version` | (project, name) | exact |
| `Topic` | normalized name | exact + reconcile component-Topics vs LLM-Topics |
| `Decision`/`Fact` (LLM) | content embedding **scoped to one issue** | light semantic, never cross-issue, never cross-scope |

No cross-issue embedding clustering for the structured core — the key is the
dedup. Full rules: `JIRA_EXTRACTION_AND_DEDUP.md`.

---

## 7. Comment grouping for the LLM pass (as-built, kept)

The normalizer (`truncate_body`) already groups an issue + its comments into one
unit before the LLM:

| Comment count | Strategy |
|---|---|
| 0 | Description only, ≤ 3000 chars |
| 1 – 10 | Description ≤ 1500 chars + all comments (each ≤ 2000) |
| > 10 | Description ≤ 1000 + first 3 + `[N middle omitted]` + last 3 |

---

## 8. Pre-LLM noise filter (kept)

Skipped before extraction:

| Pattern | Reason |
|---|---|
| Issue: < 2 words in title AND < 20 chars body | no extractable content |
| Comment: ≤ 1 char after stripping | reaction / empty |
| Comment: deleted or hidden | user-redacted |
| Issue: `archived` and config excludes archived | excluded |
| Issue: no `summary` | malformed |

---

## 9. Open questions (resolve in P3)

- **`status` granularity:** model only To Do/In Progress/Done, or the project's
  full custom workflow? Leaning: store raw status string as-is; don't normalize.
- **Component vs Topic:** emit `Component` nodes *and* `Topic`s, or fold
  components into Topics? Leaning: `Component` is structured (per project);
  `Topic` is the cross-source semantic concept — keep both, link
  `Component -ALIAS_OF-> Topic` if names match. Decide in P3.
- **`MENTIONS Issue` from prose:** when a comment cites `ENG-456`, emit
  `Decision -MENTIONS-> Issue(ENG-456)`? Yes — cheap deterministic regex on
  prose, complements the field-based links. Confirm in P3.
- **Epic as container vs initiative:** `issue_type=Epic` Issue is the container;
  do we *also* want an LLM `Decision` for the epic's strategic intent? Leaning:
  only if the epic description contains a real decision — let the prose pass
  decide, don't force it.
