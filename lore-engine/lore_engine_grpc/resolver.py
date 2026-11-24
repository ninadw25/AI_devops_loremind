"""
lore_engine_grpc.resolver — resolve engine services for a gRPC request.

This is the gRPC-path equivalent of api/dependencies.py's get_query_service /
get_memory_service: it builds a tenant-routed QueryService and resolves the
caller's MemoryService from app.state. The logic is intentionally a mirror of
the FastAPI dependencies so both transports route identically; if tenant
routing changes, change both files (they reference each other in comments).
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

from core.settings import get_settings
from core.types import UserContext

if TYPE_CHECKING:
    # Imported only for type annotations (``from __future__ import annotations``
    # keeps them as strings at runtime). The gRPC layer must not pull the
    # engine's heavy service dependencies at import time — it talks to the
    # services structurally (duck-typed), so tests can inject fakes.
    from services.memory_service import MemoryService
    from services.query_service import QueryService


class ServiceUnavailableError(Exception):
    """Raised when a required service was never built on app.state."""


class ServiceResolver(Protocol):
    """Minimal interface the gRPC servicers depend on."""

    def query_service(self, user: UserContext) -> QueryService: ...

    async def memory_service(self, user: UserContext) -> MemoryService: ...


class AppStateServiceResolver:
    """Resolves services from a FastAPI app.state, mirroring api/dependencies.py."""

    def __init__(self, app_state: Any) -> None:
        self._state = app_state

    def query_service(self, user: UserContext) -> QueryService:
        state = self._state
        registry = getattr(state, "tenant_registry", None)
        if registry is None:
            svc = getattr(state, "query_service", None)
            if svc is None:
                raise ServiceUnavailableError("Query service not initialised")
            return svc
        graph = registry.neo4j_for(user.org)
        return QueryService(
            graph=graph,
            chat=state.chat_provider,
            embedder=state.embedding_provider,
            settings=get_settings(),
            reranker=getattr(state, "rerank_provider", None),
        )

    async def memory_service(self, user: UserContext) -> MemoryService:
        state = self._state
        registry = getattr(state, "tenant_registry", None)
        if registry is None:
            svc = getattr(state, "memory_service", None)
            if svc is None:
                raise ServiceUnavailableError("Memory service not initialised")
            return svc
        return await registry.memory_for(user.org)
