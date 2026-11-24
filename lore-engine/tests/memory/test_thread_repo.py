import pytest
from memory.stores.memory import InMemoryStore
from memory.services.thread_repo import ThreadRepository
from memory.exceptions import ThreadNotFoundError, PermissionDeniedError
from memory.ids import new_ulid


@pytest.fixture
def store():
    return InMemoryStore()


@pytest.fixture
def repo(store):
    return ThreadRepository(store)


@pytest.mark.asyncio
async def test_create_thread_generates_ulid(repo):
    thread = await repo.create("u1", title="my question is long enough to truncate" * 5)
    assert len(thread.thread_id) > 0
    assert len(thread.title) <= 80


@pytest.mark.asyncio
async def test_get_own_thread(repo):
    thread = await repo.create("u1", title="hello")
    result = await repo.get(thread.thread_id, "u1")
    assert result.thread_id == thread.thread_id


@pytest.mark.asyncio
async def test_get_other_users_thread_raises(repo):
    thread = await repo.create("u1", title="hello")
    with pytest.raises(PermissionDeniedError):
        await repo.get(thread.thread_id, "u2")


@pytest.mark.asyncio
async def test_get_deleted_thread_raises(repo):
    thread = await repo.create("u1", title="hello")
    await repo.delete(thread.thread_id, "u1")
    with pytest.raises(ThreadNotFoundError):
        await repo.get(thread.thread_id, "u1")


@pytest.mark.asyncio
async def test_list_only_own_active_threads(repo):
    await repo.create("u1", title="t1")
    await repo.create("u1", title="t2")
    t3 = await repo.create("u1", title="t3")
    await repo.archive(t3.thread_id, "u1")
    results = await repo.list("u1")
    assert len(results) == 2
    assert all(t.status == "active" for t in results)


@pytest.mark.asyncio
async def test_archive_thread(repo):
    thread = await repo.create("u1", title="hello")
    await repo.archive(thread.thread_id, "u1")
    listed = await repo.list("u1")
    assert listed == []
