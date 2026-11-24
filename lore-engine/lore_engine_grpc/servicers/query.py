"""
QueryService gRPC servicer — Query (unary) + QueryStream (server-streaming).

This is a faithful port of api/routes/query.py to the gRPC transport:

* request validation (query length, k clamp) — same bounds as the HTTP route
* tenant-routed QueryService + MemoryService resolution (via the resolver)
* the streaming event ordering contract: thread_id → sources → token* → done
* turn persistence + super-admin audit side effects, including on client
  disconnect mid-stream (the HTTP route runs them in a `finally`; here they
  run via an RPC-termination callback that survives handler cancellation)

Behavioural differences from the HTTP path, by design (see GRPC_TRANSPORT.md):
* auth is fail-closed (interceptor) rather than silent-permissive when the
  internal secret is unset
* a client disconnect mid-stream persists the turn as status="failed" (the HTTP
  path leaves it "completed"); this is an explicit acceptance criterion
* pre-stream failures (e.g. thread-not-found) abort with a mapped status rather
  than returning HTTP 200; mid-stream failures remain in-band (`error` event)
  so the gateway can reproduce today's SSE exactly
"""
from __future__ import annotations

import asyncio
import contextvars
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import grpc
from google.protobuf.struct_pb2 import Struct

from core.exceptions import (
    LoreError,
    PermissionDeniedError,
    ThreadNotFoundError,
)
from core.logging import get_logger
from lore.engine.v1 import common_pb2, query_pb2, query_pb2_grpc
from lore_engine_grpc._async_bridge import aiter_sync_generator
from lore_engine_grpc.context import (
    ScopeContext,
    seed_request_correlation,
    user_context_from_metadata,
)
from lore_engine_grpc.errors import abort_for, abort_thread_lookup
from lore_engine_grpc.resolver import ServiceResolver, ServiceUnavailableError

log = get_logger(__name__)

_QUERY_MAX = 1000
_K_DEFAULT = 8


def _clamp(value: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, value))


def _to_struct(payload: dict | None) -> Struct:
    struct = Struct()
    struct.update(payload or {})
    return struct


def _sources(items: list[dict]) -> list[common_pb2.Source]:
    return [
        common_pb2.Source(
            type=str(s.get("type", "")),
            description=str(s.get("description", "")),
            thread_id=str(s.get("thread_id", "")),
            score=float(s.get("score", 0.0)),
            sensitivity=str(s.get("sensitivity", "")),
        )
        for s in items or []
    ]


def _query_response(result: dict, thread_id: str) -> query_pb2.QueryResponse:
    return query_pb2.QueryResponse(
        answer=str(result.get("answer", "")),
        confidence=str(result.get("confidence", "low")),
        sources=_sources(result.get("sources", [])),
        stats=_to_struct(result.get("stats")),
        thread_id=thread_id,
    )


