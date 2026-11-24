# lore-ingestion — GitHub + GitLab Code-Intelligence: Integration / API Surface

This document maps **everything other services connect to** in the GitHub + GitLab
code-intelligence pipeline ("chronograph"): the entry points, the data store API, the
poller/plugin surface, the query/agent read APIs, the config you must set, and the outputs
downstream services consume.

> **Scope note:** Health checks are **out of scope** for this PR. They will be redesigned
> after these source integrations land, so they are intentionally not included here.

---

## 0. Overview

Chronograph turns a Git host (GitHub or GitLab) into queryable code intelligence:

```
GitHub / GitLab  ──fetch──▶  parse (tree-sitter)  ──▶  embed (NVIDIA)  ──▶  store (pg + pgvector)
                                                                              │
                                                          graph build ◀───────┤
                                                          query / agent ◀──────┘
```

Two ways to run it: **one-shot** (`ingest`) or **scheduled daemon** (`poller`). Both share
the same plugin → cycle → store path.

---

## 1. Entry points (CLI)

Defined in `pyproject.toml` `[project.scripts]`:

| Command | Module | Purpose |
|---------|--------|---------|
| `ingest` | `runners/ingest_once.py:main` | One-shot ingest of a single repo. Args: `--source {github,gitlab}`, `--repo owner/repo`, `--gitlab-id <id>`, `--ref <branch>`, `--full`. Returns a JSON stats dict. |
| `poller` | `runners/chronograph_poller.py:main` | Long-running daemon; starts the GitHub + GitLab schedulers and blocks. Container entrypoint. |
| `sync` | `runners/sync.py:main` | Full incremental orchestrator (Gmail + pipeline stages); also starts the chronograph schedulers. |
| `agent` | `agent/run.py:main` | Interactive / one-shot LLM code-intelligence agent over the store. |

---

## 2. Scheduled pollers

- `infra/gitlab/polling.py` — `start_polling_scheduler()`; APScheduler job `_run_all_projects()`.
- `infra/github/polling.py` — `start_polling_scheduler()`; APScheduler job `_run_all_repos()`.
- `chronograph/poller.py` — `async run_cycle(plugin, store, ref="")`: the fetch → parse →
  embed → graph-build → store cycle (never raises; returns a stats dict).

Configure with: `GITHUB_REPOS` / `GITLAB_PROJECTS`, `GITHUB_PAT` / `GITLAB_PAT`,
`*_POLL_INTERVAL_SECONDS` (default `10800` = 3h). Schedulers run `max_instances=1`,
`misfire_grace_time=3600`.

---

## 3. Source plugins (GitHub / GitLab)

Duck-typed plugin interface, one class per host:

- `infra/github/fetcher.py` — `GitHubPlugin(pat, owner, repo)` · `name = "github"`
- `infra/gitlab/fetcher.py` — `GitLabPlugin(base_url, pat, project_id, gitlab_id)` · `name = "gitlab"`

Both expose:
`fetch_project_info()`, `fetch_commits(since, until, ref)`, `fetch_commit_files(sha)`,
`fetch_source_files(ref, paths)`, `fetch_members()`, and (optional) `fetch_pull_requests()`,
`fetch_issues()`.

Normalized record types (host-agnostic) — `common/source_records.py`:
`RepoRef`, `NormalizedCommit`, `NormalizedFileChange`, `NormalizedSourceFile`, `NormalizedMember`.

Auth: `GITHUB_PAT` (classic or fine-grained), `GITLAB_PAT`. Both fetchers handle rate limits
(GitHub `X-RateLimit-Remaining`; GitLab `429 Retry-After`).

---

## 4. The Store interface — **how downstream services read chronograph data**

`chronograph/store/store.py` defines the `Store` Protocol (both backends implement it).
Obtain the active store with:

```python
from chronograph.store.store_factory import make_store
from common.config import get_settings

store = make_store(get_settings())   # PostgreSQL + pgvector (DATABASE_URL / POSTGRES_URI)
store.connect()
```

