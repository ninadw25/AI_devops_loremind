"""
ThreadsService gRPC servicer — ListThreads / ListTurns / DeleteThread.

A faithful port of api/routes/threads.py. Identity comes from metadata (the
servicer resolves the caller's user_id); per-user thread ownership is enforced
by the MemoryService exactly as in the HTTP route.
"""
from __future__ import annotations

from typing import Any

import grpc
from google.protobuf import empty_pb2

from core.exceptions import LoreError, PermissionDeniedError, ThreadNotFoundError
from core.logging import get_logger
from lore.engine.v1 import threads_pb2, threads_pb2_grpc
from lore_engine_grpc.context import seed_request_correlation, user_context_from_metadata
from lore_engine_grpc.errors import abort_for, abort_thread_lookup
from lore_engine_grpc.resolver import ServiceResolver, ServiceUnavailableError

log = get_logger(__name__)


def _clamp(value: int, lo: int, hi: int, default: int) -> int:
    if not value:
        return default
    return max(lo, min(hi, int(value)))


def _thread_to_pb(t: Any) -> threads_pb2.Thread:
    return threads_pb2.Thread(
        thread_id=t.thread_id,
        title=t.title,
        turn_count=t.turn_count,
        total_tokens=t.total_tokens,
        status=t.status,
        created_at=t.created_at.isoformat(),
        updated_at=t.updated_at.isoformat(),
    )


def _turn_to_pb(t: Any) -> threads_pb2.Turn:
    msg = threads_pb2.Turn(
        turn_id=t.turn_id,
        sequence=t.sequence,
        question=t.user_message.content,
        status=t.status,
        created_at=t.created_at.isoformat(),
    )
    if t.assistant_message is not None and t.assistant_message.content is not None:
        msg.answer = t.assistant_message.content
    if t.latency_ms is not None:
        msg.latency_ms.value = int(t.latency_ms)
    return msg


class ThreadsServicer(threads_pb2_grpc.ThreadsServiceServicer):  # type: ignore[misc]
    def __init__(self, resolver: ServiceResolver) -> None:
        self._resolver = resolver

    async def ListThreads(
        self, request: threads_pb2.ListThreadsRequest, context: grpc.aio.ServicerContext
    ) -> threads_pb2.ListThreadsResponse:
        user = user_context_from_metadata(context)
        seed_request_correlation(context)
        try:
            memory = await self._resolver.memory_service(user)
        except ServiceUnavailableError as exc:
            await context.abort(grpc.StatusCode.UNAVAILABLE, str(exc))

        limit = _clamp(request.limit, 1, 100, 20)
        before_id = request.before_id or None
        try:
            threads = await memory.list_threads(
                user.user_id, limit=limit, before_id=before_id
            )
        except LoreError as exc:
            await abort_for(exc, context)
        return threads_pb2.ListThreadsResponse(threads=[_thread_to_pb(t) for t in threads])

    async def ListTurns(
        self, request: threads_pb2.ListTurnsRequest, context: grpc.aio.ServicerContext
    ) -> threads_pb2.ListTurnsResponse:
        user = user_context_from_metadata(context)
        seed_request_correlation(context, thread_id=request.thread_id)
        try:
            memory = await self._resolver.memory_service(user)
        except ServiceUnavailableError as exc:
            await context.abort(grpc.StatusCode.UNAVAILABLE, str(exc))

        limit = _clamp(request.limit, 1, 200, 50)
        try:
            turns = await memory.get_turns(request.thread_id, user.user_id, limit=limit)
        except (ThreadNotFoundError, PermissionDeniedError) as exc:
            await abort_thread_lookup(exc, context)
        except LoreError as exc:
            await abort_for(exc, context)
        return threads_pb2.ListTurnsResponse(turns=[_turn_to_pb(t) for t in turns])

    async def DeleteThread(
        self, request: threads_pb2.DeleteThreadRequest, context: grpc.aio.ServicerContext
    ) -> empty_pb2.Empty:
        user = user_context_from_metadata(context)
        seed_request_correlation(context, thread_id=request.thread_id)
        try:
            memory = await self._resolver.memory_service(user)
        except ServiceUnavailableError as exc:
            await context.abort(grpc.StatusCode.UNAVAILABLE, str(exc))

        try:
            await memory.delete_thread(request.thread_id, user.user_id)
        except (ThreadNotFoundError, PermissionDeniedError) as exc:
            await abort_thread_lookup(exc, context)
        except LoreError as exc:
            await abort_for(exc, context)
        return empty_pb2.Empty()
