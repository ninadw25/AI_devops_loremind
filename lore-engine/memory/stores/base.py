from __future__ import annotations
from abc import ABC, abstractmethod

from memory.models.thread import Thread
from memory.models.turn import Turn


class BaseLongTermStore(ABC):
    """Primary durable store. Failures propagate as exceptions."""

    # ── Threads ──────────────────────────────────────────────────────

    @abstractmethod
    async def acreate_thread(self, thread: Thread) -> None: ...

    @abstractmethod
    async def aget_thread(self, thread_id: str) -> Thread | None: ...

    @abstractmethod
    async def alist_threads(
        self,
        user_id: str,
        *,
        limit: int = 20,
        before_id: str | None = None,
        status_filter: str = "active",
    ) -> list[Thread]: ...

    @abstractmethod
    async def aupdate_thread(self, thread_id: str, **fields) -> None: ...

    @abstractmethod
    async def adelete_thread(self, thread_id: str, hard: bool = False) -> None: ...

    # ── Turns ─────────────────────────────────────────────────────────

    @abstractmethod
    async def aappend_turn(self, turn: Turn) -> None: ...

    @abstractmethod
    async def aget_turns_for_thread(
        self,
        thread_id: str,
        *,
        limit: int = 10,
        after_sequence: int | None = None,
    ) -> list[Turn]: ...

    @abstractmethod
    async def acount_turns(self, thread_id: str) -> int: ...

    @abstractmethod
    async def acommit_turn(self, turn: Turn) -> None:
        """Atomically insert turn AND update thread counters."""
        ...

    # ── Access audit (content.read_all super-admin bypass) ────────────

    async def arecord_access_audit(
        self,
        *,
        audit_id: str,
        organization_id: str | None,
        user_id: str,
        query: str,
        results_count: int,
        scope_count: int,
        super_admin: bool = True,
    ) -> None:
        """Record one super-admin-bypassed read (CONTENT_SUPERADMIN §9).

        Default no-op so non-durable stores (e.g. the in-memory test store)
        need not implement it; the Postgres store overrides this.
        """
        return None
