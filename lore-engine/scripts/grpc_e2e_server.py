#!/usr/bin/env python
"""
Standalone fake lore-engine gRPC server for the gateway ↔ engine end-to-end
check (LOR-111). Starts QueryService + ThreadsService with in-memory fakes,
binds to an ephemeral port on 127.0.0.1, and prints:

    GRPC_PORT=<port>
    GRPC_SECRET=<secret>

then serves until killed. Used by the gateway's e2e test (Bun) to prove the
two runtimes interoperate over real HTTP/2 gRPC — the riskiest assumption in
the transport choice.

Run:
    PYTHONPATH=. .venv/Scripts/python.exe scripts/grpc_e2e_server.py
"""
from __future__ import annotations

import asyncio
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from types import SimpleNamespace

# Ensure the repo root (and its lore_engine_grpc package) is importable.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lore_engine_grpc import build_server  # noqa: E402

SECRET = os.environ.get("GRPC_SECRET", "e2e-secret")
_NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


class FakeQuery:
    _chat = SimpleNamespace(model="e2e-model")
    graph_available = True

    def query(self, question, *, user_scopes, k, history_messages, user_account_id, super_admin):
        return {
            "answer": f"Answer to: {question}",
            "confidence": "high",
            "sources": [{
                "type": "Decision", "description": "decided X",
                "thread_id": "t-1", "score": 0.9, "sensitivity": "internal",
            }],
            "stats": {"chunk_count": 1, "total_ms": 5, "received_scopes": list(user_scopes)},
        }

    def query_stream(self, question, *, user_scopes, k, history_messages,
                     user_account_id, super_admin):
        yield {"type": "sources", "data": [{
            "type": "Decision", "description": "decided X",
            "thread_id": "t-1", "score": 0.8, "sensitivity": "internal",
        }]}
        for word in ("Hel", "lo"):
            yield {"type": "token", "data": word}
        yield {"type": "done", "data": {"confidence": "medium"}}


class FakeMemory:
    async def build_context(self, *a):
        return ([], "e2e-thread-123")

    async def next_sequence(self, *a):
        return 1

    def make_turn(self, **k):
        return SimpleNamespace(**k)

    async def save_turn(self, t):
        pass

    async def record_access_audit(self, **k):
        pass

    async def list_threads(self, user_id, *, limit=20, before_id=None):
        return [SimpleNamespace(
            thread_id="thr-1", title="My thread", turn_count=2, total_tokens=42,
            status="active", created_at=_NOW, updated_at=_NOW,
        )]

    async def get_turns(self, thread_id, user_id, *, limit=50):
        return [SimpleNamespace(
            turn_id="tn-1", sequence=1, status="completed", latency_ms=7,
            created_at=_NOW,
            user_message=SimpleNamespace(content="what was decided?"),
            assistant_message=SimpleNamespace(content="X was decided."),
        )]

    async def delete_thread(self, thread_id, user_id):
        pass


class FakeResolver:
    def __init__(self):
        self.query = FakeQuery()
        self.memory = FakeMemory()

    def query_service(self, user):
        return self.query

    async def memory_service(self, user):
        return self.memory


async def main():
    resolver = FakeResolver()
    server = build_server(
        secret=SECRET,
        resolver=resolver,
        pipeline_executor=ThreadPoolExecutor(max_workers=4),
    )
    port = server.add_insecure_port("127.0.0.1:0")
    await server.start()
    print(f"GRPC_PORT={port}", flush=True)
    print(f"GRPC_SECRET={SECRET}", flush=True)
    print("e2e server ready — Ctrl+C to stop", file=sys.stderr, flush=True)
    await server.wait_for_termination()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
