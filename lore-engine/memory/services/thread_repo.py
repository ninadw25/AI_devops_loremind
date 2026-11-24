from __future__ import annotations

import logging

from memory.exceptions import PermissionDeniedError, ThreadNotFoundError
from memory.ids import new_ulid
from memory.models.thread import Thread
from memory.stores.base import BaseLongTermStore

log = logging.getLogger("memory.thread_repo")


class ThreadRepository:
    """Thin facade over BaseLongTermStore that enforces user ownership."""

    def __init__(self, store: BaseLongTermStore) -> None:
        self._store = store

    async def create(self, user_id: str, title: str) -> Thread:
        thread = Thread(
            thread_id=new_ulid(),
            user_id=user_id,
            title=title[:80],
        )
        await self._store.acreate_thread(thread)
        log.debug("Thread created: %s user=%s", thread.thread_id, user_id)
        return thread

    async def get(self, thread_id: str, user_id: str) -> Thread:
        thread = await self._store.aget_thread(thread_id)
        if thread is None or thread.status == "deleted":
            raise ThreadNotFoundError(thread_id)
        if thread.user_id != user_id:
            log.warning("Permission denied: thread=%s user=%s", thread_id, user_id)
            raise PermissionDeniedError(thread_id)
        return thread

    async def list(
        self,
        user_id: str,
        *,
        limit: int = 20,
        before_id: str | None = None,
    ) -> list[Thread]:
        return await self._store.alist_threads(
            user_id, limit=limit, before_id=before_id, status_filter="active"
        )

    async def archive(self, thread_id: str, user_id: str) -> None:
        await self.get(thread_id, user_id)
        await self._store.aupdate_thread(thread_id, status="archived")

    async def delete(self, thread_id: str, user_id: str) -> None:
        await self.get(thread_id, user_id)
        await self._store.adelete_thread(thread_id)
