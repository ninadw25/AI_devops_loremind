> **Historical (as of 2026-06-10)** — superseded by `docs/JIRA_INTEGRATION.md`, `domain/retrieval/jira.py` (`retrieve_jira_sync`, `scope_gate` with `$allowed_scopes`) and `domain/retrieval/rank.py` + `infra/rerank/` (cross-encoder now implemented). Kept for context; do not treat as current.

# Jira Retrieval Findings

Experiments run against 12 queries × 9 strategies on the `loremind` demo dataset (158 issues across OPS, ENG, HR, EDGE, SCRUM projects). All timings are end-to-end retrieval latency from query to `list[SourcedChunk]`, excluding embedding generation (~100–200ms for `nvidia/llama-nemotron-embed-1b-v2` over network).

---

## Retrieval Paths

Three paths were implemented and tested. Every path ends at the same place — an Issue with its full neighbourhood (assignee, reporter, decisions, facts). The difference is how you get there.

### 1. Navigational
Triggered by an issue key (`OPS-2`) or a person name with a work/status signal.

- Issue key → direct lookup by `i.key` → neighbourhood expand
- Person name → match `p.name CONTAINS fragment` → fetch assigned/reported issues → neighbourhood expand

Best for: exact lookups where you know the identifier or the person. **Latency: 3–15ms** (single Cypher + neighbourhood expand).

### 2. Analytical
Triggered for open-ended topic or decision queries.

- Embed query → vector search over `Decision` and `Fact` nodes → `DERIVED_FROM` traversal to source `Issue` → neighbourhood expand

Best for: "what decisions were made about X", "what do we know about Y". **Latency: 60–100ms** (ANN + N neighbourhood expands, where N = k top hits).

### 3. Status Filter
Triggered when query contains a status keyword (blocked, in progress, done, pending, completed).

- Detect keyword → map to canonical Jira status → `MATCH (i:Issue) WHERE toLower(i.status) = $status` → neighbourhood expand

Best for: status-based questions. This path exists because vector search cannot find status field values — "blocked" never appears in Decision or Fact text. **Latency: 5–30ms**.

---

## Strategy Comparison

| Strategy | Avg ms | Min ms | Max ms | Avg score | Has Issue % | Notes |
|---|---|---|---|---|---|---|
| hybrid_k10 | 64 | 3 | 98 | 0.712 | 92% | Production default |
| hybrid_k5 | 43 | 2 | 71 | 0.711 | 92% | Faster, misses context for person queries |
| hybrid_k20 | 80 | 2 | 142 | 0.712 | 92% | More noise, no quality gain |
| navigational_only | 66 | 2 | 169 | 0.712 | 92% | Identical to hybrid (fallback fires) |
| analytical_only | 82 | 2 | 111 | 0.620 | 92% | Slower + lower scores on key-based queries |
| **pure_vector** | 51 | 48 | 56 | 0.642 | **0%** | Baseline: no graph traversal |
| **graph_first** | 14 | 2 | 30 | 0.400 | 50% | Fastest, but fails semantic queries |
| **agentic_heuristic** | 58 | 2 | 106 | 0.712 | 92% | Matches hybrid; slight win on status routing |
| **hybrid_rerank** | 56 | 2 | 105 | 0.712 | 92% | Reorders for better LLM context quality |

---

## Experiment Results Per Query

| Query type | Best path | Chunks (k=10) | Top score | Latency |
|---|---|---|---|---|
| Issue key (`OPS-2`) | Navigational / Agentic | 1 | 1.00 | ~3ms |
| Person name (Arnav) | Navigational | 16 | 0.77 | ~40ms |
| Named topic (AWS contract) | Analytical | 18 | 0.80 | ~90ms |
| Hiring decisions | Analytical | 18 | 0.71 | ~90ms |
| Infrastructure decisions | Analytical | 17 | 0.63 | ~85ms |
| Open engineering issues | Status filter ("open" → "To Do") | 10 | 0.95 | ~15ms |
| Status (blocked/done/in-progress) | Status filter | varies | exact | ~15ms |
| Security/compliance | Analytical | 14 | 0.57 | ~80ms |
| Vague (OPS project) | Analytical | 11 | 0.57 | ~70ms |

---

## Key Findings

**1. Navigational dramatically outperforms analytical for key-based queries.**
Exact key lookup returns 1 chunk at score 1.0 in 3ms. Forcing analytical on the same query returns 15 noisy chunks at ~0.60 in 90ms. Never use vector search when an exact identifier is present.

