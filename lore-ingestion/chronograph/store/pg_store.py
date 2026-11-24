"""
chronograph/store/pg_store.py
─────────────────────────────
PostgreSQL + pgvector implementation of the Store Protocol.

Stack:
  - SQLAlchemy 2.x ORM + Core for query construction
  - psycopg2 sync driver
  - pgvector's SQLAlchemy integration for the embedding column
  - HNSW index for cosine ANN
  - GIN index on symbols.calls array for fast trace_function lookups
  - pg_trgm indexes for fast ILIKE symbol / commit search

Configuration:
  The DSN is read from DATABASE_URL (preferred) or POSTGRES_URI.
  The make_store() factory in store_factory.py constructs this store.
"""

from __future__ import annotations

import json
import logging
import math
import re
from contextlib import contextmanager
from typing import Generator

import sqlalchemy as sa
from sqlalchemy import create_engine, event, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, sessionmaker

from chronograph.store.models import (
    Base,
    Commit,
    CommitFile,
    Edge,
    FileMetadata,
    Issue,
    Member,
    PullRequest,
    PullRequestReview,
    Repo,
    SCHEMA,
    Snapshot,
    SourceFile,
    Symbol,
    SymbolEmbedding,
    _VECTOR_AVAILABLE,
)
from common.hashing import short_hash

log = logging.getLogger("chronograph.pg_store")

# Extra DDL that SQLAlchemy ORM can't express (GIN / HNSW / trigram indexes).
# Extensions are created in _apply_schema Phase 1 before create_all().
_EXTRA_DDL = """
CREATE INDEX IF NOT EXISTS symbols_qname_trgm
    ON chronograph.symbols USING gin(qualified_name gin_trgm_ops);

CREATE INDEX IF NOT EXISTS symbols_name_trgm
    ON chronograph.symbols USING gin(name gin_trgm_ops);

CREATE INDEX IF NOT EXISTS symbols_complex_idx
    ON chronograph.symbols(project_id, complexity_cyclomatic DESC NULLS LAST)
    WHERE complexity_cyclomatic IS NOT NULL;

CREATE INDEX IF NOT EXISTS commits_msg_trgm
    ON chronograph.commits USING gin(message gin_trgm_ops);

-- pgvector HNSW max is 2000 dims; NeMo uses 2048-d so we skip HNSW.
-- search_embeddings uses <=> operator (sequential scan — fine for <100k symbols).
-- Upgrade path: use halfvec or pgvecto.rs when the corpus grows large.
"""


