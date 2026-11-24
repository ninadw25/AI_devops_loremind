> **Historical (as of 2026-09-25)** — superseded by `chronograph/store/pg_store.py` + `chronograph/store/schema.sql` (migration done; PgStore only). Kept for context; do not treat as current.

# MongoDB → PostgreSQL (+ pgvector) Migration Plan

**Status:** proposed, awaits review
**Scope:** port the **chronograph** code-intelligence store from MongoDB to PostgreSQL with `pgvector`. The **Gmail knowledge graph** stays in Neo4j (a separate ticket concerns that store).
**Branch audited:** `arnavgupta2468/lor-38-…-incremental` @ `afe31e0`
**Date:** 2026-06-01
**Author audience:** anyone about to start the work

---

## 0. Why Postgres + pgvector (and what we're not doing)

### Why not stay on Mongo
- Two different vector models in one system today: the chronograph path uses **1536-d OpenRouter `text-embedding-3-small`**, the Gmail/knowledge path uses **2048-d NVIDIA NeMo**. Mongo has no first-class vector index, so the chronograph side does a **pure-Python `for x, y in zip` cosine** in `chronograph/query/query_engine.py:100-114` over potentially hundreds of thousands of vectors. That is not a query engine; it is a slow `for` loop. Postgres + pgvector gets us a real ANN index in the same transaction.
- ACID, joins, `WITH RECURSIVE` for graph traversal, `EXPLAIN ANALYZE` for query planning — none of which Mongo gives you.
- One operational surface. The two pipelines today (Mongo for code, Neo4j for knowledge) mean two DBs to back up, monitor, and migrate. Consolidating the code-intel side into Postgres shrinks the operational surface even though we keep Neo4j for the graph-shaped knowledge queries.
- A graph store and a relational store are different things. The chronograph data is **relational with vector columns** (commits, files, symbols, edges, embeddings) — not graph-shaped at the access level. The `edges` collection is a list of typed adjacency rows, which Postgres indexes and joins better than a Mongo `$lookup` does.

### What we are NOT doing
- **We are NOT removing Neo4j.** The Gmail knowledge graph (Person / Decision / Task / Fact / Topic / Event / Organization with the 9-predicate ontology) stays in Neo4j — that is its right shape and the lore-backend service queries it via Cypher. Moving it would be a separate, much larger ticket.
- **We are NOT removing Mongo from the dependency manifest yet.** It is replaced, but the old `mongo_store.py` stays as a read-only fallback during the cutover window (Phase 4) and is deleted at the end of Phase 5.
- **We are NOT changing the embedding model in this migration.** We will, however, build the schema so that a future model swap is a one-column-type change (see §6). The current 1536-d model lives on; the 2048-d NeMo unification is a separate LORE ticket.

### Two-store architecture after migration

```
                    ┌────────────────────────────┐
                    │   External source (GitLab) │
                    └─────────────┬──────────────┘
                                  │ NormalizedRecord
                                  ▼
                    ┌────────────────────────────┐
                    │  Chronograph pipeline      │
                    │  (parse → diff → graph →   │
                    │   embed → dedup)           │
                    └─────────┬──────────┬───────┘
                              │          │
                              ▼          ▼
            ┌────────────────────┐  ┌─────────────────────┐
            │  PostgreSQL 16     │  │  Neo4j              │
            │  + pgvector 0.7+   │  │  (unchanged)        │
            │  ─ source-of-truth │  │  ─ code graph sync  │
            │    for code-intel  │  │    (LORE-49)        │
            │  ─ ANN search      │  │  ─ knowledge graph  │
            │  ─ full-text search│  │    (Gmail triples)  │
            │  ─ recursive CTE   │  │                     │
            │    for graph       │  │                     │
            └─────────┬──────────┘  └─────────┬───────────┘
                      │                       │
                      └───────────┬───────────┘
                                  ▼
                    ┌────────────────────────────┐
                    │  Query layer (Postgres)    │
                    │  + lore-backend (Neo4j)    │
                    │  + future agent layer      │
                    └────────────────────────────┘
```

---

## 1. Inventory of what is in Mongo today (so we know what to port)

This is the result of `rg "self\._db\.|self\.db\."` across the repo. Every collection and its write/read call sites:

| Collection | Rows per project (est.) | Write site(s) | Read site(s) | Embedding column? |
|---|---|---|---|---|
| `projects` | 1 | `mongo_store.py:87`, called from `ingest_pipeline.py:115, 192` | `mongo_store.py:94` | no |
| `commits` | 1k – 50k | `mongo_store.py:99` (bulk upsert), called from `ingest_pipeline.py:243, 132` | `query_engine.py:297` (regex search), `mongo_store.py:127, 138, 444, 524, 535, 563` (aggregations + trace) | no |
| `members` | 5 – 50 | `mongo_store.py:143`, called from `ingest_pipeline.py:139, 212` | `mongo_store.py:157, 563` | no |
| `commit_files` | 1k – 200k | `mongo_store.py:162`, called from `ingest_pipeline.py:146, 277` | `mongo_store.py:181, 188, 457, 494`, `graph_builder.py:102, 302`, `query_engine.py:217` (inline aggregation) | no |
| `source_files` | 100 – 5,000 | `mongo_store.py:197`, called from `ingest_pipeline.py:89, 154` | `mongo_store.py:218, 226, 535` (trace_function reads the file) | no |
| `symbols` | 1k – 100k | `mongo_store.py:230`, called from `ingest_pipeline.py:162, 328` | `mongo_store.py:258, 271, 276, 282, 300, 306, 535, 562` | no |
| `edges` | 1k – 500k | `mongo_store.py:341, 365`, called from `graph_builder.py:75, 140, 204, 238, 269, 325` | `mongo_store.py:385, 401, 562` (and `query_engine.py:311, 337, 352, 359, 374, 387, 442`) | no |
| `snapshots` | 1k – 50k | `mongo_store.py:407` and `bulk_create_snapshots` (used by `graph_builder.py:74`) | `mongo_store.py:426, 562` | no |
| `symbol_embeddings` | 1k – 100k | `mongo_store.py:310`, called from `ingest_pipeline.py:422` | `mongo_store.py:329, 332` (full table scan, used by `query_engine.py:93`) | **YES (1536-d float)** |

**Total files that import `MongoStore` or `pymongo`** (5):

```
chronograph/store/mongo_store.py
chronograph/query/query_engine.py
chronograph/gitlab_polling.py
chronograph/ingestion/graph_builder.py
chronograph/ingestion/ingest_pipeline.py
```

**Aggregation pipelines to translate** (the hard part — Mongo's `$lookup` / `$unwind` / `$group` doesn't have a 1:1 SQL equivalent, but the SQL is usually shorter and faster):

| Pipeline | Where | What it does |
|---|---|---|
| `get_commit_authors` | `mongo_store.py:444` | group commits by author_email, count + sum additions/deletions |
| `compute_hotspots` | `mongo_store.py:457` | unwind commit_files, group by path, count, sort, top N |
| `compute_bus_factor` | `mongo_store.py:494` | join commit_files↔commits, group files by path, find files with exactly 1 author |
| `get_activity_timeline` | `mongo_store.py:524` | bucket commits by month/week/day, count commits, count distinct authors |
| `trace_function` (callers side) | `mongo_store.py:535` | find symbols where `calls` array contains the target's name |
| per-developer file expertise (inline) | `query_engine.py:217` | join commit_files↔commits, group by (author, path), top 30 |
| `_build_authored_by` (file→author→earliest-date) | `graph_builder.py:102` | join commit_files↔commits, take earliest authored_date per (file, author) |
| `_build_collaborates_with` (shared files per author pair) | `graph_builder.py:302` | join commit_files↔commits, group files by author, find files with ≥2 authors |

**Indexes that exist today** (`mongo_store.py:54-79`, the `_ensure_indexes` block) — these translate directly to Postgres `CREATE INDEX` and inform the index design in §3:

- `projects(project_id)` unique
- `commits(project_id, sha)` unique, `(project_id, author_email)`, `(project_id, authored_date DESC)`
- `members(project_id, username)`
- `commit_files(project_id, commit_sha)`, `(project_id, path)`
- `source_files(project_id, path)` unique
- `symbols(project_id, qualified_name)`, `(project_id, file_path)`, `(project_id, kind)`
- `symbol_embeddings(project_id, symbol_id)` unique
- `edges(project_id, rel_type)`, `(project_id, source_id)`, `(project_id, target_id)`, `(project_id, rel_type, source_id)`
- `snapshots(project_id, commit_sha)` unique, `(project_id, timestamp DESC)`

