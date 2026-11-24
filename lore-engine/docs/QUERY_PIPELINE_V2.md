# Query Pipeline v2 — Consolidated GraphRAG (two engines, one orchestrator)

**Status:** Implemented (2026-06-12) and wired as the active path; pending graph-readiness audit + golden-set eval before deleting the superseded modules. See "Implementation status" at the end.
**Note (2026-07-13):** the retrieval semantics of this pipeline are superseded by the claims-substrate redesign — see `QUERY_PIPELINE_V3.md`. v2 remains the serving path until v3 beats it on the shared eval suite.
**Scope:** Gmail, Slack, Outlook Mail, MS Teams (the unstructured engine) **and** Jira (the structured engine). GitHub/chronograph lives in a separate store and is out of scope.

---

## 0. Consolidated architecture (2026-06-12)

The pipeline is **two specialized retrieval engines behind one router-free orchestrator** and one shared ranking + assembly + synthesis tail. The two engines exist because there are two ontologies: unstructured prose (Decision/Task/Fact/Event + Person/Topic/Org spine) vs. structured Jira (first-class `Issue` + Sprint/Component/Version with structured edges). Each retrieves in its own native way but returns the **same `SourcedChunk` envelope**, so everything after retrieval is source-agnostic.

```
question
   │
   ▼
[analyze ∥ embed]        one small LLM call (intent+entities+time) ∥ 1 embed call
   │
   ├──────────────────────────────┬───────────────────────────────┐
   ▼                              ▼   (both always run, concurrently)
 UNSTRUCTURED ENGINE            JIRA ENGINE
 domain/retrieval/unstructured  domain/retrieval/jira
 seeds → frontier/beam expand   intent-routed: navigational / status /
 → render SourcedChunks         analytical → SourcedChunks
   │                              │
   └────────► merge (dedup by node_id) ◄────────┘
                       │
                       ▼
        ONE unified rank: BM25 + cross-encoder + token budget   (rank_chunks)
                       │
                       ▼
        ONE assembler (group by source) + sources + confidence
                       │
                       ▼
                  synthesis LLM
```

**Why both engines always run (no up-front routing):** running an engine is just graph queries — it does **not** add an LLM call. The unified cross-encoder rerank is the relevance filter; irrelevant chunks from either engine are dropped there. So there is no routing decision to get wrong, and a question that spans both worlds ("why did we decide X, and what's the Jira status") is answered from both. The engines run concurrently in a thread pool, so wall-clock ≈ the slower engine, not the sum.

**Source isolation:** the two engines are kept disjoint at the Cypher level. Both share the `knowledge_embeddings` index and the Decision/Fact node space, so each filters by scope-id prefix — the unstructured engine excludes `jira:` (`unstructured_source_filter`), the Jira engine requires `jira:` (`jira_source_filter`). Both owned by `domain/scopes.py`. Nothing is double-served.

**Common envelope:** `SourcedChunk {source, node_id, node_type, text, score, scope_ids, citations, created_at}`. Each engine renders its own ontology-aware `text` (the unstructured engine folds in attribution + corroboration; the Jira engine folds in status/assignee/sprint), so the assembler and reranker stay source-agnostic. `source` is derived from scope-id prefix (`source_label`).

**Modules:** `domain/retrieval/unstructured.py` (engine A), `domain/retrieval/jira.py` (engine B), `domain/retrieval/rank.py::rank_chunks` (unified rank), `domain/context.py::build_context_chunks`, `domain/sources.py::format_sources_chunks`, `domain/scoring.py::score_confidence_chunks`, `services/query_service.py` (orchestrator). The Jira engine was modernized to the refactored standards: single-owner scope gate (`$allowed_scopes`), no `get_settings()` in domain (recency window injected), and `jira_source_filter` to stop cross-contamination.

The sections below detail the **unstructured engine** specifically; the Jira engine's strategies are documented in `JIRA_INTEGRATION.md` and `JIRA_RETRIEVAL_FINDINGS.md`.

---

**Decisions locked with product owner:**