class PgStore:
    """PostgreSQL-backed store for all Chronograph data."""

    def __init__(self, uri: str):
        self.uri = uri
        self._engine = None
        self._Session = None

    # ── Lifecycle ────────────────────────────────────────────────────

    def connect(self) -> bool:
        try:
            # Phase 0: bootstrap a raw psycopg2 connection to create extensions
            # BEFORE the pool is initialised with register_vector listeners.
            # register_vector() errors if the vector extension doesn't exist yet.
            import psycopg2 as _pg2

            _boot = _pg2.connect(self.uri)
            _boot.autocommit = True
            with _boot.cursor() as _cur:
                _cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
                _cur.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
                _cur.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")
            _boot.close()

            # Phase 1: create the SQLAlchemy engine; register pgvector codec on
            # every connection the pool hands out.
            self._engine = create_engine(
                self.uri,
                pool_size=5,
                max_overflow=10,
                pool_pre_ping=True,
                echo=False,
            )
            if _VECTOR_AVAILABLE:
                from pgvector.psycopg2 import register_vector

                @event.listens_for(self._engine, "connect")
                def _on_connect(dbapi_conn, _):
                    register_vector(dbapi_conn)

            self._Session = sessionmaker(bind=self._engine, expire_on_commit=False)

            # Phase 2: create ORM tables + extra indexes
            self._apply_schema()

            log.info("[pg] Connected to %s", self.uri.split("@")[-1])
            return True
        except Exception as exc:
            log.error("[pg] Connection failed: %s", exc)
            self._engine = None
            self._Session = None
            return False

    def disconnect(self) -> None:
        if self._engine:
            self._engine.dispose()
            self._engine = None
            self._Session = None

    def _apply_schema(self) -> None:
        # Extensions and schema already exist (created in Phase 0 of connect()).
        # Phase 2: ORM table DDL
        Base.metadata.create_all(self._engine)
        # Phase 3: extra indexes (GIN, HNSW, trigram) that ORM can't express
        with self._engine.begin() as conn:
            for stmt in _EXTRA_DDL.strip().split(";"):
                stmt = stmt.strip()
                if stmt:
                    try:
                        conn.execute(text(stmt))
                    except Exception as exc:
                        log.debug("[pg] DDL warning (likely already exists): %s", exc)

    @contextmanager
    def _session(self) -> Generator[Session, None, None]:
        session: Session = self._Session()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    # ── Projects ─────────────────────────────────────────────────────

    def upsert_project(self, project_id: str, data: dict) -> None:
        stmt = (
            pg_insert(Repo)
            .values(project_id=project_id, data=data)
            .on_conflict_do_update(
                index_elements=["project_id"],
                set_={"data": data, "updated_at": sa.func.now()},
            )
        )
        with self._session() as s:
            s.execute(stmt)

    def get_project(self, project_id: str) -> dict | None:
        with self._session() as s:
            row = s.get(Repo, project_id)
            if row is None:
                return None
            return {"project_id": row.project_id, **row.data}

    # ── Commits ──────────────────────────────────────────────────────

    def upsert_commits(self, project_id: str, commits: list[dict]) -> int:
        if not commits:
            return 0
        seen_shas: set[str] = set()
        rows = []
        for c in commits:
            sha = c["id"]
            if sha in seen_shas:
                continue
            seen_shas.add(sha)
            stats = c.get("stats") or {}
            rows.append({
                "project_id": project_id,
                "sha": c["id"],
                "short_id": c.get("short_id", c["id"][:8]),
                "title": c.get("title", ""),
                "message": c.get("message", ""),
                "author_name": c.get("author_name", ""),
                "author_email": c.get("author_email", ""),
                "authored_date": c.get("authored_date"),
                "committed_date": c.get("committed_date"),
                "parent_ids": c.get("parent_ids", []),
                "additions": stats.get("additions", 0) if isinstance(stats, dict) else 0,
                "deletions": stats.get("deletions", 0) if isinstance(stats, dict) else 0,
                "web_url": c.get("web_url"),
            })
        stmt = (
            pg_insert(Commit)
            .values(rows)
            .on_conflict_do_update(
                index_elements=["project_id", "sha"],
                set_={
                    "title": sa.literal_column("EXCLUDED.title"),
                    "message": sa.literal_column("EXCLUDED.message"),
                    "additions": sa.literal_column("EXCLUDED.additions"),
                    "deletions": sa.literal_column("EXCLUDED.deletions"),
                },
            )
        )
        with self._session() as s:
            result = s.execute(stmt)
            return result.rowcount

    def get_commits(self, project_id: str, limit: int = 0, skip: int = 0) -> list[dict]:
        with self._session() as s:
            q = (
                s.query(Commit)
                .filter(Commit.project_id == project_id)
                .order_by(Commit.authored_date.desc().nullslast())
            )
            if skip:
                q = q.offset(skip)
            if limit:
                q = q.limit(limit)
            return [_commit_to_dict(r) for r in q.all()]

    def get_commit_count(self, project_id: str) -> int:
        with self._session() as s:
            return s.query(Commit).filter(Commit.project_id == project_id).count()

    def get_commit_authors(self, project_id: str) -> list[dict]:
        sql = text("""
            SELECT
                author_email                AS email,
                MIN(author_name)            AS name,
                COUNT(*)                    AS commit_count,
                COALESCE(SUM(additions), 0) AS additions,
                COALESCE(SUM(deletions), 0) AS deletions
            FROM chronograph.commits
            WHERE project_id = :pid
            GROUP BY author_email
            ORDER BY commit_count DESC
        """)
        with self._session() as s:
            rows = s.execute(sql, {"pid": project_id}).mappings().all()
            return [dict(r) for r in rows]

    def get_activity_timeline(self, project_id: str, bucket: str = "month") -> list[dict]:
        fmt = {"day": "YYYY-MM-DD", "week": "IYYY-IW"}.get(bucket, "YYYY-MM")
        sql = text("""
            SELECT
                to_char(authored_date::timestamptz, :fmt) AS period,
                COUNT(*)                                   AS commit_count,
                COUNT(DISTINCT author_email)               AS author_count
            FROM chronograph.commits
            WHERE project_id = :pid
              AND authored_date IS NOT NULL
            GROUP BY period
            ORDER BY period
        """)
        with self._session() as s:
            rows = s.execute(sql, {"pid": project_id, "fmt": fmt}).mappings().all()
            return [dict(r) for r in rows]

    # ── Members ──────────────────────────────────────────────────────

    def upsert_members(self, project_id: str, members: list[dict]) -> int:
        if not members:
            return 0
        rows = [
            {
                "project_id": project_id,
                "username": m.get("username", ""),
                "data": {**m, "project_id": project_id},
            }
            for m in members
        ]
        stmt = (
            pg_insert(Member)
            .values(rows)
            .on_conflict_do_update(
                index_elements=["project_id", "username"],
                set_={"data": sa.literal_column("EXCLUDED.data")},
            )
        )
        with self._session() as s:
            result = s.execute(stmt)
            return result.rowcount

    def get_members(self, project_id: str) -> list[dict]:
        with self._session() as s:
            rows = s.query(Member).filter(Member.project_id == project_id).all()
            return [row.data for row in rows]

    # ── Commit files ─────────────────────────────────────────────────

    def upsert_commit_files(
        self, project_id: str, commit_files: dict[str, list[dict]]
    ) -> int:
        if not commit_files:
            return 0
        seen: set[tuple] = set()
        rows = []
        for sha, files in commit_files.items():
            for f in files:
                path = f.get("path", f.get("file_path", ""))
                key = (project_id, sha, path)
                if key in seen:
                    continue
                seen.add(key)
                rows.append({
                    "project_id": project_id,
                    "commit_sha": sha,
                    "file_path": path,
                    "old_path": f.get("old_path"),
                    "new_file": bool(f.get("new_file")),
                    "deleted_file": bool(f.get("deleted_file")),
                    "renamed_file": bool(f.get("renamed_file")),
                    "additions": int(f.get("additions", 0) or 0),
                    "deletions": int(f.get("deletions", 0) or 0),
                })
        if not rows:
            return 0
        stmt = (
            pg_insert(CommitFile)
            .values(rows)
            .on_conflict_do_update(
                index_elements=["project_id", "commit_sha", "file_path"],
                set_={
                    "additions": sa.literal_column("EXCLUDED.additions"),
                    "deletions": sa.literal_column("EXCLUDED.deletions"),
                    "old_path": sa.literal_column("EXCLUDED.old_path"),
                    "renamed_file": sa.literal_column("EXCLUDED.renamed_file"),
                },
            )
        )
        with self._session() as s:
            result = s.execute(stmt)
            return result.rowcount

    def get_commit_files(self, project_id: str, commit_sha: str) -> list[dict]:
        with self._session() as s:
            rows = (
                s.query(CommitFile)
                .filter(
                    CommitFile.project_id == project_id,
                    CommitFile.commit_sha == commit_sha,
                )
                .all()
            )
            return [_cf_to_dict(r) for r in rows]

    def get_all_commit_files(self, project_id: str) -> dict[str, list[dict]]:
        with self._session() as s:
            rows = (
                s.query(CommitFile)
                .filter(CommitFile.project_id == project_id)
                .all()
            )
            result: dict[str, list[dict]] = {}
            for r in rows:
                result.setdefault(r.commit_sha, []).append(_cf_to_dict(r))
            return result

    def compute_hotspots(self, project_id: str, limit: int = 20) -> list[dict]:
        sql = text("""
            SELECT file_path, COUNT(*) AS change_count
            FROM chronograph.commit_files
            WHERE project_id = :pid
              AND NOT deleted_file
            GROUP BY file_path
            ORDER BY change_count DESC
            LIMIT :lim
        """)
        with self._session() as s:
            rows = s.execute(sql, {"pid": project_id, "lim": limit}).mappings().all()
            return [dict(r) for r in rows]

    def compute_bus_factor(self, project_id: str, min_changes: int = 5) -> list[dict]:
        sql = text("""
            SELECT
                cf.file_path,
                COUNT(*)                       AS total_changes,
                MIN(c.author_email)            AS sole_author,
                1                              AS bus_factor
            FROM chronograph.commit_files cf
            JOIN chronograph.commits c
              ON c.project_id = cf.project_id
             AND c.sha        = cf.commit_sha
            WHERE cf.project_id = :pid
              AND NOT cf.deleted_file
            GROUP BY cf.file_path
            HAVING COUNT(*) >= :min_changes
               AND COUNT(DISTINCT c.author_email) = 1
            ORDER BY total_changes DESC
        """)
        with self._session() as s:
            rows = s.execute(
                sql, {"pid": project_id, "min_changes": min_changes}
            ).mappings().all()
            return [dict(r) for r in rows]

    # ── Source files ─────────────────────────────────────────────────

    def upsert_source_files(self, project_id: str, files: dict[str, str]) -> int:
        if not files:
            return 0
        rows = [
            {
                "project_id": project_id,
                "file_path": path,
                "content": content,
                "byte_size": len(content.encode("utf-8")),
            }
            for path, content in files.items()
        ]
        stmt = (
            pg_insert(SourceFile)
            .values(rows)
            .on_conflict_do_update(
                index_elements=["project_id", "file_path"],
                set_={
                    "content": sa.literal_column("EXCLUDED.content"),
                    "byte_size": sa.literal_column("EXCLUDED.byte_size"),
                    "updated_at": sa.func.now(),
                },
            )
        )
        with self._session() as s:
            result = s.execute(stmt)
            return result.rowcount

    def get_source_file(self, project_id: str, path: str) -> str | None:
        with self._session() as s:
            row = s.get(SourceFile, (project_id, path))
            return row.content if row else None

    def get_source_file_count(self, project_id: str) -> int:
        with self._session() as s:
            return (
                s.query(SourceFile)
                .filter(SourceFile.project_id == project_id)
                .count()
            )

    # ── Symbols ──────────────────────────────────────────────────────

    def upsert_symbols(self, project_id: str, symbols: list[dict]) -> int:
        if not symbols:
            return 0
        # Deduplicate within batch — same (project_id, symbol_id) from multiple files
        # would cause "ON CONFLICT DO UPDATE command cannot affect row a second time"
        seen_ids: set[str] = set()
        rows = []
        for sym in symbols:
            symbol_id = f"{project_id}:{sym['file_path']}:{sym['qualified_name']}"
            if symbol_id in seen_ids:
                continue
            seen_ids.add(symbol_id)
            cx = sym.get("complexity") or {}
            rows.append({
                "project_id": project_id,
                "symbol_id": symbol_id,
                "qualified_name": sym.get("qualified_name", ""),
                "name": sym.get("name", ""),
                "kind": sym.get("kind", ""),
                "file_path": sym.get("file_path", ""),
                "language": sym.get("language", ""),
                "start_line": sym.get("start_line", 0),
                "end_line": sym.get("end_line", 0),
                "signature": sym.get("signature"),
                "body_hash": sym.get("body_hash"),
                "params": (sym.get("params") or [])[:100],
                "calls": [c[:200] for c in (sym.get("calls") or [])][:200],
                "imports": (sym.get("imports") or [])[:100],
                "decorators": sym.get("decorators") or [],
                "complexity_cyclomatic": cx.get("cyclomatic") if cx else None,
                "complexity_cognitive": cx.get("cognitive") if cx else None,
                "complexity_max_nesting": cx.get("max_nesting") if cx else None,
                "complexity_lines": cx.get("lines") if cx else None,
                "complexity_params": cx.get("params") if cx else None,
            })
        stmt = (
            pg_insert(Symbol)
            .values(rows)
            .on_conflict_do_update(
                index_elements=["project_id", "symbol_id"],
                set_={
                    "calls": sa.literal_column("EXCLUDED.calls"),
                    "imports": sa.literal_column("EXCLUDED.imports"),
                    "complexity_cyclomatic": sa.literal_column("EXCLUDED.complexity_cyclomatic"),
                    "complexity_cognitive": sa.literal_column("EXCLUDED.complexity_cognitive"),
                    "complexity_max_nesting": sa.literal_column("EXCLUDED.complexity_max_nesting"),
                    "complexity_lines": sa.literal_column("EXCLUDED.complexity_lines"),
                    "complexity_params": sa.literal_column("EXCLUDED.complexity_params"),
                    "updated_at": sa.func.now(),
                },
            )
        )
        with self._session() as s:
            result = s.execute(stmt)
            return result.rowcount

    def get_symbols(
        self,
        project_id: str,
        file_path: str | None = None,
        kind: str | None = None,
        limit: int = 0,
    ) -> list[dict]:
        with self._session() as s:
            q = s.query(Symbol).filter(Symbol.project_id == project_id)
            if file_path:
                q = q.filter(Symbol.file_path == file_path)
            if kind:
                q = q.filter(Symbol.kind == kind)
            if limit:
                q = q.limit(limit)
            return [_symbol_to_dict(r) for r in q.all()]

    def get_symbol(self, project_id: str, qualified_name: str) -> dict | None:
        with self._session() as s:
            # Exact match first
            row = (
                s.query(Symbol)
                .filter(
                    Symbol.project_id == project_id,
                    Symbol.qualified_name == qualified_name,
                )
                .first()
            )
            if row:
                return _symbol_to_dict(row)
            # Partial (case-insensitive)
            row = (
                s.query(Symbol)
                .filter(
                    Symbol.project_id == project_id,
                    Symbol.qualified_name.ilike(f"%{qualified_name}%"),
                )
                .first()
            )
            return _symbol_to_dict(row) if row else None

    def search_symbols(
        self,
        project_id: str,
        query: str,
        kind: str | None = None,
        limit: int = 20,
    ) -> list[dict]:
        with self._session() as s:
            q = s.query(Symbol).filter(
                Symbol.project_id == project_id,
                sa.or_(
                    Symbol.name.ilike(f"%{query}%"),
                    Symbol.qualified_name.ilike(f"%{query}%"),
                ),
            )
            if kind:
                q = q.filter(Symbol.kind == kind)
            return [_symbol_to_dict(r) for r in q.limit(limit).all()]

    def get_most_complex(
        self,
        project_id: str,
        limit: int = 20,
        exclude_patterns: list[str] | None = None,
    ) -> list[dict]:
        with self._session() as s:
            q = s.query(Symbol).filter(
                Symbol.project_id == project_id,
                Symbol.kind.in_(["function", "method"]),
                Symbol.complexity_cyclomatic > 1,
            )
            if exclude_patterns:
                for pat in exclude_patterns:
                    q = q.filter(~Symbol.file_path.op("~")(pat))
            q = q.order_by(Symbol.complexity_cyclomatic.desc().nullslast()).limit(limit)
            return [_symbol_to_dict(r) for r in q.all()]

    def get_symbol_count(self, project_id: str) -> int:
        with self._session() as s:
            return s.query(Symbol).filter(Symbol.project_id == project_id).count()

    def trace_function(self, project_id: str, function_name: str) -> dict:
        target = self.get_symbol(project_id, function_name)
        if not target:
            return {"error": f"Function '{function_name}' not found"}

        calls_out = target.get("calls", [])

        # Callers — uses the GIN index on the calls array
        sql = text("""
            SELECT qualified_name, file_path, start_line
            FROM chronograph.symbols
            WHERE project_id = :pid
              AND kind IN ('function', 'method')
              AND :name = ANY(calls)
        """)
        with self._session() as s:
            rows = s.execute(
                sql, {"pid": project_id, "name": target["name"]}
            ).mappings().all()
            called_by = [dict(r) for r in rows]

        return {
            "function": target["qualified_name"],
            "file": target["file_path"],
            "line_range": f"{target['start_line']}-{target['end_line']}",
            "signature": target.get("signature", ""),
            "complexity": target.get("complexity"),
            "calls": calls_out,
            "called_by": called_by,
        }

    # ── Embeddings ───────────────────────────────────────────────────

    def upsert_embeddings(self, project_id: str, embeddings: list[dict]) -> int:
        if not embeddings:
            return 0
        seen_ids: set[str] = set()
        rows = []
        for emb in embeddings:
            if emb["symbol_id"] in seen_ids:
                continue
            seen_ids.add(emb["symbol_id"])
            rows.append({
                "project_id": project_id,
                "symbol_id": emb["symbol_id"],
                "qualified_name": emb.get("qualified_name", ""),
                "file_path": emb.get("file_path", ""),
                "kind": emb.get("kind"),
                "embed_text": emb.get("text", "")[:2000],
                "embedding": emb.get("vector", []),
            })
        stmt = (
            pg_insert(SymbolEmbedding)
            .values(rows)
            .on_conflict_do_update(
                index_elements=["project_id", "symbol_id"],
                set_={
                    "embedding": sa.literal_column("EXCLUDED.embedding"),
                    "embed_text": sa.literal_column("EXCLUDED.embed_text"),
                    "updated_at": sa.func.now(),
                },
            )
        )
        with self._session() as s:
            result = s.execute(stmt)
            return result.rowcount

    def get_embedding_count(self, project_id: str) -> int:
        with self._session() as s:
            return (
                s.query(SymbolEmbedding)
                .filter(SymbolEmbedding.project_id == project_id)
                .count()
            )

    def get_all_embeddings(self, project_id: str) -> list[dict]:
        with self._session() as s:
            rows = (
                s.query(SymbolEmbedding)
                .filter(SymbolEmbedding.project_id == project_id)
                .all()
            )
            return [
                {
                    "symbol_id": r.symbol_id,
                    "qualified_name": r.qualified_name,
                    "file_path": r.file_path,
                    "kind": r.kind or "",
                    "text": r.embed_text,
                    "vector": list(r.embedding) if r.embedding is not None else [],
                }
                for r in rows
            ]

    def search_embeddings(
        self,
        project_id: str,
        query_vector: list[float],
        top_k: int = 10,
    ) -> list[dict]:
        if not _VECTOR_AVAILABLE:
            return _python_cosine_search(
                self.get_all_embeddings(project_id), query_vector, top_k
            )
        sql = text("""
            SELECT
                symbol_id,
                qualified_name,
                file_path,
                kind,
                1 - (embedding <=> CAST(:vec AS vector)) AS score
            FROM chronograph.symbol_embeddings
            WHERE project_id = :pid
            ORDER BY embedding <=> CAST(:vec AS vector)
            LIMIT :k
        """)
        with self._session() as s:
            rows = s.execute(
                sql,
                {
                    "pid": project_id,
                    "vec": f"[{','.join(str(x) for x in query_vector)}]",
                    "k": top_k,
                },
            ).mappings().all()
            return [
                {
                    "qualified_name": r["qualified_name"],
                    "file_path": r["file_path"],
                    "kind": r["kind"] or "",
                    "score": round(float(r["score"]), 4),
                }
                for r in rows
            ]

    # ── Graph edges ──────────────────────────────────────────────────

    def create_edge(
        self,
        project_id: str,
        source_id: str,
        target_id: str,
        rel_type: str,
        properties: dict | None = None,
        valid_from: str | None = None,
        valid_to: str | None = None,
    ) -> str:
        raw = f"{source_id}:{target_id}:{rel_type}:{valid_from or ''}"
        edge_id = f"edge:{short_hash(raw, length=16)}"
        stmt = (
            pg_insert(Edge)
            .values(
                edge_id=edge_id,
                project_id=project_id,
                source_id=source_id,
                target_id=target_id,
                rel_type=rel_type,
                valid_from=valid_from,
                valid_to=valid_to,
                properties=properties or {},
            )
            .on_conflict_do_update(
                index_elements=["edge_id"],
                set_={"properties": sa.literal_column("EXCLUDED.properties")},
            )
        )
        with self._session() as s:
            s.execute(stmt)
        return edge_id

    def create_edges_bulk(self, project_id: str, edges: list[dict]) -> int:
        if not edges:
            return 0
        rows = []
        for e in edges:
            raw = f"{e['source_id']}:{e['target_id']}:{e['rel_type']}:{e.get('valid_from', '')}"
            edge_id = f"edge:{short_hash(raw, length=16)}"
            rows.append({
                "edge_id": edge_id,
                "project_id": project_id,
                "source_id": e["source_id"],
                "target_id": e["target_id"],
                "rel_type": e["rel_type"],
                "valid_from": e.get("valid_from"),
                "valid_to": e.get("valid_to"),
                "properties": e.get("properties") or {},
            })
        stmt = (
            pg_insert(Edge)
            .values(rows)
            .on_conflict_do_nothing()
        )
        with self._session() as s:
            result = s.execute(stmt)
            return result.rowcount

    def get_edges(
        self,
        project_id: str,
        source_id: str | None = None,
        target_id: str | None = None,
        rel_type: str | None = None,
    ) -> list[dict]:
        with self._session() as s:
            q = s.query(Edge).filter(Edge.project_id == project_id)
            if source_id:
                q = q.filter(Edge.source_id == source_id)
            if target_id:
                q = q.filter(Edge.target_id == target_id)
            if rel_type:
                q = q.filter(Edge.rel_type == rel_type)
            return [_edge_to_dict(r) for r in q.all()]

    def get_edge_count(self, project_id: str, rel_type: str | None = None) -> int:
        with self._session() as s:
            q = s.query(Edge).filter(Edge.project_id == project_id)
            if rel_type:
                q = q.filter(Edge.rel_type == rel_type)
            return q.count()

    # ── Snapshots ────────────────────────────────────────────────────

    def upsert_snapshot(self, project_id: str, commit_sha: str, data: dict) -> None:
        stmt = (
            pg_insert(Snapshot)
            .values(
                project_id=project_id,
                commit_sha=commit_sha,
                timestamp_val=data.get("timestamp"),
                author_email=data.get("author_email"),
                author_name=data.get("author_name"),
                message=data.get("message"),
                classification=data.get("classification"),
                parent_sha=data.get("parent_sha"),
            )
            .on_conflict_do_update(
                index_elements=["project_id", "commit_sha"],
                set_={"classification": sa.literal_column("EXCLUDED.classification")},
            )
        )
        with self._session() as s:
            s.execute(stmt)

    def upsert_snapshots_bulk(self, project_id: str, snapshots: list[dict]) -> int:
        if not snapshots:
            return 0
        rows = [
            {
                "project_id": project_id,
                "commit_sha": sn["commit_sha"],
                "timestamp_val": sn.get("timestamp"),
                "author_email": sn.get("author_email"),
                "author_name": sn.get("author_name"),
                "message": sn.get("message"),
                "classification": sn.get("classification"),
                "parent_sha": sn.get("parent_sha"),
            }
            for sn in snapshots
        ]
        stmt = (
            pg_insert(Snapshot)
            .values(rows)
            .on_conflict_do_update(
                index_elements=["project_id", "commit_sha"],
                set_={"classification": sa.literal_column("EXCLUDED.classification")},
            )
        )
        with self._session() as s:
            result = s.execute(stmt)
            return result.rowcount

    def get_snapshot_count(self, project_id: str) -> int:
        with self._session() as s:
            return (
                s.query(Snapshot)
                .filter(Snapshot.project_id == project_id)
                .count()
            )

    # ── Fast per-developer queries (used by persona / profile tools) ─────
    # All filter at SQL level — no Python-side filtering on full-project data.

    def get_commits_by_emails(
        self, project_id: str, emails: list[str], limit: int = 0
    ) -> list[dict]:
        """Commits authored by any of the given emails. SQL-filtered."""
        sql = text("""
            SELECT sha, short_id, title, message, author_name, author_email,
                   authored_date, parent_ids, additions, deletions, web_url
            FROM chronograph.commits
            WHERE project_id = :pid AND author_email = ANY(:emails)
            ORDER BY authored_date DESC
            """ + (f"LIMIT {int(limit)}" if limit else ""))  # int() — LIMIT can't be a bind param
        with self._session() as s:
            rows = s.execute(sql, {"pid": project_id, "emails": emails}).mappings().all()
            return [dict(r) for r in rows]

    def get_file_touch_counts(
        self, project_id: str, emails: list[str]
    ) -> dict[str, int]:
        """Per-file change counts for a developer. Returns {file_path: count}."""
        sql = text("""
            SELECT cf.file_path, COUNT(*) AS cnt
            FROM chronograph.commit_files cf
            JOIN chronograph.commits c
              ON c.project_id = cf.project_id AND c.sha = cf.commit_sha
            WHERE cf.project_id = :pid
              AND c.author_email = ANY(:emails)
              AND NOT cf.deleted_file
            GROUP BY cf.file_path
        """)
        with self._session() as s:
            rows = s.execute(sql, {"pid": project_id, "emails": emails}).mappings().all()
            return {r["file_path"]: r["cnt"] for r in rows}

    def get_total_touches_per_file(self, project_id: str) -> dict[str, int]:
        """Total commit count per file across all authors. SQL-aggregated."""
        sql = text("""
            SELECT file_path, COUNT(*) AS cnt
            FROM chronograph.commit_files
            WHERE project_id = :pid AND NOT deleted_file
            GROUP BY file_path
        """)
        with self._session() as s:
            rows = s.execute(sql, {"pid": project_id}).mappings().all()
            return {r["file_path"]: r["cnt"] for r in rows}

    def get_authored_entity_ids(
        self, project_id: str, emails: list[str]
    ) -> set[str]:
        """Source IDs from AUTHORED_BY edges for the given developer emails."""
        dev_ids = [f"dev:{e}" for e in emails]
        sql = text("""
            SELECT source_id FROM chronograph.edges
            WHERE project_id = :pid
              AND rel_type = 'AUTHORED_BY'
              AND target_id = ANY(:dev_ids)
        """)
        with self._session() as s:
            rows = s.execute(sql, {"pid": project_id, "dev_ids": dev_ids}).fetchall()
            return {r[0] for r in rows}

    def get_collab_partners(
        self, project_id: str, emails: list[str]
    ) -> list[dict]:
        """COLLABORATES_WITH partners for a developer, SQL-filtered."""
        dev_ids = [f"dev:{e}" for e in emails]
        sql = text("""
            SELECT
                CASE WHEN source_id = ANY(:dev_ids) THEN target_id ELSE source_id END
                    AS partner_id,
                properties->>'shared_files' AS shared_files
            FROM chronograph.edges
            WHERE project_id = :pid
              AND rel_type = 'COLLABORATES_WITH'
              AND (source_id = ANY(:dev_ids) OR target_id = ANY(:dev_ids))
            ORDER BY (properties->>'shared_files')::int DESC
        """)
        with self._session() as s:
            rows = s.execute(sql, {"pid": project_id, "dev_ids": dev_ids}).mappings().all()
            return [
                {"email": r["partner_id"].replace("dev:", ""), "shared_files": int(r["shared_files"] or 0)}
                for r in rows
            ]

    # ── Graph-builder helpers ────────────────────────────────────────

    def get_commit_file_authors(self, project_id: str) -> list[dict]:
        sql = text("""
            SELECT
                cf.file_path,
                c.author_email,
                c.authored_date
            FROM chronograph.commit_files cf
            JOIN chronograph.commits c
              ON c.project_id = cf.project_id
             AND c.sha        = cf.commit_sha
            WHERE cf.project_id = :pid
        """)
        with self._session() as s:
            rows = s.execute(sql, {"pid": project_id}).mappings().all()
            return [dict(r) for r in rows]

    def get_file_authors_by_path(self, project_id: str) -> list[dict]:
        sql = text("""
            SELECT
                cf.file_path,
                array_agg(DISTINCT c.author_email
                          ORDER BY c.author_email) AS authors
            FROM chronograph.commit_files cf
            JOIN chronograph.commits c
              ON c.project_id = cf.project_id
             AND c.sha        = cf.commit_sha
            WHERE cf.project_id = :pid
              AND NOT cf.deleted_file
            GROUP BY cf.file_path
            HAVING COUNT(DISTINCT c.author_email) >= 2
        """)
        with self._session() as s:
            rows = s.execute(sql, {"pid": project_id}).mappings().all()
            return [dict(r) for r in rows]

    def get_developer_file_expertise(
        self, project_id: str, limit: int = 30
    ) -> list[dict]:
        sql = text("""
            SELECT
                c.author_name  AS author,
                c.author_email AS email,
                cf.file_path   AS path,
                COUNT(*)       AS count
            FROM chronograph.commit_files cf
            JOIN chronograph.commits c
              ON c.project_id = cf.project_id
             AND c.sha        = cf.commit_sha
            WHERE cf.project_id = :pid
              AND NOT cf.deleted_file
            GROUP BY c.author_name, c.author_email, cf.file_path
            ORDER BY count DESC
            LIMIT :lim
        """)
        with self._session() as s:
            rows = s.execute(sql, {"pid": project_id, "lim": limit}).mappings().all()
            return [dict(r) for r in rows]

    def search_commits_by_message(
        self, project_id: str, query: str, limit: int = 20
    ) -> list[dict]:
        sql = text("""
            SELECT sha, short_id, message, author_name, author_email, authored_date
            FROM chronograph.commits
            WHERE project_id = :pid
              AND message ILIKE :q
            LIMIT :lim
        """)
        with self._session() as s:
            rows = s.execute(
                sql,
                {"pid": project_id, "q": f"%{query}%", "lim": limit},
            ).mappings().all()
            return [dict(r) for r in rows]

    # ── Pull requests & Issues ────────────────────────────────────────

    def upsert_pull_requests(self, project_id: str, prs: list[dict]) -> int:
        if not prs:
            return 0
        rows = []
        review_rows = []
        for pr in prs:
            rows.append({
                "project_id": project_id,
                "pr_number": pr["pr_number"],
                "title": pr.get("title", ""),
                "body": pr.get("body"),
                "state": pr.get("state", "open"),
                "author_login": pr.get("author_login"),
                "author_email": pr.get("author_email"),
                "created_at": pr.get("created_at"),
                "merged_at": pr.get("merged_at"),
                "closed_at": pr.get("closed_at"),
                "is_merged": bool(pr.get("is_merged")),
                "labels": pr.get("labels") or [],
                "comments_count": pr.get("comments_count", 0),
                "changed_files": pr.get("changed_files", 0),
                "additions": pr.get("additions", 0),
                "deletions": pr.get("deletions", 0),
                "html_url": pr.get("html_url"),
            })
            for rv in pr.get("reviews") or []:
                if rv.get("reviewer_login"):
                    review_rows.append({
                        "project_id": project_id,
                        "pr_number": pr["pr_number"],
                        "reviewer_login": rv["reviewer_login"],
                        "state": rv.get("state", "COMMENTED"),
                        "submitted_at": rv.get("submitted_at"),
                    })

        stmt = (
            pg_insert(PullRequest).values(rows)
            .on_conflict_do_update(
                index_elements=["project_id", "pr_number"],
                set_={
                    "state": sa.literal_column("EXCLUDED.state"),
                    "merged_at": sa.literal_column("EXCLUDED.merged_at"),
                    "closed_at": sa.literal_column("EXCLUDED.closed_at"),
                    "is_merged": sa.literal_column("EXCLUDED.is_merged"),
                    "comments_count": sa.literal_column("EXCLUDED.comments_count"),
                },
            )
        )
        with self._session() as s:
            result = s.execute(stmt)
            if review_rows:
                seen = set()
                uniq_reviews = []
                for r in review_rows:
                    k = (r["project_id"], r["pr_number"], r["reviewer_login"])
                    if k not in seen:
                        seen.add(k)
                        uniq_reviews.append(r)
                s.execute(
                    pg_insert(PullRequestReview).values(uniq_reviews)
                    .on_conflict_do_nothing()
                )
            return result.rowcount

    def upsert_issues(self, project_id: str, issues: list[dict]) -> int:
        if not issues:
            return 0
        rows = [{
            "project_id": project_id,
            "issue_number": i["issue_number"],
            "title": i.get("title", ""),
            "body": i.get("body"),
            "state": i.get("state", "open"),
            "author_login": i.get("author_login"),
            "created_at": i.get("created_at"),
            "closed_at": i.get("closed_at"),
            "labels": i.get("labels") or [],
            "comments_count": i.get("comments_count", 0),
            "html_url": i.get("html_url"),
        } for i in issues]
        stmt = (
            pg_insert(Issue).values(rows)
            .on_conflict_do_update(
                index_elements=["project_id", "issue_number"],
                set_={
                    "state": sa.literal_column("EXCLUDED.state"),
                    "closed_at": sa.literal_column("EXCLUDED.closed_at"),
                    "comments_count": sa.literal_column("EXCLUDED.comments_count"),
                },
            )
        )
        with self._session() as s:
            result = s.execute(stmt)
            return result.rowcount

    def get_developer_pr_activity(self, project_id: str, logins: list[str]) -> dict:
        """PRs authored + reviews given for a set of GitHub logins."""
        sql_authored = text("""
            SELECT pr_number, title, state, is_merged, created_at, merged_at,
                   changed_files, additions, deletions, labels, comments_count, html_url
            FROM chronograph.pull_requests
            WHERE project_id = :pid AND author_login = ANY(:logins)
            ORDER BY created_at DESC
        """)
        sql_reviewed = text("""
            SELECT r.pr_number, r.state AS review_state, r.submitted_at,
                   p.title, p.author_login AS pr_author, p.is_merged
            FROM chronograph.pr_reviews r
            JOIN chronograph.pull_requests p
              ON p.project_id = r.project_id AND p.pr_number = r.pr_number
            WHERE r.project_id = :pid AND r.reviewer_login = ANY(:logins)
            ORDER BY r.submitted_at DESC
        """)
        with self._session() as s:
            authored = [dict(r) for r in s.execute(sql_authored, {"pid": project_id, "logins": logins}).mappings()]
            reviewed = [dict(r) for r in s.execute(sql_reviewed, {"pid": project_id, "logins": logins}).mappings()]
        return {"authored": authored, "reviewed": reviewed}

    def get_developer_issue_activity(self, project_id: str, logins: list[str]) -> list[dict]:
        """Issues opened by a set of GitHub logins."""
        sql = text("""
            SELECT issue_number, title, state, created_at, closed_at, labels, comments_count, html_url
            FROM chronograph.issues
            WHERE project_id = :pid AND author_login = ANY(:logins)
            ORDER BY created_at DESC
        """)
        with self._session() as s:
            return [dict(r) for r in s.execute(sql, {"pid": project_id, "logins": logins}).mappings()]

    # ── File metadata ────────────────────────────────────────────────

    def build_file_metadata(self, project_id: str) -> int:
        """
        Compute and upsert per-file analytics from commit_files × commits × symbols.
        Call at end of each ingestion cycle (graph-build step).
        Returns number of files processed.
        """
        sql = text("""
            WITH file_commits AS (
                SELECT
                    cf.file_path,
                    c.sha,
                    c.author_email,
                    c.author_name,
                    c.authored_date,
                    cf.additions,
                    cf.deletions,
                    c.authored_date AS sort_date
                FROM chronograph.commit_files cf
                JOIN chronograph.commits c
                  ON c.project_id = cf.project_id AND c.sha = cf.commit_sha
                WHERE cf.project_id = :pid
                  AND NOT cf.deleted_file
            ),
            file_stats AS (
                SELECT
                    file_path,
                    COUNT(*)                       AS total_commits,
                    COUNT(DISTINCT author_email)   AS unique_authors,
                    COALESCE(SUM(additions), 0)    AS total_additions,
                    COALESCE(SUM(deletions), 0)    AS total_deletions,
                    MIN(sort_date)                 AS first_date,
                    MAX(sort_date)                 AS last_date
                FROM file_commits
                GROUP BY file_path
            ),
            first_commits AS (
                SELECT DISTINCT ON (file_path)
                    file_path, sha AS first_sha, author_email AS first_author
                FROM file_commits ORDER BY file_path, sort_date ASC
            ),
            last_commits AS (
                SELECT DISTINCT ON (file_path)
                    file_path, sha AS last_sha, author_email AS last_author
                FROM file_commits ORDER BY file_path, sort_date DESC
            ),
            author_per_file AS (
                SELECT
                    file_path,
                    author_email,
                    MIN(author_name)        AS author_name,
                    COUNT(*)                AS commits,
                    SUM(additions)          AS additions,
                    SUM(deletions)          AS deletions
                FROM file_commits
                GROUP BY file_path, author_email
            ),
            author_agg AS (
                SELECT
                    file_path,
                    jsonb_agg(jsonb_build_object(
                        'email', author_email,
                        'name',  author_name,
                        'commits', commits,
                        'additions', COALESCE(additions,0),
                        'deletions', COALESCE(deletions,0)
                    ) ORDER BY commits DESC) AS authors_json
                FROM author_per_file
                GROUP BY file_path
            ),
            sym_stats AS (
                SELECT
                    file_path,
                    COUNT(*)                                          AS symbol_count,
                    AVG(complexity_cyclomatic)                        AS complexity_avg,
                    MAX(complexity_cyclomatic)                        AS complexity_max
                FROM chronograph.symbols
                WHERE project_id = :pid
                GROUP BY file_path
            ),
            src AS (
                SELECT
                    sf.file_path,
                    MIN(sy.language) AS language,
                    MAX(sf.byte_size) AS byte_size,
                    array_length(regexp_split_to_array(MAX(sf.content), E'\\n'), 1) AS line_count
                FROM chronograph.source_files sf
                LEFT JOIN chronograph.symbols sy
                  ON sy.project_id = sf.project_id AND sy.file_path = sf.file_path
                WHERE sf.project_id = :pid
                GROUP BY sf.file_path
            )
            INSERT INTO chronograph.file_metadata (
                project_id, file_path, language, line_count, byte_size,
                first_commit_sha, first_author_email, first_date,
                last_commit_sha,  last_author_email,  last_date,
                total_commits, total_additions, total_deletions, unique_authors,
                authors_json, symbol_count, complexity_avg, complexity_max, updated_at
            )
            SELECT
                :pid,
                fs.file_path,
                src.language,
                COALESCE(src.line_count, 0),
                COALESCE(src.byte_size, 0),
                fc.first_sha,  fc.first_author, fs.first_date,
                lc.last_sha,   lc.last_author,  fs.last_date,
                fs.total_commits, fs.total_additions, fs.total_deletions, fs.unique_authors,
                COALESCE(aa.authors_json, '[]'::jsonb),
                COALESCE(ss.symbol_count, 0),
                ss.complexity_avg,
                ss.complexity_max,
                now()
            FROM file_stats fs
            LEFT JOIN first_commits fc ON fc.file_path = fs.file_path
            LEFT JOIN last_commits  lc ON lc.file_path = fs.file_path
            LEFT JOIN author_agg    aa ON aa.file_path = fs.file_path
            LEFT JOIN sym_stats     ss ON ss.file_path = fs.file_path
            LEFT JOIN src              ON src.file_path = fs.file_path
            ON CONFLICT (project_id, file_path) DO UPDATE SET
                language          = EXCLUDED.language,
                line_count        = EXCLUDED.line_count,
                byte_size         = EXCLUDED.byte_size,
                first_commit_sha  = EXCLUDED.first_commit_sha,
                first_author_email = EXCLUDED.first_author_email,
                first_date        = EXCLUDED.first_date,
                last_commit_sha   = EXCLUDED.last_commit_sha,
                last_author_email = EXCLUDED.last_author_email,
                last_date         = EXCLUDED.last_date,
                total_commits     = EXCLUDED.total_commits,
                total_additions   = EXCLUDED.total_additions,
                total_deletions   = EXCLUDED.total_deletions,
                unique_authors    = EXCLUDED.unique_authors,
                authors_json      = EXCLUDED.authors_json,
                symbol_count      = EXCLUDED.symbol_count,
                complexity_avg    = EXCLUDED.complexity_avg,
                complexity_max    = EXCLUDED.complexity_max,
                updated_at        = now()
        """)
        with self._session() as s:
            result = s.execute(sql, {"pid": project_id})
            return result.rowcount

    def get_file_metadata(self, project_id: str, file_path: str) -> dict | None:
        with self._session() as s:
            row = s.get(FileMetadata, (project_id, file_path))
            if not row:
                # partial match
                from sqlalchemy import text as _t
                rows = s.execute(
                    _t("SELECT * FROM chronograph.file_metadata WHERE project_id=:p AND file_path ILIKE :f LIMIT 1"),
                    {"p": project_id, "f": f"%{file_path}%"}
                ).mappings().all()
                if not rows:
                    return None
                return dict(rows[0])
            return {c.key: getattr(row, c.key) for c in row.__table__.columns}

    def get_files_by_churn(self, project_id: str, limit: int = 20) -> list[dict]:
        """Files ranked by total commit count (churn)."""
        sql = text("""
            SELECT file_path, total_commits, total_additions, total_deletions,
                   unique_authors, complexity_max, last_date, last_author_email
            FROM chronograph.file_metadata
            WHERE project_id = :pid
            ORDER BY total_commits DESC LIMIT :lim
        """)
        with self._session() as s:
            rows = s.execute(sql, {"pid": project_id, "lim": limit}).mappings().all()
            return [dict(r) for r in rows]

    # ── Stats ────────────────────────────────────────────────────────

    def get_stats(self, project_id: str) -> dict:
        if self._engine is None:
            return {"connected": False}
        return {
            "connected": True,
            "project_id": project_id,
            "commits": self.get_commit_count(project_id),
            "members": len(self.get_members(project_id)),
            "source_files": self.get_source_file_count(project_id),
            "symbols": self.get_symbol_count(project_id),
            "embeddings": self.get_embedding_count(project_id),
            "snapshots": self.get_snapshot_count(project_id),
            "edges": {
                "total": self.get_edge_count(project_id),
                "CALLS": self.get_edge_count(project_id, "CALLS"),
                "CONTAINS": self.get_edge_count(project_id, "CONTAINS"),
                "AUTHORED_BY": self.get_edge_count(project_id, "AUTHORED_BY"),
                "IMPORTS": self.get_edge_count(project_id, "IMPORTS"),
                "COLLABORATES_WITH": self.get_edge_count(project_id, "COLLABORATES_WITH"),
            },
        }


