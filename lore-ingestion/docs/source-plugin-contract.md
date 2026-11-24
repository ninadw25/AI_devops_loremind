> **Historical (as of 2026-09-25)** — superseded by `docs/CODE_INTEL_REPLACEMENT_REQUIREMENTS.md` (this contract is Mongo-based; most modules it names were never built). Kept for context; do not treat as current.

# Source Plugin Contract — how a code-host (GitHub, GitLab, …) becomes a queryable data source

**Status:** proposed
**Audience:** anyone adding a new code-host connector (GitHub first, then Bitbucket, Gerrit, Phabricator, SourceHut, …)
**Branch audited:** `arnavgupta2468/lor-38-…-incremental` @ `afe31e0`
**Date:** 2026-06-01

---

## 0. Why this doc exists

The query engine (`chronograph/query/query_engine.py`) only knows how to read
from MongoDB. The Gmail/knowledge side of the repo only knows how to write
graph triples to Neo4j via `builders/unified_builder.py`. Today, **only the
GitLab connector exists for code intelligence**, and it writes to MongoDB
only. There is no GitHub connector (the `GitHubCodeFetcher` class is dead
code — it has no orchestrator). Anyone adding GitHub would either:

1. **Reinvent the schema** every time → three connectors later, three
   different `symbols` collection shapes, three different edge types
   named slightly differently, query engine has to special-case each
   one, the LLM agent layer has to know which shape it's reading.
2. **Implement this contract** → GitLab, GitHub, and any future source
   write through the same shapes, the query engine reads one shape, the
   Neo4j upload is one pass, adding a new source is roughly 1 file +
   env-var changes.

This document specifies the contract. It is grounded in the shapes the
existing GitLab code actually produces (verified by reading
`chronograph/ingestion/ingest_pipeline.py`, `graph_builder.py`,
`symbol_extractor.py`, `chronograph/store/mongo_store.py`,
`chronograph/entities.py`, `common/schemas_extraction.py`, and
`builders/unified_builder.py`).

### What this is, what it isn't

- **This is a data-shape and processing contract** — every connector must
  produce the same set of Mongo collections with the same fields, and the
  same set of Neo4j node/edge labels, so the query engine is
  source-agnostic.
- **This is not a re-architecture proposal.** The existing GitLab code
  conforms to the Mongo half of this contract already. The Neo4j half
  does not exist for code yet — the contract below is what it
  **should** conform to, and what the GitHub connector should implement
  from day one. The "make it conform" ticket for GitLab is a separate,
  follow-up.
- **This is not a refactor of the query engine.** The agentic
  transformation (Planner / Executor / Synthesizer) is a separate
  proposal in `docs/feature-audit-and-agentic-query-engine.md`. This
  doc is the input contract that agent layer will read from.

---

## 1. The contract in one diagram

```
                        ┌────────────────────────────────────────────┐
                        │            EXTERNAL SOURCE                 │
                        │   GitLab API  /  GitHub API  /  Bitbucket  │
                        │   commits, files, members, diffs, etc.     │
                        └─────────────────────┬──────────────────────┘
                                              │
                                              ▼
            ┌─────────────────────────────────────────────────────────────┐
            │  PLUGIN  (one per source, conforms to the contract)         │
            │  infra/gitlab/   infra/github/   infra/bitbucket/   …       │
            │  • implements SourcePlugin interface (5 methods, §3)        │
            │  • performs HTTP / OAuth / rate-limit handling internally   │
            │  • emits raw, source-native events onto an internal bus     │
            └─────────────────────┬───────────────────────────────────────┘
                                  │
                                  ▼
            ┌─────────────────────────────────────────────────────────────┐
            │  NORMALIZER  (per source; lives inside the plugin)          │
            │  raw API response → list[NormalizedRecord] (§4)             │
            │  This is the only place source-specific shape exists.       │
            │  Downstream stages are source-agnostic.                     │
            └─────────────────────┬───────────────────────────────────────┘
                                  │
                                  ▼
            ┌─────────────────────────────────────────────────────────────┐
            │  SHARED PROCESSING PIPELINE  (source-agnostic)              │
            │                                                             │
            │  Stage A — Parse         tree-sitter symbol extraction      │
            │  Stage B — Diff          semantic diff, classification      │
            │  Stage C — Graph build   entity / snapshot / edge upsert    │
            │  Stage D — Embed         2048-d NVIDIA NeMo                 │
            │  Stage E — Dedup         (entity resolution across sources) │
            │  Stage F — Cypher emit   Neo4j nodes + edges + embeddings   │
            │                                                             │
            └─────────────────────┬───────────────────────────────────────┘
                                  │
                ┌─────────────────┴──────────────────┐
                ▼                                    ▼
   ┌────────────────────────────┐    ┌────────────────────────────────┐
   │  MongoDB (code-intel)      │    │  Neo4j (graph + vector index)  │
   │  ─ projects                │    │  ─ CodeEntity                  │
   │  ─ commits                 │    │  ─ Snapshot                    │
   │  ─ members                 │    │  ─ Developer                   │
   │  ─ commit_files            │    │  ─ Project / Repo              │
   │  ─ source_files            │    │  ─ :KnowledgeNode  (cross-type │
   │  ─ symbols                 │    │       vector search)           │
   │  ─ edges  (with rel_type)  │    │  ─ embeddings per node         │
   │  ─ snapshots               │    │                                │
   │  ─ symbol_embeddings       │    │  Used by the Gmail/knowledge   │
   │                            │    │  query side via lore-backend.  │
   │  Read by chronograph/      │    │                                │
   │  query/query_engine.py     │    │                                │
   └────────────────────────────┘    └────────────────────────────────┘
                │                                    │
                └─────────────────┬──────────────────┘
                                  ▼
                  ┌───────────────────────────────┐
                  │  QUERY LAYER (source-agnostic)│
                  │  • chronograph/query_engine.py│  ← Mongo
                  │  • lore-backend GraphRAG       │  ← Neo4j
                  │  • future agent layer          │
                  └───────────────────────────────┘
```

