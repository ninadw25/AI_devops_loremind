from __future__ import annotations
from datetime import datetime, timezone

from memory.exceptions import ThreadNotFoundError
from memory.models.thread import Thread
from memory.models.turn import Turn
from memory.stores.base import BaseLongTermStore


class InMemoryStore(BaseLongTermStore):
    """In-process store for tests and local dev. Not thread-safe."""

    def __init__(self) -> None:
        self._threads: dict[str, Thread] = {}
        self._turns: dict[str, list[Turn]] = {}  # thread_id → sorted turns

    async def acreate_thread(self, thread: Thread) -> None:
        self._threads[thread.thread_id] = thread
        self._turns.setdefault(thread.thread_id, [])

    async def aget_thread(self, thread_id: str) -> Thread | None:
        return self._threads.get(thread_id)

    async def alist_threads(
        self,
        user_id: str,
        *,
        limit: int = 20,
        before_id: str | None = None,
        status_filter: str = "active",
    ) -> list[Thread]:
        threads = [
            t for t in self._threads.values()
            if t.user_id == user_id and t.status == status_filter
        ]
        threads.sort(key=lambda t: t.updated_at, reverse=True)
        if before_id:
            idx = next(
                (i for i, t in enumerate(threads) if t.thread_id == before_id), None
            )
            if idx is not None:
                threads = threads[idx + 1:]
        return threads[:limit]

    async def aupdate_thread(self, thread_id: str, **fields) -> None:
        thread = self._threads.get(thread_id)
        if thread is None:
            raise ThreadNotFoundError(thread_id)
        self._threads[thread_id] = thread.model_copy(update=fields)

    async def adelete_thread(self, thread_id: str, hard: bool = False) -> None:
        if hard:
            self._threads.pop(thread_id, None)
            self._turns.pop(thread_id, None)
        else:
            await self.aupdate_thread(thread_id, status="deleted")

    async def aappend_turn(self, turn: Turn) -> None:
        self._turns.setdefault(turn.thread_id, []).append(turn)

    async def aget_turns_for_thread(
        self,
        thread_id: str,
        *,
        limit: int = 10,
        after_sequence: int | None = None,
    ) -> list[Turn]:
        turns = self._turns.get(thread_id, [])
        if after_sequence is not None:
            turns = [t for t in turns if t.sequence > after_sequence]
        turns = sorted(turns, key=lambda t: t.sequence)
        return turns[-limit:]

    async def acount_turns(self, thread_id: str) -> int:
        return len(self._turns.get(thread_id, []))

    async def acommit_turn(self, turn: Turn) -> None:
        await self.aappend_turn(turn)
        thread = self._threads.get(turn.thread_id)
        if thread:
            await self.aupdate_thread(
                turn.thread_id,
                turn_count=thread.turn_count + 1,
                total_tokens=thread.total_tokens + turn.token_usage.get("total", 0),
                updated_at=datetime.now(timezone.utc),
            )
