"""
services/memory_service.py
──────────────────────────
Conversation memory with observability and typed error mapping.

Wraps MemoryManager (which is already well-structured) and adds:
  - @observe tracing on every public call
  - Structured log fields (user_id, thread_id)
  - Maps memory.exceptions → core.exceptions where appropriate

All routes import this; none import MemoryManager directly.
"""

from __future__ import annotations

from core.exceptions import PermissionDeniedError, ThreadNotFoundError
from core.logging import get_logger
from core.observability import observe
from memory.exceptions import (
    PermissionDeniedError as _MemPermDenied,
    ThreadNotFoundError as _MemThreadNotFound,
)
from memory.models.thread import Thread
from memory.models.turn import Turn
from memory.services.manager import MemoryManager

log = get_logger(__name__)


class MemoryService:
    """Thin observability + error-mapping wrapper around MemoryManager."""

    def __init__(self, manager: MemoryManager) -> None:
        self._manager = manager

    @observe(name="memory.build-context")
    async def build_context(
        self,
        thread_id: str | None,
        user_id: str,
        question: str,
    ) -> tuple[list[dict], str]:
        """Return (history_messages, thread_id). Creates a new thread if None."""
        try:
            history, tid = await self._manager.build_context(
                thread_id, user_id, question
            )
            log.info(
                "memory_context_built",
                user_id=user_id,
                thread_id=tid,
                history_turns=len([m for m in history if m.get("role") == "user"]),
                new_thread=thread_id is None,
            )
            return history, tid
        except _MemThreadNotFound as exc:
            raise ThreadNotFoundError(str(exc)) from exc
        except _MemPermDenied as exc:
            raise PermissionDeniedError(str(exc)) from exc

    @observe(name="memory.save-turn")
    async def save_turn(self, turn: Turn) -> None:
        """Persist a completed turn atomically."""
        try:
            await self._manager.save_turn(turn)
            log.info(
                "memory_turn_saved",
                thread_id=turn.thread_id,
                user_id=turn.user_id,
                sequence=turn.sequence,
                status=turn.status,
            )
        except Exception as exc:
            log.error(
                "memory_turn_save_failed",
                thread_id=turn.thread_id,
                user_id=turn.user_id,
                error=str(exc),
            )
            raise

    async def next_sequence(self, thread_id: str, user_id: str) -> int:
        try:
            return await self._manager.next_sequence(thread_id, user_id)
        except _MemThreadNotFound as exc:
            raise ThreadNotFoundError(str(exc)) from exc
        except _MemPermDenied as exc:
            raise PermissionDeniedError(str(exc)) from exc

    @observe(name="memory.record-access-audit")
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
        """Best-effort: persist one super-admin-bypassed ('read all') query to
        the access audit trail (CONTENT_SUPERADMIN §9). Never raises — an audit
        write must not break the user's query. Failures are logged and swallowed.
        """
        try:
            await self._manager.record_access_audit(
                organization_id=organization_id,
                user_id=user_id,
                query=query,
                results_count=results_count,
                scope_count=scope_count,
                super_admin=super_admin,
            )
            log.info(
                "access_audit_recorded",
                user_id=user_id,
                results_count=results_count,
                super_admin=super_admin,
            )
        except Exception as exc:  # noqa: BLE001 — audit is best-effort
            log.error("access_audit_record_failed", user_id=user_id, error=str(exc))

    def make_turn(self, **kwargs) -> Turn:
        return self._manager.make_turn(**kwargs)

    async def list_threads(
        self,
        user_id: str,
        *,
        limit: int = 20,
        before_id: str | None = None,
    ) -> list[Thread]:
        return await self._manager.list_threads(user_id, limit=limit, before_id=before_id)

    async def get_turns(
        self,
        thread_id: str,
        user_id: str,
        *,
        limit: int = 50,
    ) -> list[Turn]:
        try:
            return await self._manager.get_turns(thread_id, user_id, limit=limit)
        except _MemThreadNotFound as exc:
            raise ThreadNotFoundError(str(exc)) from exc
        except _MemPermDenied as exc:
            raise PermissionDeniedError(str(exc)) from exc

    async def delete_thread(self, thread_id: str, user_id: str) -> None:
        try:
            await self._manager.delete_thread(thread_id, user_id)
        except _MemThreadNotFound as exc:
            raise ThreadNotFoundError(str(exc)) from exc
        except _MemPermDenied as exc:
            raise PermissionDeniedError(str(exc)) from exc