1. **Port, don't wrap** — the strong ideas from `lore-graph-research` (frontier expansion, BM25 + cross-encoder rerank) are reimplemented inside `lore-engine/domain/retrieval/`. The research repo stays as reference; it is not deployed.
2. **One small pre-retrieval LLM call** — structured query analysis (intent + entities + time range). No multi-call agentic retrieval in v1.
3. **Local search only for now** — GraphRAG-style "global" questions (community summaries) are out of scope, but the router reserves a `GLOBAL` intent slot so they can be added later without restructuring.
4. **Fail-closed ACL from day one** — every Cypher in the new path gates on `scope_ids` / `scope_all_ids`. A node with empty `scope_ids` is invisible to everyone, period.
5. **Reranker behind a provider Protocol** — `RerankProvider` in `infra/rerank/` mirroring the existing `ChatProvider` / `EmbeddingProvider` registries. FlashRank (local ONNX) is the default; NVIDIA reranker NIM is a config switch.
6. **Legacy department RBAC retired on this path** — `access_groups` / `department_tags` / `sensitivity_label` gates (see `domain/rbac.py` OPEN ACCESS note) are deleted from the new retrieval modules. The locked scope model is the only authorization dimension.

---

## 1. Why v2

The current pipeline (`services/query_service.py:_retrieve_full`) runs four fixed strategies — regex person resolution, top-k vector search with per-type 1-hop expansion, thread siblings, topic expansion — then truncates to ~18 nodes by insertion order. Three structural problems:

1. **The ontology is barely used.** Ingestion writes a rich graph (Decision/Task/Fact/Event/Issue knowledge nodes; Person/Organization/Topic spine; `BECAUSE` rationale edges, `STATED_BY` attribution, `PARTICIPATED_IN`, `COMMUNICATES_WITH`, cross-source `CORROBORATES`). Query-time traversal is a fixed 1-hop template per node type. `CORROBORATES` and `COMMUNICATES_WITH` are never traversed. Multi-hop chains (Decision → BECAUSE → Fact → STATED_BY → Person → other Decisions) are unreachable.
2. **No ranking.** There is no lexical signal, no cross-encoder, no score fusion. Vector cosine order from the seed search is the only ranking; expansion results are appended unranked. Context truncation at 18 nodes is arbitrary.
3. **ACL is wrong on this path.** `domain/retrieval/vector.py` gates on the legacy `access_groups` model, which `domain/rbac.py` documents as effectively OPEN ACCESS. The locked scope model (`scope_ids` OR-set + `scope_all_ids` AND-set, fail-closed) exists in ingestion artifacts and in the Jira path, but not here.

`lore-graph-research` validated the fixes for (1) and (2): intent-aware, batched frontier expansion with convergence bonuses, then BM25 + FlashRank reranking. Its plumbing is research-grade (Neo4j internal integer ids, pandas in the hot path, stdout diagnostics, zero ACL), which is why we port the algorithms, not the code.

---

## 2. Pipeline overview

```
                       ┌──────────────────────────────────────────────┐
 question, allowed_scopes, history                                    │
        │                                                             │
        ▼                                                             │
 ┌──────────────┐   ┌──────────────┐                                  │
 │ 1. ANALYZE    │   │ 2. EMBED     │   (run concurrently)            │
 │ 1 small LLM   │   │ existing     │                                  │
 │ call →        │   │ provider     │                                  │
 │ intent,       │   └──────┬───────┘                                  │
 │ entities,     │          │                                          │
 │ time range    │          │                                          │
 └──────┬───────┘          │                                          │
        ▼                  ▼                                          │
 ┌─────────────────────────────────────┐                              │
 │ 3. SEED  (all Cypher scope-gated)   │                              │
 │  a. vector seeds: ANN top-30        │                              │
 │  b. entity anchors: Person/Topic    │                              │
 │     nodes from analysis entities    │                              │
 └──────────────────┬──────────────────┘                              │
                    ▼                                                 │
 ┌─────────────────────────────────────┐                              │
 │ 4. EXPAND  frontier/beam search     │                              │
 │  batched per hop, scope predicate   │                              │
 │  inside traversal Cypher, intent-   │                              │
 │  weighted scoring, convergence      │                              │
 │  bonus, beam=30, depth≤3            │                              │
 └──────────────────┬──────────────────┘                              │
                    ▼                                                 │
 ┌─────────────────────────────────────┐                              │
 │ 5. RANK                             │                              │
 │  BM25 hybrid score → top-50 →       │                              │
 │  RerankProvider (FlashRank/NIM)     │                              │
 │  → final top-N (token-budgeted)     │                              │
 └──────────────────┬──────────────────┘                              │
                    ▼                                                 │
 ┌─────────────────────────────────────┐                              │
 │ 6. ASSEMBLE  ontology-aware context │                              │
 │  rationale/attribution/corroboration│                              │
 │  rendered per node type, provenance │                              │
 │  ids for citations, token budget    │                              │
 └──────────────────┬──────────────────┘                              │
                    ▼                                                 │
 ┌─────────────────────────────────────┐                              │
 │ 7. SYNTHESIZE  existing streaming   │◄── history (MemoryService)   │
 │    path, unchanged                  │                              │
 └─────────────────────────────────────┘                              │
        confidence = f(rerank scores, convergence, corroboration) ────┘
```

