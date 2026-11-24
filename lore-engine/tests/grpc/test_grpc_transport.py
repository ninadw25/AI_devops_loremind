"""
Engine-side acceptance tests for the gRPC transport (LOR-111).

Covers everything reachable without the engine's heavy infra (Neo4j/Postgres/
LLM): the auth trust boundary, request-id log correlation, LoreError→status
mapping, the stream ordering contract, in-band stream errors, scope unioning,
and — critically — turn persistence on mid-stream client disconnect.

The QueryService/MemoryService are fakes (see conftest.py); a real grpc.aio
server is started on 127.0.0.1 so the full interceptor + servicer + bridge
stack is exercised over TCP.
"""
from __future__ import annotations

import asyncio
import logging

import grpc
import pytest

from core.exceptions import (
    GraphUnavailableError,
    PermissionDeniedError,
    ThreadNotFoundError,
)
from core.logging import CorrelationFilter
from lore.engine.v1 import query_pb2, threads_pb2


# ── Auth trust boundary (fail-closed) ────────────────────────────────


async def test_rejects_missing_secret(grpc_env):
    """No x-internal-auth at all → UNAUTHENTICATED."""
    req = query_pb2.QueryRequest(query="hi", k=8)
    with pytest.raises(grpc.aio.AioRpcError) as ei:
        await grpc_env.query_stub.Query(req, metadata=grpc_env.md(secret=None))
    assert ei.value.code() == grpc.StatusCode.UNAUTHENTICATED


async def test_rejects_wrong_secret(grpc_env):
    req = query_pb2.QueryRequest(query="hi", k=8)
    with pytest.raises(grpc.aio.AioRpcError) as ei:
        await grpc_env.query_stub.Query(req, metadata=grpc_env.md(secret="wrong"))
    assert ei.value.code() == grpc.StatusCode.UNAUTHENTICATED


async def test_accepts_correct_secret(grpc_env):
    """Correct secret reaches the pipeline and returns a populated response."""
    req = query_pb2.QueryRequest(query="what was decided", k=8)
    resp = await grpc_env.query_stub.Query(req, metadata=grpc_env.md())
    assert resp.answer == "answer to what was decided"
    assert resp.confidence == "high"
    assert resp.thread_id == "thr-123"
    assert len(resp.sources) == 1
    assert resp.sources[0].type == "Decision"
    # stats round-trips through google.protobuf.Struct (Struct fields unwrap
    # to native Python values on access)
    assert resp.stats["chunk_count"] == 1.0


async def test_stream_rejects_missing_secret(grpc_env):
    """Auth must also fail-closed on the streaming RPC."""
    req = query_pb2.QueryRequest(query="hi", k=8)
    with pytest.raises(grpc.aio.AioRpcError) as ei:
        async for _ in grpc_env.query_stub.QueryStream(req, metadata=grpc_env.md(secret=None)):
            pass
    assert ei.value.code() == grpc.StatusCode.UNAUTHENTICATED


# ── Request-id correlation into logs ─────────────────────────────────


async def test_request_id_flows_into_engine_logs(grpc_env):
    """The gateway's x-request-id must appear on engine log lines for the
    request — including lines emitted from inside the (executor-thread) pipeline."""
    grpc_env.resolver.query.log_probe = True

    records: list[logging.LogRecord] = []
    handler = logging.Handler()
    handler.emit = records.append  # type: ignore[assignment]
    handler.addFilter(CorrelationFilter())
    root = logging.getLogger()
    root.addHandler(handler)
    level = root.level
    root.setLevel(logging.INFO)
    try:
        req = query_pb2.QueryRequest(query="probe", k=8)
        await grpc_env.query_stub.Query(req, metadata=grpc_env.md(request_id="req-abc-999"))
    finally:
        root.removeHandler(handler)
        root.setLevel(level)

    probe = [r for r in records if r.msg == "query_probe_event"]
    assert probe, "probe log line was not emitted"
    assert probe[0].request_id == "req-abc-999"


# ── Input validation ─────────────────────────────────────────────────


async def test_rejects_empty_query(grpc_env):
    req = query_pb2.QueryRequest(query="   ", k=8)
    with pytest.raises(grpc.aio.AioRpcError) as ei:
        await grpc_env.query_stub.Query(req, metadata=grpc_env.md())
    assert ei.value.code() == grpc.StatusCode.INVALID_ARGUMENT


async def test_rejects_overlong_query(grpc_env):
    req = query_pb2.QueryRequest(query="x" * 1001, k=8)
    with pytest.raises(grpc.aio.AioRpcError) as ei:
        await grpc_env.query_stub.Query(req, metadata=grpc_env.md())
    assert ei.value.code() == grpc.StatusCode.INVALID_ARGUMENT