---

## 2. The 7 design goals, in priority order

1. **Semantic equivalence.** Every query the current Mongo store answers must be answerable from Postgres with the same answer, byte-for-byte (where ordering is unspecified). This is a hard constraint — if the agent layer reads the same shape, the LLM outputs don't change.
2. **Single source of truth.** The store is one schema. No "two collections that mean the same thing" the way `commit_files` and `commit_files` aggregations do today.
3. **pgvector for the embedding column.** Replace the in-Python cosine loop with a real HNSW or IVF index.
4. **First-class graph via recursive CTE.** The query engine's `trace_function`, `what_breaks_if_changed`, and the `developer_collaboration` patterns are all 2-3 hop graph traversals. `WITH RECURSIVE` in Postgres is the right primitive; no need for a separate graph DB for code-intel.
5. **Optimized for the query engine's hot path.** The 13 public methods on `QueryEngine` are the dominant read workload. Their shapes dictate the index strategy.
6. **Boring, mainstream.** asyncpg, SQLAlchemy 2.x Core (not ORM — see §7), `pgvector` Python client, Alembic for migrations. No exotic extensions beyond what we list in §6.
7. **Zero-downtime migration.** Postgres is built in parallel, written to in parallel, then cut over read-side. The old Mongo store stays as a read fallback for the duration of the cutover window.

---

## 3. The target schema (the heart of the doc)

All tables live in the schema `chronograph` (vs. the default `public`) so we can co-locate with Neo4j-style separation later. Primary keys are **TEXT, deterministic**, and use the same ID formats as the Mongo docs (`entity:...`, `snap:...`, `dev:...`) — so the Neo4j code-intel emitter (LORE-49) can pick the same identifiers and the query engine's `edge` table joins are stable.

```sql
CREATE SCHEMA IF NOT EXISTS chronograph;
SET search_path TO chronograph, public;

-- Enable required extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";      -- for gen_random_uuid() in case
CREATE EXTENSION IF NOT EXISTS "pgcrypto";       -- for short_hash() on the app side
CREATE EXTENSION IF NOT EXISTS "pg_trgm";        -- trigram for LIKE/regex fast path
CREATE EXTENSION IF NOT EXISTS "vector";          -- pgvector

-- Domain enums (cheap integrity, big planner win)
CREATE TYPE source_kind AS ENUM ('gitlab','github','bitbucket','gitea','sourcehut');
CREATE TYPE symbol_kind AS ENUM ('function','method','class','module');
CREATE TYPE classification AS ENUM ('feature','bugfix','refactor','performance','docs','test','style','chore','merge');
CREATE TYPE edge_rel AS ENUM (
    'NEXT_SNAPSHOT','AUTHORED_BY','CALLS','CONTAINS',
    'IMPORTS','COLLABORATES_WITH','ALIAS_OF'  -- ALIAS_OF is from the dedup stage
);
```

### 3.1 `repos` (one row per source×project, replaces `projects`)

```sql
CREATE TABLE repos (
    source           source_kind   NOT NULL,
    external_id      TEXT          NOT NULL,        -- GitLab project ID, GitHub repo ID
    repo_url         TEXT          NOT NULL,        -- canonical, https://gitlab.com/foo/bar
    full_name        TEXT          NOT NULL,        -- "owner/repo"
    name             TEXT,
    description      TEXT,
    visibility       TEXT,                          -- public/private/internal
    default_branch   TEXT,
    web_url          TEXT,
    created_at       TIMESTAMPTZ,
    updated_at       TIMESTAMPTZ  NOT NULL DEFAULT now(),

    PRIMARY KEY (source, external_id)
);

-- Hot path: get repo by (source, full_name) for the query engine
CREATE UNIQUE INDEX repos_url_idx     ON repos (repo_url);
CREATE UNIQUE INDEX repos_full_idx    ON repos (source, full_name);
CREATE INDEX        repos_name_trgm    ON repos USING gin (full_name gin_trgm_ops);
```

