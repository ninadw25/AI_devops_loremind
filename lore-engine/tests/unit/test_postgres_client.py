"""
Unit tests for PostgresClient (infra/postgres_client.py).

Tests DSN conversion and session factory without a live DB.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from infra.postgres_client import PostgresClient, _asyncpg_dsn


# ── DSN conversion ────────────────────────────────────────────────────

def test_asyncpg_dsn_converts_plain_postgresql():
    result = _asyncpg_dsn("postgresql://user:pw@host:5432/db")
    assert result == "postgresql+asyncpg://user:pw@host:5432/db"


def test_asyncpg_dsn_leaves_already_correct_dsn():
    dsn = "postgresql+asyncpg://user:pw@host:5432/db"
    assert _asyncpg_dsn(dsn) == dsn


def test_asyncpg_dsn_is_idempotent():
    dsn = "postgresql+asyncpg://x:y@localhost/mydb"
    assert _asyncpg_dsn(_asyncpg_dsn(dsn)) == dsn


# ── Session factory ───────────────────────────────────────────────────

def test_session_returns_async_session_without_connecting():
    """session() should be creatable before connect() — SQLAlchemy is lazy."""
    client = PostgresClient("postgresql://lore:pw@localhost:5433/lore_memory")
    session = client.session()
    assert isinstance(session, AsyncSession)


def test_engine_property_accessible_without_connect():
    from sqlalchemy.ext.asyncio import AsyncEngine
    client = PostgresClient("postgresql://lore:pw@localhost:5433/lore_memory")
    assert isinstance(client.engine, AsyncEngine)
