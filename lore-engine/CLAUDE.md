# CLAUDE.md — lore-engine

> **Dormant.** lore-engine is not deployed in prod since the CA-firm pivot; it is kept for possible future use. Don't assume changes here reach users.

This file guides Claude Code when working inside `lore-engine/` (formerly `lore-backend`; `pyproject.toml` still uses that name).

## Quick commands

```bash
# Always run from lore-engine/ using the shared venv
../.venv/bin/uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload

# Tests
../.venv/bin/pytest tests/ -x -q

# Single source-of-truth audit. Known exceptions today: infra/{llm,embeddings}/bedrock.py,
# chronograph/query/query_engine.py, alembic/env.py and a few standalone scripts/ (see D-ENGINE-001)
grep -rn "os\.getenv\|os\.environ" --include="*.py" . | grep -v core/settings.py | grep -v tests/

# Layer boundary audit (domain/ must not import from infra/)
grep -rn "^from infra" domain/ --include="*.py"
```

## Architecture — 5 layers (dependencies point inward only)

```
api/        HTTP handlers, auth, DI wiring, error mapping
services/   Orchestration — composes domain + infra
domain/     Pure logic — no IO, no env reads, fully unit-testable
infra/      External adapters — Neo4j, PostgreSQL (memory + pgvector knowledge), LLM providers, embeddings
core/       Settings, logging, observability, exceptions, types — no upward deps
```

**The rule:** outer layers import inner layers. Never the reverse. `domain/` imports only from `core/`. `infra/` imports only from `core/`. `services/` imports from `domain/` + `infra/` + `core/`. `api/` imports from `services/` + `domain/` + `core/`.

## Key files and what they own

| File | Single responsibility |
|------|----------------------|
| `core/settings.py` | **Only place `os.getenv` is called.** All config flows from `get_settings()`. |
| `core/exceptions.py` | Typed `LoreError` hierarchy. Every error in the system is a subclass. |
| `core/types.py` | Shared types. v2 pipeline: `UserContext`, `UserScopes`, `SourcedChunk`, `QueryAnalysis`, `RerankProvider`, `GraphOp`. `KnowledgeNode`/`RetrievalResult`/`GraphRepository` remain for the legacy v1 path. |
| `core/observability.py` | Single Langfuse client. No-op if disabled. Import `lf`, `observe` from here only. |
| `core/logging.py` | JSON structured logger. `configure_logging()` called once at startup. |
| `infra/llm/registry.py` | Maps `settings.llm_provider` → concrete `ChatProvider`. One line per provider. |
| `infra/embeddings/registry.py` | Same for embedding providers. |
| `infra/rerank/registry.py` | Same for rerank providers (`flashrank` \| `nvidia`). |
| `infra/tenant_registry.py` | Per-org Neo4j/Postgres resolution via elysia-rbac when `TENANT_ROUTING_ENABLED`. |
| `infra/neo4j_client.py` | Driver lifecycle + `run(cypher, **params)`. No Cypher templates here. |
| `domain/retrieval/*.py` | Cypher templates live next to the function that uses them. |
| `domain/scopes.py` | Query-time ACL: fail-closed `scope_gate` (`scope_ids` OR, `scope_all_ids` AND, audited `super_admin`). `domain/rbac.py` is legacy and not on any query path. |
| `services/query_service.py` | Query pipeline orchestrator (v2): (analyze ∥ embed) → unstructured + Jira engines concurrently → merge → BM25 + cross-encoder rank → assemble context → synthesize. See `docs/QUERY_PIPELINE_V2.md`. `domain/retrieval/{vector,expansion}.py` are legacy v1 and not on this path. |
| `services/memory_service.py` | MemoryManager wrapper with tracing + typed error mapping. |
| `api/dependencies.py` | All FastAPI `Depends` factories. Resources read from `app.state`. |
| `api/errors.py` | `LoreError` → HTTP status code mapping. One place. |
| `api/main.py:lifespan` | Builds all resources, attaches to `app.state`. Shuts them down in reverse. |
| `api/auth.py:require_gateway` | The only way in: constant-time `X-Internal-Auth` check against `INTERNAL_AUTH_SECRET`. Fails closed (503 when unset). No login/JWT-issuing path. |
| `lore_engine_grpc/`, `proto/lore/engine/v1/` | gRPC server (`lore.engine.v1`: Query, QueryStream, ListThreads, ListTurns, DeleteThread), started when `GRPC_ENABLED`. Regenerate stubs with `scripts/gen_grpc.py`. |

## Adding a new LLM provider

Example: adding Anthropic.

