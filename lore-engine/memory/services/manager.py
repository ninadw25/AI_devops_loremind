from __future__ import annotations

import logging

from memory.config import MemoryConfig
from memory.ids import new_ulid
from memory.models.context import RetrievedContext
from memory.models.message import Message
from memory.models.thread import Thread
from memory.models.turn import Turn
from memory.services.builder import ContextBuilder
from memory.services.thread_repo import ThreadRepository
from memory.stores.base import BaseLongTermStore
from memory.tokenizer import count_tokens

log = logging.getLogger("memory.manager")


class MemoryManager:
    """
    Single public entry point for conversation memory.

    Application code (api/, engine/) ONLY imports this class.
    Never import from memory.stores or memory.services directly.
    """

    def __init__(self, config: MemoryConfig, store: BaseLongTermStore) -> None:
        self._config = config
        self._store = store
        self._repo = ThreadRepository(store)
        self._builder = ContextBuilder(store, self._repo, recent_turns=config.recent_turns_in_context)

    async def build_context(
        self,
        thread_id: str | None,
        user_id: str,
        question: str,
    ) -> tuple[list[dict], str]:
        """Return (history_messages, thread_id). Creates thread if thread_id is None."""
        return await self._builder.build(thread_id, user_id, question)

    async def save_turn(self, turn: Turn) -> None:
        """Durably persist a completed turn (atomic with thread counter update)."""
        await self._store.acommit_turn(turn)
        log.debug("Turn saved: thread=%s seq=%d", turn.thread_id, turn.sequence)

    async def record_access_audit(
        self,
        *,
        organization_id: str | None,
        user_id: str,
        query: str,
        results_count: int,
        scope_count: int,
        super_admin: bool = True,
    ) -> None:
        """Persist one super-admin-bypassed ('read all') query to the access
        audit trail (CONTENT_SUPERADMIN §9). Id is a fresh ULID."""
        await self._store.arecord_access_audit(
            audit_id=new_ulid(),
            organization_id=organization_id,
            user_id=user_id,
            query=query,
            results_count=results_count,
            scope_count=scope_count,
            super_admin=super_admin,
        )

    async def next_sequence(self, thread_id: str, user_id: str) -> int:
        """Monotonic sequence number for the next turn in this thread."""
        await self._repo.get(thread_id, user_id)  # ownership check
        return await self._builder.next_sequence(thread_id)

    async def list_threads(
        self,
        user_id: str,
        *,
        limit: int = 20,
        before_id: str | None = None,
    ) -> list[Thread]:
        return await self._repo.list(user_id, limit=limit, before_id=before_id)

    async def get_turns(
        self,
        thread_id: str,
        user_id: str,
        *,
        limit: int = 50,
    ) -> list[Turn]:
        await self._repo.get(thread_id, user_id)  # ownership check
        return await self._store.aget_turns_for_thread(thread_id, limit=limit)

    async def archive_thread(self, thread_id: str, user_id: str) -> None:
        await self._repo.archive(thread_id, user_id)

    async def delete_thread(self, thread_id: str, user_id: str) -> None:
        await self._repo.delete(thread_id, user_id)

    # Called by api/routes/query.py to build a Turn from engine output before save_turn().
    def make_turn(
        self,
        *,
        thread_id: str,
        user_id: str,
        sequence: int,
        question: str,
        answer: str,
        sources: list[dict],
        model: str,
        latency_ms: int | None,
        status: str = "completed",
    ) -> Turn:
        """Construct a Turn from query engine output."""
        token_est = count_tokens(question) + count_tokens(answer)
        return Turn(
            turn_id=new_ulid(),
            thread_id=thread_id,
            user_id=user_id,
            sequence=sequence,
            user_message=Message(role="user", content=question),
            assistant_message=Message(role="assistant", content=answer),
            retrieved_context=[
                RetrievedContext(
                    chunk_id=s.get("thread_id") or s.get("description", "")[:20],
                    source=s.get("type", "unknown"),
                    content=s.get("description", ""),
                    score=float(s.get("score", 0.0)),
                    metadata={"sensitivity": s.get("sensitivity", "")},
                )
                for s in sources
            ],
            token_usage={"total": token_est},
            model=model,
            latency_ms=latency_ms,
            status=status,
        )
