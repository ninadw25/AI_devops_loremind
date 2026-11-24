# Jira Query Engine — Integration (consolidated pipeline)

**Status:** DONE (2026-06-12). The Jira engine is wired into the live synthesis
path alongside the unstructured engine. This supersedes the original plan, which
targeted the pre-refactor `QueryService` (`_retrieve_full`, `user_groups`,
`build_context(RetrievalResult)`) — all of which were replaced by the v2
consolidation. See `QUERY_PIPELINE_V2.md` §0.

---

## How it works now

`services/query_service.py` runs **both engines concurrently** inside
`_retrieve()` (a `ThreadPoolExecutor`), then merges and ranks them together:

```
retrieve_unstructured(graph, embedding, analysis, scopes, …)   # engine A
retrieve_jira_sync(question, embedding, scopes, k, graph,       # engine B
                   user_account_id, recency_window_days)
        │
        └─► _merge_chunks(...)  → rank_chunks(...)  → build_context_chunks(...)
```

Both return `list[SourcedChunk]`, so merge/rank/assemble/cite are source-agnostic.
There is **no routing gate** deciding whether to call Jira — it always runs
(it's just graph queries, no extra LLM call), and the unified cross-encoder
rerank drops whatever isn't relevant.

## What changed in `domain/retrieval/jira.py` (modernization)

The module came from PR #3 (`lor-62`), written before the engine refactor. To
meet current standards it was updated **without changing its retrieval
strategy** (navigational / status / analytical intent routing is unchanged):

1. **Single-owner scope gate.** All hand-written `scope_ids IS NOT NULL AND …`
   gates replaced by `domain/scopes.py::scope_gate(var)`; the Cypher parameter
   is now `$allowed_scopes` (was `$user_scopes`), bound via `scope_params`.
   Same fail-closed semantics, one definition shared with the unstructured path.
2. **Source isolation.** The shared Decision/Fact vector search now ANDs in
   `jira_source_filter('n')` (`scope_id STARTS WITH 'jira:'`), so it can no
   longer pull Gmail/Slack/Outlook/Teams knowledge into Jira results. This was a
   real cross-contamination bug (it tagged non-Jira nodes `source="jira"`).
3. **No env reads in domain.** `rerank_chunks` and `_sync_retrieve` take
   `recency_window_days` as a parameter; `QueryService` passes
   `settings.jira_recency_window_days`. The `get_settings()` call is gone.
4. **Sync entry for the orchestrator.** `retrieve_jira_sync(...)` exposes the
   sync core so both engines run in one thread pool. The async `retrieve_jira()`
   wrapper is retained for async callers/tests.
5. **Fail-closed short-circuit.** `_sync_retrieve` returns `[]` immediately when
   `user_scopes` is empty (mirrors the gate; avoids pointless queries).
6. **Bugfix.** `_ISSUE_NEIGHBOURHOOD_CYPHER` now returns `issue_id`, so Jira
   issue chunks carry a real `node_id` (previously `""`, which the merge
   correctly dropped).

## Identity / proximity

`user_account_id` (Atlassian accountId) flows from the gateway's `X-Account-Id` header / `x-account-id` gRPC metadata (JWT `account_id` claim as fallback) →
`UserContext.jira_account_id` → `QueryService.query(..., user_account_id=…)` →
the Jira engine's person-proximity rerank signal. `None` disables proximity only.

## What does NOT change

- The Jira retrieval strategy (intent routing, neighbourhood expansion, the
  status/recency/proximity heuristic rerank — which now feeds the chunk score
  that the unified cross-encoder reranks over).
- `core/types.py::SourcedChunk` — already the universal envelope; now used by
  both engines.
- RBAC — enforced in Cypher (single-owner gate), fail-closed.

## Deferred

- First-name-only person queries → cross-source identity resolution.
- Tune unified fusion weights + Jira recency weight from production traces.