class QueryServicer(query_pb2_grpc.QueryServiceServicer):  # type: ignore[misc]
    def __init__(self, resolver: ServiceResolver, executor: ThreadPoolExecutor) -> None:
        self._resolver = resolver
        self._executor = executor

    # ── Unary Query ───────────────────────────────────────────────────

    async def Query(
        self, request: query_pb2.QueryRequest, context: grpc.aio.ServicerContext
    ) -> query_pb2.QueryResponse:
        user = user_context_from_metadata(context)
        question = (request.query or "").strip()
        if not question or len(question) > _QUERY_MAX:
            await context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                f"query must be 1..{_QUERY_MAX} chars",
            )
        k = _clamp(int(request.k or _K_DEFAULT), 1, 25)
        thread_id_in = request.thread_id or None
        seed_request_correlation(context, thread_id=thread_id_in or "new")

        try:
            qs = self._resolver.query_service(user)
            memory = await self._resolver.memory_service(user)
        except ServiceUnavailableError as exc:
            await context.abort(grpc.StatusCode.UNAVAILABLE, str(exc))

        try:
            history, thread_id = await memory.build_context(
                thread_id_in, user.user_id, question
            )
            seq = await memory.next_sequence(thread_id, user.user_id)
        except (ThreadNotFoundError, PermissionDeniedError) as exc:
            await abort_thread_lookup(exc, context)
        except LoreError as exc:
            await abort_for(exc, context)

        seed_request_correlation(context, thread_id=thread_id)

        scope_ctx = ScopeContext.from_metadata(context)
        scopes = scope_ctx.allowed_scopes
        super_admin = scope_ctx.super_admin

        loop = asyncio.get_running_loop()
        call_ctx = contextvars.copy_context()
        t0 = time.time()
        try:
            result = await loop.run_in_executor(
                self._executor,
                lambda: call_ctx.run(
                    lambda: qs.query(
                        question,
                        user_scopes=scopes,
                        k=k,
                        history_messages=history,
                        user_account_id=user.jira_account_id,
                        super_admin=super_admin,
                    )
                ),
            )
        except LoreError as exc:
            await abort_for(exc, context)
        except Exception as exc:  # noqa: BLE001 — last-resort guard before the client
            log.exception("grpc_query_failed", error=str(exc), thread_id=thread_id)
            await context.abort(grpc.StatusCode.INTERNAL, "query failed")

        latency_ms = round((time.time() - t0) * 1000)
        model = getattr(qs._chat, "model", "unknown")
        turn = memory.make_turn(
            thread_id=thread_id,
            user_id=user.user_id,
            sequence=seq,
            question=question,
            answer=result.get("answer", ""),
            sources=result.get("sources", []),
            model=model,
            latency_ms=latency_ms,
        )
        try:
            await memory.save_turn(turn)
        except Exception as save_exc:  # noqa: BLE001 — persistence must not lose the answer
            log.error("grpc_turn_save_failed", error=str(save_exc), thread_id=thread_id)

        if super_admin:
            await memory.record_access_audit(
                organization_id=user.org,
                user_id=user.user_id,
                query=question,
                results_count=len(result.get("sources", [])),
                scope_count=len(scopes),
                super_admin=True,
            )

        return _query_response(result, thread_id)

    # ── Server-streaming QueryStream ──────────────────────────────────

    async def QueryStream(
        self, request: query_pb2.QueryRequest, context: grpc.aio.ServicerContext
    ) -> Any:
        user = user_context_from_metadata(context)
        question = (request.query or "").strip()
        if not question or len(question) > _QUERY_MAX:
            await context.abort(
                grpc.StatusCode.INVALID_ARGUMENT,
                f"query must be 1..{_QUERY_MAX} chars",
            )
        k = _clamp(int(request.k or _K_DEFAULT), 1, 25)
        thread_id_in = request.thread_id or None
        seed_request_correlation(context, thread_id=thread_id_in or "new")

        try:
            qs = self._resolver.query_service(user)
            memory = await self._resolver.memory_service(user)
        except ServiceUnavailableError as exc:
            await context.abort(grpc.StatusCode.UNAVAILABLE, str(exc))

        # Pre-stream failures (e.g. unknown thread) abort with a mapped status so
        # the gateway can return the right HTTP code. Mid-stream failures stay
        # in-band via the `error` event, preserving today's SSE semantics.
        try:
            history, thread_id = await memory.build_context(
                thread_id_in, user.user_id, question
            )
            seq = await memory.next_sequence(thread_id, user.user_id)
        except (ThreadNotFoundError, PermissionDeniedError) as exc:
            await abort_thread_lookup(exc, context)
        except LoreError as exc:
            await abort_for(exc, context)

        seed_request_correlation(context, thread_id=thread_id)
        scope_ctx = ScopeContext.from_metadata(context)
        scopes = scope_ctx.allowed_scopes
        super_admin = scope_ctx.super_admin

        collected_tokens: list[str] = []
        collected_sources: list[dict] = []
        model = getattr(qs._chat, "model", "unknown")
        loop = asyncio.get_running_loop()

        # Persistence is the acceptance criterion: a turn is always written,
        # even on client disconnect. ``status`` defaults to "failed" and is only
        # flipped to "completed" when the stream reaches its terminal ``done``
        # event — so a disconnect at any earlier point persists "failed".
        state = {"status": "failed", "persisted": False}

        async def _persist() -> None:
            if state["persisted"]:
                return
            state["persisted"] = True
            turn = memory.make_turn(
                thread_id=thread_id,
                user_id=user.user_id,
                sequence=seq,
                question=question,
                answer="".join(collected_tokens),
                sources=collected_sources,
                model=model,
                latency_ms=None,
                status=state["status"],
            )
            try:
                await memory.save_turn(turn)
            except Exception as save_exc:  # noqa: BLE001
                log.error("grpc_stream_turn_save_failed", error=str(save_exc), thread_id=thread_id)
            if super_admin:
                await memory.record_access_audit(
                    organization_id=user.org,
                    user_id=user.user_id,
                    query=question,
                    results_count=len(collected_sources),
                    scope_count=len(scopes),
                    super_admin=True,
                )

        # add_done_callback fires on every RPC termination — normal completion
        # AND cancellation/disconnect. The handler task may be cancelling at
        # that point, so persistence is scheduled as an independent task on
        # the loop (it is not a child of the cancelled handler and survives it).
        def _on_rpc_done(*_: object) -> None:
            if not state["persisted"]:
                loop.create_task(_persist())

        context.add_done_callback(_on_rpc_done)

        def _factory():
            return qs.query_stream(
                question,
                user_scopes=scopes,
                k=k,
                history_messages=history,
                user_account_id=user.jira_account_id,
                super_admin=super_admin,
            )

        # grpc.aio requires the server-streaming handler to yield directly.
        # thread_id is emitted first — the frontend allocates/continues the
        # thread from this event before any content arrives.
        yield query_pb2.QueryStreamEvent(thread_id=thread_id)
        try:
            async for event in aiter_sync_generator(_factory, self._executor):
                kind = event.get("type")
                if kind == "sources":
                    data = event.get("data", []) or []
                    collected_sources.extend(data)
                    yield query_pb2.QueryStreamEvent(
                        sources=common_pb2.SourceList(sources=_sources(data))
                    )
                elif kind == "token":
                    chunk = event.get("data", "")
                    collected_tokens.append(chunk)
                    yield query_pb2.QueryStreamEvent(token=chunk)
                elif kind == "done":
                    state["status"] = "completed"
                    confidence = (event.get("data") or {}).get("confidence", "low")
                    yield query_pb2.QueryStreamEvent(
                        done=common_pb2.Done(confidence=confidence)
                    )
                elif kind == "error":
                    yield query_pb2.QueryStreamEvent(
                        error=common_pb2.StreamError(message=str(event.get("data", "")))
                    )
                # unknown event types are ignored (forward-compat)
        except LoreError as exc:
            log.warning("grpc_stream_lore_error", error=str(exc), thread_id=thread_id)
            yield query_pb2.QueryStreamEvent(
                error=common_pb2.StreamError(message=str(exc))
            )
        except Exception as exc:  # noqa: BLE001
            log.exception("grpc_stream_failed", error=str(exc), thread_id=thread_id)
            yield query_pb2.QueryStreamEvent(
                error=common_pb2.StreamError(message=str(exc))
            )