# ── Private helpers ──────────────────────────────────────────────────

def _commit_to_dict(row: Commit) -> dict:
    return {
        "project_id": row.project_id,
        "sha": row.sha,
        "id": row.sha,  # downstream consumers read the commit's 'id' as its sha
        "short_id": row.short_id,
        "title": row.title,
        "message": row.message,
        "author_name": row.author_name,
        "author_email": row.author_email,
        "authored_date": row.authored_date,
        "committed_date": row.committed_date,
        "parent_ids": row.parent_ids or [],
        "stats": {"additions": row.additions, "deletions": row.deletions},
        "web_url": row.web_url,
        "classification": row.classification,
    }


def _cf_to_dict(row: CommitFile) -> dict:
    return {
        "path": row.file_path,
        "old_path": row.old_path or "",
        "new_file": row.new_file,
        "deleted_file": row.deleted_file,
        "renamed_file": row.renamed_file,
    }


def _symbol_to_dict(row: Symbol) -> dict:
    cx = None
    if row.complexity_cyclomatic is not None:
        cx = {
            "cyclomatic": row.complexity_cyclomatic,
            "cognitive": row.complexity_cognitive,
            "max_nesting": row.complexity_max_nesting,
            "lines": row.complexity_lines,
            "params": row.complexity_params,
        }
    return {
        "project_id": row.project_id,
        "symbol_id": row.symbol_id,
        "qualified_name": row.qualified_name,
        "name": row.name,
        "kind": row.kind,
        "file_path": row.file_path,
        "language": row.language,
        "start_line": row.start_line,
        "end_line": row.end_line,
        "signature": row.signature,
        "body_hash": row.body_hash,
        "params": row.params or [],
        "calls": row.calls or [],
        "imports": row.imports or [],
        "decorators": row.decorators or [],
        "complexity": cx,
    }


def _edge_to_dict(row: Edge) -> dict:
    return {
        "edge_id": row.edge_id,
        "project_id": row.project_id,
        "source_id": row.source_id,
        "target_id": row.target_id,
        "rel_type": row.rel_type,
        "valid_from": row.valid_from,
        "valid_to": row.valid_to,
        "properties": row.properties or {},
    }


def _python_cosine_search(
    embeddings: list[dict], query_vector: list[float], top_k: int
) -> list[dict]:
    """Fallback in-Python cosine search when pgvector extension is absent."""
    scored = []
    for emb in embeddings:
        vec = emb.get("vector", [])
        if not vec:
            continue
        dot = sum(x * y for x, y in zip(query_vector, vec))
        na = math.sqrt(sum(x * x for x in query_vector))
        nb = math.sqrt(sum(x * x for x in vec))
        sim = dot / (na * nb) if na and nb else 0.0
        scored.append({
            "qualified_name": emb["qualified_name"],
            "file_path": emb["file_path"],
            "kind": emb.get("kind", ""),
            "score": round(sim, 4),
        })
    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:top_k]