**2. Analytical works well for named topics, poorly for structural questions.**
Scores of 0.70–0.80 indicate strong semantic match. Scores below 0.60 (security, vague queries) indicate weak match — the engine is returning something but without confidence. The synthesis layer should treat low-score chunks with lower weight.

**3. Status queries require a dedicated path.**
"Which issues are blocked?" returned wrong results (score 0.59, top chunk: "close this bug") before the status-filter path was added. After adding it, status queries return correct results via direct field lookup at ~15ms. This is a fundamental limitation of vector search — it cannot answer questions about field values.

**4. Intent classifier had zero misclassifications.**
All 12 queries were correctly classified across all 108 runs (12 queries × 9 strategies). The classifier correctly routes issue key, person, and topic queries without overlap.

**5. k=10 is the right default.**
- k=5 saves ~20ms but misses context — person queries return fewer neighbourhood chunks
- k=10 returns complete context at 64ms avg
- k=20 adds ~16ms and doubles chunk count with no score improvement

**6. Pure vector confirms graph traversal is essential (Strategy 1).**
Removing DERIVED_FROM traversal and neighbourhood expand:
- avg_score drops from 0.71 → 0.64
- **has_issue drops from 92% → 0%**

Decision/Fact chunks alone are insufficient — users need Issue context (status, assignee, sprint, linked decisions). Graph traversal is load-bearing, not decorative. The consistent ~50ms latency (vs 3–100ms range for hybrid) also shows it's not faster in practice for most query types.

**7. Graph-first fails for semantic queries (Strategy 2).**
Text search on Issue.description returns 0 chunks for 7 of 12 queries — avg 2.2 chunks, avg score 0.40. Queries like "what hiring decisions were made?" produce no matches because the word "hiring" does not appear literally in any Issue description field. The Decision/Fact layer with vector embeddings is the correct semantic entry point. Graph-first via text search is a viable supplementary path for queries that contain literal issue terminology, but not as a standalone strategy.

**8. Agentic heuristic matches hybrid with one edge advantage (Strategy 4 — simplified).**
The agentic strategy was implemented as deterministic rule-based routing (key → key lookup, person → person lookup, status → status filter, else → analytical) rather than LLM-driven tool selection. This proved equivalent to hybrid_k10 on quality (0.712 avg score, 92% has_issue) with slightly lower avg latency (58ms vs 64ms). A navigational→analytical fallback is in place: if navigational returns no results (key not found, person not matched), the pipeline falls back to vector search automatically.

One notable win: "what are the open engineering issues?" is routed to the status filter via "open" → "To Do" keyword detection, returning 10 issues at score 0.95 vs 0.62 for the hybrid's analytical path.

Full LLM-driven agentic (giving the model Cypher tools to call iteratively) was not implemented for the experiment — the heuristic produces equivalent routing decisions at zero LLM cost per retrieval call. True LLM-agentic is appropriate only when query intent is genuinely ambiguous and cannot be resolved deterministically.

**9. Reranking works — limited demo data effect, will matter in production (Strategy 6 — complete).**
`hybrid_rerank` now applies all three signals: status relevance, recency (`created_at_jira`), and person proximity (issues assigned to/reported by the querying user). Reranking changed the top-3 order in 7 of 12 queries. Top-score metrics are unchanged because the top chunk is almost always a Decision/Fact node with a high vector score (unaffected by reranking — only Issue nodes are reordered).

The recency window (default 90 days) is configurable via `JIRA_RECENCY_WINDOW_DAYS` in `.env`. Issues older than the window score 0.0 on recency. Tune once production query data is available.

Two demo data limitations suppress the visible effect:
- All issues were ingested within days of each other → `created_at_jira` variation is only ~7 days → recency signal is weak in demo
- Test user (Reia Baid) has EDGE project issues; test queries target OPS/ENG topics → proximity boost rarely fires

In production with issues spread over months and users querying their own projects, recency and proximity will significantly change which Issues surface at the top of the context window sent to the LLM.

---

## Recommended Production Configuration

```
strategy: hybrid (route: status → navigational → analytical fallback)
k: 10
status_check: before intent routing, suppressed when person name is present
rerank: rerank_chunks(chunks, graph, user_scopes, user_account_id)
        — status relevance + recency (created_at_jira) + person proximity
        — pass user_account_id from JWT/session; None disables proximity only
fallback: navigational → analytical if navigational returns nothing
recency_window: JIRA_RECENCY_WINDOW_DAYS (default 90, tune from production data)
```

`agentic_heuristic` is a valid upgrade path: it removes the navigational→analytical fallback (reducing noise for failed key lookups) at a 6ms average latency reduction. Promote it when the intent classifier has been validated on production traffic.

