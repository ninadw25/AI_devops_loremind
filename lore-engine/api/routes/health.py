"""
api/routes/health.py
────────────────────
Health check — probes each external dependency independently.

Public endpoint (no auth) used by Azure health probes.
No longer imports from other route modules — reads from app.state only.
"""

from fastapi import APIRouter, Request

router = APIRouter(tags=["health"])


@router.get("/health")
async def health(request: Request):
    state = request.app.state

    # Neo4j
    graph_ok = False
    try:
        qs = getattr(state, "query_service", None)
        graph_ok = qs.graph_available if qs else False
    except Exception:
        pass

    # Memory (Postgres)
    memory_ok = False
    try:
        memory_ok = getattr(state, "memory_service", None) is not None
    except Exception:
        pass

    # Chronograph (GitLab) — org-routed per request; report routing mode only.
    # This endpoint is public (no auth), so it must never read any tenant's data.
    chrono_routing = "static"
    try:
        registry = getattr(state, "tenant_registry", None)
        if registry is not None and getattr(registry, "enabled", False):
            chrono_routing = "per-org"
    except Exception:
        pass

    # LLM + embedding providers
    chat_info = "not_init"
    embed_info = "not_init"
    try:
        chat = getattr(state, "chat_provider", None)
        chat_info = f"{chat.name}/{chat.model}" if chat else "not_init"
    except Exception:
        pass
    try:
        emb = getattr(state, "embedding_provider", None)
        embed_info = f"{emb.name}/{emb.model}" if emb else "not_init"
    except Exception:
        pass

    overall_ok = graph_ok and memory_ok
    return {
        "status": "ok" if overall_ok else "degraded",
        "services": {
            "neo4j":        "up" if graph_ok else "down",
            "memory_postgres": "up" if memory_ok else "down",
            "llm":          chat_info,
            "embeddings":   embed_info,
        },
        "pipelines": {
            "gitlab": {
                "routing": chrono_routing,
            }
        },
    }
