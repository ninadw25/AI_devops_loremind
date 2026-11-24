"""
Integration tests for PostgresLongTermStore.

Requires MEMORY_POSTGRES_URI. Skipped when not set.
Tests cover every method of BaseLongTermStore + JSONB round-trips.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent.parent / ".env")
from sqlalchemy import text

from infra.postgres_client import PostgresClient
from memory.exceptions import ThreadNotFoundError
from memory.ids import new_ulid
from memory.models.context import RetrievedContext
from memory.models.message import Message
from memory.models.thread import Thread
from memory.models.tool import ToolCall
from memory.models.turn import Turn
from memory.stores.postgres_store import PostgresLongTermStore

POSTGRES_URI = os.getenv("MEMORY_POSTGRES_URI")
pytestmark = pytest.mark.skipif(
    not POSTGRES_URI, reason="MEMORY_POSTGRES_URI not set"
)


# ── Fixtures ──────────────────────────────────────────────────────────

@pytest.fixture
async def store():
    """Fresh store per test. Truncates all rows on teardown."""
    client = PostgresClient(POSTGRES_URI)
    await client.connect()
    s = PostgresLongTermStore(client)
    yield s
    async with client.engine.begin() as conn:
        await conn.execute(text("TRUNCATE turns, threads RESTART IDENTITY CASCADE"))
    await client.close()


def make_thread(user_id: str = "u1", title: str = "test thread") -> Thread:
    return Thread(thread_id=new_ulid(), user_id=user_id, title=title)


def make_turn(
    thread: Thread,
    seq: int = 0,
    tokens: int = 10,
    content: str = "hello",
    with_context: bool = False,
    with_tools: bool = False,
) -> Turn:
    return Turn(
        turn_id=new_ulid(),
        thread_id=thread.thread_id,
        user_id=thread.user_id,
        sequence=seq,
        user_message=Message(role="user", content=content),
        assistant_message=Message(role="assistant", content=f"reply to {content}"),
        retrieved_context=(
            [RetrievedContext(chunk_id="c1", source="Fact", content="fact", score=0.9)]
            if with_context else []
        ),
        tool_calls=(
            [ToolCall(tool_call_id="t1", tool_name="search", arguments={"q": "x"})]
            if with_tools else []
        ),
        token_usage={"total": tokens, "prompt": tokens - 2, "completion": 2},
        model="test-model",
        latency_ms=123,
        status="completed",
    )


# ── Thread CRUD ───────────────────────────────────────────────────────

async def test_create_and_get_thread(store):
    t = make_thread(title="hello world")
    await store.acreate_thread(t)
    result = await store.aget_thread(t.thread_id)
    assert result is not None
    assert result.thread_id == t.thread_id
    assert result.title == "hello world"
    assert result.user_id == "u1"
    assert result.turn_count == 0
    assert result.status == "active"


async def test_get_missing_thread_returns_none(store):
    result = await store.aget_thread("nonexistent-id")
    assert result is None


async def test_update_thread_title(store):
    t = make_thread()
    await store.acreate_thread(t)
    await store.aupdate_thread(t.thread_id, title="updated title")
    result = await store.aget_thread(t.thread_id)
    assert result.title == "updated title"


async def test_update_thread_summary(store):
    t = make_thread()
    await store.acreate_thread(t)
    await store.aupdate_thread(t.thread_id, summary="summary text", summary_up_to_sequence=3)
    result = await store.aget_thread(t.thread_id)
    assert result.summary == "summary text"
    assert result.summary_up_to_sequence == 3


async def test_update_missing_thread_raises(store):
    with pytest.raises(ThreadNotFoundError):
        await store.aupdate_thread("ghost-id", title="nope")


async def test_update_non_mutable_field_raises(store):
    t = make_thread()
    await store.acreate_thread(t)
    with pytest.raises(ValueError, match="Non-updatable"):
        await store.aupdate_thread(t.thread_id, turn_count=999)


async def test_soft_delete_sets_status_deleted(store):
    t = make_thread()
    await store.acreate_thread(t)
    await store.adelete_thread(t.thread_id, hard=False)
    result = await store.aget_thread(t.thread_id)
    assert result.status == "deleted"


async def test_soft_deleted_thread_excluded_from_list(store):
    t = make_thread()
    await store.acreate_thread(t)
    await store.adelete_thread(t.thread_id)
    listed = await store.alist_threads("u1")
    assert listed == []


async def test_hard_delete_removes_thread_and_cascades_turns(store):
    t = make_thread()
    await store.acreate_thread(t)
    await store.aappend_turn(make_turn(t, seq=0))
    await store.adelete_thread(t.thread_id, hard=True)
    assert await store.aget_thread(t.thread_id) is None
    assert await store.acount_turns(t.thread_id) == 0


# ── Thread listing ────────────────────────────────────────────────────

async def test_list_threads_filters_by_user(store):
    t1 = make_thread(user_id="alice")
    t2 = make_thread(user_id="bob")
    await store.acreate_thread(t1)
    await store.acreate_thread(t2)
    results = await store.alist_threads("alice")
    assert len(results) == 1
    assert results[0].user_id == "alice"


async def test_list_threads_respects_limit(store):
    for _ in range(5):
        t = make_thread()
        await store.acreate_thread(t)
    results = await store.alist_threads("u1", limit=3)
    assert len(results) == 3


async def test_list_threads_ordered_by_updated_at_desc(store):
    threads = []
    for i in range(3):
        t = make_thread(title=f"t{i}")
        await store.acreate_thread(t)
        threads.append(t)
        # bump updated_at so ordering is deterministic
        from datetime import datetime, timezone, timedelta
        ts = datetime.now(timezone.utc) + timedelta(seconds=i)
        await store.aupdate_thread(t.thread_id, updated_at=ts)
    results = await store.alist_threads("u1", limit=10)
    assert results[0].thread_id == threads[-1].thread_id  # most recently updated first


async def test_list_threads_cursor_pagination(store):
    created = []
    from datetime import datetime, timezone, timedelta
    for i in range(4):
        t = make_thread(title=f"page-{i}")
        await store.acreate_thread(t)
        ts = datetime.now(timezone.utc) + timedelta(seconds=i)
        await store.aupdate_thread(t.thread_id, updated_at=ts)
        created.append(t)

    # First page: latest 2
    page1 = await store.alist_threads("u1", limit=2)
    assert len(page1) == 2
    assert page1[0].thread_id == created[3].thread_id

    # Second page: 2 older ones
    page2 = await store.alist_threads("u1", limit=2, before_id=page1[-1].thread_id)
    assert len(page2) == 2
    assert page2[0].thread_id == created[1].thread_id


async def test_list_archived_threads(store):
    t = make_thread()
    await store.acreate_thread(t)
    await store.aupdate_thread(t.thread_id, status="archived")
    active = await store.alist_threads("u1", status_filter="active")
    archived = await store.alist_threads("u1", status_filter="archived")
    assert active == []
    assert len(archived) == 1


# ── Turns ─────────────────────────────────────────────────────────────

async def test_append_and_get_turns_chronological(store):
    t = make_thread()
    await store.acreate_thread(t)
    for i in range(3):
        await store.aappend_turn(make_turn(t, seq=i, content=f"q{i}"))
    turns = await store.aget_turns_for_thread(t.thread_id, limit=10)
    assert [turn.sequence for turn in turns] == [0, 1, 2]


async def test_get_turns_limit_returns_latest_n_chronologically(store):
    t = make_thread()
    await store.acreate_thread(t)
    for i in range(5):
        await store.aappend_turn(make_turn(t, seq=i))
    turns = await store.aget_turns_for_thread(t.thread_id, limit=3)
    assert len(turns) == 3
    assert [turn.sequence for turn in turns] == [2, 3, 4]  # latest 3, in order


async def test_get_turns_after_sequence(store):
    t = make_thread()
    await store.acreate_thread(t)
    for i in range(5):
        await store.aappend_turn(make_turn(t, seq=i))
    turns = await store.aget_turns_for_thread(t.thread_id, after_sequence=2, limit=10)
    assert [turn.sequence for turn in turns] == [3, 4]


async def test_count_turns(store):
    t = make_thread()
    await store.acreate_thread(t)
    assert await store.acount_turns(t.thread_id) == 0
    await store.aappend_turn(make_turn(t, seq=0))
    await store.aappend_turn(make_turn(t, seq=1))
    assert await store.acount_turns(t.thread_id) == 2


# ── acommit_turn atomicity ────────────────────────────────────────────

async def test_commit_turn_updates_thread_counters_atomically(store):
    t = make_thread()
    await store.acreate_thread(t)
    turn = make_turn(t, seq=0, tokens=75)
    await store.acommit_turn(turn)

    updated_thread = await store.aget_thread(t.thread_id)
    assert updated_thread.turn_count == 1
    assert updated_thread.total_tokens == 75

    turns = await store.aget_turns_for_thread(t.thread_id, limit=5)
    assert len(turns) == 1
    assert turns[0].turn_id == turn.turn_id


async def test_commit_multiple_turns_accumulates_counters(store):
    t = make_thread()
    await store.acreate_thread(t)
    for i in range(3):
        await store.acommit_turn(make_turn(t, seq=i, tokens=20))
    updated = await store.aget_thread(t.thread_id)
    assert updated.turn_count == 3
    assert updated.total_tokens == 60


# ── JSONB round-trips ─────────────────────────────────────────────────

async def test_turn_jsonb_fields_round_trip(store):
    t = make_thread()
    await store.acreate_thread(t)
    turn = make_turn(t, seq=0, with_context=True, with_tools=True)
    await store.aappend_turn(turn)

    [fetched] = await store.aget_turns_for_thread(t.thread_id, limit=1)
    assert fetched.user_message.role == "user"
    assert fetched.user_message.content == turn.user_message.content
    assert fetched.assistant_message.role == "assistant"

    assert len(fetched.retrieved_context) == 1
    assert fetched.retrieved_context[0].chunk_id == "c1"
    assert fetched.retrieved_context[0].score == 0.9

    assert len(fetched.tool_calls) == 1
    assert fetched.tool_calls[0].tool_name == "search"
    assert fetched.tool_calls[0].arguments == {"q": "x"}

    assert fetched.token_usage == turn.token_usage
    assert fetched.model == "test-model"
    assert fetched.latency_ms == 123


async def test_turn_null_assistant_message_round_trips(store):
    t = make_thread()
    await store.acreate_thread(t)
    turn = Turn(
        turn_id=new_ulid(),
        thread_id=t.thread_id,
        user_id=t.user_id,
        sequence=0,
        user_message=Message(role="user", content="pending"),
        assistant_message=None,
        status="pending",
    )
    await store.aappend_turn(turn)
    [fetched] = await store.aget_turns_for_thread(t.thread_id, limit=1)
    assert fetched.assistant_message is None
