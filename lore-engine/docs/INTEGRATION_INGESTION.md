# Integration: lore-engine ↔ lore-ingestion

**Short answer: there is no runtime connection, and you should not add one.**
The two services are intentionally decoupled — ingestion *writes* the Neo4j
graph (+ artifacts), lore-engine *reads* it at query time. They communicate
only through **shared data contracts**, never a live API call. This doc records
those contracts and what (if anything) would change in ingestion.

---

## 1. Why no direct connection

- ingestion is a batch/scheduled producer; lore-engine is a low-latency request
  serving consumer. Coupling them at runtime would put batch jobs on the query
  hot path and break independent deploy/scaling.
- The contract is the **Neo4j graph itself** (shape, ids, scopes, embeddings)
  plus a few on-disk artifacts that `elysia-rbac` consumes. As long as the
  contract holds, either side can change freely.

Keep them decoupled. If a future feature *seems* to need ingestion at query
time (e.g. "ingest this thread now and answer"), prefer: lore-engine enqueues a
job → ingestion processes → graph updates → next query sees it. Don't make
lore-engine call ingestion synchronously.

## 2. The shared contracts (invariants that MUST hold)

| Contract | Owner | Consumer | Breaker |
|---|---|---|---|
| **Embedding model + dim** | ingestion (`pipeline/embed_knowledge`) | lore-engine (query embed) | If the model that produced `node.embedding` ≠ the model lore-engine embeds queries with, vector search returns meaningless neighbours — **silently**. Both default to `nvidia/llama-nemotron-embed-1b-v2` / 2048-dim today; any `.env` override must change **both** repos in lockstep. |
| **`knowledge_embeddings` vector index** on `:KnowledgeNode.embedding` | ingestion (builder pass 0) | both engines | Missing index → no vector seeds. |
| **Node id scheme** (`dec:`/`task:`/`fact:`/`evt:`/`person:`/`org:`/`topic:` prefixes; per-type id property + unique constraint) | `common/hashing.py` + builder | frontier per-type matching | Renaming a prefix or id field silently breaks traversal. |
| **Scope id formats** (`slack:`/`teams:`/`ms365:`/`outlook:`/`jira:…`) | `common/scopes.py` | lore-engine source filters + elysia-rbac | lore-engine separates engines by these prefixes (`unstructured_source_filter` excludes `jira:`, `jira_source_filter` requires it) and labels chunk source from them. A new source prefix must be taught to `domain/scopes.py`. |
| **`scope_ids` / `scope_all_ids` populated** on content nodes | ingestion | lore-engine fail-closed gate | Empty/absent → node invisible. This is the readiness gate (`scripts/audit_scope_coverage.py`, ≥99% for Gmail/Slack). |
| **`source` discriminator** | — | lore-engine | NOT a property today; lore-engine infers source from scope-prefix. See §4 optional improvement. |

## 3. Does anything need to change in ingestion *right now*?

**No — nothing is required** for the query pipeline to work, provided the §2
invariants hold (they do today). The query consolidation was implemented
entirely query-side against the existing graph shape.

## 4. Optional ingestion improvements (not required)

1. **Stamp a `source` property** (`'gmail'|'slack'|'outlook'|'teams'|'jira'`) on
   every content node in the builder. Today lore-engine *infers* source from
   `scope_id` prefixes, which works because the prefixes are distinct — but an
   explicit `source` property would be more robust and let the engines filter on
   `n.source` instead of prefix string-matching. One-line `SET` in
   `builders/unified_builder.py` + a backfill query. Low priority.
2. **Fix stale docstrings**: `infra/embeddings/nvidia.py` and a couple of
   CLAUDE.md lines still name `nvidia/llama-3.2-nemoretriever-300m-embed-v1`; the
   actual default is `nvidia/llama-nemotron-embed-1b-v2`. Cosmetic, but it's the
   kind of drift that causes the §2 embedding-model footgun — worth correcting.

## 5. The other ingestion coupling: elysia-rbac consumes artifacts

This is **not** lore-engine's concern, but it's part of the integration map, so
note it: `elysia-rbac` loads ingestion's scope/identity
artifacts from `lore-ingestion/output` (`ARTIFACT_LOCAL_PATH`, or S3 later) to
compute `allowedScopes`. So ingestion → elysia-rbac is a (batch, file-based)
coupling. If ingestion changes the scope-principal artifact format, elysia-rbac
must follow — but lore-engine is unaffected (it only reads the graph).

```
ingestion ──writes──► Neo4j graph ──read──► lore-engine
        └──writes──► scope/identity artifacts ──read──► elysia-rbac ──► allowedScopes ──► gateway ──► lore-engine (body)
```
