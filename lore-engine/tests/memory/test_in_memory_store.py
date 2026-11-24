import pytest
from memory.stores.memory import InMemoryStore
from memory.models.thread import Thread
from memory.models.turn import Turn
from memory.models.message import Message
from memory.exceptions import ThreadNotFoundError
from memory.ids import new_ulid


@pytest.fixture
def store():
    return InMemoryStore()


@pytest.fixture
def sample_thread():
    return Thread(thread_id=new_ulid(), user_id="u1", title="test thread")


@pytest.fixture
def sample_turn(sample_thread):
    return Turn(
        turn_id=new_ulid(),
        thread_id=sample_thread.thread_id,
        user_id="u1",
        sequence=0,
        user_message=Message(role="user", content="hello"),
        assistant_message=Message(role="assistant", content="hi"),
        token_usage={"total": 10},
        status="completed",
    )


@pytest.mark.asyncio
async def test_create_and_get_thread(store, sample_thread):
    await store.acreate_thread(sample_thread)
    result = await store.aget_thread(sample_thread.thread_id)
    assert result is not None
    assert result.thread_id == sample_thread.thread_id
    assert result.user_id == "u1"


@pytest.mark.asyncio
async def test_get_nonexistent_thread_returns_none(store):
    result = await store.aget_thread("nonexistent")
    assert result is None


@pytest.mark.asyncio
async def test_list_threads_by_user(store):
    t1 = Thread(thread_id=new_ulid(), user_id="u1", title="a")
    t2 = Thread(thread_id=new_ulid(), user_id="u2", title="b")
    await store.acreate_thread(t1)
    await store.acreate_thread(t2)
    results = await store.alist_threads("u1")
    assert len(results) == 1
    assert results[0].user_id == "u1"


@pytest.mark.asyncio
async def test_update_thread(store, sample_thread):
    await store.acreate_thread(sample_thread)
    await store.aupdate_thread(sample_thread.thread_id, title="updated")
    result = await store.aget_thread(sample_thread.thread_id)
    assert result.title == "updated"


@pytest.mark.asyncio
async def test_update_nonexistent_raises(store):
    with pytest.raises(ThreadNotFoundError):
        await store.aupdate_thread("bad-id", title="x")


@pytest.mark.asyncio
async def test_soft_delete_thread(store, sample_thread):
    await store.acreate_thread(sample_thread)
    await store.adelete_thread(sample_thread.thread_id)
    result = await store.aget_thread(sample_thread.thread_id)
    assert result.status == "deleted"
    listed = await store.alist_threads("u1")
    assert listed == []


@pytest.mark.asyncio
async def test_commit_turn_updates_thread_counters(store, sample_thread, sample_turn):
    await store.acreate_thread(sample_thread)
    await store.acommit_turn(sample_turn)
    thread = await store.aget_thread(sample_thread.thread_id)
    assert thread.turn_count == 1
    assert thread.total_tokens == 10


@pytest.mark.asyncio
async def test_get_turns_returns_ordered(store, sample_thread):
    await store.acreate_thread(sample_thread)
    for i in range(3):
        turn = Turn(
            turn_id=new_ulid(),
            thread_id=sample_thread.thread_id,
            user_id="u1",
            sequence=i,
            user_message=Message(role="user", content=f"q{i}"),
            status="completed",
        )
        await store.aappend_turn(turn)
    turns = await store.aget_turns_for_thread(sample_thread.thread_id, limit=10)
    assert [t.sequence for t in turns] == [0, 1, 2]


@pytest.mark.asyncio
async def test_get_turns_respects_limit(store, sample_thread):
    await store.acreate_thread(sample_thread)
    for i in range(5):
        turn = Turn(
            turn_id=new_ulid(),
            thread_id=sample_thread.thread_id,
            user_id="u1",
            sequence=i,
            user_message=Message(role="user", content=f"q{i}"),
            status="completed",
        )
        await store.aappend_turn(turn)
    turns = await store.aget_turns_for_thread(sample_thread.thread_id, limit=3)
    assert len(turns) == 3
    assert turns[-1].sequence == 4  # last 3 = sequences 2,3,4
