"""
api/dependencies.py
───────────────────
FastAPI dependency providers.

All stateful resources (QueryService, MemoryService, providers) are built
once in api/main.py:lifespan() and stored on app.state. These functions
retrieve them via the request object — no module globals, no set_X setters.

Adding a new dependency:
  1. Build it in lifespan() and assign to app.state.<name>
  2. Add a get_<name>(request: Request) function here
  3. Use Depends(get_<name>) in route handlers
"""

from fastapi import Depends, HTTPException, Request

from api.auth import require_gateway
from core.settings import Settings, get_settings
from core.types import UserContext
from infra.embeddings.base import EmbeddingProvider
from infra.llm.base import ChatProvider
from services.memory_service import MemoryService
from services.query_service import QueryService


def get_settings_dep(request: Request) -> Settings:
    return get_settings()


def get_query_service(
    request: Request,
    user: UserContext = Depends(require_gateway),
) -> QueryService:
    """Build a QueryService bound to the caller's tenant.

    The LLM / embedding / rerank providers are process-wide singletons; only the
    graph varies per org. When tenant routing is disabled, the registry returns
    the single static client, so this collapses to the original behaviour.
    """
    state = request.app.state
    registry = getattr(state, "tenant_registry", None)
    if registry is None:
        # Fallback for setups that didn't build a registry (e.g. older tests).
        svc = getattr(state, "query_service", None)
        if svc is None:
            raise HTTPException(status_code=503, detail="Query service not initialised")
        return svc

    graph = registry.neo4j_for(user.org)
    return QueryService(
        graph=graph,
        chat=state.chat_provider,
        embedder=state.embedding_provider,
        settings=get_settings(),
        reranker=getattr(state, "rerank_provider", None),
    )


def get_graph(
    request: Request,
    user: UserContext = Depends(require_gateway),
):
    """Return the caller's tenant-routed Neo4j graph for analytics routes."""
    state = request.app.state
    registry = getattr(state, "tenant_registry", None)
    if registry is not None:
        return registry.neo4j_for(user.org)
    graph = getattr(state, "graph", None)
    if graph is None:
        raise HTTPException(status_code=503, detail="Graph not initialised")
    return graph


async def get_memory_service(
    request: Request,
    user: UserContext = Depends(require_gateway),
) -> MemoryService:
    """Return a MemoryService routed to the caller's tenant Postgres.

    When tenant routing is disabled the registry returns the single static memory
    service, so this collapses to the original behaviour.
    """
    state = request.app.state
    registry = getattr(state, "tenant_registry", None)
    if registry is None:
        # Fallback for setups that didn't build a registry (e.g. older tests).
        svc = getattr(state, "memory_service", None)
        if svc is None:
            raise HTTPException(status_code=503, detail="Memory service not initialised")
        return svc
    return await registry.memory_for(user.org)


def get_chat_provider(request: Request) -> ChatProvider:
    p = getattr(request.app.state, "chat_provider", None)
    if p is None:
        raise HTTPException(status_code=503, detail="Chat provider not initialised")
    return p


def get_embedding_provider(request: Request) -> EmbeddingProvider:
    p = getattr(request.app.state, "embedding_provider", None)
    if p is None:
        raise HTTPException(status_code=503, detail="Embedding provider not initialised")
    return p


def get_rerank_provider(request: Request):
    """The reranker is optional (fail-soft): None when its dependency/endpoint
    is unavailable. Callers degrade to the hybrid ranking order."""
    return getattr(request.app.state, "rerank_provider", None)


def get_chronograph_store(
    request: Request,
    user: UserContext = Depends(require_gateway),
):
    """Return a chronograph `PgStore` routed to the caller's tenant Postgres.

    This is the isolation boundary for code intelligence: each org's commits,
    source-file contents, and author data live in that org's own DB, so a caller
    can only ever read their own tenant's chronograph. When tenant routing is
    disabled the registry returns the single static store (single-tenant/dev).

    The returned store is cached and long-lived — callers MUST NOT `.disconnect()`
    it; the registry closes all stores at shutdown.
    """
    registry = getattr(request.app.state, "tenant_registry", None)
    if registry is None:
        # Legacy fallback for setups without a registry (e.g. older tests).
        from chronograph.store import make_store
        store = make_store()
        if not store.connect():
            raise HTTPException(status_code=503, detail="Chronograph store connection failed")
        return store
    return registry.chronograph_for(user.org)
