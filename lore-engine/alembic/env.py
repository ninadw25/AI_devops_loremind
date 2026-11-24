from __future__ import annotations

import asyncio
import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

# Make sure the project root is importable
sys.path.insert(0, str(Path(__file__).parent.parent))

from infra.db.models import Base  # noqa: E402

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _get_url() -> str:
    """Read DB URL from env, fall back to alembic.ini value."""
    raw = os.getenv("MEMORY_POSTGRES_URI", config.get_main_option("sqlalchemy.url", ""))
    if not raw:
        raise RuntimeError("MEMORY_POSTGRES_URI is not set")
    if raw.startswith("postgresql://"):
        return raw.replace("postgresql://", "postgresql+asyncpg://", 1)
    return raw


def do_run_migrations(connection):
    # Memory tables live in a dedicated Postgres schema inside the shared
    # client-side `lore_data` DB. Create it and route DDL there.
    schema = os.getenv("MEMORY_SCHEMA", "memory")
    connection.exec_driver_sql(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
    connection.exec_driver_sql(f'SET search_path TO "{schema}", public')
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        version_table_schema=schema,
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_offline() -> None:
    """Generate SQL script without a live DB connection."""
    context.configure(
        url=_get_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    cfg = config.get_section(config.config_ini_section, {})
    cfg["sqlalchemy.url"] = _get_url()
    connectable = async_engine_from_config(
        cfg,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