---

## Production Function Interface

The implemented signature diverges from the ticket sketch for architectural reasons:

```python
async def retrieve_jira(
    query: str,
    query_embedding: list[float],   # pre-computed by service layer
    user_scopes: list[str],
    org: str,
    k: int = 10,
    *,
    graph: GraphRepository,         # injected from app.state via lifespan
    user_account_id: str | None = None,  # Atlassian accountId from JWT; enables proximity reranking
) -> list[SourcedChunk]: ...
```

`query_embedding` is passed by the caller (`services/query_service.py`) because the embedding provider lives in `infra/` — passing it into `domain/` would violate the layer boundary. `graph` is dependency-injected for testability (all 19 RBAC tests run without a live Neo4j using a fake graph). The ticket's `(query, user_scopes, org, k)` sketch was a simplified interface that predated the finalised architecture.

---

## RBAC Integration

Every Cypher template that touches scoped nodes contains the fail-closed gate:

```cypher
WHERE n.scope_ids IS NOT NULL
  AND size(n.scope_ids) > 0
  AND any(s IN n.scope_ids WHERE s IN $user_scopes)
  AND all(s IN coalesce(n.scope_all_ids, []) WHERE s IN $user_scopes)
```

This is verified by `tests/test_jira_rbac.py` (19 tests, all passing):
- All 5 Cypher templates are asserted to contain the gate
- Wrong-scope user → empty results for both navigational and analytical paths
- Correct-scope user → results visible
- `scope_all_ids` AND gate enforced for security-level scoping
- Status-filter path also gated
- Graph unavailable → returns `[]` (not an error)
- Intent classifier tested against 6 representative queries with 0 misclassifications

Additional test coverage:
- `tests/test_jira_rerank.py` (13 tests) — edge cases for `rerank_chunks`: missing timestamps, future dates, timezone-naive, malformed, non-Issue passthrough, 90-day boundary
- `tests/test_jira_routing.py` (36 tests) — synthetic routing tests: `_detect_status`, `_classify_intent`, and end-to-end path selection via `_PathTrackingGraph`; includes RBAC scope miss, multiple issue keys, status false positive suppression

---

## Known Gaps

**Missing status values in demo data.**
No issues have status "Blocked" in the demo dataset — status filter returns 0 results for "blocked" queries. Not a code bug; a data gap.

**Person name matching is fragile.**
Uses `CONTAINS` on `p.name`. Nicknames, partial names, or typos will miss. Production should normalise names at ingestion and use a fuzzy match.

**Vague/broad queries return low-confidence results.**
Queries like "tell me about OPS project" or "what security decisions exist" score 0.55–0.57. Consider returning a "not enough information" response below a configurable score threshold rather than synthesising from weak chunks.

**Reranking is fully implemented (status, recency, person proximity).**
`rerank_chunks(chunks, graph, user_scopes, user_account_id)` applies all three signals. Cross-encoder reranking is not implemented — the ticket marked it as latency-budget dependent and it requires a separate cross-encoder model.

**Sprint, component, and recency data absent in demo.**
`IN_SPRINT`, `HAS_COMPONENT` relationship types and `updated_at_jira` property don't exist on demo nodes. Neo4j warns on every neighbourhood expand query. Sprint/component fields always return empty, and `created_at_jira` falls back to empty string (recency defaults to 0.5). This is a data gap, not a code issue — production nodes ingested via the full pipeline will have these fields.

**Gmail/Slack nodes not yet scoped.**
Old Gmail and Slack nodes have empty `scope_ids` and are invisible to the RBAC gate. Cross-source synthesis (Jira + Gmail) is blocked until Gmail/Slack ingestion writes `scope_ids`.

**Graph-first is not viable as a standalone strategy.**
Text search on Issue descriptions cannot handle semantic or conceptual queries. It could augment analytical results for literal-keyword queries but not replace the vector path.

**First-name-only person queries return empty (deferred).**
`_PERSON_NAME_RE` requires two capitalised words (e.g. "Arnav Gupta"). A query like "what is Arnav working on?" does not match the regex — the person path never fires and the query falls back to analytical vector search. Fix requires matching query tokens against a known person registry loaded from `{org}_jira_person_scopes.json`. Deferred to cross-source identity resolution ticket.

**Status keywords in person queries were causing false positives (fixed).**
Queries like "what has Arnav done this sprint?" contained the keyword "done", which caused the status filter path to fire and return all Done tasks — ignoring the person context entirely. Fixed in `_sync_retrieve`: status path is now suppressed when a person name is detected in the query.
