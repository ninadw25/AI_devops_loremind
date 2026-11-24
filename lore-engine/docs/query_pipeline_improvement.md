# Query Pipeline Improvement Plan

> **Scope:** `lore-engine` unstructured (Gmail/Slack/Outlook/Teams) query pipeline — the
> GraphRAG path defined in `docs/QUERY_PIPELINE_V2.md`.
> **Goal:** lower latency and raise answer quality while preserving the ontology-driven,
> fail-closed design.
> **Status:** proposal / backlog. Nothing here changes behaviour until implemented and
> measured against the eval harness (§12).

---

## 1. Current architecture (baseline)

The pipeline (`services/query_service.py`) is:

```
(analyze ∥ embed)
  → seed (vector ANN + entity anchors)         domain/retrieval/seeds.py
  → frontier/beam expand over ontology         domain/retrieval/frontier.py
  → merge (unstructured + jira) → unified rank  domain/retrieval/rank.py
  → assemble context                            domain/context.py
  → synthesize (LLM)                            services/query_service.py
```

**What is already strong (do not regress):**

- Fail-closed content-scope gate compiled into the Cypher itself (`domain/scopes.py`).
- Dual seeding: dense ANN (`vector_seeds`) + entity anchors (`entity_anchors`).
- Intent-conditioned beam expansion with relation weights × hop penalties × intent
  priors × label boosts, plus a **convergence bonus** for multi-path nodes
  (`frontier.EXPANSION_RULES`, `_INTENT_RELATION_PRIORS`, `_CONVERGENCE_FACTOR`).
- Hybrid rank: engine score ⊕ BM25 ⊕ cross-encoder, token-budgeted
  (`rank.rank_chunks`).
- "Both engines always run, no upfront router" — the unified cross-encoder drops
  whatever is irrelevant.

**Observed baseline (query: "What is the PIP process and who made it and why?",
ezobooks corpus read as a content super-admin, flashrank now installed —
this measurement predates the removal of the `QUERY_OPEN_ACCESS` flag, which
opened the gate the same way but globally):**

| Stage | Time |
|-------|------|
| embed + analyze | ~2.08 s (analysis LLM dominates) |
| retrieve (seed + 3-hop beam, 12 Neo4j queries) | ~0.46 s |
| synthesize (Bedrock Claude 3.5 Sonnet) | ~8.8 s |
| **total** | **~11.4 s** |

Key knobs (`core/settings.py`): `frontier_beam_width=30`, `frontier_max_depth=3`,
`vector_seed_top_k`, `graph_weight`, `bm25_weight`, `rerank_weight`, `hybrid_weight`,
`rerank_candidate_limit`, `context_token_budget`, `analysis_timeout_ms`.

Improvements below are ordered by leverage (impact ÷ effort).

---

## 2. Global / thematic layer (community summaries) — highest quality unlock

**Problem.** `GLOBAL` intent is stubbed: `query_service._embed_and_analyze` degrades it to
`TOPIC_EXPLORATION` and logs `intent_global_unserved`. Beam expansion is inherently
*local* (seed → 3 hops), so "what are the main themes?" / "summarise everything about
attendance issues" cannot be answered. These are query-focused **summarization**, not
retrieval.

**Leverage the fact that you are already 60% there.** `lore-ingestion/pipeline/comm_graph.py`
already runs community detection (union-find over the person graph), and a Topic spine
exists. The missing piece is *summarization*.

**How.**
1. At ingestion, after community detection, generate an LLM "community report" per
   cluster/topic (entities, recurring decisions, key facts) → store as
   `:CommunitySummary` nodes with embeddings.
2. At query, route `GLOBAL`/`SUMMARY` intents to a **map-reduce over community reports**
   instead of the beam.
3. **Cost control — LazyGraphRAG:** defer per-community summarization to query time and
   only for communities relevant to the query, rather than summarizing the whole graph
   up front.

**Research:** Microsoft GraphRAG / Local-to-Global QFS (arXiv 2404.16130); LazyGraphRAG;
DRIFT Search (global+local).

**Impact:** high · **Effort:** medium (reuses ingestion clustering).

---

## 3. Hybrid seeding — add a sparse / full-text seed source

**Problem.** Seeds are **purely dense ANN** (`_VECTOR_SEEDS_CYPHER`). BM25 only runs
*after* retrieval on the candidate pool, so a node that is never seeded can't be rescued.
Dense embeddings systematically miss rare tokens: invoice numbers, error codes, specific
names, GST/statutory terms.

**How.** Add `fulltext_seeds()` beside `vector_seeds()` using a **Neo4j native full-text
index** over `description`, then `merge_seeds(vector, fulltext, anchors)` (merge plumbing
already exists). Makes seeding true hybrid search.

