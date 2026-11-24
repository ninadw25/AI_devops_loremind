-- chronograph/store/schema.sql
-- PostgreSQL 16 + pgvector schema for the Chronograph code-intelligence store.
-- Run via: psql -U lore -d lore_chronograph -f schema.sql
-- Applied automatically by PgStore.connect() on first connection.

CREATE SCHEMA IF NOT EXISTS chronograph;

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- ── Projects / repos ────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS chronograph.repos (
    project_id  TEXT        NOT NULL PRIMARY KEY,
    data        JSONB       NOT NULL DEFAULT '{}',
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ── Commits ─────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS chronograph.commits (
    project_id     TEXT   NOT NULL,
    sha            TEXT   NOT NULL,
    short_id       TEXT   NOT NULL DEFAULT '',
    title          TEXT   NOT NULL DEFAULT '',
    message        TEXT   NOT NULL DEFAULT '',
    author_name    TEXT   NOT NULL DEFAULT '',
    author_email   TEXT   NOT NULL DEFAULT '',
    authored_date  TEXT,
    committed_date TEXT,
    parent_ids     TEXT[]      NOT NULL DEFAULT '{}',
    additions      INT         NOT NULL DEFAULT 0,
    deletions      INT         NOT NULL DEFAULT 0,
    web_url        TEXT,
    classification TEXT,
    PRIMARY KEY (project_id, sha)
);
CREATE INDEX IF NOT EXISTS commits_author_idx ON chronograph.commits(project_id, author_email);
CREATE INDEX IF NOT EXISTS commits_date_idx   ON chronograph.commits(project_id, authored_date DESC NULLS LAST);
CREATE INDEX IF NOT EXISTS commits_msg_trgm   ON chronograph.commits USING gin(message gin_trgm_ops);

-- ── Members ─────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS chronograph.members (
    project_id  TEXT  NOT NULL,
    username    TEXT  NOT NULL,
    data        JSONB NOT NULL DEFAULT '{}',
    PRIMARY KEY (project_id, username)
);

-- ── Commit files (normalised — one row per file per commit) ─────────
-- MongoStore stored {commit_sha, files: [{path,...}]}; here one row per file.
CREATE TABLE IF NOT EXISTS chronograph.commit_files (
    project_id   TEXT    NOT NULL,
    commit_sha   TEXT    NOT NULL,
    file_path    TEXT    NOT NULL,
    old_path     TEXT,
    new_file     BOOLEAN NOT NULL DEFAULT false,
    deleted_file BOOLEAN NOT NULL DEFAULT false,
    renamed_file BOOLEAN NOT NULL DEFAULT false,
    PRIMARY KEY (project_id, commit_sha, file_path)
);
CREATE INDEX IF NOT EXISTS commit_files_path_idx   ON chronograph.commit_files(project_id, file_path) WHERE NOT deleted_file;
CREATE INDEX IF NOT EXISTS commit_files_commit_idx ON chronograph.commit_files(project_id, commit_sha);

-- ── Source files ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS chronograph.source_files (
    project_id  TEXT        NOT NULL,
    file_path   TEXT        NOT NULL,
    content     TEXT        NOT NULL,
    byte_size   INT         NOT NULL DEFAULT 0,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (project_id, file_path)
);

-- ── Symbols ─────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS chronograph.symbols (
    project_id              TEXT        NOT NULL,
    symbol_id               TEXT        NOT NULL,
    qualified_name          TEXT        NOT NULL,
    name                    TEXT        NOT NULL DEFAULT '',
    kind                    TEXT        NOT NULL,
    file_path               TEXT        NOT NULL,
    language                TEXT        NOT NULL DEFAULT '',
    start_line              INT         NOT NULL DEFAULT 0,
    end_line                INT         NOT NULL DEFAULT 0,
    signature               TEXT,
    body_hash               TEXT,
    params                  TEXT[]      NOT NULL DEFAULT '{}',
    calls                   TEXT[]      NOT NULL DEFAULT '{}',
    imports                 TEXT[]      NOT NULL DEFAULT '{}',
    decorators              TEXT[]      NOT NULL DEFAULT '{}',
    complexity_cyclomatic   INT,
    complexity_cognitive    INT,
    complexity_max_nesting  INT,
    complexity_lines        INT,
    complexity_params       INT,
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (project_id, symbol_id)
);
CREATE INDEX IF NOT EXISTS symbols_qname_idx   ON chronograph.symbols(project_id, qualified_name);
CREATE INDEX IF NOT EXISTS symbols_file_idx    ON chronograph.symbols(project_id, file_path);
CREATE INDEX IF NOT EXISTS symbols_kind_idx    ON chronograph.symbols(project_id, kind);
-- GIN on calls array — makes trace_function callers lookup an index seek instead of full scan
CREATE INDEX IF NOT EXISTS symbols_calls_gin   ON chronograph.symbols USING gin(calls);
CREATE INDEX IF NOT EXISTS symbols_qname_trgm  ON chronograph.symbols USING gin(qualified_name gin_trgm_ops);
CREATE INDEX IF NOT EXISTS symbols_name_trgm   ON chronograph.symbols USING gin(name gin_trgm_ops);
CREATE INDEX IF NOT EXISTS symbols_complex_idx ON chronograph.symbols(project_id, complexity_cyclomatic DESC NULLS LAST)
    WHERE complexity_cyclomatic IS NOT NULL;

-- ── Symbol embeddings (pgvector) ────────────────────────────────────
CREATE TABLE IF NOT EXISTS chronograph.symbol_embeddings (
    project_id     TEXT         NOT NULL,
    symbol_id      TEXT         NOT NULL,
    qualified_name TEXT         NOT NULL,
    file_path      TEXT         NOT NULL,
    kind           TEXT,
    embed_text     TEXT         NOT NULL,
    embedding      vector(1536) NOT NULL,
    updated_at     TIMESTAMPTZ  NOT NULL DEFAULT now(),
    PRIMARY KEY (project_id, symbol_id)
);
-- HNSW for cosine ANN — replaces the in-Python cosine loop in query_engine.py
CREATE INDEX IF NOT EXISTS symbol_embeddings_hnsw ON chronograph.symbol_embeddings
    USING hnsw(embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

-- ── Graph edges ─────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS chronograph.edges (
    edge_id    TEXT  NOT NULL PRIMARY KEY,
    project_id TEXT  NOT NULL,
    source_id  TEXT  NOT NULL,
    target_id  TEXT  NOT NULL,
    rel_type   TEXT  NOT NULL,
    valid_from TEXT,
    valid_to   TEXT,
    properties JSONB NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS edges_source_idx ON chronograph.edges(project_id, source_id, rel_type);
CREATE INDEX IF NOT EXISTS edges_target_idx ON chronograph.edges(project_id, target_id, rel_type);
CREATE INDEX IF NOT EXISTS edges_rel_idx    ON chronograph.edges(project_id, rel_type);

-- ── Snapshots (temporal chain) ──────────────────────────────────────
CREATE TABLE IF NOT EXISTS chronograph.snapshots (
    project_id     TEXT NOT NULL,
    commit_sha     TEXT NOT NULL,
    timestamp_val  TEXT,
    author_email   TEXT,
    author_name    TEXT,
    message        TEXT,
    classification TEXT,
    parent_sha     TEXT,
    PRIMARY KEY (project_id, commit_sha)
);
CREATE INDEX IF NOT EXISTS snapshots_date_idx ON chronograph.snapshots(project_id, timestamp_val DESC NULLS LAST);