Stages 1 and 2 run concurrently; the analysis call has a **hard 800 ms timeout** with fallback to the keyword classifier, so the worst case adds zero wall-clock over embedding alone when the LLM is slow.

---

## 3. Stage specifications

### Stage 1 — Query analysis (`domain/analysis.py`, new)

One structured-output call to a small fast model (via the existing `ChatProvider`; add a `fast_llm_model` setting so synthesis and analysis can use different models on the same provider).

Output schema (Pydantic, validated; on validation failure → keyword fallback):

```python
class QueryAnalysis(BaseModel):
    intent: Literal["PERSON_LOOKUP", "TASK_LOOKUP", "DECISION_LOOKUP",
                    "REASONING", "EVENT_LOOKUP", "TOPIC_EXPLORATION",
                    "GLOBAL",      # reserved: routed to TOPIC_EXPLORATION until community summaries exist
                    "SUMMARY"]     # default / mixed
    persons:  list[str]   # surface forms as written in the question
    orgs:     list[str]
    topics:   list[str]
    time_range: TimeRange | None   # {start, end} resolved to absolute dates
```

- **Fallback:** port of the keyword classifier from `lore-graph-research/src/intent_analyzer.py` (pure function, no IO). Used on timeout, provider error, or schema violation. The pipeline must never fail because analysis failed.
- **Person resolution** uses the analysis `persons` list instead of today's fragile regex (`domain/retrieval/person.py:resolve_persons`), matched against Person nodes via name/alias index. Keep the regex as part of the fallback path only.
- `GLOBAL` intent currently degrades to `TOPIC_EXPLORATION` with a logged marker (`intent_global_unserved`) so we can measure demand for community summaries before building them.

### Stage 2 — Embed (existing)