Two physical stores, one logical data model. The Mongo side is the
**operational** store: hot for the query engine, indexable by any field,
holds raw source code. The Neo4j side is the **graph** store: optimized
for traversal, vector-indexed for semantic search, the surface the
LoreMind LLM pipeline queries.

---

## 2. Existing data the contract must describe

This is what the **current GitLab pipeline** produces. The contract is
derived from these shapes (and is a strict superset — nothing already
written by GitLab breaks, and the Neo4j mapping is a *new* target, not a
replacement).

### 2.1 Mongo collections actually written today

From `chronograph/store/mongo_store.py` (lines 87-410) and the call
sites in `chronograph/ingestion/ingest_pipeline.py:177-280` and
`chronograph/ingestion/graph_builder.py`:

| Collection | Doc shape (verified) | Written by |
|---|---|---|
| `projects` | `{project_id, name, full_name, description, visibility, default_branch, web_url, created_at, source, gitlab_id, updated_at}` (`source` is hard-coded `"gitlab"` today — the contract generalises to `"github"` etc.) | `upsert_project()` |
| `commits` | `{project_id, sha, short_id, title, message, author_name, author_email, authored_date, committed_date, parent_ids, stats{additions,deletions,total}, web_url}` (classification: feature/bugfix/refactor/perf/docs/test/style/chore/merge — set by `chronograph/diffing/change_classifier.classify_commit(message)`) | `upsert_commits()` + `graph_builder._build_snapshots()` |
| `members` | raw GitLab member objects, tagged with `project_id` | `upsert_members()` |
| `commit_files` | `{project_id, commit_sha, files: [{path, old_path, new_file, deleted_file}]}` | `upsert_commit_files()` |
| `source_files` | `{project_id, path, content}` — actual source text | `upsert_source_files()` |
| `symbols` | `{project_id, name, kind, qualified_name, file_path, language, start_line, end_line, signature, body_hash, params[], calls[], imports[], decorators[], complexity{cyclomatic,cognitive,max_nesting,lines,params}}` (kind ∈ {`function`, `class`, `method`, `module`} — module is filtered out before insert) | `upsert_symbols()` (data constructed in `ingest_pipeline.py:300-323`) |
| `edges` | `{project_id, source_id, target_id, rel_type, valid_from, valid_to, properties, edge_id}` (rel_type ∈ {`NEXT_SNAPSHOT`, `AUTHORED_BY`, `CALLS`, `CONTAINS`, `IMPORTS`, `COLLABORATES_WITH`}) | `create_edge()` / `bulk_create_edges()` |
| `snapshots` | `{project_id, commit_sha, timestamp, author_email, author_name, message, classification, …}` (one per commit) | `graph_builder._build_snapshots()` |
| `symbol_embeddings` | `{project_id, symbol_id, qualified_name, file_path, kind, text, vector: list[float][1536]}` (OpenRouter `text-embedding-3-small`, **NOT** the 2048-d NeMo used by Gmail) | `upsert_embeddings()` |