**Research:** hybrid dense+sparse retrieval consistently beats dense-only on entity/keyword
queries.

**Impact:** medium-high · **Effort:** low.

---

## 4. Better entity linking for anchors

**Problem.** `entity_anchors` matches with `toLower(name) CONTAINS toLower(nm)` — raw
substring. Fails on misspellings, role references ("the finance head"), and aliases.
Meanwhile ingestion already built **cross-domain identity resolution / email-alias
merging** that is not used at query time.

**How.** (a) Load the identity/alias table into the anchor lookup; (b) fall back to
embedding-similarity entity linking over Person/Topic names when substring match returns
nothing.

**Impact:** medium-high (person queries are core) · **Effort:** low-medium.

---

## 5. Query decomposition for multi-hop / compound questions

**Problem.** Demonstrated by the baseline query itself: *"What is the PIP process **and who
made it and why**?"* is three sub-questions collapsed into one embedding vector, which
blurs the retrieval centroid (the "who made it" part came back empty).

**How.** Have the analysis LLM (already an LLM call) also emit `sub_queries: []`. Embed and
seed each independently, union candidate pools, rank once. Reuses the existing analysis
round-trip — no new call.

**Research:** least-to-most / query decomposition; strong multi-hop gains.

**Impact:** medium-high · **Effort:** low-medium.

---

## 6. Personalized PageRank retriever (HippoRAG) — algorithmic upgrade

**Problem / opportunity.** `frontier.expand()` is a hand-tuned heuristic
(`score = parent · relation_weight · hop_penalty · intent_prior · label_boost` +
convergence bonus). The convergence bonus is already a **poor man's PageRank**.

**How.** Keep intent priors as edge-weight biases, but run **Personalized PageRank** over
the scoped subgraph seeded at the query entities instead of fixed-width beam pruning. Ship
behind a flag and A/B against the golden set.

