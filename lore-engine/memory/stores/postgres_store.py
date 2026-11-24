"""
memory/stores/postgres_store.py
────────────────────────────────
PostgreSQL implementation of BaseLongTermStore via SQLAlchemy 2.0 async ORM.
Implements BaseLongTermStore. Single source of truth for conversation memory.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import func, select, text, update

from infra.db.models import ThreadModel, TurnModel
from infra.postgres_client import PostgresClient
from memory.exceptions import ThreadNotFoundError
from memory.models.context import RetrievedContext
from memory.models.message import Message
from memory.models.thread import Thread
from memory.models.tool import ToolCall
from memory.models.turn import Turn
from memory.stores.base import BaseLongTermStore

log = logging.getLogger("memory.postgres_store")

_MUTABLE_THREAD_FIELDS = frozenset({
    "status", "title", "summary", "summary_up_to_sequence", "updated_at",
})


# ── Converters ────────────────────────────────────────────────────────

def _to_thread(m: ThreadModel) -> Thread:
    return Thread(
        thread_id=m.thread_id,
        user_id=m.user_id,
        title=m.title,
        created_at=m.created_at,
        updated_at=m.updated_at,
        turn_count=m.turn_count,
        total_tokens=m.total_tokens,
        summary=m.summary,
        summary_up_to_sequence=m.summary_up_to_sequence,
        status=m.status,
        schema_version=m.schema_version,
    )


def _turn_to_model(turn: Turn) -> TurnModel:
    return TurnModel(
        turn_id=turn.turn_id,
        thread_id=turn.thread_id,
        user_id=turn.user_id,
        sequence=turn.sequence,
        created_at=turn.created_at,
        user_message=turn.user_message.model_dump(),
        assistant_message=(
            turn.assistant_message.model_dump() if turn.assistant_message else None
        ),
        retrieved_context=[c.model_dump() for c in turn.retrieved_context],
        tool_calls=[t.model_dump() for t in turn.tool_calls],
        token_usage=turn.token_usage,
        model=turn.model,
        latency_ms=turn.latency_ms,
        status=turn.status,
        schema_version=turn.schema_version,
    )


def _to_turn(m: TurnModel) -> Turn:
    return Turn(
        turn_id=m.turn_id,
        thread_id=m.thread_id,
        user_id=m.user_id,
        sequence=m.sequence,
        created_at=m.created_at,
        user_message=Message(**m.user_message),
        assistant_message=(
            Message(**m.assistant_message) if m.assistant_message else None
        ),
        retrieved_context=[RetrievedContext(**c) for c in (m.retrieved_context or [])],
        tool_calls=[ToolCall(**t) for t in (m.tool_calls or [])],
        token_usage=m.token_usage or {},
        model=m.model,
        latency_ms=m.latency_ms,
        status=m.status,
        schema_version=m.schema_version,
    )


# ── Store ─────────────────────────────────────────────────────────────

class PostgresLongTermStore(BaseLongTermStore):
    """Durable PostgreSQL store — all mutations go through ORM sessions."""

    def __init__(self, client: PostgresClient) -> None:
        self._client = client

    async def ensure_indexes(self) -> None:
        # Schema + indexes managed by Alembic — nothing to do at runtime.
        log.info("postgres_store_ready")

    def close(self) -> None:
        # Engine lifecycle owned by PostgresClient — caller disposes.
        pass

    # ── Threads ───────────────────────────────────────────────────────

    async def acreate_thread(self, thread: Thread) -> None:
        async with self._client.session() as session:
            async with session.begin():
                session.add(ThreadModel(
                    thread_id=thread.thread_id,
                    user_id=thread.user_id,
                    title=thread.title,
                    created_at=thread.created_at,
                    updated_at=thread.updated_at,
                    turn_count=thread.turn_count,
                    total_tokens=thread.total_tokens,
                    summary=thread.summary,
                    summary_up_to_sequence=thread.summary_up_to_sequence,
                    status=thread.status,
                    schema_version=thread.schema_version,
                ))

    async def aget_thread(self, thread_id: str) -> Thread | None:
        async with self._client.session() as session:
            row = await session.get(ThreadModel, thread_id)
            return _to_thread(row) if row else None

    async def alist_threads(
        self,
        user_id: str,
        *,
        limit: int = 20,
        before_id: str | None = None,
        status_filter: str = "active",
    ) -> list[Thread]:
        async with self._client.session() as session:
            stmt = (
                select(ThreadModel)
                .where(
                    ThreadModel.user_id == user_id,
                    ThreadModel.status == status_filter,
                )
                .order_by(ThreadModel.updated_at.desc())
                .limit(limit)
            )
            if before_id:
                cursor_ts = (
                    select(ThreadModel.updated_at)
                    .where(ThreadModel.thread_id == before_id)
                    .scalar_subquery()
                )
                stmt = stmt.where(ThreadModel.updated_at < cursor_ts)
            result = await session.execute(stmt)
            return [_to_thread(r) for r in result.scalars().all()]

    async def aupdate_thread(self, thread_id: str, **fields) -> None:
        if not fields:
            return
        bad = set(fields) - _MUTABLE_THREAD_FIELDS
        if bad:
            raise ValueError(f"Non-updatable thread fields: {bad}")
        async with self._client.session() as session:
            async with session.begin():
                result = await session.execute(
                    update(ThreadModel)
                    .where(ThreadModel.thread_id == thread_id)
                    .values(**fields)
                    .returning(ThreadModel.thread_id)
                )
                if result.first() is None:
                    raise ThreadNotFoundError(thread_id)

    async def adelete_thread(self, thread_id: str, hard: bool = False) -> None:
        if hard:
            # Turns cascade via FK ON DELETE CASCADE
            async with self._client.session() as session:
                async with session.begin():
                    row = await session.get(ThreadModel, thread_id)
                    if row:
                        await session.delete(row)
        else:
            await self.aupdate_thread(thread_id, status="deleted")

    # ── Turns ─────────────────────────────────────────────────────────

    async def aappend_turn(self, turn: Turn) -> None:
        async with self._client.session() as session:
            async with session.begin():
                session.add(_turn_to_model(turn))

    async def aget_turns_for_thread(
        self,
        thread_id: str,
        *,
        limit: int = 10,
        after_sequence: int | None = None,
    ) -> list[Turn]:
        async with self._client.session() as session:
            stmt = select(TurnModel).where(TurnModel.thread_id == thread_id)
            if after_sequence is not None:
                stmt = stmt.where(TurnModel.sequence > after_sequence)
            # Fetch DESC to apply limit on latest, then reverse to chronological
            stmt = stmt.order_by(TurnModel.sequence.desc()).limit(limit)
            result = await session.execute(stmt)
            rows = result.scalars().all()
            return [_to_turn(r) for r in reversed(rows)]

    async def acount_turns(self, thread_id: str) -> int:
        async with self._client.session() as session:
            return await session.scalar(
                select(func.count())
                .select_from(TurnModel)
                .where(TurnModel.thread_id == thread_id)
            )

    async def acommit_turn(self, turn: Turn) -> None:
        """Insert turn and update thread counters atomically."""
        async with self._client.session() as session:
            async with session.begin():
                session.add(_turn_to_model(turn))
                await session.execute(
                    update(ThreadModel)
                    .where(ThreadModel.thread_id == turn.thread_id)
                    .values(
                        turn_count=ThreadModel.turn_count + 1,
                        total_tokens=(
                            ThreadModel.total_tokens
                            + turn.token_usage.get("total", 0)
                        ),
                        updated_at=datetime.now(timezone.utc),
                    )
                )

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
        """Insert one row into public.access_audit_log.

        Raw SQL (the table is intentionally not an ORM model — it is a
        write-only compliance trail read by the admin backends, not by the
        memory layer). Explicitly schema-qualified so it always lands in
        `public` regardless of the memory search_path. See
        docs/CONTENT_SUPERADMIN.md §9.
        """
        async with self._client.session() as session:
            async with session.begin():
                await session.execute(
                    text(
                        "INSERT INTO public.access_audit_log "
                        "(id, organization_id, user_id, query, results_count, "
                        " scope_count, super_admin) "
                        "VALUES (:id, :org, :uid, :q, :rc, :sc, :sa)"
                    ),
                    {
                        "id": audit_id,
                        "org": organization_id,
                        "uid": user_id,
                        "q": query,
                        "rc": results_count,
                        "sc": scope_count,
                        "sa": super_admin,
                    },
                )
