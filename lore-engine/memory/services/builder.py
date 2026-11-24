from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from memory.stores.base import BaseLongTermStore

if TYPE_CHECKING:
    from memory.services.thread_repo import ThreadRepository

log = logging.getLogger("memory.builder")


class ContextBuilder:
    """Assembles the LLM history_messages list for a thread."""

    def __init__(self, store: BaseLongTermStore, repo: "ThreadRepository", recent_turns: int = 10) -> None:
        self._store = store
        self._repo = repo
        self._recent_turns = recent_turns

    async def build(
        self,
        thread_id: str | None,
        user_id: str,
        question: str,
    ) -> tuple[list[dict], str]:
        """
        Returns (history_messages, thread_id).

        history_messages is a list of {role, content} dicts for PRIOR turns.
        The caller (synthesis.py) appends the current user question as the
        final user message after injecting the graph context block.
        """
        if thread_id is None:
            thread = await self._repo.create(user_id, title=question)
            log.debug("New thread created: %s", thread.thread_id)
            return [], thread.thread_id

        thread = await self._repo.get(thread_id, user_id)
        turns = await self._store.aget_turns_for_thread(
            thread_id, limit=self._recent_turns
        )

        messages: list[dict] = []

        if thread.summary:
            messages.append({
                "role": "system",
                "content": f"Previous conversation summary: {thread.summary}",
            })

        for turn in turns:
            messages.append({"role": "user", "content": turn.user_message.content})
            if turn.assistant_message:
                messages.append({
                    "role": "assistant",
                    "content": turn.assistant_message.content,
                })

        log.debug("Built context: thread=%s turns=%d", thread_id, len(turns))
        return messages, thread_id

    async def next_sequence(self, thread_id: str) -> int:
        return await self._store.acount_turns(thread_id)
