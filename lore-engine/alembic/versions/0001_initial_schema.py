"""initial schema — threads and turns

Revision ID: 0001
Revises:
Create Date: 2026-06-05

"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "threads",
        sa.Column("thread_id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("title", sa.String(80), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("turn_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("summary_up_to_sequence", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="active"),
        sa.Column("schema_version", sa.Integer(), nullable=False, server_default="1"),
        sa.CheckConstraint(
            "status IN ('active', 'archived', 'deleted')",
            name="ck_threads_status",
        ),
        sa.PrimaryKeyConstraint("thread_id", name=op.f("pk_threads")),
    )
    op.create_index("idx_threads_user_status", "threads", ["user_id", "status"])
    op.create_index(
        "idx_threads_user_updated",
        "threads",
        ["user_id", sa.text("updated_at DESC")],
    )

    op.create_table(
        "turns",
        sa.Column("turn_id", sa.String(), nullable=False),
        sa.Column("thread_id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("user_message", postgresql.JSONB(), nullable=False),
        sa.Column("assistant_message", postgresql.JSONB(), nullable=True),
        sa.Column(
            "retrieved_context",
            postgresql.JSONB(),
            nullable=False,
            server_default="[]",
        ),
        sa.Column(
            "tool_calls", postgresql.JSONB(), nullable=False, server_default="[]"
        ),
        sa.Column(
            "token_usage", postgresql.JSONB(), nullable=False, server_default="{}"
        ),
        sa.Column("model", sa.String(), nullable=False, server_default=""),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("schema_version", sa.Integer(), nullable=False, server_default="1"),
        sa.CheckConstraint(
            "status IN ('pending', 'completed', 'failed')",
            name="ck_turns_status",
        ),
        sa.ForeignKeyConstraint(
            ["thread_id"],
            ["threads.thread_id"],
            ondelete="CASCADE",
            name=op.f("fk_turns_thread_id_threads"),
        ),
        sa.PrimaryKeyConstraint("turn_id", name=op.f("pk_turns")),
        sa.UniqueConstraint("thread_id", "sequence", name="uq_turns_thread_seq"),
    )
    op.create_index("idx_turns_thread_seq", "turns", ["thread_id", "sequence"])


def downgrade() -> None:
    op.drop_table("turns")
    op.drop_table("threads")