1. Create `infra/llm/anthropic.py` with a class satisfying `ChatProvider` Protocol (`name`, `model`, `chat()`, `chat_stream()`, `from_settings()`).
2. Add one line in `infra/llm/registry.py`: `"anthropic": AnthropicChatProvider.from_settings`
3. Extend the `Literal` in `core/settings.py`: `llm_provider: Literal["nvidia", "openai", "openrouter", "bedrock", "anthropic"]`
4. Add `anthropic_api_key: str = ""` to `Settings` if needed.
5. Set `LLM_PROVIDER=anthropic` in `.env`.

Zero other files change. Run `pytest` to verify.

## Adding a new route

1. Create `api/routes/your_route.py`. Use `Depends(get_query_service)` / `Depends(get_memory_service)` — never import services directly.
2. Include in `api/main.py` under the appropriate auth group.
3. If the route needs a new service, build it in `lifespan()` and attach to `app.state`. Add a getter to `api/dependencies.py`.

Never add module-level globals or `set_X()` functions to routes. That pattern is gone.

## Observability

Every IO-path function should have `@observe(name="...")` from `core.observability`. The format is `layer.verb-noun`: `retrieval.vector-search`, `memory.save-turn`, `domain.build-context`.

Correlation context (`request_id`, `user_id`, `thread_id`) is injected automatically into every log line via `core/logging.py` contextvars. Set it with `set_correlation(user_id=user_id)` — this is already done in `api/auth.py` and `api/routes/query.py`.

## Testing

```bash
../.venv/bin/pytest tests/ -x -q          # all tests
../.venv/bin/pytest tests/memory/ -q      # memory layer only
../.venv/bin/pytest tests/test_query_with_memory.py -v   # integration smoke
```

The integration test in `tests/test_query_with_memory.py` uses a fake `QueryService` and `InMemoryStore` — no live databases needed.

Two tests are skipped without a live `MEMORY_POSTGRES_URI` set (`tests/integration/test_postgres_store.py`).

## What NOT to do

- Do not call `os.getenv()` anywhere except `core/settings.py`.
- Do not add module-level globals to routes. Use `app.state` + `Depends`.
- Do not import `infra.*` from `domain/`. Use `core.types.GraphRepository` Protocol.
- Do not add Cypher queries to `infra/neo4j_client.py`. They belong in the domain function that uses them.
- Do not catch bare `Exception` and return `None`. Raise a typed `LoreError` subclass.
- Do not add a new LLM/embedding provider anywhere except `infra/llm/` or `infra/embeddings/`.
- Do not touch `memory/` internals from routes. Routes use `services/memory_service.py` only.

## Environment variables (key ones)

All read through `core/settings.py`. See that file for the full list. Critical ones:

```
LLM_PROVIDER=nvidia          # nvidia | openai | openrouter | bedrock
EMBEDDING_PROVIDER=nvidia    # nvidia | openai | openrouter | bedrock
LLM_MODEL=nvidia/nemotron-3-super-120b-a12b
EMBEDDING_MODEL=nvidia/llama-nemotron-embed-1b-v2   # 2048-dim; must match ingestion
NVIDIA_API_KEY=...
NEO4J_URI=bolt://...
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=...
MEMORY_POSTGRES_URI=...      # defaults to a localhost DSN; set it everywhere else
INTERNAL_AUTH_SECRET=...     # must equal the gateway's AI_SERVER_INTERNAL_SECRET; unset = every request 503
ENVIRONMENT=development      # development | staging | production
GRPC_ENABLED=false           # gRPC on GRPC_PORT (default 50051)
TENANT_ROUTING_ENABLED=false # per-org DSNs from elysia-rbac
RBAC_URL=...                 # elysia-rbac base URL (tenant routing)
RBAC_INTERNAL_SECRET=...     # elysia-rbac internal secret (tenant routing)
RERANK_PROVIDER=flashrank    # flashrank | nvidia
```

<!-- LOREKEEPER:BEGIN (generated - do not edit by hand) -->
## Lorekeeper decision context
_Generated from `lorekeeper/decisions/`. Records are the source of truth; edit them, not this block. Run `lorekeeper compile` to refresh._

### lore-engine

**Load-bearing invariants:**
- **[D-ENGINE-001]** MUST read environment variables ONLY in `core/settings.py` (the settings singleton).

**Decisions:**
- `D-ENGINE-001` [accepted] Only core/settings.py reads environment variables ⭐ — `lore-engine/**/*.py`

### cross-cutting

**Load-bearing invariants:**
- **[D-CROSS-001]** lore-ingestion MUST write artifacts (Neo4j graph, embeddings, scope/identity files).

**Decisions:**
- `D-CROSS-001` [accepted] Ingestion produces artifacts; engine consumes them (no shared runtime) ⭐ — `lore-ingestion/**, lore-engine/**`
<!-- LOREKEEPER:END -->