**Research:** HippoRAG / HippoRAG 2 (NeurIPS'24; arXiv 2405.14831, 2502.14802) — PPR for
multi-hop associative retrieval; Token-Efficient Graph RAG (arXiv 2509.18667) — PPR at
~80% of SOTA accuracy on **3–11% of output tokens**.

**Impact:** medium-high · **Effort:** medium · **Risk:** medium (A/B before default).

---

## 7. Adaptive retrieval depth + early exit — latency and precision

**Problem.** Every query pays fixed `beam_width=30, max_depth=3` (~12 Neo4j round-trips).
Simple lookups don't need 3 hops (adds noise + latency); reasoning may need more.

**How.**
- If the top vector-seed score is very high **and** intent is factual/lookup, answer from
  seeds and skip expansion.
- Scale `max_depth` by intent: `REASONING`/`GLOBAL` deep; `PERSON_LOOKUP`/`TASK_LOOKUP`
  shallow.

**Impact:** medium (cuts p50 latency, improves easy-query precision) · **Effort:** low.

---

## 8. Attack synthesis latency (the real time sink, ~8.8 s of 11.4 s)

Retrieval is already fast (~0.46 s). Synthesis dominates.

- **Stream by default** — `query_stream` already exists; wire the frontend to it so
  time-to-first-token is ~2–3 s instead of ~11 s wall-clock.
- **Prompt-cache the system prompt** — `SYNTHESIS_SYSTEM_PROMPT` is constant; Bedrock /
  Anthropic prompt caching cuts input cost + latency every call.
- **Adaptive model routing** — route factual/lookup intents to a cheaper/faster model
  (e.g. Haiku); reserve Sonnet for `REASONING`/`GLOBAL`. The provider registry is already
  swappable.
- **Trim the ~2 s intent call** — run `keyword_analysis` (already exists) first; only fire
  the LLM analyzer when intent is ambiguous or entities are present.

**Impact:** high (perceived latency) · **Effort:** low-medium.

---

## 9. Context hygiene — dedup and junk-node filtering

**Problem.** Baseline runs surfaced near-duplicate facts (*"PIP period is…"* ×5) and junk
placeholder nodes like *"No decision extracted from email"* / *"No supporting rationale
provided"*. These waste the token budget and dilute synthesis.

**How.** (a) Quality gate dropping placeholder/empty-extraction nodes — at ingestion and
defensively in `build_context_chunks`; (b) semantic dedup — cluster chunks by embedding
cosine, keep one representative per cluster before assembly.

**Impact:** medium (more distinct evidence per token) · **Effort:** low.

---

## 10. Fold recency / authority / corroboration into unstructured fusion

**Problem.** The Jira path applies recency/authority priors in final fusion; the
unstructured path does not.

**How.** Extend `rank.rank_chunks` final fusion for unstructured chunks to include a
recency decay (nodes carry `created_at`), a source-authority prior, and the corroboration
signal already computed in expansion (`path_count` / `CORROBORATES`).

**Impact:** medium · **Effort:** low.

---

## 11. Answer grounding + calibrated confidence

**Problem.** No post-synthesis check that claims trace to retrieved chunks.
`score_confidence_chunks` is retrieval-based only.

**How.** Lightweight grounding pass (align each answer sentence to a citation; flag
unsupported claims) — CRAG / Self-RAG style. At minimum, recalibrate displayed confidence
using the now-available cross-encoder rerank scores (post-flashrank), which are far more
meaningful than raw beam scores.

**Impact:** medium (trust) · **Effort:** medium.

---

## 12. Evaluation — measure everything (do this first)

Every change above is a guess until measurable. Two **distinct** eval layers:

### 12a. Extraction-quality track — ALREADY IN FLIGHT (LOR-73 / LOR-85 / LOR-74)
- **LOR-73 golden dataset — done, frozen v1.** 47 hand-labeled ezobooks threads across 8
  categories; guide composite inter-labeler agreement 0.813. Format:
  `{thread_id: {source_text, category, extraction:{triples, thread_summary,
  department_tags, sensitivity}}}` (`lore-ingestion/golden_evaluation_dataset/`).
- **LOR-85 quality metric — specced, not built.** structural (validator) + coverage P/R/F1
  (rapidfuzz node match + predicate equality) + LLM-judge semantic.
- **LOR-74 Bedrock model R&D — downstream of LOR-85.**
- This measures *ingestion: email → triples*. It is the **ceiling** for retrieval quality
  but says nothing about whether the engine retrieves the right nodes or synthesizes
  faithfully.

### 12b. Retrieval / answer-quality track — MISSING (needed for §2–§11)
Bootstrap it off the existing golden set instead of rebuilding:
- The engine's `SourcedChunk.citations = [source_thread_id]`; golden is keyed by
  `thread_id` → direct join for **context precision/recall** ("for a question about golden
  thread T, did retrieval surface T's nodes?").
- Generate one question per golden thread from its `extraction` (LLM drafts, human
  quick-check). The `category` field enables per-query-type reporting that maps onto the
  intent classes.
- Layer RAGAS-style **faithfulness / answer-relevance** on the synthesized answer.

---

## 13. Phased roadmap

**Phase 1 — measure + cheap wins (low risk, immediate):**
1. Finish **LOR-85** extraction scorer (unblocks LOR-74).
2. Stand up the **retrieval/answer eval** layer (§12b) reusing the 47 golden threads.
3. **Synthesis latency** (§8): stream by default, prompt cache, keyword fast-path.
4. **Hybrid full-text seeds** (§3) + **context hygiene** (§9).

**Phase 2 — quality:**
5. **Community summaries** for global questions (§2) — biggest quality ceiling.
6. **Entity linking** (§4) + **query decomposition** (§5).

**Phase 3 — algorithmic:**
7. **PPR retriever** behind a flag, A/B on the golden set (§6).
8. **Adaptive depth** (§7) + **fusion priors** (§10) + **grounding/confidence** (§11).

**Start today:** LOR-85 scorer (already specced) and the retrieval-eval layer, so §2–§11
land as measured A/Bs rather than guesses. Community summaries are the highest-quality
unlock for the least new infrastructure because ingestion clustering already exists.

---

## 14. Done

- **flashrank cross-encoder installed** and active (`rerank_provider=flashrank`,
  `ms-marco-MiniLM-L-12-v2`) — was previously off because the optional dependency was
  missing (`pip install 'lore-backend[rerank]'`). Visibly improved ranking (promoted PIP
  *Decision* nodes above generic performance *Facts*).

---

## 15. References

- Microsoft GraphRAG — *A Graph RAG Approach to Query-Focused Summarization*, arXiv
  2404.16130.
- LazyGraphRAG — Microsoft Research (quality/cost via deferred summarization).
- DRIFT Search — Microsoft Research (combined global + local).
- HippoRAG — *Neurobiologically Inspired Long-Term Memory for LLMs*, arXiv 2405.14831 (NeurIPS'24).
- HippoRAG 2 — *From RAG to Memory*, arXiv 2502.14802.
- *Token-Efficient Graph-Based RAG*, arXiv 2509.18667 (PPR, low output-token cost).
- Self-RAG / CRAG — retrieval self-reflection & correction.
- RAGAS — retrieval/answer quality metrics (context precision/recall, faithfulness,
  answer relevance).