`edge_id` is deterministic: `f"edge:{short_hash(f'{src}:{tgt}:{rel_type}:{valid_from}', length=16)}"`. `edge` documents are
**upserted by `(source_id, target_id, rel_type, valid_from)`** — so a
re-ingest of the same commit produces the same edge, idempotently.

### 2.2 ID naming conventions (the part that has to be stable)

The whole point of the contract is that **node IDs are stable across
sources** so cross-source queries work. The current code uses these
patterns:

| ID pattern | Meaning | Source |
|---|---|---|
| `entity:{repo_url}:{file_path}:{qualified_name}` | a code symbol (function, class, method) | `chronograph/entities.py:57` |
| `snap:{repo_url}:{commit_sha[:12]}` | a commit (snapshot) | `chronograph/entities.py:39` |
| `ver:{entity_id}:{snapshot_id}` | a specific version of a symbol at a commit | `chronograph/entities.py:78` |
| `dev:{email}` | a developer | `chronograph/ingestion/graph_builder.py:131` |
| `edge:{short_hash(src:tgt:rel_type:valid_from)}` | an edge | `chronograph/entities.py:98` |
| `f"{project_id}:{file_path}:{qualified_name}"` (no prefix) | a symbol id used in `symbol_embeddings` — **inconsistent with `entity:` prefix above, noted as a bug** | `chronograph/ingestion/ingest_pipeline.py:367` |
| Gmail knowledge graph: `person:{short_hash}`, `dec:{short_hash}`, `fact:{short_hash}`, `task:{short_hash}`, `evt:{short_hash}`, `topic:{short_hash}`, `org:{short_hash}` | knowledge triples (separate namespace from code-intel) | `common/hashing.py` + `builders/unified_builder.py` |

**Contract requirement:** the `repo_url` slot in the entity / snapshot /
version IDs must be a stable identifier. Today the GitLab code uses
`project_id` (e.g. `makarandKate/gmc-server`) as `repo_url`. The
contract mandates it be a **canonical URL** (e.g.
`https://gitlab.com/makarandKate/gmc-server`) so the same logical repo
on GitLab vs. a GitHub mirror vs. an on-prem Gitea instance all
generate IDs that are **disambiguated by host**. A repo imported from
two hosts is a *different* node, which is correct.

---

## 3. The plugin interface (Python)

The existing GitLab code does not have a `SourcePlugin` base class — the
plumbing is implicit. The contract formalises it. The interface lives
in `chronograph/plugins/base.py` (new file).

```python
from typing import AsyncIterator, Protocol, runtime_checkable
from datetime import datetime

@runtime_checkable
class SourcePlugin(Protocol):
    """Every code-host connector implements this. The contract is small:

    The plugin owns its own auth, rate-limiting, pagination, and
    source-specific quirks. It emits NormalizedRecord instances (defined
    in §4) onto the bus. Downstream stages are 100% source-agnostic.
    """

    name: str  # e.g. "gitlab", "github", "bitbucket"

    async def list_repos(self) -> list[RepoRef]:
        """Discover all repos this plugin is configured to ingest.
        A RepoRef is the unit of polling — one polling cycle handles
        all repos returned by this method.
        """
        ...

    async def fetch_commits(
        self, repo: RepoRef, since: datetime | None, until: datetime | None
    ) -> AsyncIterator[NormalizedCommit]:
        """Yield commits in chronological order. `since=None` means
        full backfill. Must be resumable: the same (repo, since, until)
        should produce the same commits even after a process restart
        (i.e. the plugin should page through the API correctly).
        """
        ...

    async def fetch_commit_files(
        self, repo: RepoRef, sha: str
    ) -> list[NormalizedFileChange]:
        """Files touched by a single commit. The poller can call this
        lazily — it only needs the diff for the changed-files list
        during incremental runs.
        """
        ...

    async def fetch_source_files(
        self, repo: RepoRef, ref: str, paths: list[str] | None
    ) -> dict[str, str]:
        """Fetch source content. `paths=None` means all source files
        in the repo tree. For incremental runs, `paths` is the union
        of `new_path` from each commit's `fetch_commit_files`.

        Returns {file_path: content} where content is the raw text.
        """
        ...

    async def fetch_members(
        self, repo: RepoRef
    ) -> list[NormalizedMember]:
        """All humans with access to the repo. Source-agnostic shape
        (login, name, email, role, avatar_url, web_url).
        """
        ...
```