**Read API (representative):**
`get_project`, `get_commits`, `get_commit_count`, `get_commit_authors`,
`get_activity_timeline`, `get_members`, `get_source_file`, `get_symbols`, `get_symbol`,
`search_symbols`, `get_most_complex`, `trace_function`, `compute_hotspots`,
`compute_bus_factor`, `get_edges`, `search_embeddings(query_vector, top_k)`, `get_stats`.
(Write side: `upsert_*` + `create_edge(s)`.)

**Backend:** PostgreSQL + pgvector (the only backend).
- `chronograph/store/pg_store.py` — `PgStore` (SQLAlchemy 2.x + psycopg2 + **pgvector** HNSW).
  Schema `chronograph`; models in `chronograph/store/models.py` (`Repo, Commit, CommitFile,
  SourceFile, Symbol, SymbolEmbedding, Member, Edge, Snapshot, FileMetadata, PullRequest,
  PullRequestReview, Issue`).

The DSN is read from `DATABASE_URL` (preferred) or `POSTGRES_URI` — see `common/config.py`.

---

## 5. Query + agent (read-side APIs)

- `chronograph/query/query_engine.py` — `QueryEngine(store, config)`:
  `get_stats`, `get_people`, `get_hotspots`, `get_bus_factor`, `get_timeline`, `get_complexity`,
  `search_symbols`, `trace_function`, `get_function_code`, `get_file_content`,
  `semantic_search(query, top_k)`, `chat(project_id, question)` (OpenRouter LLM over context).
- `agent/run.py` + `agent/graph.py` + `agent/tools.py` — LangGraph ReAct agent with 17
  code-intel tools (ownership, bus factor, hotspots, symbol/semantic search, call-graph trace, …).

---

## 6. Outputs other services consume

- **Chronograph store** (Postgres `chronograph` schema, above) — the
  primary read surface for any consumer (e.g. lore-engine's GitLab/chronograph routes).
- **Neo4j graph** — `builders/unified_builder.py` (4-pass Cypher + NVIDIA embedding upload).
  Edge types: `AUTHORED_BY, CALLS, CONTAINS, IMPORTS, COLLABORATES_WITH, NEXT_SNAPSHOT`.
  Configure via `NEO4J_URI`, `NEO4J_USERNAME`, `NEO4J_PASSWORD`.

---

## 7. Config / env — the "what to set to connect" list

Read centrally through `common/config.py` (`get_settings()`).

| Var | Default | Purpose |
|-----|---------|---------|
| `GITHUB_PAT` | — | GitHub auth |
| `GITHUB_REPOS` | — | `"owner/repo,owner2/repo2"` |
| `GITHUB_POLL_INTERVAL_SECONDS` | `10800` | GitHub poll cadence |
| `GITLAB_PAT` | — | GitLab auth |
| `GITLAB_BASE_URL` | `https://gitlab.com` | GitLab instance |
| `GITLAB_PROJECTS` | — | `"org/repo:12345,org2/repo2:67890"` |
| `GITLAB_POLL_INTERVAL_SECONDS` | `10800` | GitLab poll cadence |
| `DATABASE_URL` or `POSTGRES_URI` | — | Chronograph store DSN (PostgreSQL + pgvector). `DATABASE_URL` wins. |
| `CHRONOGRAPH_BACKFILL_DAYS` | `90` | Initial commit backfill |
| `CHRONOGRAPH_MAX_FILE_SIZE` | `1048576` | Max source file size |
| `NVIDIA_API_KEY` / `NVIDIA_BASE_URL` | — / NVIDIA Build | Embeddings (+ extraction) |
| `EMBEDDING_MODEL` / `EMBEDDING_DIM` | NVIDIA NeMo / `2048` | Embedding config |
| `OPENROUTER_API_KEY` / `OPENROUTER_LLM_MODEL` | — | Query-engine `chat()` LLM |
| `NEO4J_URI` / `NEO4J_USERNAME` / `NEO4J_PASSWORD` | — | Neo4j graph upload (builders) |

---

## 8. HTTP surface

There is **no HTTP API** in this pipeline today — it's CLI runners + APScheduler daemons.
A health HTTP endpoint exists on a separate branch and is being **redesigned post-integration**
(out of scope here). Consumers integrate via the **Store interface** (§4), the **QueryEngine**
(§5), or the **Neo4j graph** (§6) — not over HTTP.
