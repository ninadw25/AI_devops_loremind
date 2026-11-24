"""
chronograph/store/models.py
───────────────────────────
SQLAlchemy 2.x ORM models for the Chronograph PostgreSQL store.

All tables live in the `chronograph` schema so they co-exist cleanly
with the Neo4j-fronted knowledge graph in the same Postgres instance
(if ever needed) without polluting `public`.

The pgvector `Vector` type is used for the embedding column so the
ORM model participates in HNSW index creation via `Base.metadata`.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

try:
    from pgvector.sqlalchemy import Vector
    _VECTOR_AVAILABLE = True
except ImportError:
    # Graceful degradation so imports don't crash without the extension
    Vector = None  # type: ignore[assignment]
    _VECTOR_AVAILABLE = False

SCHEMA = "chronograph"
EMBED_DIM = 2048  # nvidia/llama-nemotron-embed-vl-1b-v2 (2048-d NeMo)


class Base(DeclarativeBase):
    pass


# ── Repos ────────────────────────────────────────────────────────────

class Repo(Base):
    __tablename__ = "repos"
    __table_args__ = {"schema": SCHEMA}

    project_id: Mapped[str] = mapped_column(Text, primary_key=True)
    data: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


# ── Commits ──────────────────────────────────────────────────────────

class Commit(Base):
    __tablename__ = "commits"
    __table_args__ = (
        Index("commits_author_idx", "project_id", "author_email"),
        Index("commits_date_idx", "project_id", "authored_date"),
        # pg_trgm GIN on message is created via raw DDL in PgStore._apply_schema
        {"schema": SCHEMA},
    )

    project_id: Mapped[str] = mapped_column(Text, primary_key=True, nullable=False)
    sha: Mapped[str] = mapped_column(Text, primary_key=True, nullable=False)
    short_id: Mapped[str] = mapped_column(Text, nullable=False, default="")
    title: Mapped[str] = mapped_column(Text, nullable=False, default="")
    message: Mapped[str] = mapped_column(Text, nullable=False, default="")
    author_name: Mapped[str] = mapped_column(Text, nullable=False, default="")
    author_email: Mapped[str] = mapped_column(Text, nullable=False, default="")
    authored_date: Mapped[str | None] = mapped_column(Text, nullable=True)
    committed_date: Mapped[str | None] = mapped_column(Text, nullable=True)
    parent_ids: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, default=list
    )
    additions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    deletions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    web_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    classification: Mapped[str | None] = mapped_column(Text, nullable=True)


# ── Members ──────────────────────────────────────────────────────────

class Member(Base):
    __tablename__ = "members"
    __table_args__ = {"schema": SCHEMA}

    project_id: Mapped[str] = mapped_column(Text, primary_key=True, nullable=False)
    username: Mapped[str] = mapped_column(Text, primary_key=True, nullable=False)
    data: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)


# ── Commit files (normalised) ────────────────────────────────────────

class CommitFile(Base):
    __tablename__ = "commit_files"
    __table_args__ = (
        Index("commit_files_path_idx", "project_id", "file_path",
              postgresql_where=~Column("deleted_file")),
        Index("commit_files_commit_idx", "project_id", "commit_sha"),
        {"schema": SCHEMA},
    )

    project_id: Mapped[str] = mapped_column(Text, primary_key=True, nullable=False)
    commit_sha: Mapped[str] = mapped_column(Text, primary_key=True, nullable=False)
    file_path: Mapped[str] = mapped_column(Text, primary_key=True, nullable=False)
    old_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    new_file: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    deleted_file: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    renamed_file: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    additions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    deletions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


# ── Source files ─────────────────────────────────────────────────────

class SourceFile(Base):
    __tablename__ = "source_files"
    __table_args__ = {"schema": SCHEMA}

    project_id: Mapped[str] = mapped_column(Text, primary_key=True, nullable=False)
    file_path: Mapped[str] = mapped_column(Text, primary_key=True, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


# ── Symbols ──────────────────────────────────────────────────────────

class Symbol(Base):
    __tablename__ = "symbols"
    __table_args__ = (
        Index("symbols_qname_idx", "project_id", "qualified_name"),
        Index("symbols_file_idx", "project_id", "file_path"),
        Index("symbols_kind_idx", "project_id", "kind"),
        # GIN on calls array and trigram indexes are created via raw DDL
        {"schema": SCHEMA},
    )

    project_id: Mapped[str] = mapped_column(Text, primary_key=True, nullable=False)
    symbol_id: Mapped[str] = mapped_column(Text, primary_key=True, nullable=False)
    qualified_name: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False, default="")
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    file_path: Mapped[str] = mapped_column(Text, nullable=False)
    language: Mapped[str] = mapped_column(Text, nullable=False, default="")
    start_line: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    end_line: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    signature: Mapped[str | None] = mapped_column(Text, nullable=True)
    body_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    params: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, default=list
    )
    calls: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, default=list
    )
    imports: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, default=list
    )
    decorators: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, default=list
    )
    complexity_cyclomatic: Mapped[int | None] = mapped_column(Integer, nullable=True)
    complexity_cognitive: Mapped[int | None] = mapped_column(Integer, nullable=True)
    complexity_max_nesting: Mapped[int | None] = mapped_column(Integer, nullable=True)
    complexity_lines: Mapped[int | None] = mapped_column(Integer, nullable=True)
    complexity_params: Mapped[int | None] = mapped_column(Integer, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


# ── Symbol embeddings ────────────────────────────────────────────────

def _make_embedding_col():
    if _VECTOR_AVAILABLE:
        return mapped_column(Vector(EMBED_DIM), nullable=False)
    return mapped_column(Text, nullable=False)  # fallback: store as text


class SymbolEmbedding(Base):
    __tablename__ = "symbol_embeddings"
    __table_args__ = (
        # HNSW index is created via raw DDL in PgStore._apply_schema
        {"schema": SCHEMA},
    )

    project_id: Mapped[str] = mapped_column(Text, primary_key=True, nullable=False)
    symbol_id: Mapped[str] = mapped_column(Text, primary_key=True, nullable=False)
    qualified_name: Mapped[str] = mapped_column(Text, nullable=False)
    file_path: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str | None] = mapped_column(Text, nullable=True)
    embed_text: Mapped[str] = mapped_column(Text, nullable=False)
    embedding = _make_embedding_col()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


# ── Graph edges ──────────────────────────────────────────────────────

class Edge(Base):
    __tablename__ = "edges"
    __table_args__ = (
        Index("edges_source_idx", "project_id", "source_id", "rel_type"),
        Index("edges_target_idx", "project_id", "target_id", "rel_type"),
        Index("edges_rel_idx", "project_id", "rel_type"),
        {"schema": SCHEMA},
    )

    edge_id: Mapped[str] = mapped_column(Text, primary_key=True)
    project_id: Mapped[str] = mapped_column(Text, nullable=False)
    source_id: Mapped[str] = mapped_column(Text, nullable=False)
    target_id: Mapped[str] = mapped_column(Text, nullable=False)
    rel_type: Mapped[str] = mapped_column(Text, nullable=False)
    valid_from: Mapped[str | None] = mapped_column(Text, nullable=True)
    valid_to: Mapped[str | None] = mapped_column(Text, nullable=True)
    properties: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)


# ── Snapshots ────────────────────────────────────────────────────────

class Snapshot(Base):
    __tablename__ = "snapshots"
    __table_args__ = (
        Index("snapshots_date_idx", "project_id", "timestamp_val"),
        {"schema": SCHEMA},
    )

    project_id: Mapped[str] = mapped_column(Text, primary_key=True, nullable=False)
    commit_sha: Mapped[str] = mapped_column(Text, primary_key=True, nullable=False)
    timestamp_val: Mapped[str | None] = mapped_column(Text, nullable=True)
    author_email: Mapped[str | None] = mapped_column(Text, nullable=True)
    author_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    classification: Mapped[str | None] = mapped_column(Text, nullable=True)
    parent_sha: Mapped[str | None] = mapped_column(Text, nullable=True)


# ── Pull requests ────────────────────────────────────────────────────

class PullRequest(Base):
    __tablename__ = "pull_requests"
    __table_args__ = (
        Index("pr_author_idx", "project_id", "author_login"),
        Index("pr_state_idx", "project_id", "state"),
        {"schema": SCHEMA},
    )

    project_id: Mapped[str] = mapped_column(Text, primary_key=True, nullable=False)
    pr_number: Mapped[int] = mapped_column(Integer, primary_key=True, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False, default="")
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    state: Mapped[str] = mapped_column(Text, nullable=False, default="open")  # open|closed|merged
    author_login: Mapped[str | None] = mapped_column(Text, nullable=True)
    author_email: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    merged_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    closed_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_merged: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    labels: Mapped[list] = mapped_column(ARRAY(Text), nullable=False, default=list)
    comments_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    changed_files: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    additions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    deletions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    html_url: Mapped[str | None] = mapped_column(Text, nullable=True)


class PullRequestReview(Base):
    __tablename__ = "pr_reviews"
    __table_args__ = (
        Index("pr_review_reviewer_idx", "project_id", "reviewer_login"),
        {"schema": SCHEMA},
    )

    project_id: Mapped[str] = mapped_column(Text, primary_key=True, nullable=False)
    pr_number: Mapped[int] = mapped_column(Integer, primary_key=True, nullable=False)
    reviewer_login: Mapped[str] = mapped_column(Text, primary_key=True, nullable=False)
    state: Mapped[str] = mapped_column(Text, nullable=False, default="COMMENTED")
    submitted_at: Mapped[str | None] = mapped_column(Text, nullable=True)


# ── Issues ────────────────────────────────────────────────────────────

class Issue(Base):
    __tablename__ = "issues"
    __table_args__ = (
        Index("issue_author_idx", "project_id", "author_login"),
        Index("issue_state_idx", "project_id", "state"),
        {"schema": SCHEMA},
    )

    project_id: Mapped[str] = mapped_column(Text, primary_key=True, nullable=False)
    issue_number: Mapped[int] = mapped_column(Integer, primary_key=True, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False, default="")
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    state: Mapped[str] = mapped_column(Text, nullable=False, default="open")
    author_login: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    closed_at: Mapped[str | None] = mapped_column(Text, nullable=True)
    labels: Mapped[list] = mapped_column(ARRAY(Text), nullable=False, default=list)
    comments_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    html_url: Mapped[str | None] = mapped_column(Text, nullable=True)


# ── File metadata (aggregated at graph-build time) ───────────────────

class FileMetadata(Base):
    """Pre-computed per-file analytics: history, churn, authors, complexity."""
    __tablename__ = "file_metadata"
    __table_args__ = (
        Index("file_metadata_project_idx", "project_id"),
        Index("file_metadata_churn_idx", "project_id", "total_commits"),
        {"schema": SCHEMA},
    )

    project_id: Mapped[str] = mapped_column(Text, primary_key=True, nullable=False)
    file_path: Mapped[str] = mapped_column(Text, primary_key=True, nullable=False)
    language: Mapped[str | None] = mapped_column(Text, nullable=True)
    line_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # History
    first_commit_sha: Mapped[str | None] = mapped_column(Text, nullable=True)
    first_author_email: Mapped[str | None] = mapped_column(Text, nullable=True)
    first_date: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_commit_sha: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_author_email: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_date: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Churn
    total_commits: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_additions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_deletions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    unique_authors: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Authors JSON: [{email, name, commits, additions, deletions}]
    authors_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=list)
    # Complexity (aggregated from symbols)
    symbol_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    complexity_avg: Mapped[float | None] = mapped_column(nullable=True)
    complexity_max: Mapped[int | None] = mapped_column(Integer, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


__all__ = [
    "Base",
    "SCHEMA",
    "EMBED_DIM",
    "Repo",
    "Commit",
    "Member",
    "CommitFile",
    "SourceFile",
    "Symbol",
    "SymbolEmbedding",
    "Edge",
    "Snapshot",
    "FileMetadata",
    "PullRequest",
    "PullRequestReview",
    "Issue",
]
