"""
infra/db/models.py
──────────────────
SQLAlchemy 2.0 ORM models for the memory layer and knowledge graph.
These are the source-of-truth for Alembic autogenerate.
Domain models (memory/models/) are separate — converters live in the store.
"""

from __future__ import annotations

import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.sql import func

_EMBED_DIM = 2048  # must match settings.embedding_dim


class Base(DeclarativeBase):
    pass


class ThreadModel(Base):
    __tablename__ = "threads"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'archived', 'deleted')",
            name="ck_threads_status",
        ),
        Index("idx_threads_user_status", "user_id", "status"),
    )

    thread_id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(String, nullable=False)
    title: Mapped[str] = mapped_column(String(80), nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    turn_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary_up_to_sequence: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


# Descending index on updated_at must be defined after the class
Index("idx_threads_user_updated", ThreadModel.user_id, ThreadModel.updated_at.desc())


class TurnModel(Base):
    __tablename__ = "turns"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'completed', 'failed')",
            name="ck_turns_status",
        ),
        UniqueConstraint("thread_id", "sequence", name="uq_turns_thread_seq"),
        Index("idx_turns_thread_seq", "thread_id", "sequence"),
    )

    turn_id: Mapped[str] = mapped_column(String, primary_key=True)
    thread_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("threads.thread_id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[str] = mapped_column(String, nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    user_message: Mapped[dict] = mapped_column(JSONB, nullable=False)
    assistant_message: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    retrieved_context: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list
    )
    tool_calls: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    token_usage: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    model: Mapped[str] = mapped_column(String, nullable=False, default="")
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


# ── Knowledge graph ────────────────────────────────────────────────────

class KnowledgeNodeModel(Base):
    """One node in the org knowledge graph (Decision/Task/Fact/Event/Person/Topic/JiraIssue).

    scope_ids + access_groups use Postgres ARRAY(String) so the && overlap
    operator works for fail-closed scope gating in every query.
    embedding is nullable — spine nodes (Person, Topic, Org) may not have one.
    """
    __tablename__ = "kg_nodes"
    __table_args__ = (
        Index("idx_kg_nodes_org_type", "org_id", "node_type"),
        Index("idx_kg_nodes_scope", "scope_ids", postgresql_using="gin"),
        Index("idx_kg_nodes_access", "access_groups", postgresql_using="gin"),
        Index("idx_kg_nodes_thread", "org_id", "source_thread_id"),
        # Vector ANN index is created via Alembic migration after data load.
        # pgvector HNSW/IVFFlat cap at 2000 dims; our embedding_dim is 2048.
        # Until dim is reduced or pgvector raises the cap, exact KNN is used.
    )

    node_id: Mapped[str] = mapped_column(String, primary_key=True)
    org_id: Mapped[str] = mapped_column(String, nullable=False)
    node_type: Mapped[str] = mapped_column(String(30), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    source_thread_id: Mapped[str | None] = mapped_column(String, nullable=True)
    sensitivity_label: Mapped[str | None] = mapped_column(String(50), nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    # ARRAY for && overlap operator (scope gating, access control)
    scope_ids: Mapped[list] = mapped_column(ARRAY(String), nullable=False, default=list)
    access_groups: Mapped[list] = mapped_column(ARRAY(String), nullable=False, default=list)
    department_tags: Mapped[list] = mapped_column(ARRAY(String), nullable=False, default=list)
    # pgvector embedding — nullable for spine nodes that don't need ANN search
    embedding: Mapped[list[float] | None] = mapped_column(Vector(_EMBED_DIM), nullable=True)
    # Node-type-specific fields: primary_email, is_internal, jira_key, status, etc.
    meta: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)


class KnowledgeEdgeModel(Base):
    """Directed edge in the knowledge graph.

    rel_type matches the ontology relation names used in Cypher:
    DECIDED, ASSIGNED_TO, BECAUSE, STATED_BY, PARTICIPATED_IN, REGARDING,
    CORROBORATES, CONTAINS, REPORTS_TO, DERIVED_FROM.
    """
    __tablename__ = "kg_edges"
    __table_args__ = (
        Index("idx_kg_edges_org_src", "org_id", "src_id"),
        Index("idx_kg_edges_org_dst", "org_id", "dst_id"),
        Index("idx_kg_edges_rel", "org_id", "rel_type"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    org_id: Mapped[str] = mapped_column(String, nullable=False)
    src_id: Mapped[str] = mapped_column(
        String, ForeignKey("kg_nodes.node_id", ondelete="CASCADE"), nullable=False
    )
    dst_id: Mapped[str] = mapped_column(
        String, ForeignKey("kg_nodes.node_id", ondelete="CASCADE"), nullable=False
    )
    rel_type: Mapped[str] = mapped_column(String(50), nullable=False)
    meta: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