### 3.1 Concrete implementations to ship

| Plugin | Status | Notes |
|---|---|---|
| `infra/gitlab/` (already exists, partially) | **conform** | The current `chronograph/ingestion/ingest_pipeline.py:39-100` does the right thing structurally; refactor it to emit `NormalizedRecord` instances and have `GitLabCodeFetcher` (already a class) implement the `SourcePlugin` protocol. After this, the polling code in `chronograph/gitlab_polling.py` is a generic 5-line wrapper. |
| `infra/github/` (does not exist) | **build** | Mirror of `infra/gitlab/`. Two non-trivial differences: (a) the GitHub Trees API has a `truncated` flag that must be checked (`code_fetcher.py:139` currently does NOT check it), and (b) the unauthenticated `raw.githubusercontent.com` URL must be replaced with `api.github.com/repos/{owner}/{repo}/contents/{path}` with the `Authorization: token …` header for private repos. The pre-refactor code at commit `2ac679ab` had a full `_fetch_github_metadata()` worth porting. |
| `infra/bitbucket/`, `infra/sourcehut/`, `infra/gitea/` | future | trivial — they all expose REST with the same general shape. |

### 3.2 The 4 "must not" rules for any plugin

These are the things that, if violated, will break data consistency:

1. **A plugin MUST NOT write to MongoDB or Neo4j directly.** It emits
   `NormalizedRecord` instances only. All persistence is the shared
   pipeline's job. (This is what makes the query engine source-agnostic.)
2. **A plugin MUST NOT change the canonical ID format for the entities
   it emits.** It can *populate* the fields that compose the ID
   (`repo_url`, `commit_sha`, `qualified_name`), but it must not
   redefine what those fields mean.
3. **A plugin MUST NOT silently swallow non-200 responses.** Today
   `_fetch_gitlab_metadata` (lines 186-244) checks `resp.status_code`
   for the commits endpoint but not for the project or members
   endpoints, and a 401 gets logged at warning level while the cycle
   *still saves a checkpoint*. New plugins must raise on any
   non-2xx response, so the shared retry/dead-letter machinery
   (to be added — see §7) handles it.
4. **A plugin MUST be idempotent under re-polling.** Given the same
   `(repo, since)`, two runs must produce the same NormalizedRecord
   stream. Upserts in the store layer handle the dedup; the plugin's
   job is to not invent new fields on each run.

---

## 4. The NormalizedRecord shapes (the contract payload)

These are Pydantic models. They live in `common/source_records.py`
(new file). Every plugin must produce only these types.

```python
from datetime import datetime
from typing import Literal, Optional
from pydantic import BaseModel, Field
from common.hashing import short_hash

class RepoRef(BaseModel):
    """A pointer to a single repository on a single source."""
    source: Literal["gitlab", "github", "bitbucket", "gitea", "sourcehut"]
    repo_url: str          # canonical, e.g. "https://gitlab.com/makarandKate/gmc-server"
    external_id: str       # the source's own ID, e.g. GitLab project ID "59760967"
    full_name: str         # "owner/repo" form

class NormalizedCommit(BaseModel):
    """One commit on one repo, normalized from any source."""
    repo: RepoRef
    sha: str
    short_id: str
    title: str
    message: str
    author_name: str
    author_email: str
    authored_date: datetime
    committed_date: datetime
    parent_shas: list[str] = []
    stats: dict = {}  # {additions, deletions, total}
    web_url: str = ""

    @property
    def snapshot_id(self) -> str:
        return f"snap:{self.repo.repo_url}:{self.sha[:12]}"

    @property
    def classification(self) -> str:
        # Shared classifier, source-agnostic
        from chronograph.diffing.change_classifier import classify_commit
        return classify_commit(self.message)


class NormalizedFileChange(BaseModel):
    """One file in one commit's diff."""
    repo: RepoRef
    commit_sha: str
    path: str
    old_path: str = ""
    new_file: bool = False
    deleted_file: bool = False
    renamed_file: bool = False


class NormalizedSourceFile(BaseModel):
    """Actual source text for one file at one ref."""
    repo: RepoRef
    ref: str
    path: str
    content: str
    language: Optional[str] = None  # set by parser stage if plugin doesn't detect


class NormalizedMember(BaseModel):
    """One human who can see the repo."""
    repo: RepoRef
    login: str
    name: str
    email: str = ""
    role: Literal["owner", "maintainer", "developer", "reporter", "guest", "unknown"] = "unknown"
    avatar_url: str = ""
    web_url: str = ""
```

