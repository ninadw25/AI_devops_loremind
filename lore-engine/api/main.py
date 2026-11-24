"""
Lore Backend — FastAPI application entry point.

Startup sequence (lifespan):
  1. Configure structured logging
  2. Build infra adapters (Neo4j, LLM provider, embedding provider)
  3. Build services (QueryService, MemoryService)
  4. Attach everything to app.state
  5. Initialise GitLab chronograph route

Shutdown sequence (reverse):
  1. Close Neo4j driver
  2. Close PostgreSQL connection pool

All stateful resources live on app.state and are accessed via FastAPI Depends.
No module-level globals. No set_X() functions.

Run:
  uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address

from api.auth import require_gateway
from api.errors import lore_error_handler
from api.middleware import CorrelationMiddleware, SecurityHeadersMiddleware
from api.routes import (
    chronograph as chronograph_router,
    continuity as continuity_router,
    experts as experts_router,
    gitlab as gitlab_router,
    offboarding as offboarding_router,
    onboarding as onboarding_router,
    people as people_router,
    health as health_router,
    query as query_router,
    threads as threads_router,
)
from core.exceptions import LoreError
from core.logging import configure_logging, get_logger
from core.settings import get_settings

log = get_logger("api.main")


# ── Lifespan ──────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    s = get_settings()
    log.info("startup_begin", version="4.0.0")

    # ── Infra: Neo4j ──────────────────────────────────────────────────
    from infra.neo4j_client import Neo4jClient
    graph = Neo4jClient(s.neo4j_uri, s.neo4j_username, s.neo4j_password)
    app.state.graph = graph

    # ── Infra: LLM + embedding providers ─────────────────────────────
    from infra.llm import build_chat_provider
    from infra.embeddings import build_embedding_provider
    chat_provider = build_chat_provider(s)
    embedding_provider = build_embedding_provider(s)
    app.state.chat_provider = chat_provider
    app.state.embedding_provider = embedding_provider
    log.info(
        "providers_ready",
        llm=f"{chat_provider.name}/{chat_provider.model}",
        embed=f"{embedding_provider.name}/{embedding_provider.model}",
    )

    # ── Infra: reranker (quality enhancer — fail-soft) ───────────────
    # The reranker only reorders candidates; if its optional dependency or
    # remote endpoint is unavailable, retrieval still works (rank() degrades
    # to the hybrid graph+BM25 order). So a build failure here is logged, not
    # fatal — unlike the LLM/embedding providers above.
    from infra.rerank.registry import build_rerank_provider
    try:
        if s.rerank_provider == "none":   # deliberate ablation: same degraded path as a missing reranker
            raise ValueError("rerank disabled by RERANK_PROVIDER=none")
        rerank_provider = build_rerank_provider(s)
        log.info("rerank_ready", provider=f"{rerank_provider.name}/{rerank_provider.model}")
    except Exception as exc:
        rerank_provider = None
        log.warning("rerank_unavailable", error=str(exc),
                    note="retrieval will use hybrid order without cross-encoder rerank")
    app.state.rerank_provider = rerank_provider

    # ── Service: QueryService ─────────────────────────────────────────
    from services.query_service import QueryService
    query_service = QueryService(
        graph=graph,
        chat=chat_provider,
        embedder=embedding_provider,
        settings=s,
        reranker=rerank_provider,
    )
    app.state.query_service = query_service
    log.info("query_service_ready", graph_available=graph.available)

    # ── Service: MemoryService ────────────────────────────────────────
    from memory import MemoryManager, MemoryConfig
    from memory.stores.postgres_store import PostgresLongTermStore
    from infra.postgres_client import PostgresClient
    from services.memory_service import MemoryService

    if not s.memory_postgres_uri:
        raise RuntimeError("MEMORY_POSTGRES_URI is not set — cannot start memory layer")

    pg_client = PostgresClient(s.memory_postgres_uri, schema=s.memory_schema)
    await pg_client.connect()
    memory_store = PostgresLongTermStore(pg_client)
    await memory_store.ensure_indexes()
    memory_manager = MemoryManager(
        config=MemoryConfig(recent_turns_in_context=s.memory_recent_turns),
        store=memory_store,
    )
    memory_service = MemoryService(memory_manager)
    app.state.pg_client      = pg_client
    app.state.memory_store   = memory_store
    app.state.memory_service = memory_service
    log.info("memory_service_ready", backend="postgres")

    # ── Infra: per-org connection routing (hybrid deployment) ─────────
    # Resolves each client's Neo4j + Postgres-memory from the elysia-rbac registry
    # when enabled; otherwise neo4j_for()/memory_for() return the static clients
    # (`graph`, `memory_service`) built above. Built last so both fallbacks exist.
    from infra.tenant_registry import TenantRegistry
    tenant_registry = TenantRegistry(s, fallback_graph=graph, fallback_memory=memory_service)
    app.state.tenant_registry = tenant_registry
    log.info("tenant_registry_ready", routing_enabled=tenant_registry.enabled)

    # ── Chronograph / GitLab ──────────────────────────────────────────
    # No global store/project init: the chronograph store is resolved per request
    # from the caller's org (tenant_registry.chronograph_for) so code intelligence
    # is tenant-isolated. See api/routes/gitlab.py.

    # ── gRPC server (gateway transport; LOR-111) ──────────────────────
    # Started last, after every service the servicers resolve is on app.state.
    # Reads the same tenant-routed services the HTTP API serves.
    grpc_server = None
    if s.grpc_enabled:
        from lore_engine_grpc import serve_grpc
        grpc_server = await serve_grpc(app.state, s)
        log.info("grpc_started", enabled=grpc_server is not None)
    app.state.grpc_server = grpc_server

    log.info("startup_complete")

    yield

    # ── Shutdown ──────────────────────────────────────────────────────
    log.info("shutdown_begin")
    # Stop the gRPC listener first (stop accepting RPCs, let in-flight finish)
    # before tearing down the DB/infra those RPCs may still be persisting to.
    if getattr(app.state, "grpc_server", None) is not None:
        await app.state.grpc_server.stop(grace=5.0)
        pipeline_exec = getattr(app.state, "grpc_pipeline_executor", None)
        if pipeline_exec is not None:
            # Non-blocking: a cancelled-RPC producer thread can't be force-killed
            # (Python threads); it winds down on its own. Don't block app shutdown.
            pipeline_exec.shutdown(wait=False, cancel_futures=True)
        log.info("grpc_stopped")
    await tenant_registry.close_memory()
    tenant_registry.close_chronograph()
    tenant_registry.close()
    graph.close()
    await pg_client.close()
    log.info("shutdown_complete")


# ── App ───────────────────────────────────────────────────────────────

limiter = Limiter(key_func=get_remote_address)

app = FastAPI(
    title="Lore Backend API",
    description="Knowledge intelligence — Gmail + GitLab + Neo4j hybrid search",
    version="4.0.0",
    lifespan=lifespan,
)

# Middleware (order matters: added last = runs first)
s = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=s.cors_origins.split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(CorrelationMiddleware)
app.state.limiter = limiter
app.add_middleware(SlowAPIMiddleware)

# Exception handlers
app.add_exception_handler(LoreError, lore_error_handler)

@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    from fastapi import HTTPException
    raise HTTPException(status_code=429, detail="Rate limit exceeded. Try again later.")


# ── Routes ────────────────────────────────────────────────────────────

@app.get("/")
def root():
    return {
        "service": "Lore Backend API",
        "version": "4.0.0",
        "auth": "all routes require the gateway's X-Internal-Auth secret",
    }

# Public (no auth)
app.include_router(health_router.router, prefix="/api")

# Protected — runs BEHIND the core-backend gateway. Auth is gateway trust
# (require_gateway): the shared internal secret in X-Internal-Auth + the
# gateway-forwarded (already JWKS-verified, EdDSA) JWT decoded for claims.
# This is the ONLY way in. The legacy HS256 direct-login was removed 2026-07-22.
app.include_router(
    query_router.router, prefix="/api",
    dependencies=[Depends(require_gateway)],
)
app.include_router(
    experts_router.router, prefix="/api",
    dependencies=[Depends(require_gateway)],
)
app.include_router(
    continuity_router.router, prefix="/api",
    dependencies=[Depends(require_gateway)],
)
app.include_router(
    offboarding_router.router, prefix="/api",
    dependencies=[Depends(require_gateway)],
)
app.include_router(
    onboarding_router.router, prefix="/api",
    dependencies=[Depends(require_gateway)],
)
app.include_router(
    people_router.router, prefix="/api",
    dependencies=[Depends(require_gateway)],
)
app.include_router(
    threads_router.router, prefix="/api",
    dependencies=[Depends(require_gateway)],
)
app.include_router(
    gitlab_router.router, prefix="/api/gitlab",
    dependencies=[Depends(require_gateway)],
)
app.include_router(
    chronograph_router.router, prefix="/api/chronograph",
    dependencies=[Depends(require_gateway)],
)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "api.main:app",
        host="0.0.0.0",
        port=get_settings().port,
        reload=True,
        log_level="info",
    )
