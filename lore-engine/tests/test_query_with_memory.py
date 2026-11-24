"""
Integration smoke tests for the query endpoint with memory.

Uses InMemoryStore + fake QueryService so no Neo4j, Postgres, or LLM
API calls are needed.
"""

import pytest
from unittest.mock import MagicMock, AsyncMock
from fastapi.testclient import TestClient

# lore-engine has exactly one entrance: a gateway-forwarded request carrying the
# shared internal secret. These tests go through it like the gateway does.
TEST_INTERNAL_SECRET = "test-gateway-secret"


@pytest.fixture(autouse=True)
def _gateway_secret(monkeypatch):
    """Configure the secret these tests authenticate with.

    Set explicitly rather than inherited: Settings falls back to .env, and a
    test suite that silently picks up the developer's secret would still pass
    with the auth check broken.
    """
    from core.settings import _reset_settings

    monkeypatch.setenv("INTERNAL_AUTH_SECRET", TEST_INTERNAL_SECRET)
    _reset_settings()
    yield
    _reset_settings()


@pytest.fixture
def test_app():
    """Return a TestClient with faked services on app.state."""
    from api.main import app
    from memory import MemoryConfig, MemoryManager
    from memory.stores.memory import InMemoryStore
    from services.memory_service import MemoryService
    from services.query_service import QueryService

    # Build real memory with in-memory store
    store = InMemoryStore()
    memory_manager = MemoryManager(config=MemoryConfig(), store=store)
    memory_service = MemoryService(memory_manager)

    # Fake QueryService
    fake_qs = MagicMock(spec=QueryService)
    fake_qs.graph_available = False
    fake_qs.query.return_value = {
        "answer":     "Test answer.",
        "confidence": "high",
        "sources":    [],
        "stats":      {"total_ms": 100},
    }
    fake_qs._chat = MagicMock()
    fake_qs._chat.model = "fake/model"

    # Inject into app.state (bypasses lifespan)
    app.state.query_service  = fake_qs
    app.state.memory_service = memory_service
    app.state.chat_provider  = fake_qs._chat
    app.state.embedding_provider = MagicMock(name="nvidia", model="fake/embed")

    return TestClient(app, raise_server_exceptions=True), fake_qs


def _auth_headers():
    """What the gateway sends: the forwarded user JWT + the internal secret.

    The token is signed with an arbitrary key on purpose — require_gateway does
    not verify the signature (the gateway already did). The internal secret is
    what actually authenticates the call.
    """
    import jwt

    token = jwt.encode({"sub": "admin", "org": "*"}, "not-checked", algorithm="HS256")
    return {
        "Authorization": f"Bearer {token}",
        "X-Internal-Auth": TEST_INTERNAL_SECRET,
    }


def test_query_creates_thread(test_app):
    client, _ = test_app
    resp = client.post(
        "/api/query",
        json={"query": "Who is Alice?", "thread_id": None, "k": 5},
        headers=_auth_headers(),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "thread_id" in body
    assert body["thread_id"]
    assert body["answer"] == "Test answer."


def test_query_continues_thread(test_app):
    client, fake_qs = test_app
    headers = _auth_headers()

    r1 = client.post(
        "/api/query",
        json={"query": "What did Alice decide?", "thread_id": None},
        headers=headers,
    )
    assert r1.status_code == 200
    thread_id = r1.json()["thread_id"]

    r2 = client.post(
        "/api/query",
        json={"query": "And what about Bob?", "thread_id": thread_id},
        headers=headers,
    )
    assert r2.status_code == 200
    assert r2.json()["thread_id"] == thread_id

    # On the second call, history_messages should be non-empty
    call_kwargs = fake_qs.query.call_args.kwargs
    assert call_kwargs.get("history_messages")


def test_list_threads(test_app):
    client, _ = test_app
    headers = _auth_headers()

    client.post("/api/query", json={"query": "Hello", "thread_id": None}, headers=headers)

    resp = client.get("/api/threads", headers=headers)
    assert resp.status_code == 200
    assert len(resp.json()) >= 1


def test_get_turns_for_thread(test_app):
    client, _ = test_app
    headers = _auth_headers()

    r = client.post(
        "/api/query",
        json={"query": "First Q", "thread_id": None},
        headers=headers,
    )
    thread_id = r.json()["thread_id"]

    resp = client.get(f"/api/threads/{thread_id}/turns", headers=headers)
    assert resp.status_code == 200
    turns = resp.json()
    assert len(turns) == 1
    assert turns[0]["question"] == "First Q"