async def test_k_is_clamped(grpc_env):
    """k above 25 is clamped down. (proto3 has no distinct "unset" value for
    int32, so k=0 means unset → the default 8, exactly like the HTTP route's
    default=8.)"""
    req = query_pb2.QueryRequest(query="hi", k=9999)
    await grpc_env.query_stub.Query(req, metadata=grpc_env.md())
    assert grpc_env.resolver.query.last_call["k"] == 25

    req2 = query_pb2.QueryRequest(query="hi")  # k unset → default 8
    await grpc_env.query_stub.Query(req2, metadata=grpc_env.md())
    assert grpc_env.resolver.query.last_call["k"] == 8


# ── Scope handling ───────────────────────────────────────────────────


async def test_scope_union_deduped_order_preserving(grpc_env):
    """scope_ids ∪ scope_all_ids, deduped, order-preserving — the fail-closed
    gate's input. QueryRequest has no scope fields at all (not just unset by
    convention — the message schema carries none); scopes travel as call
    metadata, and only the union reaches the pipeline."""
    req = query_pb2.QueryRequest(query="hi", k=8)
    await grpc_env.query_stub.Query(
        req,
        metadata=grpc_env.md(scope_ids=["a", "b"], scope_all_ids=["b", "c"]),
    )
    assert grpc_env.resolver.query.last_call["user_scopes"] == ["a", "b", "c"]


async def test_super_admin_flag_propagates(grpc_env):
    req = query_pb2.QueryRequest(query="hi", k=8)
    await grpc_env.query_stub.Query(req, metadata=grpc_env.md(super_admin=True))
    assert grpc_env.resolver.query.last_call["super_admin"] is True
    # super-admin queries are audit-recorded
    assert grpc_env.resolver.memory.audits, "expected an access-audit row"


# ── LoreError → gRPC status mapping ──────────────────────────────────


async def test_graph_unavailable_maps_to_unavailable(grpc_env):
    grpc_env.resolver.query.query_raises = GraphUnavailableError("neo4j down")
    req = query_pb2.QueryRequest(query="hi", k=8)
    with pytest.raises(grpc.aio.AioRpcError) as ei:
        await grpc_env.query_stub.Query(req, metadata=grpc_env.md())
    assert ei.value.code() == grpc.StatusCode.UNAVAILABLE


async def test_thread_not_found_pre_stream_maps_to_not_found(grpc_env):
    grpc_env.resolver.memory.build_raises = ThreadNotFoundError("nope")
    req = query_pb2.QueryRequest(query="hi", k=8)
    with pytest.raises(grpc.aio.AioRpcError) as ei:
        async for _ in grpc_env.query_stub.QueryStream(req, metadata=grpc_env.md()):
            pass
    # Pre-stream failure → mapped status (not HTTP-200-with-error parity).
    assert ei.value.code() == grpc.StatusCode.NOT_FOUND


async def test_thread_permission_denied_is_masked_as_not_found(grpc_env):
    """PermissionDeniedError during thread resolution is masked to NOT_FOUND.

    The HTTP path collapses 403→404 for thread lookups (api/routes/query.py for
    build_context, threads.py for get_turns/delete_thread) so a caller can't
    enumerate thread ids by distinguishing "no such thread" from "not yours".
    The gRPC path must match — and must use a generic detail string so the
    existence-leak-via-error-text class is closed too.
    """
    grpc_env.resolver.memory.build_raises = PermissionDeniedError("not yours")
    req = query_pb2.QueryRequest(query="hi", k=8)
    with pytest.raises(grpc.aio.AioRpcError) as ei:
        await grpc_env.query_stub.Query(req, metadata=grpc_env.md())
    assert ei.value.code() == grpc.StatusCode.NOT_FOUND
    # Generic detail — the exception message must not leak through.
    assert "not yours" not in (ei.value.details() or "")


async def test_list_turns_permission_denied_is_masked_as_not_found(grpc_env):
    """Same thread-existence masking on the Threads RPC as on Query."""
    grpc_env.resolver.memory.list_raises = PermissionDeniedError("not yours")
    with pytest.raises(grpc.aio.AioRpcError) as ei:
        await grpc_env.threads_stub.ListTurns(
            threads_pb2.ListTurnsRequest(thread_id="x", limit=5),
            metadata=grpc_env.md(),
        )
    assert ei.value.code() == grpc.StatusCode.NOT_FOUND
    assert "not yours" not in (ei.value.details() or "")


# ── Metadata size ceiling (scopes ride in metadata) ─────────────────


async def test_large_scope_set_in_metadata_is_accepted(grpc_env):
    """Scopes travel in gRPC metadata (x-scope-ids / x-scope-all-ids). The
    server's grpc.max_metadata_size must be raised above the 8 KiB default so
    a user with a large scope set doesn't fail where the HTTP body path never
    did. ~26 KiB of scope metadata here would exceed the default."""
    big = [f"scope-{i:06d}-aclhash" * 2 for i in range(500)]
    req = query_pb2.QueryRequest(query="hi", k=8)
    await grpc_env.query_stub.Query(
        req, metadata=grpc_env.md(scope_ids=big, scope_all_ids=big)
    )
    # The full union reached the pipeline (deduped — both headers hold `big`).
    assert grpc_env.resolver.query.last_call["user_scopes"] == big