That's it. Five records. The whole shared pipeline downstream operates
on these five shapes regardless of source.

---

## 5. The shared processing pipeline (source-agnostic)

A plugin emits records. Everything from this point on is in
`chronograph/processing/` (new) and works on `NormalizedRecord` only.

### 5.1 Stage A — Parse

`chronograph/processing/parse.py` (new). Walks each
`NormalizedSourceFile.content`, runs tree-sitter, emits
`list[Symbol]` from `chronograph/entities.py`. Already exists in
`chronograph/parsing/symbol_extractor.py:69-79` — wrap it.

**Input contract:** `dict[path, content]` (which is exactly what
`fetch_source_files` already returns).

**Output contract:** `list[CodeEntity]` (Pydantic, from
`chronograph/entities.py:50-65`). The `repo_url` slot in
`CodeEntity.id` is set from `NormalizedSourceFile.repo.repo_url`.

**Language detection:** use `chronograph/parsing/languages.detect_language(path)`.
Plugins should not pre-detect.

### 5.2 Stage B — Diff

`chronograph/processing/diff.py` (new). For each commit, compare
the symbol set at the parent commit to the symbol set at this
commit. Emit `list[DiffRecord]` from
`chronograph/diffing/differ.py:50-100` (already exists).

**Input contract:** `[(commit_sha, list[CodeEntity])]`.

**Output contract:** `list[DiffRecord]` with these variants:
`ADDED`, `REMOVED`, `MODIFIED`, `RENAMED`. Each carries the
before/after `entity_id` so temporal edges can be created.

### 5.3 Stage C — Graph build

`chronograph/processing/graph_build.py` (new, refactor of
`chronograph/ingestion/graph_builder.py:32-280`). Given a batch
of commits + their files + their symbols + their diffs, write:

- `projects` doc (idempotent upsert)
- `commits` docs (one per commit, classification pre-filled)
- `commit_files` docs
- `members` docs
- `source_files` docs
- `symbols` docs
- `snapshots` docs (one per commit, with classification)
- `edges` docs:
  - `NEXT_SNAPSHOT` (snapshot → snapshot) — temporal chain
  - `AUTHORED_BY` (entity → dev) — derived from
    `commit.author_email` and `commit_files`
  - `CALLS` (entity → entity) — derived from `symbol.calls`
  - `CONTAINS` (class → method) — derived from
    `qualified_name` hierarchy
  - `IMPORTS` (file → file) — derived from `symbol.imports`
  - `COLLABORATES_WITH` (dev ↔ dev) — derived from
    co-changed files in a window

This is the data shape the **query engine reads today** (see
`chronograph/query/query_engine.py:309-380` for examples). The
contract guarantees that the query engine, given any repo ingested
by any conforming plugin, sees the same edge names, the same
id-prefix conventions, and the same symbol fields. A GitHub-fed
repo and a GitLab-fed repo are indistinguishable to the query
engine.

### 5.4 Stage D — Embed

`chronograph/processing/embed.py` (new, refactor of
`chronograph/ingestion/ingest_pipeline.py:336-427`).
Embed symbols. **Today's code embeds 1536-d OpenRouter vectors**;
the Gmail/knowledge side embeds 2048-d NVIDIA NeMo vectors into
Neo4j. These are two different embedding models, which is a
**consistency bug** the contract should fix:

**Contract requirement:** the canonical embedding model is the
**2048-d NVIDIA NeMo Retriever** (the one
`pipeline/embed_knowledge.py` and `builders/unified_builder.py`
already use). The chronograph side should migrate to it.
The OpenRouter model stays in `.env` as a fallback only.

**Why this matters for the agent layer:** the lore-backend LLM
pipeline queries the 2048-d NeMo vectors in Neo4j. If
chronograph code-intel uses a *different* embedding model, you
can't do cross-source semantic search. The contract mandates one
model.

### 5.5 Stage E — Dedup

`chronograph/processing/dedup.py` (new, refactor of
`pipeline/deduplicator.py`). Cross-source entity resolution:
detect the case where a repo is mirrored on GitHub and GitLab,
or where a developer is the same person across sources, or where
two functions with the same name in different repos are
semantically the same.

**Input:** all entities (CodeEntity, Snapshot, Developer) across
all sources for one org.

**Output:** groups of "same logical thing" with one canonical ID.
The other IDs in the group become `ALIAS_OF` edges.

