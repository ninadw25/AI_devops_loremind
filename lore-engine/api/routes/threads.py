"""
api/routes/threads.py
─────────────────────
Conversation thread management.

  GET    /api/threads                    list user's threads (paginated)
  GET    /api/threads/{thread_id}/turns  get turns for a thread
  DELETE /api/threads/{thread_id}        soft-delete a thread
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from api.auth import require_gateway
from api.dependencies import get_memory_service
from core.exceptions import PermissionDeniedError, ThreadNotFoundError
from core.types import UserContext
from services.memory_service import MemoryService

router = APIRouter(tags=["threads"])


class ThreadOut(BaseModel):
    thread_id:    str
    title:        str
    turn_count:   int
    total_tokens: int
    status:       str
    created_at:   str
    updated_at:   str


class TurnOut(BaseModel):
    turn_id:    str
    sequence:   int
    question:   str
    answer:     str | None
    status:     str
    latency_ms: int | None
    created_at: str


@router.get("/threads", response_model=list[ThreadOut])
async def list_threads(
    limit:     int        = Query(default=20, ge=1, le=100),
    before_id: str | None = Query(default=None),
    user:      UserContext   = Depends(require_gateway),
    memory:    MemoryService = Depends(get_memory_service),
):
    threads = await memory.list_threads(
        user.user_id, limit=limit, before_id=before_id
    )
    return [
        ThreadOut(
            thread_id=t.thread_id,
            title=t.title,
            turn_count=t.turn_count,
            total_tokens=t.total_tokens,
            status=t.status,
            created_at=t.created_at.isoformat(),
            updated_at=t.updated_at.isoformat(),
        )
        for t in threads
    ]


@router.get("/threads/{thread_id}/turns", response_model=list[TurnOut])
async def get_thread_turns(
    thread_id: str,
    limit:     int           = Query(default=50, ge=1, le=200),
    user:      UserContext   = Depends(require_gateway),
    memory:    MemoryService = Depends(get_memory_service),
):
    try:
        turns = await memory.get_turns(thread_id, user.user_id, limit=limit)
    except (ThreadNotFoundError, PermissionDeniedError) as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return [
        TurnOut(
            turn_id=t.turn_id,
            sequence=t.sequence,
            question=t.user_message.content,
            answer=t.assistant_message.content if t.assistant_message else None,
            status=t.status,
            latency_ms=t.latency_ms,
            created_at=t.created_at.isoformat(),
        )
        for t in turns
    ]


@router.delete("/threads/{thread_id}", status_code=204)
async def delete_thread(
    thread_id: str,
    user:      UserContext   = Depends(require_gateway),
    memory:    MemoryService = Depends(get_memory_service),
):
    try:
        await memory.delete_thread(thread_id, user.user_id)
    except (ThreadNotFoundError, PermissionDeniedError) as exc:
        raise HTTPException(status_code=404, detail=str(exc))
