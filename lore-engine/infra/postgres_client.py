"""
infra/postgres_client.py
────────────────────────
SQLAlchemy 2.0 async engine + session factory.
asyncpg is the underlying driver; SQLAlchemy handles pooling and ORM.
"""

from __future__ import annotations

from pgvector.asyncpg import register_vector
from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from core.logging import get_logger

log = get_logger(__name__)


def _asyncpg_dsn(dsn: str) -> str:
    """Ensure the DSN uses the postgresql+asyncpg:// scheme."""
    if dsn.startswith("postgresql+asyncpg://"):
        return dsn
    return dsn.replace("postgresql://", "postgresql+asyncpg://", 1)


class PostgresClient:
    """SQLAlchemy async engine with a session factory.

    Lifecycle:
        client = PostgresClient(dsn)
        await client.connect()   # validates connection
        ...
        await client.close()     # disposes pool
    """

    def __init__(self, dsn: str, schema: str | None = None) -> None:
        self._dsn = dsn
        connect_args: dict = {}
        if schema:
            # All memory tables live in this Postgres schema inside the shared
            # client-side `lore_data` DB (alongside the `chronograph` schema).
            connect_args["server_settings"] = {"search_path": f"{schema},public"}
        self._engine: AsyncEngine = create_async_engine(
            _asyncpg_dsn(dsn),
            pool_size=5,
            max_overflow=10,
            pool_pre_ping=True,   # detect stale connections before use
            pool_recycle=1800,    # recycle after 30 min
            echo=False,
            connect_args=connect_args,
        )
        # Register pgvector codec so asyncpg can bind/return vector values
        @event.listens_for(self._engine.sync_engine, "connect")
        def _on_connect(dbapi_conn, _record):
            dbapi_conn.run_async(register_vector)
        self._session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
            self._engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )

    async def connect(self) -> None:
        """Eagerly validate the connection. Raises on bad DSN / unreachable host."""
        async with self._engine.begin() as conn:
            await conn.run_sync(lambda _: None)
        log.info("postgres_engine_ready", dsn=self._dsn[:40] + "...")

    def session(self) -> AsyncSession:
        """Return a new AsyncSession. Use as `async with client.session() as s:`."""
        return self._session_factory()

    async def close(self) -> None:
        await self._engine.dispose()
        log.info("postgres_engine_closed")

    @property
    def engine(self) -> AsyncEngine:
        return self._engine