**The Gmail-side person registry (`pipeline/person_registry.py`)
already does this for humans across Gmail + GitLab.** The
chronograph side needs the same cross-source merge for
CodeEntity and Developer.

### 5.6 Stage F — Cypher emit

`chronograph/processing/cypher_emit.py` (new, refactor of
`builders/unified_builder.py:271-345`). Reads everything in
Mongo and emits a Neo4j Cypher file (or runs it directly via the
Python driver).

**Node labels in the Neo4j graph (the part that does not exist
today for code-intel, but should):**

| Node label | Source | Properties | Vector-indexed? |
|---|---|---|---|
| `CodeEntity` (function/class/method) | `chronograph` | `entity_id, kind, qualified_name, file_path, language, complexity{...}, source, repo_url` | yes (`embedding` property) |
| `Snapshot` (commit) | `chronograph` | `snapshot_id, repo_url, commit_sha, author_email, author_name, timestamp, message, classification, source` | no |
| `CodeEntityVersion` (entity @ snapshot) | `chronograph` | `version_id, entity_id, snapshot_id, start_line, end_line, body_hash, signature` | no |
| `Developer` | `chronograph` + `Gmail` | `dev_id, email, name, source` (cross-source merge) | yes |
| `Repo` (project) | `chronograph` | `repo_url, source, external_id, full_name, default_branch, web_url` | no |
| `:KnowledgeNode` (secondary label, cross-type vector) | `Gmail` only for now | `knowledge_id, type ∈ {Decision, Task, Fact, Event}, date, description, source` | yes |

**Edge labels in the Neo4j graph:**

| Edge | From | To | Source |
|---|---|---|---|
| `NEXT_SNAPSHOT` | `Snapshot` | `Snapshot` | chronograph (commit chain) |
| `HAS_VERSION` | `CodeEntity` | `CodeEntityVersion` | chronograph |
| `VERSION_OF` | `CodeEntityVersion` | `Snapshot` | chronograph |
| `AUTHORED_BY` | `CodeEntityVersion` | `Developer` | chronograph (per-commit) |
| `CALLS` | `CodeEntity` | `CodeEntity` | chronograph |
| `CONTAINS` | `CodeEntity` (class) | `CodeEntity` (method) | chronograph |
| `IMPORTS` | `CodeEntity` | `CodeEntity` | chronograph |
| `COLLABORATES_WITH` | `Developer` | `Developer` | chronograph |
| `ALIAS_OF` | `CodeEntity`/`Developer` | `CodeEntity`/`Developer` | dedup stage |
| `WORKS_AT` | `Developer` | `Organization` | Gmail (already exists) |
| `DECIDED` | `Developer` | `Decision` | Gmail (already exists) |
| `BECAUSE` | `Decision` | `Fact` | Gmail (already exists) |
| `ASSIGNED_TO` | `Task` | `Developer` | Gmail (already exists) |
| `REGARDING` | `Fact`/`Decision`/`Task` | `Topic` | Gmail (already exists) |
| `SUBJECT_OF` | `Event` | `Topic` | Gmail (already exists) |
| `PARTICIPATED_IN` | `Developer` | `Event` | Gmail (already exists) |
| `MENTIONS` | `Fact`/`Decision` | `Developer`/`Organization` | Gmail (already exists) |
| `STATED_BY` | `Fact`/`Decision` | `Developer` | Gmail (already exists) |

**The two namespaces are separate but share `Developer` and
`Organization` nodes** — that's the join point. A query like "who
made the API breaking change in `parseUserInput` and what was
the team's decision about it?" can traverse
`CodeEntity → AUTHORED_BY → Developer → DECIDED → Decision` across
both pipelines.

**Embedding model requirement:** the `embedding` property on
`CodeEntity`, `Developer`, and `:KnowledgeNode` is the same
**2048-d NVIDIA NeMo** vector, so a single vector index can
serve "find similar code" and "find similar decisions" with the
same similarity function.

---

## 6. What this means for the query engine

The query engine's `chronograph/query/query_engine.py` (472 LOC)
already reads from MongoDB and is structurally source-agnostic
— it never inspects a `source: "gitlab"` field; it just queries
by `project_id`, `qualified_name`, etc. So the existing
methods work on a GitHub-ingested repo **unchanged** once the
GitHub plugin conforms to the contract above.

Two small changes the contract should drive in the query engine
to make it future-proof:

