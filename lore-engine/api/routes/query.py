"""
api/routes/query.py
───────────────────
Query endpoints backed by QueryService + MemoryService.

  POST /api/query         non-streaming (JSON response)
  POST /api/query/stream  streaming via Server-Sent Events
  GET  /api/query/health  lightweight service status
"""

from __future__ import annotations

import json
import time

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from api.auth import require_gateway
from api.dependencies import get_memory_service, get_query_service
from core.exceptions import PermissionDeniedError, ThreadNotFoundError
from core.logging import get_logger, set_correlation
from core.types import UserContext
from services.memory_service import MemoryService
from services.query_service import QueryService

log = get_logger(__name__)
router = APIRouter(tags=["query"])


# ── Request / response models ─────────────────────────────────────────

class QueryRequest(BaseModel):
    query:     str        = Field(..., max_length=1000)
    thread_id: str | None = Field(default=None)
    k:         int        = Field(default=8, ge=1, le=25)

    # ── Gateway-injected content scopes (core-backend Plane B) ────────
    # Computed by rbac-service and injected by the gateway after it verifies
    # the user's JWT. Trusted ONLY because the request is authenticated via the
    # internal secret (require_gateway) — a direct client cannot forge them.
    # LOCKED names (see elysia SCOPE_FILTER_HANDOFF). Identity (user_id/org)
    # comes from the forwarded JWT via require_gateway, not from the body.
    scope_ids:     list[str] = Field(default_factory=list)
    scope_all_ids: list[str] = Field(default_factory=list)

    # Content super-admin bypass — set by the gateway when the caller holds the
    # `content.read_all` authority (rbac-service Plane A). Trusted ONLY because
    # require_gateway authenticates the request via the internal secret; a direct
    # client cannot forge it. True ⇒ the fail-closed content gate opens for THIS
    # request only. Tenant-bounded (lore-engine is per-org routed) and audited at
    # the gateway. See docs/CONTENT_SUPERADMIN.md.
    super_admin:   bool      = Field(default=False)

    @property
    def allowed_scopes(self) -> list[str]:
        """The caller's full allowed-scope set for the fail-closed gate.
        Union of the gateway's scope_ids (primary) and scope_all_ids,
        deduped, order-preserving."""
        return list(dict.fromkeys([*self.scope_ids, *self.scope_all_ids]))


class SourceOut(BaseModel):
    type:        str
    description: str
    thread_id:   str
    score:       float
    sensitivity: str


class QueryResponse(BaseModel):
    answer:     str
    confidence: str
    sources:    list[SourceOut]
    stats:      dict
    thread_id:  str


# ── Endpoints ─────────────────────────────────────────────────────────

@router.get("/query/health")
def health(qs: QueryService = Depends(get_query_service)):
    return {"ready": True, "graph_available": qs.graph_available}


@router.post("/query", response_model=QueryResponse)
async def query_endpoint(
    req:    QueryRequest,
    user:   UserContext    = Depends(require_gateway),
    qs:     QueryService   = Depends(get_query_service),
    memory: MemoryService  = Depends(get_memory_service),
):
    set_correlation(thread_id=req.thread_id or "new")

    try:
        history, thread_id = await memory.build_context(
            req.thread_id, user.user_id, req.query
        )
        seq = await memory.next_sequence(thread_id, user.user_id)
    except (ThreadNotFoundError, PermissionDeniedError):
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Thread not found")

    set_correlation(thread_id=thread_id)

    t0 = time.time()
    result = qs.query(
        req.query,
        user_scopes=req.allowed_scopes,
        k=req.k,
        history_messages=history,
        user_account_id=user.jira_account_id,
        super_admin=req.super_admin,
    )
    latency_ms = round((time.time() - t0) * 1000)

    model = getattr(qs._chat, "model", "unknown")
    turn = memory.make_turn(
        thread_id=thread_id,
        user_id=user.user_id,
        sequence=seq,
        question=req.query,
        answer=result.get("answer", ""),
        sources=result.get("sources", []),
        model=model,
        latency_ms=latency_ms,
    )
    await memory.save_turn(turn)

    # Content super-admin ("read all") queries are surveillance-sensitive — persist
    # a compliance row (best-effort; never blocks the response). CONTENT_SUPERADMIN §9.
    if req.super_admin:
        await memory.record_access_audit(
            organization_id=user.org,
            user_id=user.user_id,
            query=req.query,
            results_count=len(result.get("sources", [])),
            scope_count=len(req.allowed_scopes),
            super_admin=True,
        )

    return {**result, "thread_id": thread_id}


@router.post("/query/stream")
async def query_stream_endpoint(
    req:    QueryRequest,
    user:   UserContext    = Depends(require_gateway),
    qs:     QueryService   = Depends(get_query_service),
    memory: MemoryService  = Depends(get_memory_service),
):
    set_correlation(thread_id=req.thread_id or "new")

    try:
        history, thread_id = await memory.build_context(
            req.thread_id, user.user_id, req.query
        )
        seq = await memory.next_sequence(thread_id, user.user_id)
    except (ThreadNotFoundError, PermissionDeniedError):
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Thread not found")

    set_correlation(thread_id=thread_id)

    collected_tokens: list[str] = []
    collected_sources: list[dict] = []
    model = getattr(qs._chat, "model", "unknown")

    async def event_stream():
        yield f"data: {json.dumps({'type': 'thread_id', 'data': thread_id})}\n\n"
        status = "completed"
        try:
            for event in qs.query_stream(
                req.query,
                user_scopes=req.allowed_scopes,
                k=req.k,
                history_messages=history,
                user_account_id=user.jira_account_id,
                super_admin=req.super_admin,
            ):
                if event["type"] == "sources":
                    collected_sources.extend(event["data"])
                elif event["type"] == "token":
                    collected_tokens.append(event["data"])
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        except Exception as exc:
            log.exception("stream_failed", error=str(exc), thread_id=thread_id)
            status = "failed"
            yield f"data: {json.dumps({'type': 'error', 'data': str(exc)})}\n\n"
        finally:
            turn = memory.make_turn(
                thread_id=thread_id,
                user_id=user.user_id,
                sequence=seq,
                question=req.query,
                answer="".join(collected_tokens),
                sources=collected_sources,
                model=model,
                latency_ms=None,
                status=status,
            )
            try:
                await memory.save_turn(turn)
            except Exception as save_exc:
                log.error("stream_turn_save_failed", error=str(save_exc), thread_id=thread_id)

            # Audit super-admin ("read all") streamed queries too (best-effort).
            if req.super_admin:
                await memory.record_access_audit(
                    organization_id=user.org,
                    user_id=user.user_id,
                    query=req.query,
                    results_count=len(collected_sources),
                    scope_count=len(req.allowed_scopes),
                    super_admin=True,
                )

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
