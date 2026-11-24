import pytest
from memory import MemoryManager, MemoryConfig
from memory.stores.memory import InMemoryStore
from memory.ids import new_ulid


@pytest.fixture
def memory():
    store = InMemoryStore()
    config = MemoryConfig()
    return MemoryManager(config=config, store=store)


@pytest.mark.asyncio
async def test_build_context_new_thread(memory):
    messages, thread_id = await memory.build_context(None, "u1", "what is lore?")
    assert thread_id
    assert messages == []


@pytest.mark.asyncio
async def test_save_turn_and_list_threads(memory):
    messages, thread_id = await memory.build_context(None, "u1", "first question")
    seq = await memory.next_sequence(thread_id, "u1")
    turn = memory.make_turn(
        thread_id=thread_id,
        user_id="u1",
        sequence=seq,
        question="first question",
        answer="first answer",
        sources=[],
        model="nemotron",
        latency_ms=500,
    )
    await memory.save_turn(turn)
    threads = await memory.list_threads("u1")
    assert len(threads) == 1
    assert threads[0].turn_count == 1


@pytest.mark.asyncio
async def test_multi_turn_conversation(memory):
    _, thread_id = await memory.build_context(None, "u1", "q1")
    turn1 = memory.make_turn(
        thread_id=thread_id, user_id="u1", sequence=0,
        question="q1", answer="a1", sources=[], model="m", latency_ms=100,
    )
    await memory.save_turn(turn1)

    messages, _ = await memory.build_context(thread_id, "u1", "q2")
    assert len(messages) == 2
    assert messages[0] == {"role": "user", "content": "q1"}
    assert messages[1] == {"role": "assistant", "content": "a1"}


@pytest.mark.asyncio
async def test_get_turns_enforces_ownership(memory):
    from memory.exceptions import PermissionDeniedError
    _, thread_id = await memory.build_context(None, "u1", "q1")
    with pytest.raises(PermissionDeniedError):
        await memory.get_turns(thread_id, "u2")
