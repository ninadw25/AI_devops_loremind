"""
Shared fixtures + fakes for the gRPC transport tests.

These tests drive a REAL grpc.aio server bound to 127.0.0.1 over TCP, so they
exercise the full stack — interceptors, servicers, the sync→async bridge —
without any of the engine's heavy dependencies (Neo4j/Postgres/LLM). The
QueryService/MemoryService are fakes; the real retrieval pipeline is unchanged
by this ticket and is covered by its own tests.
"""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from typing import Any

import pytest
import pytest_asyncio

from lore_engine_grpc import build_server
from lore_engine_grpc.context import metadata_value  # noqa: F401 (re-export convenience)

SECRET = "test-internal-secret"


# ── Fakes ─────────────────────────────────────────────────────────────


class _Chat:
    model = "fake-model"


class FakeQueryService:
    """Records what the servicer passed through and yields canned stream events."""

    def __init__(self) -> None:
        self.last_call: dict[str, Any] | None = None
        # Configurable scripted behaviour for query_stream.
        self.stream_events: list[dict] | None = None
        self.stream_raises: BaseException | None = None
        self.query_raises: BaseException | None = None
        self.log_probe: bool = False
        self.slow: float = 0.0
        self._chat = _Chat()

    @property
    def graph_available(self) -> bool:
        return True

    def query(self, question, *, user_scopes, k, history_messages,
              user_account_id, super_admin) -> dict:
        self.last_call = {
            "question": question, "user_scopes": list(user_scopes), "k": k,
            "super_admin": super_admin, "user_account_id": user_account_id,
        }
        if self.log_probe:
            from core.logging import get_logger
            get_logger("test.fake_query").info("query_probe_event")
        if self.query_raises:
            raise self.query_raises
        return {
            "answer": f"answer to {question}",
            "confidence": "high",
            "sources": [{
                "type": "Decision", "description": "decided X",
                "thread_id": "t-1", "score": 0.9, "sensitivity": "internal",
            }],
            "stats": {"total_ms": 5, "chunk_count": 1},
        }

    def query_stream(self, question, *, user_scopes, k, history_messages,
                     user_account_id, super_admin):
        self.last_call = {
            "question": question, "user_scopes": list(user_scopes), "k": k,
            "super_admin": super_admin, "user_account_id": user_account_id,
        }
        if self.log_probe:
            from core.logging import get_logger
            get_logger("test.fake_query").info("stream_probe_event")
        if self.stream_raises:
            yield {"type": "sources", "data": []}
            raise self.stream_raises
        for ev in (self.stream_events or [
            {"type": "sources", "data": [
                {"type": "Decision", "description": "d", "thread_id": "t-1",
                 "score": 0.8, "sensitivity": "internal"}]},
            {"type": "token", "data": "Hel"},
            {"type": "token", "data": "lo"},
            {"type": "done", "data": {"confidence": "medium"}},
        ]):
            if self.slow:
                import time
                time.sleep(self.slow)
            yield ev


class FakeMemoryService:
    def __init__(self) -> None:
        self.thread_id = "thr-123"
        self.saved_turns: list[Any] = []
        self.audits: list[dict] = []
        self.build_raises: BaseException | None = None
        self.list_raises: BaseException | None = None

    async def build_context(self, thread_id, user_id, question):
        if self.build_raises:
            raise self.build_raises
        return ([], self.thread_id)

    async def next_sequence(self, thread_id, user_id):
        return 1

    def make_turn(self, **kwargs):
        return SimpleNamespace(**kwargs)

    async def save_turn(self, turn):
        self.saved_turns.append(turn)

    async def record_access_audit(self, **kwargs):
        self.audits.append(kwargs)

    async def list_threads(self, user_id, *, limit=20, before_id=None):
        return [SimpleNamespace(
            thread_id="thr-1", title="T1", turn_count=2, total_tokens=10,
            status="active",
            created_at=_dt("2026-01-01T00:00:00"),
            updated_at=_dt("2026-01-02T00:00:00"),
        )]

    async def get_turns(self, thread_id, user_id, *, limit=50):
        if self.list_raises:
            raise self.list_raises
        return [SimpleNamespace(
            turn_id="tn-1", sequence=1, status="completed", latency_ms=42,
            created_at=_dt("2026-01-01T00:00:00"),
            user_message=SimpleNamespace(content="q?"),
            assistant_message=SimpleNamespace(content="a."),
        )]

    async def delete_thread(self, thread_id, user_id):
        if self.list_raises:
            raise self.list_raises


def _dt(iso):
    from datetime import datetime, timezone
    return datetime.fromisoformat(iso).replace(tzinfo=timezone.utc)


class FakeResolver:
    def __init__(self) -> None:
        self.query = FakeQueryService()
        self.memory = FakeMemoryService()

    def query_service(self, user):
        return self.query

    async def memory_service(self, user):
        return self.memory


# ── Server / client fixtures ─────────────────────────────────────────


@pytest_asyncio.fixture
async def grpc_env():
    """Start a real grpc.aio server on an ephemeral port and yield (stub_factory, resolver)."""
    import grpc
    from lore.engine.v1 import query_pb2_grpc, threads_pb2_grpc

    resolver = FakeResolver()
    executor = ThreadPoolExecutor(max_workers=4)
    server = build_server(secret=SECRET, resolver=resolver, pipeline_executor=executor)
    port = server.add_insecure_port("127.0.0.1:0")
    await server.start()

    channel = grpc.aio.insecure_channel(f"127.0.0.1:{port}")

    def md(secret=SECRET, request_id="req-test-1", user_id="u-1",
           org="org-1", role="member", account_id="acc-1",
           scope_ids=None, scope_all_ids=None, super_admin=False):
        import json

        out = []
        if secret is not None:
            out.append(("x-internal-auth", secret))
        if request_id is not None:
            out.append(("x-request-id", request_id))
        out.extend([
            ("x-user-id", user_id), ("x-org", org),
            ("x-role", role), ("x-account-id", account_id),
            ("x-scope-ids", json.dumps(scope_ids or [])),
            ("x-scope-all-ids", json.dumps(scope_all_ids or [])),
            ("x-super-admin", "true" if super_admin else "false"),
        ])
        return out

    try:
        yield SimpleNamespace(
            query_stub=query_pb2_grpc.QueryServiceStub(channel),
            threads_stub=threads_pb2_grpc.ThreadsServiceStub(channel),
            resolver=resolver,
            md=md,
            channel=channel,
        )
    finally:
        await channel.close()
        await server.stop(grace=None)
        # Shut the pipeline pool down off the event-loop thread: a producer
        # thread may be blocked on run_coroutine_threadsafe(..., loop), which
        # needs the loop to make progress — a synchronous shutdown(wait=True)
        # here would deadlock the loop against that producer.
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None, lambda: executor.shutdown(wait=True, cancel_futures=True)
        )


@pytest.fixture(autouse=True)
def _isolate_settings():
    """Each test gets a clean correlation contextvar state."""
    from core.logging import clear_correlation
    clear_correlation()
    yield
    clear_correlation()