**Why these keys:**
- `(source, external_id)` is the natural key — same source can have colliding `external_id` only across hosts (never happens in practice but the constraint is cheap).
- `repo_url` is a `UNIQUE` secondary index because the **whole codebase keys entities by `repo_url`** (`entity:{repo_url}:{file_path}:{qualified_name}`) — this is the lookup the graph builder does on every symbol it creates.
- `pg_trgm` on `full_name` for fuzzy / `ILIKE '%foo%'` lookups (replaces Mongo's regex `$options: "i"`).

### 3.2 `commits` (replaces `commits`)

```sql
CREATE TABLE commits (
    source           source_kind   NOT NULL,
    repo_url         TEXT          NOT NULL,        -- FK to repos.repo_url
    sha              TEXT          NOT NULL,
    short_id         TEXT          NOT NULL,        -- sha[:8], denormalized for display
    title            TEXT          NOT NULL,
    message          TEXT          NOT NULL,
    author_name      TEXT          NOT NULL,
    author_email     TEXT          NOT NULL,
    authored_date    TIMESTAMPTZ   NOT NULL,        -- critical: used by all date queries
    committed_date   TIMESTAMPTZ   NOT NULL,
    parent_shas      TEXT[]        NOT NULL DEFAULT '{}',
    additions        INT           NOT NULL DEFAULT 0,
    deletions        INT           NOT NULL DEFAULT 0,
    web_url          TEXT,
    classification   classification,                 -- set by graph_builder, not by ingest
    -- snapshot_id is NOT a column — it's derived: 'snap:' || repo_url || ':' || sha[:12]

    PRIMARY KEY (source, repo_url, sha),
    FOREIGN KEY (source, repo_url) REFERENCES repos(source, repo_url) ON DELETE CASCADE  -- see §3.10
);
-- The FK column order (source, repo_url) is intentional — matches the unique index
-- on repos(source, full_name)... wait, no, FK must match the *target* index, not the natural
-- shape. Fix below.

-- Actually: we need a unique index on (source, repo_url) for the FK to work.
-- We have one (repos_url_idx is unique on repo_url alone). So this FK works:
--   FOREIGN KEY (repo_url) REFERENCES repos(repo_url)
-- Drop the multi-column FK above and use:
--   FOREIGN KEY (repo_url) REFERENCES repos(repo_url) ON DELETE CASCADE
-- (See §3.10 for why we keep repo_url as the join key everywhere.)

CREATE INDEX commits_author_email_idx ON commits (repo_url, author_email);
CREATE INDEX commits_date_idx         ON commits (repo_url, authored_date DESC);
CREATE INDEX commits_class_idx        ON commits (repo_url, classification) WHERE classification IS NOT NULL;
```

**Design notes:**
- `additions` and `deletions` are promoted to top-level columns instead of being buried in a `stats: {additions, deletions}` sub-document. This is the one deliberate denormalization — Mongo stores them nested; we hoist them because **every aggregation in §1 hits them**, and SQL aggregations over a sub-document require a JSON path expression that the planner handles poorly.
- `parent_shas` is a `TEXT[]` array. Mongo stored this as `["abc", "def"]`; we keep the same shape. Useful for `parent_shas && ARRAY['abc']` queries (does any parent match?).
- `classification` is nullable — it gets set by the graph builder, not the ingest. The partial index `WHERE classification IS NOT NULL` is small and fast.

### 3.3 `commit_files` (replaces `commit_files`)

The Mongo shape is `{commit_sha, files: [{path, old_path, new_file, deleted_file}, ...]}` — a **nested array**. In SQL we normalize to one row per file-per-commit. This is the biggest single shape change in the migration and the only one that breaks a `find_one` shape contract.

```sql
CREATE TABLE commit_files (
    source           source_kind   NOT NULL,
    repo_url         TEXT          NOT NULL,
    commit_sha       TEXT          NOT NULL,
    file_path        TEXT          NOT NULL,
    old_path         TEXT,
    new_file         BOOLEAN       NOT NULL DEFAULT false,
    deleted_file     BOOLEAN       NOT NULL DEFAULT false,
    renamed_file     BOOLEAN       NOT NULL DEFAULT false,

    PRIMARY KEY (source, repo_url, commit_sha, file_path),
    FOREIGN KEY (source, repo_url, commit_sha) REFERENCES commits(source, repo_url, sha) ON DELETE CASCADE
);

-- The hot path: "for these commits, what files changed?" — covered by PK
-- The hot path: "for this file, who changed it?" — needs
CREATE INDEX commit_files_path_idx ON commit_files (repo_url, file_path);
-- The hot path: "for this file in this repo, who changed it most?" — needs
CREATE INDEX commit_files_path_email_idx ON commit_files (repo_url, file_path, commit_sha)
    INCLUDE (commit_sha);  -- covering index for the JOIN below

-- For the deletion filter (hotspots excludes deleted):
CREATE INDEX commit_files_active_idx ON commit_files (repo_url, file_path)
    WHERE NOT deleted_file;
```

**Design notes:**
- Primary key is `(source, repo_url, commit_sha, file_path)`. This guarantees **idempotent re-ingestion**: re-fetching a commit is a no-op via `ON CONFLICT DO NOTHING`.
- The partial index `WHERE NOT deleted_file` makes the hotspots query scan ~half the rows in the worst case (most repos have far fewer deleted files than changed files).
- We keep `old_path` and `renamed_file` even though no current query uses them. They are cheap to store and the plugin contract (§4 of `docs/source-plugin-contract.md`) emits them.

### 3.4 `members` (replaces `members`)

```sql
CREATE TABLE members (
    source           source_kind   NOT NULL,
    repo_url         TEXT          NOT NULL,
    login            TEXT          NOT NULL,        -- GitLab/GitHub username
    name             TEXT,
    email            TEXT,
    access_level     INT,                           -- 10/20/30/40/50 in GitLab; 0.. in GitHub
    role             TEXT,                           -- normalized label, see §3.11
    avatar_url       TEXT,
    web_url          TEXT,
    contributions    INT,                           -- GitHub-only
    fetched_at       TIMESTAMPTZ   NOT NULL DEFAULT now(),

    PRIMARY KEY (source, repo_url, login),
    FOREIGN KEY (source, repo_url) REFERENCES repos(source, repo_url) ON DELETE CASCADE
);

CREATE INDEX members_email_idx ON members (email) WHERE email IS NOT NULL;
```

**Design notes:**
- `email` is rare on GitLab but common on GitHub. The partial index keeps it cheap.
- No FK to a `developers` table — the developer identity is **derived** by the dedup stage, not enforced at write time. Cross-source person merge is a graph concern, not a storage concern.

### 3.5 `source_files` (replaces `source_files`)

```sql
CREATE TABLE source_files (
    source           source_kind   NOT NULL,
    repo_url         TEXT          NOT NULL,
    file_path        TEXT          NOT NULL,
    ref              TEXT          NOT NULL DEFAULT 'main',
    content          TEXT          NOT NULL,
    content_sha      TEXT          NOT NULL,        -- sha256 of content; for diff
    language         TEXT,                          -- filled by parse stage
    byte_size        INT           NOT NULL,
    line_count       INT,
    fetched_at       TIMESTAMPTZ   NOT NULL DEFAULT now(),

    PRIMARY KEY (source, repo_url, ref, file_path)
);

-- Hot path: read file by exact path
-- Already covered by PK
-- Hot path: read file by (repo, ref) — covered by PK prefix (source, repo_url, ref)
-- Hot path: read all source files in a file_path across refs (rare)
CREATE INDEX source_files_path_idx ON source_files (repo_url, file_path);

-- pg_trgm on file_path for fuzzy file search
CREATE INDEX source_files_path_trgm ON source_files USING gin (file_path gin_trgm_ops);
```

**Design notes:**
- We add `ref` (was implicit `main` in the Mongo code) and `content_sha` (was a property of `body_hash` on the symbol side, not the file side). This makes **incremental file diffs possible** without a tree-sitter re-parse: if `content_sha` matches the previously-stored `content_sha`, the file hasn't changed and we can skip the parse.
- `content` is `TEXT`, not `BYTEA`. The Mongo code stored it as text. Encoding detection is up to the parser.
- `line_count` is denormalized for `get_function_code` (line 71 of query_engine.py), which currently calls `content.split('\n')` on every read.

### 3.6 `symbols` (replaces `symbols`)

The biggest table. One row per parsed symbol.

```sql
CREATE TABLE symbols (
    source              source_kind   NOT NULL,
    repo_url            TEXT          NOT NULL,
    file_path           TEXT          NOT NULL,
    qualified_name      TEXT          NOT NULL,
    name                TEXT          NOT NULL,        -- basename
    kind                symbol_kind   NOT NULL,
    language            TEXT          NOT NULL,
    start_line          INT           NOT NULL,
    end_line            INT           NOT NULL,
    signature           TEXT,
    body_hash           TEXT,                           -- sha256 of body
    params              TEXT[]        NOT NULL DEFAULT '{}',
    calls               TEXT[]        NOT NULL DEFAULT '{}',
    imports             TEXT[]        NOT NULL DEFAULT '{}',
    decorators          TEXT[]        NOT NULL DEFAULT '{}',

    -- complexity, denormalized as columns (was a sub-doc in Mongo)
    complexity_cyclomatic    INT,
    complexity_cognitive     INT,
    complexity_max_nesting   INT,
    complexity_lines         INT,
    complexity_params        INT,

    -- the deterministic ID — kept as a generated column for joins
    entity_id           TEXT          GENERATED ALWAYS AS
        ('entity:' || repo_url || ':' || file_path || ':' || qualified_name) STORED,

    PRIMARY KEY (source, repo_url, file_path, qualified_name),
    FOREIGN KEY (source, repo_url, file_path) REFERENCES source_files(source, repo_url, file_path) ON DELETE CASCADE
);

CREATE INDEX symbols_qname_idx    ON symbols (repo_url, qualified_name);
CREATE INDEX symbols_kind_idx     ON symbols (repo_url, kind);
CREATE INDEX symbols_file_idx     ON symbols (repo_url, file_path);
CREATE INDEX symbols_complex_idx  ON symbols (repo_url, complexity_cyclomatic DESC NULLS LAST)
    WHERE complexity_cyclomatic IS NOT NULL;
CREATE INDEX symbols_calls_gin    ON symbols USING gin (calls);                -- trace_function: callers search
CREATE INDEX symbols_qname_trgm   ON symbols USING gin (qualified_name gin_trgm_ops);  -- search_symbols
```

**Design notes:**
- `entity_id` is a **generated column** so the codebase can keep using the `entity:{repo_url}:{file_path}:{qualified_name}` ID format that `chronograph/entities.py:50-65` builds, and joins against the `edges` table don't need a CASE/WHEN dance.
- The complexity fields are **columns**, not a sub-document. Every query the engine runs (`get_most_complex`, `what_breaks_if_changed`, `developer_risk_report`) reads them. SQL aggregations over a JSONB sub-doc require `->` and `->>` and the planner often gives up. The cost is 5 nullable columns vs. one JSONB column; both are fine, the columns are easier.
- The GIN index on `calls` is the **single biggest performance win** in the whole schema. It replaces a sequential scan with index lookup for `trace_function` (line 535 of `mongo_store.py`, currently `{"calls": target["name"]}` — a full-collection scan with no index). The Mongo store has **no index on the `calls` array** today, so every trace query is a full scan. With the GIN index, it's an index seek.
- `qname_trgm` replaces Mongo's regex search in `search_symbols` (line 271-282 of `mongo_store.py`).
- `complex_idx` is partial: the planner uses it for `get_most_complex` and skips the rest of the table.

### 3.7 `symbol_embeddings` (replaces `symbol_embeddings`)

The vector table. This is the one where pgvector earns its keep.

```sql
CREATE TABLE symbol_embeddings (
    source           source_kind   NOT NULL,
    repo_url         TEXT          NOT NULL,
    file_path        TEXT          NOT NULL,
    qualified_name   TEXT          NOT NULL,
    symbol_id        TEXT          NOT NULL,        -- = entity_id
    text             TEXT          NOT NULL,        -- the text that was embedded
    embedding        vector(1536) NOT NULL,        -- change to vector(2048) when LORE-45 lands
    model            TEXT          NOT NULL DEFAULT 'openai/text-embedding-3-small',
    embedded_at      TIMESTAMPTZ   NOT NULL DEFAULT now(),

    PRIMARY KEY (source, repo_url, file_path, qualified_name),
    FOREIGN KEY (source, repo_url, file_path, qualified_name)
        REFERENCES symbols(source, repo_url, file_path, qualified_name) ON DELETE CASCADE
);

-- HNSW index for cosine similarity. m=16, ef_construction=64 are the pgvector defaults
-- and the right starting point for up to ~1M rows. Switch to IVF when row count
-- exceeds ~5M and you have time to retrain.
CREATE INDEX symbol_embeddings_hnsw_idx ON symbol_embeddings
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

-- For the "have I already embedded this?" check (idempotency in the embed stage)
CREATE INDEX symbol_embeddings_symbol_idx ON symbol_embeddings (symbol_id);
```

**Design notes:**
- `vector(1536)` is hard-coded. When LORE-45 unifies the embedding model, this becomes `vector(2048)` via a single `ALTER TABLE` (pgvector supports this). The HNSW index has to be rebuilt, but it's a one-time cost.
- The GIN/HNSW choice: **HNSW is better than IVFFlat for up to ~1M rows** (better recall, no training step). For larger sets, IVFFlat with `lists = sqrt(rows)` is faster at query time. We have ~10-100k symbols per project, so HNSW is correct.
- The planner hint `vector_cosine_ops` is mandatory for cosine; `vector_l2_ops` is for Euclidean. The codebase computes cosine similarity in Python today, so this matches.
- The FK to `symbols` means **deleting a symbol cascades to its embedding**. This is correct — orphaned embeddings are useless and would waste index space.

### 3.8 `edges` (replaces `edges`)

The graph table. Recursive CTE queries run over this.

```sql
CREATE TABLE edges (
    source           source_kind   NOT NULL,
    repo_url         TEXT          NOT NULL,
    edge_id          TEXT          NOT NULL,        -- 'edge:' || short_hash(src:tgt:rel:valid_from)
    rel_type         edge_rel      NOT NULL,
    source_id        TEXT          NOT NULL,        -- 'entity:...' | 'snap:...' | 'dev:...' | 'edge:...'
    target_id        TEXT          NOT NULL,
    valid_from       TIMESTAMPTZ   NOT NULL,
    valid_to         TIMESTAMPTZ,                    -- null = currently valid
    properties       JSONB         NOT NULL DEFAULT '{}'::jsonb,

    PRIMARY KEY (edge_id),
    -- NOTE: no FK on source_id/target_id — they can be 'entity:...', 'snap:...',
    -- 'dev:...', or another 'edge:...' (for derived composition). Enforce in the
    -- application layer. This is a deliberate design choice for graph flexibility.
    FOREIGN KEY (source, repo_url) REFERENCES repos(source, repo_url) ON DELETE CASCADE
);

-- The hottest graph read patterns
CREATE INDEX edges_source_idx      ON edges (repo_url, source_id, rel_type);
CREATE INDEX edges_target_idx      ON edges (repo_url, target_id, rel_type);
CREATE INDEX edges_rel_idx         ON edges (repo_url, rel_type);
-- For "what's currently valid?" queries
CREATE INDEX edges_valid_idx       ON edges (repo_url, source_id, rel_type)
    WHERE valid_to IS NULL;
```

**Design notes:**
- The `edges` table is the most-denormalized in the schema. It carries:
  - `repo_url` for fast project scoping (the most common filter in queries).
  - `rel_type` as a Postgres `ENUM` for index compression and integrity.
  - `source_id` / `target_id` as **TEXT** with **no FK** because the target can be a symbol, a snapshot, a developer, or another edge (composite edges). The application layer is responsible for referential sanity, and the dedup stage reconciles any drift. This is a deliberate departure from "everything is a FK" — graph data is too flexible for that.
- The partial index `WHERE valid_to IS NULL` speeds up the most common traversal pattern: "what does X currently point to?" The full index covers the rare temporal queries.

### 3.9 `snapshots` (replaces `snapshots`)

```sql
CREATE TABLE snapshots (
    source           source_kind   NOT NULL,
    repo_url         TEXT          NOT NULL,
    commit_sha       TEXT          NOT NULL,
    snapshot_id      TEXT          GENERATED ALWAYS AS
        ('snap:' || repo_url || ':' || substring(sha, 1, 12)) STORED,
    timestamp        TIMESTAMPTZ   NOT NULL,
    author_email     TEXT,
    author_name      TEXT,
    message          TEXT,
    classification   classification,

    PRIMARY KEY (source, repo_url, commit_sha),
    FOREIGN KEY (source, repo_url, commit_sha) REFERENCES commits(source, repo_url, sha) ON DELETE CASCADE
);

CREATE INDEX snapshots_id_idx       ON snapshots (snapshot_id);
CREATE INDEX snapshots_date_idx     ON snapshots (repo_url, timestamp DESC);
```

**Design notes:**
- `snapshot_id` is generated, so the application doesn't have to remember the format. The same format is used as `source_id` / `target_id` in the `edges` table.
- Most fields are also in `commits`; `snapshots` exists because the `NEXT_SNAPSHOT` edge needs them denormalized. We could have just used `commits` + an edge to `commits.sha`, but a separate `snapshots` table makes the temporal-chain graph cleaner and avoids polluting the `commits` table with graph-specific fields.

### 3.10 Foreign key discipline — the design choice

All FKs in the schema point to `repos(source, repo_url)`. **Every table has a `repo_url` column**, and the FKs form a star:

```
repos ──┬── commits ──┬── commit_files
        │             ├── snapshots
        ├── source_files ──── symbols ──── symbol_embeddings
        ├── members
        └── edges
```

**Why `repo_url` and not `(source, external_id)`?**
- The rest of the codebase uses `repo_url` as the ID slot for entities (`entity:{repo_url}:{file_path}:{qualified_name}`). Joining on `repo_url` means entity IDs compose into FK relationships without translation.
- `repo_url` is a single column, so `FOREIGN KEY (repo_url) REFERENCES repos(repo_url)` is the cleanest FK definition. Postgres requires the target of an FK to be a UNIQUE/PRIMARY KEY constraint; `repos_url_idx` is unique, so the FK works.
- Cross-source queries become possible: "show me all symbols in any source whose file_path matches `*/auth/*`" is a single `WHERE file_path LIKE '%auth%'` query across all `source_files`. With `(source, external_id)` as the key, you'd need a join to `repos` for every cross-source query.

**Cascade behavior.** `ON DELETE CASCADE` from `repos` to everything. Drop a repo, drop everything that references it. This is correct for ingestion (a repo can be un-ingested) but means **deleting a repo is destructive** — use a soft-delete (`deleted_at TIMESTAMPTZ` on `repos`) if you want a non-destructive option. Add it later if needed.

### 3.11 The role/access_level view

`members.access_level` is GitLab-shaped (10/20/30/40/50). GitHub's equivalent is "Admin / Maintain / Write / Triage / Read / None" (string). A small **view** normalizes both into a single label:

```sql
CREATE VIEW member_roles AS
SELECT
    m.source, m.repo_url, m.login, m.email,
    CASE
        WHEN m.source = 'gitlab' AND m.access_level >= 50 THEN 'owner'
        WHEN m.source = 'gitlab' AND m.access_level >= 40 THEN 'maintainer'
        WHEN m.source = 'gitlab' AND m.access_level >= 30 THEN 'developer'
        WHEN m.source = 'gitlab' AND m.access_level >= 20 THEN 'reporter'
        WHEN m.source = 'gitlab' AND m.access_level >= 10 THEN 'guest'
        WHEN m.role IS NOT NULL THEN lower(m.role)
        ELSE 'unknown'
    END AS role_normalized
FROM members m;
```

This is used by the dedup stage and any access-control decisions the agent layer makes. Don't store the normalized role in `members`; let the view compute it. Storage is cheap, but the moment you store it, the GitLab/GitHub semantic drifts will bit-rot.

### 3.12 Sequence diagrams in SQL — the 5 critical queries

These are the actual SQL the migration produces for the 5 highest-traffic query-engine methods. They are the **acceptance test** of the schema: if these run as fast or faster than the Mongo versions on the same data, the migration is a success.

**Q1: `compute_hotspots(repo_url, limit=20)`**

```sql
SELECT file_path, COUNT(*) AS change_count
FROM commit_files
WHERE repo_url = $1
  AND NOT deleted_file
GROUP BY file_path
ORDER BY change_count DESC
LIMIT $2;
```

Index used: `commit_files_active_idx (repo_url, file_path) WHERE NOT deleted_file`. Plan: index scan → group aggregate → sort → limit. Should be O(N log N) where N is changed-files per repo.

**Q2: `compute_bus_factor(repo_url, min_changes=5)`**

```sql
SELECT
    cf.file_path,
    COUNT(*) AS total_changes,
    MIN(c.author_email) FILTER (WHERE bool_and(c.author_email IS NOT NULL)) AS sole_author
FROM commit_files cf
JOIN commits c
  ON c.source = cf.source
 AND c.repo_url = cf.repo_url
 AND c.sha = cf.commit_sha
WHERE cf.repo_url = $1
  AND NOT cf.deleted_file
GROUP BY cf.file_path
HAVING COUNT(*) >= $2
   AND COUNT(DISTINCT c.author_email) = 1
ORDER BY total_changes DESC;
```

Index used: `commit_files_active_idx` for the scan, `commits` PK for the join. Plan: index scan → hash join → group → having → sort.

**Q3: `get_commit_authors(repo_url)`**

```sql
SELECT
    author_email,
    MIN(author_name)            AS name,
    COUNT(*)                    AS commit_count,
    SUM(additions)              AS additions,
    SUM(deletions)              AS deletions
FROM commits
WHERE repo_url = $1
GROUP BY author_email
ORDER BY commit_count DESC;
```

Index used: `commits_author_email_idx (repo_url, author_email)`. Plan: index scan → group aggregate → sort. This is the one where the denormalized columns (`additions`, `deletions` as top-level) pay off — the equivalent Mongo aggregation needs `$stats.additions` and the planner gives up.

**Q4: `trace_function(repo_url, function_name)` (callers side)**

```sql
SELECT s.qualified_name, s.file_path, s.start_line
FROM symbols s
WHERE s.repo_url = $1
  AND s.kind IN ('function', 'method')
  AND $2 = ANY (s.calls);
```

Index used: `symbols_calls_gin (calls)`. Plan: bitmap index scan on the GIN. This is the **largest performance win** in the whole migration — Mongo does a collection scan with no usable index.

**Q5: `semantic_search(repo_url, query_vector, top_k=10)`**

```sql
SELECT
    se.symbol_id,
    se.qualified_name,
    se.file_path,
    se.kind,
    1 - (se.embedding <=> $1::vector) AS cosine_similarity
FROM symbol_embeddings se
WHERE se.repo_url = $2
ORDER BY se.embedding <=> $1::vector
LIMIT $3;
```

Index used: `symbol_embeddings_hnsw_idx`. Plan: HNSW approximate nearest neighbor. 50ms on 100k vectors with m=16, ef_construction=64. Replaces the current pure-Python loop in `query_engine.py:100-114` which is O(N) per query and runs in seconds at scale.

**Bonus: graph traversal (recursive CTE) — what Mongo can't easily do**

```sql
-- "Who transitively calls function X?" — 3-hop BFS
WITH RECURSIVE call_chain AS (
    SELECT s.qualified_name, s.file_path, 1 AS depth
    FROM symbols s
    WHERE s.repo_url = $1
      AND s.qualified_name = $2
  UNION ALL
    SELECT s.qualified_name, s.file_path, cc.depth + 1
    FROM symbols s
    JOIN call_chain cc
      ON $3 = ANY (s.calls)
    WHERE s.repo_url = $1
      AND cc.depth < 3
)
SELECT * FROM call_chain ORDER BY depth, qualified_name;
```

This is the shape of the question the agent layer will want to ask. Mongo would require a multi-stage `$lookup` pipeline or maintaining a pre-computed `transitive_calls` collection. Postgres does it natively.

---

## 4. Embedding column decisions (the easy part if you do the hard work first)

### 4.1 Dimension

`vector(1536)` today, `vector(2048)` after LORE-45. pgvector supports up to 2000 dimensions in a single index as of 0.7.0 — both 1536 and 2048 are fine.

### 4.2 Distance function

`<=>` is **cosine distance** (1 - cosine_similarity). The codebase already computes cosine similarity in Python, so this is a 1:1 mapping. The index operator class is `vector_cosine_ops`.

### 4.3 Index type: HNSW

- **HNSW** (Hierarchical Navigable Small World): graph-based, no training step, higher recall, slightly slower build.
- **IVFFlat**: cluster-based, requires training, lower recall but faster query at scale.

For up to ~1M rows, **HNSW wins** on recall and operational simplicity. Switch to IVFFlat only when the HNSW build time becomes a problem (it scales superlinearly). At 100k symbols per project and 10 projects, you're at 1M rows — exactly the breakpoint.

Build parameters:
- `m = 16` — connections per layer (default; works well for 100-1000 dim)
- `ef_construction = 64` — candidate list size at build time (default; 100-200 is the high-quality range)

Query parameters (set at session level, not index level):
- `SET hnsw.ef_search = 40` — candidate list at query time. Default 40, raise to 100 for better recall.

### 4.4 The "have I embedded this?" check

Today: `if existing >= len(to_embed): return "skipped"` (`ingest_pipeline.py:358`). This works because every symbol gets exactly one embedding and the count matches.

In Postgres: same logic, but use a **LEFT JOIN with a `WHERE` filter** so we get the symbols that *don't* have an embedding yet, and embed only those. The 100% idempotent version of this:

```sql
-- Find symbols in repo that lack an embedding
SELECT s.qualified_name, s.file_path, ...
FROM symbols s
LEFT JOIN symbol_embeddings se
  ON se.source = s.source
 AND se.repo_url = s.repo_url
 AND se.file_path = s.file_path
 AND se.qualified_name = s.qualified_name
WHERE s.repo_url = $1
  AND s.kind IN ('function', 'method')
  AND se.symbol_id IS NULL;
```

This replaces the brittle count comparison and lets the embed stage resume from interruption correctly.

---

## 5. The 8 aggregations translated

The hard part of the migration. Each is the SQL equivalent of a Mongo aggregation pipeline. None of these is exotic — they all fit in 5-20 lines of SQL.

### 5.1 `get_commit_authors` (mongo line 444)

Mongo: `$match` → `$group` by `author_email` with `$sum` on count + additions + deletions → `$sort` by count desc.

SQL: see Q3 in §3.12.

### 5.2 `compute_hotspots` (mongo line 457)

Mongo: `$match` → `$unwind files` → `$match deleted_file != true` → `$group` by path with `$sum` → `$sort` → `$limit` → `$project`.

SQL: see Q1 in §3.12. **Faster** because the partial index `WHERE NOT deleted_file` lets the planner skip the `$unwind` and the deletion filter in one step.

### 5.3 `compute_bus_factor` (mongo line 494)

Mongo: `$match` → `$unwind` → `$lookup` commits → `$unwind` commit → `$group` by path with `$addToSet` authors → `$match` total_changes >= N AND `$size(authors) == 1` → `$sort`.

SQL: see Q2 in §3.12. The `COUNT(DISTINCT author_email) = 1` predicate replaces the `$addToSet` + `$size` combo. **Much faster** because it avoids materializing the set.

### 5.4 `get_activity_timeline` (mongo line 524)

Mongo: `$match` → `$addFields` `date_parsed = $dateFromString` → `$match date_parsed != null` → `$group` by `$dateToString(format)` → `$sort` by `_id`.

SQL:

```sql
SELECT
    to_char(authored_date, $1) AS period,        -- 'YYYY-MM' for month, 'YYYY-WW' for week, 'YYYY-MM-DD' for day
    COUNT(*) AS commit_count,
    COUNT(DISTINCT author_email) AS author_count
FROM commits
WHERE repo_url = $2
  AND authored_date IS NOT NULL
GROUP BY period
ORDER BY period;
```

The Mongo code does string parsing on `authored_date` because GitLab returns ISO strings. In Postgres, `authored_date` is a `TIMESTAMPTZ` and `to_char` does the bucketing natively. **Faster and more correct** (handles timezone correctly).

### 5.5 `trace_function` callers (mongo line 535)

Mongo: `find({project_id, kind in [function, method], calls: target_name})`.

SQL: see Q4 in §3.12. **Order of magnitude faster** with the GIN index on `calls`.

### 5.6 `developer file expertise` (query_engine.py:217)

Mongo: `$match` → `$unwind files` → `$lookup commits` → `$unwind` → `$group` by (email, path) with `$first` and `$sum` → `$sort` → `$limit 30`.

SQL:

```sql
SELECT
    c.author_email AS email,
    MIN(c.author_name) AS author,
    cf.file_path AS path,
    COUNT(*) AS change_count
FROM commit_files cf
JOIN commits c
  ON c.source = cf.source
 AND c.repo_url = cf.repo_url
 AND c.sha = cf.commit_sha
WHERE cf.repo_url = $1
  AND NOT cf.deleted_file
GROUP BY c.author_email, cf.file_path
ORDER BY change_count DESC
LIMIT 30;
```

Same shape, no `$unwind` cost.

### 5.7 `graph_builder._build_authored_by` (graph_builder.py:102)

Mongo: $match → $unwind → $lookup commits → group files by (file_path, author_email) taking min(authored_date) → in-memory `file_authors` dict.

SQL:

```sql
SELECT
    cf.file_path,
    c.author_email,
    MIN(c.authored_date) AS earliest_date
FROM commit_files cf
JOIN commits c
  ON c.source = cf.source
 AND c.repo_url = cf.repo_url
 AND c.sha = cf.commit_sha
WHERE cf.repo_url = $1
  AND NOT cf.deleted_file
GROUP BY cf.file_path, c.author_email;
```

The in-memory dict becomes a result set the application iterates. Same shape.

### 5.8 `graph_builder._build_collaborates_with` (graph_builder.py:302)

Mongo: $match → $unwind → $lookup → $group by path with $addToSet authors → $match $size(authors) >= 2 → in-memory combination of author pairs.

SQL:

```sql
SELECT
    cf.file_path,
    array_agg(DISTINCT c.author_email ORDER BY c.author_email) AS authors
FROM commit_files cf
JOIN commits c
  ON c.source = cf.source
 AND c.repo_url = cf.repo_url
 AND c.sha = cf.commit_sha
WHERE cf.repo_url = $1
  AND NOT cf.deleted_file
GROUP BY cf.file_path
HAVING COUNT(DISTINCT c.author_email) >= 2;
```

Then in Python: iterate, for each `authors` list emit edges for every pair `(a, b)` where `a < b`. The pair-generation logic is the same as the Mongo code; only the data fetch is SQL.

---

## 6. Postgres extensions and config

| Extension | Version | Purpose | Required? |
|---|---|---|---|
| `pgvector` | 0.7+ | vector column type, HNSW/IVF index | **YES** |
| `pg_trgm` | bundled | trigram indexes for `LIKE`/`ILIKE` | **YES** (replaces Mongo regex) |
| `pgcrypto` | bundled | `digest()`, `gen_random_uuid()` | no (app-side hashing is in Python) |
| `uuid-ossp` | bundled | uuid generators | no |
| `pg_stat_statements` | bundled | query perf monitoring | **YES** for ops |

### 6.1 Postgres server config to tune

These are the bits that bite if you forget them:

```ini
# postgresql.conf

# Vector index build time: HNSW with m=16, ef_construction=64 on 100k rows takes
# ~30s with 4GB work_mem. Bump if you see "out of memory" during index builds.
maintenance_work_mem = 4GB

# Vector query time: pgvector loads the candidate list into work_mem. Bigger is
# better up to the dataset size. 256MB is a safe starting point.
work_mem = 256MB

# Shared buffers: 25% of RAM is the rule of thumb.
shared_buffers = 4GB

# Effective cache size: tells the planner how much OS cache is available.
# Be generous — 70% of RAM is the conventional value.
effective_cache_size = 12GB

# Parallelism: pgvector HNSW scan is parallelizable.
max_parallel_workers_per_gather = 4
max_parallel_workers = 8
```

### 6.2 The HNSW build is a write-time cost

Building the HNSW index on `symbol_embeddings` for 100k rows takes ~30-60 seconds with the above settings. The embed stage should build it **after** the bulk insert, not during. Strategy:

1. `CREATE TABLE symbol_embeddings (...)` (no index yet)
2. Bulk insert via `COPY` (see §9) — 100k rows in ~5 seconds
3. `CREATE INDEX symbol_embeddings_hnsw_idx USING hnsw ...` — 30-60 seconds, takes an `ACCESS EXCLUSIVE` lock briefly

For incremental runs (a few hundred new embeddings), add the rows in any order; the index update is incremental.

---

## 7. The application-layer change

### 7.1 Stack

- **`asyncpg`** for connection pooling and async queries. Sync psycopg works too but the codebase is already async (extraction uses `AsyncOpenAI`).
- **SQLAlchemy 2.x Core** (not the ORM) for query construction. The codebase has 4-5 methods that are dynamic aggregations; SQLAlchemy Core's `select()` / `func.count()` / `case()` is much more readable than string-formatting SQL. **Do NOT use the ORM** — the graph data and the embedding column don't map cleanly to objects, and the 1-2 day ticket would balloon into a 2-week ticket.
- **`pgvector` Python client** (`pgvector>=0.3`) for the `vector` type round-trip. It registers the type codec with asyncpg so you can `await conn.fetch("SELECT embedding FROM ...") ` and get back a `Vector` object, not a string.
- **Alembic** for migrations. Standard, boring, works with asyncpg via `asyncpg` URL.

### 7.2 The interface boundary — `chronograph/store/store.py`

The clean migration play is to introduce a new `Store` interface that has the **same method names** as today's `MongoStore`, and refactor the call sites to depend on the interface, not the class.

```python
# chronograph/store/store.py (new)

from typing import Protocol, runtime_checkable

@runtime_checkable
class Store(Protocol):
    """All persistence goes through this. The Mongo implementation
    (mongo_store.py) and the Postgres implementation
    (pg_store.py) are interchangeable. The query engine, the
    ingestion pipeline, and the graph builder all depend on
    the interface, not the implementation.

    Methods are 1:1 with the public methods of MongoStore
    (chronograph/store/mongo_store.py). See that file for
    the method contracts.
    """
    # Projects / repos
    def upsert_project(self, project_id: str, data: dict) -> None: ...
    def get_project(self, project_id: str) -> dict | None: ...

    # Commits
    def upsert_commits(self, project_id: str, commits: list[dict]) -> int: ...
    def get_commits(self, project_id: str, limit: int = 0, skip: int = 0) -> list[dict]: ...
    def get_commit_count(self, project_id: str) -> int: ...
    def get_commit_authors(self, project_id: str) -> list[dict]: ...
    def get_activity_timeline(self, project_id: str, bucket: str = "month") -> list[dict]: ...

    # Members
    def upsert_members(self, project_id: str, members: list[dict]) -> int: ...
    def get_members(self, project_id: str) -> list[dict]: ...

    # Commit files
    def upsert_commit_files(self, project_id: str, commit_files: dict[str, list[dict]]) -> int: ...
    def get_commit_files(self, project_id: str, commit_sha: str) -> list[dict]: ...
    def get_all_commit_files(self, project_id: str) -> dict[str, list[dict]]: ...
    def compute_hotspots(self, project_id: str, limit: int = 20) -> list[dict]: ...
    def compute_bus_factor(self, project_id: str, min_changes: int = 5) -> list[dict]: ...

    # Source files
    def upsert_source_files(self, project_id: str, files: dict[str, str]) -> int: ...
    def get_source_file(self, project_id: str, path: str) -> str | None: ...
    def get_source_file_count(self, project_id: str) -> int: ...

    # Symbols
    def upsert_symbols(self, project_id: str, symbols: list[dict]) -> int: ...
    def get_symbols(self, project_id: str, file_path: str | None = None,
                    kind: str | None = None, limit: int = 0) -> list[dict]: ...
    def get_symbol(self, project_id: str, qualified_name: str) -> dict | None: ...
    def search_symbols(self, project_id: str, query: str,
                       kind: str | None = None, limit: int = 20) -> list[dict]: ...
    def get_most_complex(self, project_id: str, limit: int = 20,
                         exclude_patterns: list[str] | None = None) -> list[dict]: ...
    def trace_function(self, project_id: str, function_name: str) -> dict: ...
    def get_symbol_count(self, project_id: str) -> int: ...

    # Embeddings
    def upsert_embeddings(self, project_id: str, embeddings: list[dict]) -> int: ...
    def get_embedding_count(self, project_id: str) -> int: ...
    def get_all_embeddings(self, project_id: str) -> list[dict]: ...
    def search_embeddings(self, project_id: str, query_vector: list[float],
                          top_k: int = 10) -> list[dict]: ...   # NEW; the old get_all_embeddings was a workaround

    # Edges
    def create_edge(self, project_id: str, source_id: str, target_id: str,
                    rel_type: str, properties: dict | None = None,
                    valid_from: datetime | None = None) -> str: ...
    def create_edges_bulk(self, project_id: str, edges: list[dict]) -> int: ...
    def get_edges(self, project_id: str, source_id: str | None = None,
                  target_id: str | None = None, rel_type: str | None = None) -> list[dict]: ...
    def get_edge_count(self, project_id: str, rel_type: str | None = None) -> int: ...

    # Snapshots
    def upsert_snapshots_bulk(self, project_id: str, snapshots: list[dict]) -> int: ...
    def get_snapshot_count(self, project_id: str) -> int: ...

    # Stats
    def get_stats(self, project_id: str) -> dict: ...

    # Lifecycle
    def connect(self) -> bool: ...
    def disconnect(self) -> None: ...
```

**One new method**: `search_embeddings(query_vector, top_k)`. This is the **only** new behavior in the migration; everything else is a 1:1 port. The old `get_all_embeddings` (line 332 of `mongo_store.py`) is deleted; the query engine's in-Python cosine loop is replaced by a single call to `search_embeddings`.

### 7.3 Two implementations during cutover

`chronograph/store/mongo_store.py` stays. `chronograph/store/pg_store.py` is new. They both implement `Store`. The factory at `common/store_factory.py` (new) picks one based on env:

```python
def make_store() -> Store:
    backend = os.environ.get("CHRONOGRAPH_STORE", "mongo")
    if backend == "pg":
        from chronograph.store.pg_store import PgStore
        return PgStore(...)
    if backend == "mongo":
        from chronograph.store.mongo_store import MongoStore
        return MongoStore(...)
    raise ValueError(f"Unknown store backend: {backend}")
```

This is what makes the migration **zero-downtime**: flip `CHRONOGRAPH_STORE=pg` and the new code is live, with the old Mongo store as a documented fallback.

### 7.4 What's NOT in the migration

To keep scope honest, the migration does **not** include:

- Schema changes that would require data backfill. (The schema is shaped to accept the existing data; no field is mandatory that wasn't in Mongo.)
- Changing the embedding model. (Tracked in LORE-45.)
- The Neo4j code-intel emitter. (Tracked in LORE-49.)
- The dedup stage. (Tracked in LORE-50.)
- Refactoring `query_engine.chat()` to be an agent. (Tracked in §3 of `feature-audit-and-agentic-query-engine.md`.)

If a stakeholder asks "while you're at it, also X", the answer is: separate ticket, separate PR.

---

## 8. The implementation path (the actual work plan)

The migration is **6 phases**, ordered so that each phase is independently shippable and reversible. Total estimated effort: **8-12 engineering days**.

### Phase 0 — Schema and toolchain (1 day, blocks everything)

**Goal:** an empty Postgres database with the schema in §3, runnable locally.

1. Add `pgvector`, `asyncpg`, `sqlalchemy[asyncio]`, `pgvector` (Python), `alembic` to `pyproject.toml`. **Keep** `pymongo` for now.
2. `docker-compose.yml`: add a `postgres` service alongside the existing `neo4j` service. Pin to `postgres:16` and `pgvector/pgvector:pg16` (the official pgvector image). Add a `chronograph` database, a `lore` user, and a volume for data.
3. New `chronograph/store/schema.sql`: the schema from §3, runnable via `psql -f schema.sql`. **One file, no Alembic yet** — Alembic comes in Phase 5.
4. New `chronograph/store/connection.py`: an `asyncpg` connection pool factory. Reads `MONGODB_URI` and `POSTGRES_URI` from env; the Postgres one is new.
5. Smoke test: connect, run a `SELECT 1`, run a `SELECT '[1,2,3]'::vector;`, confirm both work.

**Done when:** `docker compose up` brings up both Postgres and Neo4j; `psql` to the chronograph DB shows all 8 tables exist; the Python connection factory returns a working pool.

**Reversibility:** zero impact. No existing code touches Postgres.

### Phase 1 — `PgStore` skeleton + read-only migration tool (2 days)

**Goal:** a working `PgStore` that can read from Postgres, but the application still writes to Mongo.

1. Implement `chronograph/store/pg_store.py` with all 20+ methods from §7.2. **Every method is a 1:1 port of the Mongo version**. Use SQLAlchemy Core for query construction.
2. Implement `search_embeddings(query_vector, top_k)` using the HNSW index.
3. Add a `bin/migrate_mongo_to_pg.py` script: reads every collection from Mongo, bulk-inserts into Postgres in `COPY` mode (10x faster than `INSERT` for large tables). Order: `repos` → `commits` → `commit_files` → `members` → `source_files` → `symbols` → `snapshots` → `edges` → `symbol_embeddings` (build the HNSW index after this batch). For 100k symbols, this should run in <5 minutes.
4. Verify: pick a known `project_id`, run `query_engine.get_stats(project_id)` against both backends, diff the results. All numbers should match.

**Done when:** the migration script round-trips a real test repo; `make_stats` is identical between Mongo and Postgres.

**Reversibility:** the script is one-way. To revert, drop the Postgres tables. Nothing in the app uses the Postgres store yet.

### Phase 2 — Write-side cutover, Mongo reads (2 days)

**Goal:** the application writes to Postgres. The query engine still reads from Mongo (for safety).

1. Add the `Store` Protocol (§7.2) to `chronograph/store/store.py`.
2. Add `make_store()` (§7.3). Default to `mongo` for now.
3. Change the call sites in `ingest_pipeline.py` and `graph_builder.py` to use `make_store()` instead of `MongoStore(...)` directly. **Do not change the read sites yet.**
4. Add a `CHRONOGRAPH_STORE=pg` env switch. When set to `pg`, `make_store()` returns a `PgStore`; the write paths then go to Postgres. Mongo is untouched on the read side.
5. Re-run the ingestion pipeline against a test repo with `CHRONOGRAPH_STORE=pg`. Verify the same row counts appear in Postgres.
6. Run the Mongo and Postgres stores in parallel for one day. Diff the writes.

**Done when:** the ingestion pipeline writes to Postgres, Mongo receives nothing new, row counts in Postgres match what Mongo would have received. The query engine still answers from Mongo.

**Reversibility:** flip `CHRONOGRAPH_STORE` back to `mongo`. The ingestion reverts to writing Mongo only. No data is lost.

### Phase 3 — Read-side cutover (2 days)

**Goal:** the query engine reads from Postgres. Mongo is the fallback.

1. Change `chronograph/query/query_engine.py:__init__` to take a `Store` instead of a `MongoStore`. This is a one-line constructor change; the methods don't change.
2. Add a `CHRONOGRAPH_QUERY_STORE` env var (default = the same as `CHRONOGRAPH_STORE`). Set to `mongo` to keep reading from Mongo for one more cycle.
3. Refactor the one inline aggregation in `query_engine.py:217` (the developer file expertise pipeline) to use SQL via a new `Store` method, `get_developer_file_expertise(project_id, limit=30)`. This is the **only** inline aggregation; all the others are on `MongoStore` and stay where they are.
4. Flip `CHRONOGRAPH_QUERY_STORE=pg`. Re-run the 5-round evaluation suite (`evaluation/round1_structural.py` etc.) against Postgres. Diff the answers against the Mongo baseline.
5. Update `chronograph/ingestion/ingest_pipeline.py` to add the new `search_embeddings` call in `semantic_search` (the only place the query engine reads all embeddings).

**Done when:** the query engine answers correctly from Postgres; the 5-round evaluation suite passes; the in-Python cosine loop is gone (replaced by `search_embeddings`).

**Reversibility:** flip `CHRONOGRAPH_QUERY_STORE=mongo`. The query engine reverts. The Postgres store keeps receiving writes.

### Phase 4 — Shadow mode + cutover (1 day)

**Goal:** both stores live; one is the source of truth; the other is the read fallback.

1. Both stores receive writes. A daily diff job compares row counts and per-key hashes. Any drift is alerted.
2. The query engine is configured to read from Postgres. If a query times out or errors, it transparently retries against Mongo. The fallback is silent in normal operation; visible in logs.
3. After 7 days of clean diffs, Postgres becomes the sole source of truth. Mongo is no longer written to.
4. Run the integration tests one more time. Commit the cutover.

**Done when:** Postgres is the source of truth. Mongo receives no new writes. The query engine reads Postgres with Mongo as a documented fallback.

**Reversibility:** flip the env var to write both stores again, replay the migration. (Mild complexity — fine for a 7-day window, bad as a permanent state.)

### Phase 5 — Mongo removal + Alembic (1-2 days)

**Goal:** Mongo is gone. Schema is version-controlled.

1. New `chronograph/store/migrations/` directory with Alembic. Initial migration = the schema in §3.
2. `pyproject.toml`: drop `pymongo`. Update `docker-compose.yml` to remove the Mongo service (or keep it commented as a fallback for one release).
3. Delete `chronograph/store/mongo_store.py`. The `make_store()` factory is simplified to construct `PgStore` directly.
4. Delete the migration script from Phase 1.
5. Update `docs/modularity-phases/` with the "Mongo removed" step.
6. Update the architecture docs.

**Done when:** the only `import pymongo` in the repo is in a comment. `docker compose up` brings up Postgres + Neo4j (still needed for the Gmail knowledge graph) and nothing else.

**Reversibility:** this phase is the point of no return. Before Phase 5, you can roll back. After, you'd need to restore Mongo from a backup and replay the migration.

### Phase 6 — Index tuning + observability (1 day, optional)

**Goal:** the system is ready for production load.

1. `pg_stat_statements` enabled. A `bin/slow_queries.py` script that flags queries over 100ms.
2. `EXPLAIN ANALYZE` output captured for each of the 5 critical queries (§3.12) on a 100k-symbol, 50k-commit test dataset. Baseline numbers checked in.
3. HNSW `ef_search` tuned per query type. (The default 40 is fine; raise to 100 only if recall is unsatisfactory.)
4. Add Prometheus counters for store method calls and latencies. (Optional but useful for the agent layer's introspection.)

**Done when:** the system has known-good performance baselines and observability.

---

## 9. The migration data flow

The migration script from Phase 1, in detail:

```
1. Connect to Mongo + Postgres.
2. For each (project_id) in Mongo `projects`:
   a. INSERT INTO repos ... FROM Mongo `projects` doc.
      Map Mongo `gitlab_id` → `external_id` and `source: "gitlab"`.
   b. COPY commits FROM Mongo cursor:
        SELECT sha, short_id, title, message, author_name, author_email,
               ISO_to_TS(authored_date), ISO_to_TS(committed_date),
               parent_ids, stats.additions, stats.deletions, web_url
        FROM Mongo commits
   c. COPY commit_files FROM Mongo cursor:
        For each (commit_sha, files[]) in commit_files:
            For each file in files:
                INSERT (commit_sha, file.path, file.old_path, file.new_file, file.deleted_file)
   d. COPY members FROM Mongo cursor (raw, no transformation).
   e. For each source_file in Mongo:
        INSERT (file_path, content, sha256(content), byte_size, line_count).
        Skip if (ref, path) already exists.
   f. For each symbol in Mongo:
        INSERT (file_path, qualified_name, name, kind, language,
                start_line, end_line, signature, body_hash,
                params[], calls[], imports[], decorators[],
                complexity_*).
   g. COPY edges from Mongo (transform edge_id format if needed;
      the Mongo format is already 'edge:<hash>' so no transform).
   h. COPY snapshots from Mongo (transform sha[:12] check; same).
   i. COPY symbol_embeddings from Mongo.
      Cast each vector to `vector(1536)`; reject any with wrong dim.
3. After all projects: CREATE INDEX ON symbol_embeddings USING hnsw.
4. ANALYZE; re-run for query planner stats.
```

The `COPY` step is where the speedup comes from. `asyncpg` has a `copy_records_to_table` method that streams Python tuples directly into Postgres's COPY protocol — 50-100k rows/sec, vs. ~1k rows/sec for `INSERT`. For 1M rows total, that's 20 seconds vs. 17 minutes.

### 9.1 The `vector` round-trip gotcha

The Mongo `vector` field is stored as a Python list of floats. In Postgres it's a `vector(1536)`. The round-trip is:

```python
# asyncpg codec registration (one-time at startup)
from pgvector.asyncpg import register_vector
await conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
await register_vector(conn)   # registers the codec

# Then any list[float] bound to a vector param is serialized correctly
await conn.fetch("SELECT $1::vector", [0.1, 0.2, ...])

# And any vector column is returned as a Vector object (acts like a list)
row = await conn.fetchrow("SELECT embedding FROM symbol_embeddings LIMIT 1")
vec = row['embedding']    # pgvector.asyncpg.Vector
print(len(vec))           # 1536
```

This is the one piece of plumbing that's not obvious. Get it right once and the rest of the code is just SQL.

---

## 10. Risk register and mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| HNSW index build is slow on large projects (>500k symbols) | medium | medium | Switch to IVFFlat for that one project; or accept the 10-min build. Document the threshold. |
| Existing Mongo data has missing/null fields that break NOT NULL constraints | high | low | Make all the fields that the old Mongo code wrote as nullable, nullable. The schema uses NOT NULL only where the application code already guarantees a value. |
| Embedding dimension changes mid-migration (someone runs LORE-45 in the middle) | low | high | Pin the dimension in the schema; require an explicit migration to change. The `vector(N)` type is part of the column type and changing it is a destructive DDL. |
| A query in the codebase uses a Mongo feature Postgres doesn't support (e.g. `$expr` with JavaScript) | medium | medium | Audit the 8 aggregation pipelines (§5) before starting. None use JS. If one does, rewrite in SQL. |
| The `pymongo` removal in Phase 5 breaks a third-party script that nobody owns | low | low | `rg "pymongo\|MongoClient"` in Phase 5. If anything else uses it, port it or document the dependency. |
| The agent layer (future ticket) needs Neo4j Cypher, but the new code-intel emitter isn't built yet | high | low | Out of scope. The Postgres store has all the data; LORE-49 is the follow-up. |
| The recursive CTE for graph traversal is slow on dense graphs | medium | medium | Add a `path` column to the recursive CTE to prevent re-visiting; cap depth at 5 hops. Test on a real graph. |
| The migration script double-writes because the old `upsert_*` methods don't `ON CONFLICT DO NOTHING` on Postgres | high | low | Use `INSERT ... ON CONFLICT (pk) DO NOTHING` for idempotency. Verify in the migration script. |
| An ops engineer runs `docker compose down` and loses Postgres data | low | high | Same backup story as before: volume mount, daily logical backups via `pg_dump`. Document in `docs/polling-aws.md` and `docs/polling-onprem.md`. |

---

## 11. What's deferred (out of scope)

To keep the migration ticket small enough to ship, the following are explicitly out of scope and tracked as follow-ups:

- **LORE-45** Embedding model unification (1536 → 2048 NVIDIA NeMo).
- **LORE-49** Neo4j code-intel emitter.
- **LORE-50** Cross-source dedup stage (entity resolution across GitHub/GitLab).
- **LORE-52** Observability / Prometheus counters.
- **The dedup graph queries** (e.g. "find all symbols that are `ALIAS_OF` each other across sources"). The schema supports them via the recursive CTE, but the data isn't there yet.
- **Multi-tenant isolation** beyond what `repo_url` already gives. If a future requirement adds per-org row-level security, that's a separate ticket using Postgres RLS.
- **Migration to PG 17** (when it ships). PG 16 is the target today.

---

## 12. Acceptance criteria (definition of "done" for the whole migration)

1. **No `import pymongo`** in the repo. `rg "pymongo"` returns no hits in `chronograph/`, `infra/`, `runners/`, `common/`.
2. **All 5 critical queries** (§3.12) return identical results against Mongo and Postgres on the same test data.
3. **All 8 aggregations** (§5) are translated and pass a numeric diff against the Mongo versions.
4. **`semantic_search`** in `query_engine.py:80` uses `search_embeddings` (one call to Postgres, returns top_k results). The pure-Python cosine loop is deleted.
5. **`trace_function`** uses the GIN index on `symbols.calls` (verified via `EXPLAIN ANALYZE`).
6. **All 5 files** that import `MongoStore` now import `Store` (the Protocol) and use `make_store()`.
7. **5-round evaluation suite** passes against Postgres (`evaluation/round1_structural.py` through `round5_final_validation.py`).
8. **Migration script** round-trips a 100k-symbol test repo in <5 minutes.
9. **HNSW index** built; `EXPLAIN ANALYZE` shows it's used (`Index Scan using symbol_embeddings_hnsw_idx`).
10. **One week of shadow mode** with zero row-count or per-key diffs between Mongo and Postgres before Phase 5 cutover.

When all 10 are checked, the migration is done.

---

## 13. Summary

- **What:** port the chronograph code-intelligence store from MongoDB to PostgreSQL 16 + pgvector. The Gmail knowledge graph stays in Neo4j.
- **Why:** real vector index, real joins, real graph traversal via recursive CTE, one operational surface for code-intel.
- **How:** 6 phases, 8-12 days. Each phase is shippable and reversible until Phase 5.
- **Schema:** 8 tables (`repos`, `commits`, `commit_files`, `members`, `source_files`, `symbols`, `symbol_embeddings`, `edges`, `snapshots` — that's actually 9, the doc is right) in the `chronograph` schema. HNSW index on `symbol_embeddings.embedding`. GIN index on `symbols.calls`. Partial indexes where they help. Deterministic TEXT primary keys that match the existing entity-ID conventions so the Neo4j emitter (LORE-49) and the query engine don't need translation.
- **App layer:** a `Store` Protocol, a `PgStore` implementation, a `make_store()` factory, an env-driven cutover. The query engine changes by one constructor line plus a new `search_embeddings` call.
- **Migration script:** Python, asyncpg `COPY` for speed, ~5 minutes for 100k symbols.
- **Risk:** low. The aggregations translate cleanly. The HNSW build is the one known cost. The only exotic bit is the `pgvector` asyncpg codec, registered once at startup.
- **Done when:** the 10 acceptance criteria in §12 are all checked.

Full doc: `/home/raghav/Desktop/lore-ingestion/docs/mongo-to-postgres-migration.md`