1. **Add a `source: str` parameter to every public method**
   (`get_hotspots`, `get_bus_factor`, `semantic_search`, etc.),
   defaulting to `None` meaning "all sources." Today the
   methods filter by `project_id` only — which works for
   single-source repos but breaks when two sources contribute
   to the same logical project (e.g. a mirror).
2. **Replace the in-memory cosine-similarity loop in
   `semantic_search` (line 100-114) with a NumPy dot product**
   and optionally a `pgvector` or `lancedb` index when the
   embedding count exceeds ~50k per project. This is a
   pre-requisite for cross-source semantic search.

The LLM `chat()` method (line 140-293) needs a larger rewrite
to become the agent described in
`docs/feature-audit-and-agentic-query-engine.md`, but that's a
separate ticket.

---

## 7. What's missing today (concrete backlog)

This is the work to do, in order, to make the contract real. Each item
points to the file that has to change.

### 7.1 Plugin side

| Ticket | Effort | What |
|---|---|---|
| **LORE-41** Create `chronograph/plugins/base.py` with `SourcePlugin` Protocol + `common/source_records.py` with the 5 Pydantic models. | S | 2 new files, ~150 LOC. |
| **LORE-42** Refactor `chronograph/ingestion/ingest_pipeline.py` to emit `NormalizedRecord`s; have `GitLabCodeFetcher` implement `SourcePlugin`. | M | The current code is already structurally correct — wrap it. ~200 LOC refactor, no behavior change. |
| **LORE-43** Build `infra/github/` per the contract: implement `SourcePlugin`, fix the unauthenticated-raw-URL bug at `code_fetcher.py:152`, check `truncated` on the Trees API response, add `X-RateLimit-Remaining` handling, add `fetch_specific_files()`. | M | ~400 LOC. Use the pre-refactor `_fetch_github_metadata()` at commit `2ac679ab` as the reference shape. |
| **LORE-44** Move `chronograph/gitlab_polling.py` to be a generic `polling_cycle(plugins: list[SourcePlugin])` that calls each plugin's `list_repos()`. | S | The current 169-LOC file is 90% generic already. |

### 7.2 Storage / processing side

| Ticket | Effort | What |
|---|---|---|
| **LORE-45** Move `chronograph/ingestion/ingest_pipeline.py:336-427` (OpenRouter embedder) to use NVIDIA NeMo 2048-d, matching `pipeline/embed_knowledge.py`. Drop the in-memory 1536-d cache in `query_engine.py:92-114`. | S | This unifies the embedding model across both pipelines. Without it, cross-source semantic search is impossible. |
| **LORE-46** Add per-source checkpointing: the current single-timestamp `chronograph_gitlab_checkpoint.json` becomes `chronograph_{source}_checkpoint.json` (one per source), keyed by `(source, repo_url)`. Fixes the cross-source-blame bug. | S | `chronograph/gitlab_polling_checkpoint.py` is 127 LOC; the new generic version is ~150. |
| **LORE-47** Add retry / dead-letter on non-2xx responses in `_fetch_gitlab_metadata` (lines 186-244) and the new GitHub equivalent. Currently the 401 I diagnosed silently gets logged and the checkpoint still advances — that's a real bug. | S | Add a `raise_on_status` helper in `chronograph/ingestion/`. |
| **LORE-48** Add `chronograph/processing/parse.py`, `diff.py`, `graph_build.py`, `embed.py`, `dedup.py`, `cypher_emit.py` as the explicit pipeline stages. They already exist as functions in `chronograph/ingestion/`, `chronograph/diffing/`, etc. — promote them. | M | Mostly moves and renames, plus a `PipelineContext` dataclass to hold shared state across stages. |
| **LORE-49** Build the Neo4j-side emitter for code-intel: a new pass in `builders/unified_builder.py` (or a sibling `chronograph/unified_builder.py`) that reads Mongo and emits Cypher for `CodeEntity`, `Snapshot`, `CodeEntityVersion`, `Developer`, `Repo`, edges. Uses the same `cypher_utils.cypher_escape` and constraint/index patterns as the existing Gmail-side passes. | L | ~400 LOC new + tests. This is the part that *doesn't exist today* and that makes the cross-source graph real. |
| **LORE-50** Fix the inconsistent `entity_id` format used in `symbol_embeddings` (`ingest_pipeline.py:367`: `f"{project_id}:{file_path}:{qualified_name}"` with no `entity:` prefix) to match the rest of the codebase. Migration: re-key on next full sync. | S | One-line change + a one-time migration script. |

### 7.3 Cross-cutting