# ── Streaming parity ────────────────────────────────────────────────


def _event_kinds(events):
    return [e.WhichOneof("event") for e in events]


async def test_stream_ordering_contract(grpc_env):
    """thread_id → sources → token* → done, the contract the frontend relies on."""
    req = query_pb2.QueryRequest(query="hi", k=8)
    events = [e async for e in grpc_env.query_stub.QueryStream(req, metadata=grpc_env.md())]
    assert _event_kinds(events) == ["thread_id", "sources", "token", "token", "done"]
    assert events[0].thread_id == "thr-123"
    assert len(events[1].sources.sources) == 1
    assert events[2].token == "Hel"
    assert events[3].token == "lo"
    assert events[4].done.confidence == "medium"
    # success → persisted as completed
    assert len(grpc_env.resolver.memory.saved_turns) == 1
    assert grpc_env.resolver.memory.saved_turns[0].status == "completed"


async def test_stream_in_band_error(grpc_env):
    """A failure mid-stream arrives as the in-band `error` event (HTTP-200
    parity), not as a terminal gRPC error status."""
    grpc_env.resolver.query.stream_raises = RuntimeError("boom")
    req = query_pb2.QueryRequest(query="hi", k=8)
    events = [e async for e in grpc_env.query_stub.QueryStream(req, metadata=grpc_env.md())]
    # thread_id + sources emitted before the raise, then the error event
    assert _event_kinds(events) == ["thread_id", "sources", "error"]
    assert "boom" in events[-1].error.message


# ── Cancellation / disconnect persistence (key criterion) ────────────


async def test_client_disconnect_persists_failed_turn(grpc_env):
    """A client that disconnects mid-stream must still persist the turn as
    status='failed' (an explicit LOR-111 improvement over the HTTP path)."""
    # Script a slow stream so we can cancel mid-way.
    grpc_env.resolver.query.stream_events = [
        {"type": "sources", "data": []},
        {"type": "token", "data": "x"},
        {"type": "token", "data": "y"},
        {"type": "token", "data": "z"},
        {"type": "done", "data": {"confidence": "high"}},
    ]
    grpc_env.resolver.query.slow = 0.15  # type: ignore[attr-defined]

    req = query_pb2.QueryRequest(query="hi", k=8)
    call = grpc_env.query_stub.QueryStream(req, metadata=grpc_env.md())
    ait = call.__aiter__()
    first = await ait.__anext__()  # thread_id
    assert first.WhichOneof("event") == "thread_id"

    call.cancel()

    # After cancel the client iterator ends with CancelledError (asyncio) or an
    # AioRpcError(CANCELLED) depending on timing — either is acceptable.
    try:
        async for _ in ait:
            pass
    except (grpc.aio.AioRpcError, asyncio.CancelledError):
        pass

    # Persistence runs on RPC-termination callback, off the cancelled task —
    # give the loop a beat to run it.
    for _ in range(20):
        await asyncio.sleep(0.05)
        if grpc_env.resolver.memory.saved_turns:
            break

    assert len(grpc_env.resolver.memory.saved_turns) == 1, "turn was not persisted on disconnect"
    assert grpc_env.resolver.memory.saved_turns[0].status == "failed"


# ── Threads ──────────────────────────────────────────────────────────


async def test_list_threads(grpc_env):
    resp = await grpc_env.threads_stub.ListThreads(
        threads_pb2.ListThreadsRequest(limit=10), metadata=grpc_env.md()
    )
    assert len(resp.threads) == 1
    assert resp.threads[0].thread_id == "thr-1"
    assert resp.threads[0].turn_count == 2


async def test_list_turns(grpc_env):
    resp = await grpc_env.threads_stub.ListTurns(
        threads_pb2.ListTurnsRequest(thread_id="thr-123", limit=50),
        metadata=grpc_env.md(),
    )
    assert len(resp.turns) == 1
    turn = resp.turns[0]
    assert turn.question == "q?"
    assert turn.answer == "a."
    assert turn.latency_ms.value == 42


async def test_list_turns_not_found(grpc_env):
    from core.exceptions import ThreadNotFoundError
    grpc_env.resolver.memory.list_raises = ThreadNotFoundError("missing")
    with pytest.raises(grpc.aio.AioRpcError) as ei:
        await grpc_env.threads_stub.ListTurns(
            threads_pb2.ListTurnsRequest(thread_id="x", limit=5),
            metadata=grpc_env.md(),
        )
    assert ei.value.code() == grpc.StatusCode.NOT_FOUND


async def test_delete_thread(grpc_env):
    from google.protobuf import empty_pb2
    resp = await grpc_env.threads_stub.DeleteThread(
        threads_pb2.DeleteThreadRequest(thread_id="thr-123"),
        metadata=grpc_env.md(),
    )
    assert isinstance(resp, empty_pb2.Empty)
