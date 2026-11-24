import pytest
from memory.stores.memory import InMemoryStore
from memory.services.builder import ContextBuilder
from memory.services.thread_repo import ThreadRepository
from memory.models.thread import Thread
from memory.models.turn import Turn
from memory.models.message import Message
from memory.ids import new_ulid


@pytest.fixture
def store():
    return InMemoryStore()


@pytest.fixture
def builder(store):
    repo = ThreadRepository(store)
    return ContextBuilder(store, repo, recent_turns=10)


@pytest.mark.asyncio
async def test_new_thread_created_when_no_thread_id(builder, store):
    messages, thread_id = await builder.build(None, "u1", "what is X?")
    assert thread_id
    assert messages == []
    thread = await store.aget_thread(thread_id)
    assert thread is not None
    assert thread.title == "what is X?"


@pytest.mark.asyncio
async def test_history_injected_for_existing_thread(builder, store):
    thread = Thread(thread_id=new_ulid(), user_id="u1", title="t")
    await store.acreate_thread(thread)
    for i in range(2):
        turn = Turn(
            turn_id=new_ulid(),
            thread_id=thread.thread_id,
            user_id="u1",
            sequence=i,
            user_message=Message(role="user", content=f"q{i}"),
            assistant_message=Message(role="assistant", content=f"a{i}"),
            status="completed",
        )
        await store.aappend_turn(turn)

    messages, returned_tid = await builder.build(thread.thread_id, "u1", "follow-up?")
    assert returned_tid == thread.thread_id
    assert len(messages) == 4
    assert messages[0] == {"role": "user", "content": "q0"}
    assert messages[1] == {"role": "assistant", "content": "a0"}
    assert messages[2] == {"role": "user", "content": "q1"}
    assert messages[3] == {"role": "assistant", "content": "a1"}


@pytest.mark.asyncio
async def test_summary_prepended_when_present(builder, store):
    thread = Thread(
        thread_id=new_ulid(), user_id="u1", title="t", summary="Prev summary."
    )
    await store.acreate_thread(thread)
    messages, _ = await builder.build(thread.thread_id, "u1", "next?")
    assert messages[0]["role"] == "system"
    assert "Prev summary." in messages[0]["content"]


@pytest.mark.asyncio
async def test_only_recent_turns_included(store):
    repo = ThreadRepository(store)
    builder = ContextBuilder(store, repo, recent_turns=3)
    thread = Thread(thread_id=new_ulid(), user_id="u1", title="t")
    await store.acreate_thread(thread)
    for i in range(5):
        turn = Turn(
            turn_id=new_ulid(),
            thread_id=thread.thread_id,
            user_id="u1",
            sequence=i,
            user_message=Message(role="user", content=f"q{i}"),
            assistant_message=Message(role="assistant", content=f"a{i}"),
            status="completed",
        )
        await store.aappend_turn(turn)
    messages, _ = await builder.build(thread.thread_id, "u1", "new?")
    assert len(messages) == 6
    assert messages[0]["content"] == "q2"


@pytest.mark.asyncio
async def test_next_sequence_increments(builder, store):
    thread = Thread(thread_id=new_ulid(), user_id="u1", title="t")
    await store.acreate_thread(thread)
    assert await builder.next_sequence(thread.thread_id) == 0
    turn = Turn(
        turn_id=new_ulid(), thread_id=thread.thread_id, user_id="u1",
        sequence=0, user_message=Message(role="user", content="q"), status="completed",
    )
    await store.aappend_turn(turn)
    assert await builder.next_sequence(thread.thread_id) == 1