| Ticket | Effort | What |
|---|---|---|
| **LORE-51** Add an `org_repos.json` config (per org, per source) replacing the env-var-only project list in `chronograph/gitlab_polling.py:64-82`. One line per repo: `{"source": "github", "url": "https://github.com/acme/core", "external_id": "12345"}`. | S | Removes the TODO at line 64. |
| **LORE-52** Add observability: per-stage timing, per-tool call counts, empty-result rates. Logs already exist; add Prometheus counters. | M | The data the agent layer needs to debug itself. |

---

## 8. Migration plan

The good news: **the existing GitLab code already produces the right
shapes.** It does not conform to the `SourcePlugin` *interface* (the
classes are named differently, the methods have different signatures),
but it produces all the right data with all the right field names. So
the migration is:

1. **Land the new interface (`LORE-41`).** Nothing else changes.
2. **Wrap the GitLab code behind the interface (`LORE-42`).** All
   existing behaviour preserved. The polling module is now generic.
3. **Build GitHub on the same interface (`LORE-43`).** This is the
   first "real" plugin after the wrapper lands.
4. **Unify embeddings (`LORE-45`).** Cross-source semantic search now
   possible.
5. **Build the Neo4j code-intel emitter (`LORE-49`).** This is the
   *new* capability — code-intel nodes now show up in the same graph
   that the Gmail/knowledge side uses, and the LLM can answer
   "who broke the auth flow last quarter and what was the team's
   decision about it?" in a single query.
6. **Add the next source (Bitbucket, Gitea, SourceHut).** This is
   now a 1-2 day job because the interface does the heavy lifting.

At no point during this migration does existing data need to be
re-ingested from scratch. The new Neo4j emitter is a read-only pass
over Mongo; the new GitHub plugin adds new data alongside the GitLab
data. A re-ingest is only needed if you want to migrate the
embedding model (LORE-45), and even then only for the symbols
collection, not commits or members.

---

## 9. Open questions (not blocking the contract, but worth deciding)

1. **Should `Developer` nodes be shared between the chronograph side
   and the Gmail side?** Today they live in different ID namespaces
   (`dev:{email}` in chronograph, `person:{short_hash(name)}` in
   Gmail). To answer "who decided X in the org and what code did
   they write?", they need to merge. `LORE-52` (cross-source
   person dedup) is a prerequisite.

2. **Should the same repo on two sources (a GitHub mirror + a GitLab
   primary) produce one `CodeEntity` or two?** Two is simpler and
   more correct (the `source` field disambiguates), but it means
   search results have duplicates. The `ALIAS_OF` edge in
   `Stage E — Dedup` lets the LLM traverse the link without forcing
   a merge.

3. **Should the embedding model be re-evaluated periodically?** Embedding
   models improve. A v2 NeMo model would need a re-embed pass. The
   contract should not lock us into the current model — but it
   *should* lock us into a *single* model at any one time.

4. **Should PR / MR data be a first-class entity?** GitLab MRs and
   GitHub PRs are a rich source of review discussions, approvals,
   linked issues. The current GitLab ingest ignores MRs entirely
   (`ingest_pipeline.py:215-244` only fetches commits and members,
   not merge_requests). A future `NormalizedMergeRequest` record
   would be additive to the contract, not breaking.

5. **Should commit *reviews* and *comments* be in scope?** Same as
   MRs — additive. Worth a separate doc when we get there.

---

## 10. Summary

- The data the GitLab pipeline produces is in `chronograph/store/mongo_store.py`
  (Mongo) and the `edges` collection shapes in `graph_builder.py`.
  A future GitHub connector must produce the same shapes.
- The contract has two pieces: a `SourcePlugin` interface (5 methods,
  source-specific quirks stay inside) and a set of `NormalizedRecord`
  shapes (5 Pydantic models, the only types that flow across the
  plugin boundary).
- The shared pipeline downstream of the plugin (`parse → diff →
  graph → embed → dedup → cypher emit`) is source-agnostic. It already
  exists in pieces; the contract just makes the boundaries explicit.
- The Neo4j side of the contract **does not exist for code-intel
  today** — `chronograph` writes to Mongo only. Adding it is the
  biggest piece of new work and is the piece that makes the
  cross-source GraphRAG query possible.
- The unified embedding model (2048-d NVIDIA NeMo) is the linchpin.
  Two different embedding models = no cross-source semantic search.
- Adding GitHub is a 1-2 day ticket once the interface exists.
  Adding a fourth source after that is a 1-day ticket.