Unchanged: `EmbeddingProvider.embed(question)`, NVIDIA NeMo 2048-dim by default (must match ingestion's `nvidia/llama-3.2-nemoretriever-300m-embed-v1`).

### Stage 3 — Seeding (`domain/retrieval/seeds.py`, new)

Two seed sources, both scope-gated, merged and deduped by stable `node_id`:

**a. Vector seeds** — ANN over `knowledge_embeddings`, top-30 (up from k=8; the reranker, not the ANN, now decides what survives):

```cypher
CALL db.index.vector.queryNodes('knowledge_embeddings', $k, $embedding)
YIELD node, score
WHERE {SCOPE_GATE(node)} AND {UNSTRUCTURED_SOURCE_FILTER(node)}
  AND ($since IS NULL OR node.created_at >= datetime($since))
  AND ($until IS NULL OR node.created_at <= datetime($until))
RETURN coalesce(node.decision_id, node.task_id, node.fact_id, node.event_id) AS node_id,
       labels(node)[0] AS type, node.description AS description,
       node.scope_ids AS scope_ids, node.source_thread_id AS source_thread_id,
       node.created_at AS created_at, score
ORDER BY score DESC LIMIT $limit
```

> **Two corrections to the original sketch, verified against the live builder
> (`lore-ingestion/builders/unified_builder.py`):**
>
> 1. **No unified `node_id` property exists.** Each content node has a
>    type-specific id (`decision_id`/`task_id`/`fact_id`/`event_id`), each under
>    a unique constraint (auto-indexed), with type-prefixed values
>    (`dec:`/`task:`/`fact:`/`evt:`). Stable ids are read via `coalesce(...)` on
>    the RETURN (the existing vector.py already does this). Frontier traversal
>    (§4) matches **per type on the indexed id field**, never on a `node_id`.
> 2. **Jira lives in the same `:KnowledgeNode` space** (its LLM prose pass writes
>    Decision/Fact nodes), and content nodes carry no `source` property. The
>    `UNSTRUCTURED_SOURCE_FILTER` (owned by `domain/scopes.py`,
>    `any(s IN node.scope_ids WHERE NOT s STARTS WITH 'jira:')`) keeps v2 to the
>    four unstructured sources so it never double-serves the Jira path. A real
>    user holds both unstructured and Jira scopes, so the ACL gate alone does not
>    separate them — this filter does.

**b. Entity anchors** — Person/Topic/Organization nodes resolved from analysis entities. Anchors enter the frontier with seed score 1.0 (vector seeds carry their cosine score). This is what makes "what did Priya decide about the GST rollout" work even when the question text embeds poorly: Priya's Person node and the GST Topic node are guaranteed frontier entry points, and expansion walks `DECIDED` / `REGARDING` from there.

Time-range predicates from analysis apply to vector seeds and to every expansion hop.

### Stage 4 — Frontier expansion (`domain/retrieval/frontier.py`, new)

Port of `lore-graph-research/src/graph_expansion.py:expand_nodes` with these corrections:

| Research repo | v2 |
|---|---|
| Neo4j internal integer `node.id` | Stable property ids matched **per type on the indexed id field** (`decision_id`/`task_id`/`fact_id`/`event_id`/`person_id`/`topic_id`/`org_id`). Frontier is grouped by node type each hop; one batched UNWIND query per type present, each using that type's unique-constraint index. Neighbour ids derived via `coalesce(...)` on the RETURN (no scan — neighbours are bound by the traversal). |
| pandas DataFrames in hot path | Plain dataclasses (`BeamCandidate` stays, becomes a `core/types.py` citizen) |
| `print()` diagnostics | `@observe(name="retrieval.frontier-expand")` + structured log per hop (frontier size, pruned count, convergence events, neo4j round-trips) |
| No ACL in `get_neighbors_in_batch` | Scope gate **inside** the traversal Cypher (below) — out-of-scope nodes never enter the frontier, never influence scores, never leak via paths |
| `max_depth=5` | `max_depth=3` default (hop weights at depth 4–5 are 0.4/0.2 — they add latency, rarely add signal; setting-controlled) |
| Expansion rules for 3 labels | Full ontology rules (below) |

**Batched neighbor fetch, scope-gated (one round-trip per hop):**

```cypher
UNWIND $frontier_ids AS fid
MATCH (n {node_id: fid})-[r]-(m)
WHERE type(r) IN $allowed_relations
  AND ( m:Person OR m:Organization OR m:Topic OR {SCOPE_GATE_ON_m} )
RETURN fid AS parent_id, type(r) AS rel,
       m.node_id AS neighbor_id, labels(m)[0] AS neighbor_type,
       m.description AS description, m.name AS name,
       m.created_at AS created_at, m.source_thread_id AS source_thread_id
```

> **Spine nodes (Person/Organization/Topic) are traversable but never citable.** They carry no content and no scopes (they are the MERGE spine shared across sources); gating them would break all traversal. Content reached *through* them is still scope-gated, so no content leaks. Spine nodes appear in context only as names/attribution, never with content. This mirrors the fail-closed contract: content nodes with empty `scope_ids` are invisible.

**Expansion rules — full ontology** (replaces the research repo's 3-label table; weights seeded from `lore-graph-research/src/scoring.py:RELATION_WEIGHTS`, to be tuned against the golden set):

```python
EXPANSION_RULES = {
    "Decision": ["BECAUSE", "REGARDING", "SUBJECT_OF", "DECIDED", "CORROBORATES"],
    "Task":     ["ASSIGNED_TO", "REGARDING", "BECAUSE", "CORROBORATES"],
    "Fact":     ["STATED_BY", "REGARDING", "CORROBORATES"],
    "Event":    ["PARTICIPATED_IN", "REGARDING", "CORROBORATES"],
    "Person":   ["DECIDED", "ASSIGNED_TO", "STATED_BY", "PARTICIPATED_IN", "COMMUNICATES_WITH"],
    "Topic":    ["REGARDING", "SUBJECT_OF"],
    "Organization": ["REGARDING"],
}

RELATION_WEIGHTS = {
    "BECAUSE": 1.0,        # rationale chains are the highest-value traversal
    "REGARDING": 0.9,
    "DECIDED": 0.8,
    "ASSIGNED_TO": 0.8,
    "CORROBORATES": 0.75,  # cross-source confirmation; also feeds confidence
    "STATED_BY": 0.7,
    "PARTICIPATED_IN": 0.6,
    "SUBJECT_OF": 0.6,
    "COMMUNICATES_WITH": 0.3,  # high fan-out; useful only under PERSON_LOOKUP intent boost
}
```

**Scoring** (unchanged math from the research repo, proven in its evals):

```
score(candidate) = parent_score × relation_weight × hop_penalty[hop]
                   × intent_relation_prior × intent_label_boost
then per destination node: best path wins,
convergence bonus = 1 + 0.1 × (path_count − 1)
then beam prune to beam_width (default 30)
```

Hop penalties `{1: 1.0, 2: 0.8, 3: 0.6}`. Intent priors come from Stage 1 (e.g. `REASONING` → `BECAUSE`: 1.8; `PERSON_LOOKUP` → `Person` label boost). The traversal *path* of every kept candidate is retained for context assembly and debugging.

**Latency:** 2 batched Cypher round-trips per hop (neighbors + properties; collapse to 1 by returning properties inline as above) × ≤3 hops ≈ 3–4 graph queries total, each over ≤30 frontier nodes. Target ≤400 ms.

### Stage 5 — Ranking (`domain/retrieval/rank.py` + `infra/rerank/`, new)

1. **BM25** (port of `src/bm25_reranker.py`, `rank-bm25` dependency): corpus = candidate descriptions; `hybrid_score = α·graph_score_norm + β·bm25_norm` (start α=0.6, β=0.4; tune on golden set). Catches exact keywords/jargon that embeddings smear ("UPI", "v2 webhook", invoice numbers).
2. **Truncate to top-50**, batch-fetch rich properties for those only (one Cypher round-trip).
3. **Cross-encoder rerank** via new provider abstraction:

```python
# infra/rerank/base.py
class RerankProvider(Protocol):
    name: str
    def rerank(self, query: str, documents: list[RerankDoc]) -> list[RankedDoc]: ...
    @classmethod
    def from_settings(cls, settings: Settings) -> "RerankProvider": ...
```

- `infra/rerank/flashrank.py` — default; local ONNX (`ms-marco-MiniLM-L-12-v2`), zero per-query cost, ~50–150 ms for 50 candidates on CPU. Model loaded once in `api/main.py:lifespan`.
- `infra/rerank/nvidia.py` — NVIDIA reranker NIM (`nvidia/llama-3.2-nv-rerankqa-1b-v2`), config switch for when we don't want model weights in the container.
- `infra/rerank/registry.py` — one line per provider, `RERANK_PROVIDER` env var, exactly like the LLM/embedding registries.
- `final_score = w1·rerank_norm + w2·hybrid_norm` (start 0.7/0.3).
4. **Final cut is token-budgeted, not node-counted:** greedily take ranked nodes until the context budget (default 8k tokens, setting-controlled) is filled. Replaces the arbitrary 18-node cap in `domain/context.py`.

### Stage 6 — Context assembly (`domain/context.py`, rewritten)

Ontology-aware rendering, grouped to tell the LLM *what kind of knowledge it is*:

- **Decision** → description + `decided by {persons}` + `because: {BECAUSE facts, each with STATED_BY attribution}` + topics + date.
- **Fact** → description + `stated by` + `used in decisions: …`.
- **Task** → description + assignees + rationale facts.
- **Event** → description + participants + related decisions.
- **Cross-source corroboration** → when a kept node has a `CORROBORATES` edge to another kept node, render once with `corroborated across {gmail, slack}` instead of duplicating — this is a high-trust signal for synthesis and feeds confidence.
- **Provenance on every block:** `source_thread_id` / channel / session id rendered as a stable citation token (`[S3]`) that `domain/sources.py` maps to real Source objects for the SSE `sources` event. The synthesis prompt instructs citation by token.
- People section stays first (profiles from anchor resolution), then primary (top-ranked), then supporting — same shape the synthesis prompt already expects, so `domain/prompts.py` changes are minimal.

### Stage 7 — Synthesis (existing, minor prompt update)

Unchanged streaming path (`_synthesize_stream`). Prompt updated to explain corroboration markers and citation tokens.

**Confidence** (`domain/scoring.py`, rewritten): replace node-count heuristic with
`f(top rerank scores, convergence events, corroboration count, anchor-hit?)` → high/medium/low. Exact thresholds calibrated on the golden set; keep the Langfuse numeric score mapping.

---

## 4. ACL: the one scope gate

`domain/scopes.py` (new) is the **single owner** of the gate fragment; every retrieval Cypher interpolates it from there — never hand-written per query:

```cypher
-- SCOPE_GATE(node):
size(coalesce(node.scope_ids, [])) > 0
AND any(s IN node.scope_ids     WHERE s IN $allowed_scopes)
AND all(s IN coalesce(node.scope_all_ids, []) WHERE s IN $allowed_scopes)
```

- `$allowed_scopes` arrives from core-backend's Plane B injection (`allowedScopes` → request body), threaded `UserContext → QueryService → every retrieval function`. The `user_groups` parameter is **removed** from the unstructured path.
- Fail-closed both ways: empty `scope_ids` → invisible; empty `$allowed_scopes` → user sees nothing (and we log `query_zero_scopes` loudly rather than erroring).
- Applied at: vector seed search, every frontier hop (on content labels), rich-property fetch (defense in depth), anchor *expansion* (anchors themselves are spine).
- Legacy `access_groups` / `department_tags` / `sensitivity_label` predicates are deleted from this path; `domain/rbac.py` shrinks to whatever the Jira path still needs until it migrates too.

**Hard prerequisite (ingestion side):** Gmail/Slack `scope_ids` must actually be populated on all knowledge nodes in the live graph (ingestion emits them; prior session notes flagged a Gmail thread-grouping gap since fixed — must be *verified on the live Neo4j*, see rollout step 0). With fail-closed gates, unscoped nodes silently vanish — which is correct, but means a half-populated graph looks like a recall bug.

---

## 5. Module map (lore-engine changes)

```
domain/
  analysis.py                NEW  query-analysis prompt, Pydantic schema, keyword fallback
  scopes.py                  NEW  scope-gate Cypher fragment + UserScopes type (single owner)
  retrieval/
    seeds.py                 NEW  vector seeds + entity anchors (absorbs person.py resolution)
    frontier.py              NEW  beam expansion: rules, weights, BeamCandidate, batched Cypher
    rank.py                  NEW  BM25 + hybrid fusion + provider call + token-budget cut
    person.py                MOD  anchor profile expansion kept; regex resolution → fallback only
    vector.py                DEL  superseded by seeds.py (delete after parity)
    expansion.py             DEL  superseded by frontier.py (delete after parity)
    jira.py                  UNCHANGED
  context.py                 REWRITE  ontology-aware rendering, token budget, citation tokens
  scoring.py                 REWRITE  rerank/convergence/corroboration-based confidence
  prompts.py                 MOD  corroboration + citation-token instructions
infra/
  rerank/                    NEW  base.py, flashrank.py, nvidia.py, registry.py
services/
  query_service.py           MOD  new orchestration: analyze ∥ embed → seed → expand → rank → assemble → synthesize
core/
  settings.py                MOD  RERANK_PROVIDER, fast_llm_model, beam_width, max_depth,
                                  context_token_budget, analysis_timeout_ms
  types.py                   MOD  QueryAnalysis, BeamCandidate, UserScopes, RankedDoc
```

Layer rules preserved: all Cypher stays in `domain/retrieval/*`; `domain/` imports only `core/`; FlashRank model lifecycle lives in `api/main.py:lifespan` via `infra/`.

Dependencies added: `rank-bm25`, `flashrank` (both pure-Python/ONNX, no GPU).

---

## 6. Latency & cost budget (per query, pre-synthesis)

| Stage | Target | Notes |
|---|---|---|
| Analyze ∥ Embed | ≤800 ms | concurrent; analysis hard-timeout → keyword fallback |
| Vector seed + anchors | ≤150 ms | 2 Cypher queries |
| Frontier expansion | ≤400 ms | ≤3 batched round-trips |
| BM25 + property fetch | ≤100 ms | in-process + 1 Cypher |
| Cross-encoder (local) | ≤150 ms | 50 candidates, CPU ONNX |
| **Total to first synthesis token** | **≤1.6 s** | vs ~1 s today, for far better context |

LLM cost: exactly **+1 small structured call** per query (fast model). Embedding: 1 call (unchanged). Reranker: $0 with FlashRank default.

---

## 7. Rollout

0. **Verify graph readiness:** audit live Neo4j — % of Decision/Task/Fact/Event nodes with non-empty `scope_ids`, per source. Gate the cutover on ≥99% for Gmail+Slack. (Script: `scripts/audit_scope_coverage.py`, new.)
1. Build `domain/scopes.py` + `infra/rerank/` + `domain/analysis.py` (independent, unit-testable, no behavior change).
2. Build `seeds.py` / `frontier.py` / `rank.py` with the new orchestration in `QueryService`; old strategy code path deleted in the same PR that passes parity evals (decision: port-and-replace, not long-lived dual mode).
3. **Golden-set eval before cutover:** port the research repo's eval harness (`src/evaluate_retrieval.py`, `temp_full_pipeline_eval.py`) into `tests/eval/` against EZO Books data; compare v1 vs v2 on retrieval hit-rate and answer quality on ~30 curated questions (mix of person/decision/why/topic/temporal). v2 ships when it wins.
4. Langfuse: per-stage spans (`retrieval.analyze`, `retrieval.seed`, `retrieval.frontier-expand`, `retrieval.rank`) with frontier diagnostics as span metadata — the research repo's stdout diagnostics become structured traces.
5. Outlook/Teams arrive for free: they map to the same thread/session node shapes at ingestion, so this pipeline serves them with zero query-side changes.

## 8. Explicitly deferred

- **Community summaries / `GLOBAL` intent** — router slot reserved; build when `intent_global_unserved` traffic justifies it (needs an ingestion-time summarize+embed stage).
- **Query decomposition / multi-query retrieval** — analysis schema can grow a `sub_queries` field later; rejected for v1 latency.
- **Agentic / iterative retrieval, text2cypher** — revisit only if golden-set failures show multi-hop questions the frontier can't reach.
- **Learned weights** — relation/fusion weights start hand-tuned; revisit once Langfuse accumulates labeled traces.
- **Jira/GitHub unification** — separate plan per product owner.

---

## 9. Implementation status (2026-06-12)

**Built and wired as the active query path** (159 unit tests pass; no live DB/model needed):

| Module | State |
|---|---|
| `core/types.py` | `QueryAnalysis`, `TimeRange`, `UserScopes`, `BeamCandidate`, `RerankDoc`, `RankedDoc`; `ChatProvider` + `RerankProvider` Protocols moved here (so `domain/` never imports `infra/`); `UserContext.allowed_scopes` added |
| `core/settings.py` | rerank/fast-model/frontier/ranking settings + `analysis_model` derived prop |
| `domain/scopes.py` | NEW — fail-closed `scope_gate()` + `unstructured_source_filter()` (Jira exclusion) + Python mirrors |
| `domain/analysis.py` | NEW — query-analysis LLM call + JSON parse + keyword fallback |
| `domain/retrieval/ontology.py` | NEW — single owner of label/id-field/prefix graph-shape constants |
| `domain/retrieval/seeds.py` | NEW — vector seeds + entity anchors |
| `domain/retrieval/frontier.py` | NEW — beam expansion, per-type indexed match, scope-gated traversal |
| `domain/retrieval/rank.py` | NEW — BM25 hybrid + cross-encoder + token-budget cut |
| `domain/context.py` / `sources.py` / `scoring.py` | `*_v2` functions added (ontology-aware assembly, ranked sources, corroboration-aware confidence) |
| `domain/prompts.py` | corroboration marker note (no inline citation tokens — provenance stays in the separate `sources` event, per existing product behavior) |
| `infra/rerank/` | NEW — `base` (re-export), `flashrank`, `nvidia`, `registry` |
| `services/query_service.py` | rewritten to the v2 orchestration; `user_groups`→`user_scopes` |
| `api/auth.py`, `api/routes/query.py`, `api/main.py`, `api/dependencies.py` | `allowed_scopes` claim threaded; reranker built fail-soft in lifespan |
| `scripts/audit_scope_coverage.py` | NEW — rollout step 0 |

**Two doc corrections made during implementation** (see §3): no unified `node_id` (per-type indexed match), and `UNSTRUCTURED_SOURCE_FILTER` for Jira separation (Jira kept on its own path per LOR-63).

**Deviation from the original sketch:** no inline `[S3]` citation tokens — the existing synthesis prompt forbids inline citations and the frontend renders the `sources` event separately. Provenance flows through `format_sources_v2` unchanged.

**Pending before deleting superseded modules** (`domain/retrieval/vector.py`, `expansion.py`, `person.py`, and the legacy `build_context`/`format_sources`/`score_confidence`): run `scripts/audit_scope_coverage.py` against the live graph (gate ≥99% Gmail+Slack scoped) and the golden-set eval (step 3). Until then the old modules remain importable but unused by `QueryService`. Optional deps `rank-bm25` + `flashrank` install via `pip install '.[rerank]'`; both ranking and reranking degrade gracefully if absent.

### Consolidation update (2026-06-12) — Jira engine folded in

Both engines now run concurrently behind one orchestrator (§0). Changes on top of the above:

| Module | State |
|---|---|
| `domain/retrieval/unstructured.py` | NEW — unstructured engine; `retrieve_unstructured(...) -> list[SourcedChunk]` (seeds + frontier + ontology-aware chunk rendering) |
| `domain/retrieval/jira.py` | MODERNIZED — single-owner `scope_gate` (`$allowed_scopes`), `jira_source_filter` (source isolation), recency window injected (no `get_settings()`), `retrieve_jira_sync` entry, empty-scope short-circuit, `issue_id` returned by neighbourhood query (bugfix) |
| `domain/retrieval/rank.py` | `rank_chunks(query, chunks, reranker, settings)` over the merged `SourcedChunk` pool (was `BeamCandidate`-based) |
| `domain/context.py` / `sources.py` / `scoring.py` | `build_context_chunks` / `format_sources_chunks` / `score_confidence_chunks` (replaced the `_v2` variants) |
| `domain/scopes.py` | added `jira_source_filter` + `source_label`; `BeamCandidate` gained `scope_ids` |
| `services/query_service.py` | runs both engines concurrently, `_merge_chunks`, unified rank/assemble; `query()`/`query_stream()` accept `user_account_id` |
| `api/routes/query.py`, `api/auth.py` | thread `jira_account_id` through |

Engines are kept source-disjoint at the Cypher level (`unstructured_source_filter` excludes `jira:`, `jira_source_filter` requires it), so nothing is double-served. 163 unit tests pass. The no-routing decision is deliberate: an engine call is graph-only (no extra LLM cost), and the unified cross-encoder is the relevance filter.
