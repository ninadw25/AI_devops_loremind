# lore-ingestion

> **Dormant (as of 2026-09).** Not deployed in prod since the CA-firm pivot;
> kept for possible reuse. The deploy/run notes below describe how it last
> worked. The last deploy was systemd on an EC2 box with a control API on
> `:8100`; see [`EXPLAIN.md`](EXPLAIN.md).

The data ingestion pipeline for **Lore**, Loremind's passive knowledge
capture platform. Fetches a customer's organizational communications
(Gmail, GitLab), filters noise, extracts structured knowledge as graph
triples via LLM, and builds a Neo4j knowledge graph plus a vector
index that `lore-engine` queries.

```
┌─────────────┐    ┌──────────────────────────────┐    ┌─────────────┐
│  Gmail API  │───▶│  lore-ingestion              │───▶│  Neo4j      │
│  GitLab API │───▶│  (this repo)                 │───▶│  + vector   │
└─────────────┘    │  fetch → filter → extract    │    │  index      │
                   │  → validate → embed → build  │    └──────┬──────┘
                   └──────────────────────────────┘           │
                                                              ▼
                                                       ┌─────────────┐
                                                       │ lore-engine │
                                                       │ query API   │
                                                       └─────────────┘
```

> **TL;DR for new team members:** start with [Quickstart](#quickstart),
> then read [Pipeline stages in depth](#pipeline-stages-in-depth) and
> [Folder structure](#folder-structure). Deployment and the control API
> are described in [`EXPLAIN.md`](EXPLAIN.md) and
> [`docs/DEPLOY_CHECKLIST.md`](docs/DEPLOY_CHECKLIST.md).
>
> **Two pipelines live here.** This README mostly covers the legacy
> 6-stage triple pipeline (`pipeline/`, `builders/unified_builder.py`).
> The newer **4-layer substrate** (L0 Postgres ledger → L1 registries →
> L2 edges → L3 claims → projection to a separate claims Neo4j;
> `builders/l0_ledger.py`…`l3_claims.py`, `runners/ledger_sync.py`,
> `runners/zoho_ledger_sync.py`, `runners/extract_claims.py`,
> `runners/substrate_flow.py`) is the primary path for Gmail and Zoho.
> See `docs/PIPELINE_THROUGHPUT.md` and `docs/ZOHO_ON_SUBSTRATE.md`.

---

## Table of contents

1. [What this repo does](#1-what-this-repo-does)
2. [Quickstart](#2-quickstart)
3. [Architecture at a glance](#3-architecture-at-a-glance)
4. [Pipeline stages in depth](#4-pipeline-stages-in-depth)
5. [Knowledge ontology](#5-knowledge-ontology)
6. [Folder structure](#6-folder-structure)
7. [Output directory layout](#7-output-directory-layout)
8. [Environment variables](#8-environment-variables)
9. [Running stages — full command reference](#9-running-stages--full-command-reference)
10. [Incremental polling (cron-style scheduling)](#10-incremental-polling-cron-style-scheduling)
11. [Docker deployment](#11-docker-deployment)
12. [Design principles](#12-design-principles)
13. [Where to look when something breaks](#13-where-to-look-when-something-breaks)
14. [Contributing](#14-contributing)

---

## 1. What this repo does

For each customer ("org"), this pipeline:

1. **Fetches** their Gmail and (optionally) GitLab data via service
   accounts with domain-wide delegation.
2. **Filters** out ~70% of inbound traffic that is automated noise
   (newsletters, notifications, calendar pings) using signal-based
   rules — *no LLM cost* for this stage.
3. **Resolves identities** across multiple org domains
   (`alice@ezobooks.in` = `alice@ezobanks.com`) into a canonical
   person registry.
4. **Builds a communication graph** of directed who-talks-to-whom
   edges, with Union-Find communities for org-structure inference.
5. **Extracts graph triples** thread-by-thread using a single
   high-RPM LLM (NVIDIA DeepSeek v3.2), producing
   `(Person)-[DECIDED]->(Decision)` style statements.
6. **Validates** every triple against a deterministic schema, content,
   and person-existence gate. Rejected triples go to an audit file,
   not the graph.
7. **Embeds** every knowledge node with NVIDIA NeMo Retriever (2048-d)
   for hybrid vector + graph search.
8. **Deduplicates** entities by embedding cosine + connected components.
9. **Upserts** the result into Neo4j and a vector index that
   `lore-engine` queries.

The output is two things `lore-engine` consumes: a Neo4j knowledge
graph and an embeddings file (`.npy` + index JSON). The pipeline never
serves queries itself.

---

## 2. Quickstart

Prerequisites: Python 3.12, access to the shared venv at `../.venv/`,
a Google service-account `key.json` with DWD, a `.env` (see
[§8](#8-environment-variables)), and a Neo4j instance (`docker compose up -d` for local dev).

```bash
# From inside lore-ingestion/
cd lore-ingestion

# 1. Activate venv (we use ../.venv/ from the lore monorepo root)
source ../.venv/bin/activate
# or just always invoke with ../.venv/bin/python

# 2. Run the full pipeline for one org (first-time / historical import)
../.venv/bin/python -m infra.gmail.run \
    --org ezobooks --domain ezobooks.in \
    --admin-email admin@ezobooks.in --key key.json

../.venv/bin/python -m pipeline.filter \
    --input output/raw/ezobooks_gmail_*.jsonl --org ezobooks --skip-llm

../.venv/bin/python -m pipeline.person_registry \
    --input output/filtered/ezobooks_gmail_filtered.jsonl --org ezobooks

../.venv/bin/python -m pipeline.comm_graph \
    --input output/filtered/ezobooks_gmail_filtered.jsonl --org ezobooks

../.venv/bin/python -m pipeline.extraction.cli --org ezobooks --resume

../.venv/bin/python -m pipeline.validation.cli --org ezobooks

../.venv/bin/python -m pipeline.embed_knowledge --org ezobooks

../.venv/bin/python -m pipeline.deduplicator --org ezobooks

../.venv/bin/python -m builders.unified_builder --org ezobooks

../.venv/bin/python -m builders.post_processor --org ezobooks
```

For incremental subsequent runs (every 6 hours), use the **single
orchestrator**:

```bash
../.venv/bin/python -m runners.sync \
    --org ezobooks --domain ezobooks.in \
    --admin-email admin@ezobooks.in --key key.json
```

`runners.sync` reads the per-user watermark, runs only the delta
through every stage, and advances the watermark only after the whole
chain succeeds. See [§10](#10-incremental-polling-cron-style-scheduling).

For testing without burning LLM tokens: any stage that calls an LLM
accepts `--limit 5` or `--dry-run`.

---

## 3. Architecture at a glance

The codebase has **four ownership zones**, each with a clear
responsibility and no upward dependencies:

```
┌─────────────────────────────────────────────────────────────────┐
│  runners/         ← thin CLI dispatchers (incl. sync orchestrator)│
└────────────────────────────┬────────────────────────────────────┘
                             │ calls
┌────────────────────────────▼────────────────────────────────────┐
│  infra/          pipeline/         builders/                    │
│  ─────           ────────          ────────                     │
│  External        Source-agnostic   Database materialisation     │
│  adapters        per-stage logic   (Neo4j + vector index)       │
│   gmail/          filter             unified_builder            │
│   llm/            person_registry    post_processor             │
│   embeddings/     comm_graph                                    │
│                   extraction/                                   │
│                   validation/                                   │
│                   embed_knowledge                               │
│                   deduplicator                                  │
└────────────────────────────┬────────────────────────────────────┘
                             │ uses
┌────────────────────────────▼────────────────────────────────────┐
│  common/                                                        │
│  ──────                                                         │
│  Pure helpers shared by every layer. Zero IO at import time.    │
│   config, hashing, schemas, rbac, logger, watermark, ...        │
└─────────────────────────────────────────────────────────────────┘
```

Two things that live alongside but are **independent pipelines**:

- `chronograph/` — GitLab code-intelligence pipeline (tree-sitter
  AST parsing → PostgreSQL + pgvector → embeddings). Standalone, optional, owned
  separately.
- `knowledge_distillation/` — research workflow for distilling smaller
  LLMs from the production extraction model. Not part of the runtime
  pipeline; lives here because it shares the same triple schema.

### Data flow (Gmail path)

```
Gmail API (DWD service account)
  ↓
infra/gmail/run.py
  ↓ raw JSONL
output/raw/{org}_gmail_{date}.jsonl
  ↓
pipeline/filter.py                        ← signal-based, ~70% noise removal
  ↓
output/filtered/{org}_gmail_filtered.jsonl
  ↓                                        ↘
pipeline/person_registry.py               pipeline/comm_graph.py
  ↓                                        ↓
output/knowledge/registry/                output/knowledge/registry/
  {org}_person_registry.json                {org}_comm_graph.json
  ↓
pipeline/extraction/   (Stage 5: LLM)     ← thread-grouped, async, rate-limited
  ↓
output/knowledge/triples/{org}_triples_raw.json
  ↓
pipeline/validation/   (Stage 6: gate)    ← schema + content + person check
  ↓
output/knowledge/triples/{org}_triples_validated.json   (approved)
output/knowledge/triples/{org}_triples_rejected.json    (audit)
  ↓
pipeline/embed_knowledge.py               ← NVIDIA NeMo 2048-d
  ↓
output/knowledge/embeddings/{org}_knowledge_embeddings.npy
  ↓
pipeline/deduplicator.py                  ← cosine + connected components
  ↓
output/knowledge/triples/{org}_triples_deduped.json
  ↓
builders/unified_builder.py               ← writes Neo4j via MERGE (idempotent)
  ↓
Neo4j
  ↓
builders/post_processor.py                ← topic clustering, processes,
                                            cross-thread edges
                                            (cold loop, daily)
```

### Why this shape

- **Ingestion ↔ backend boundary.** `lore-ingestion` produces
  artefacts; `lore-engine` consumes them. No shared runtime state.
  Either side can be redeployed independently.
- **Pure functions wherever possible.** Stages 2–6 are pure functions
  of their input files. The filter doesn't know it's the second
  stage; it transforms a JSONL into another JSONL. Re-running a stage
  is always safe.
- **Deterministic node IDs.** SHA-256 hashes via
  `common/hashing.py:node_id_for()` ensure the vector index and the
  Neo4j graph reference the same nodes by ID, even though they're
  generated by different code paths.
- **Checkpoint everywhere.** Stage 1 saves after each user; Stage 5
  saves every 50 threads. `--resume` resumes from disk; a crash
  costs one batch of work, not the run.
- **Graceful degradation.** If Neo4j is down, the pipeline still
  produces validated triples and embeddings — `lore-engine` falls
  back to vector-only search until Neo4j comes back.

---

## 4. Pipeline stages in depth

### Stage 1 — Gmail fetch (`infra/gmail/`)

Lists all users in the customer's Google Workspace via the Directory
API, then for each user fetches all (or incremental) mail via the
Gmail API impersonated through domain-wide delegation. Uses Gmail's
batch endpoint (50 messages/request), a 0.1-second rate limit, and
exponential backoff on 429.

- **Watermark-aware:** `--use-watermark` reads
  `output/checkpoints/{org}_sync.json` and applies a per-user
  `after:<internalDate>` filter so each mailbox resumes from exactly
  where it left off. See [§10](#10-incremental-polling-cron-style-scheduling).
- **Pre-filtering at the API level:** the Gmail search query excludes
  `-in:spam -in:trash`, common noreply senders, and (for personal
  mailboxes) promotions/social categories. Cuts down what we even
  fetch.
- **Functional mailboxes** (`hr@`, `support@`, etc.) are detected and
  filtered to internal-only conversation to avoid downloading vendor
  pitches.
- **Output:** `output/raw/{org}_gmail_{YYYY-MM-DD}.jsonl` (one
  `KnowledgeEvent` per line, plus thread-aggregated events for
  multi-message threads).

### Stage 2 — Filter (`pipeline/filter.py`)

Signal-based noise removal. Combines:

- Gmail label categories (`CATEGORY_PROMOTIONS`, etc.)
- Header signals (`List-Unsubscribe`, `Auto-Submitted`)
- Sender patterns (`noreply@`, `notifications@`)
- Domain-ratio heuristics (a domain seen >50 times always sending
  *to* but never *from* org members is noise)

Removes ~70% of fetched mail before any LLM touches it. Optional
`--no-skip-llm` mode adds a Claude pass for the ambiguous middle
ground; off by default to save tokens.

**Output:** `output/filtered/{org}_gmail_filtered.jsonl`.

### Stage 3 — Person registry (`pipeline/person_registry.py`)

Walks the filtered events and produces a canonical list of people:

- One canonical identity per cross-domain alias group:
  `alice@ezobooks.in` and `alice@ezobanks.com` → same `Person`.
- Display-name normalisation (handles `"Alice K." <alice@...>`).
- Internal vs. external classification by domain.

**Output:** `output/knowledge/registry/{org}_person_registry.json`.

### Stage 4 — Communication graph (`pipeline/comm_graph.py`)

Directed graph of `(sender) → (recipient)` edges weighted by message
count. Union-Find clusters identify communities (often align with
departments). Feeds the org-structure context that Stage 5 hands the
LLM.

**Output:** `output/knowledge/registry/{org}_comm_graph.json`.

### Stage 5 — Triple extraction (`pipeline/extraction/`)

The core LLM stage. Groups messages by `thread_id` and asks the LLM
to extract structured statements per thread. **One LLM call per
thread**, not per message — that's what keeps cost flat as inboxes
grow.

- **Model:** `deepseek-ai/deepseek-v3.2` via NVIDIA Build (40 RPM
  provider cap).
- **Concurrency:** `asyncio.Semaphore(20)` + a sliding-window rate
  limiter at 38 calls/min (under the 40 RPM cap).
- **Thread-grouping strategy** by message count:
  - ≤6 messages → all bodies, full text
  - ≤15 messages → first 2 + last 2 full, middle subjects only
  - \>15 messages → first 3 + last 3 full, middle subjects only
- **Self-correction:** validation failures trigger one
  retry-with-error-feedback before giving up on a thread.
- **Checkpointing:** every 50 threads. `--resume` skips already-done
  ones.
- **Incremental mode:** `--thread-ids <file>` re-extracts only the
  threads in the file. The module pre-loads the existing
  `_triples_raw.json` and preserves untouched threads — running with
  a small thread-id list does not wipe the rest.

**Output:** `output/knowledge/triples/{org}_triples_raw.json`.

### Stage 6 — Validator (`pipeline/validation/`)

Deterministic, no-LLM quality gate before any Neo4j writes:

- **Schema:** valid predicate + correct subject/object node types
  per predicate direction.
- **Content:** descriptions ≥8 chars, no HTML/CSS/placeholders.
- **Evidence:** `DECIDED`, `BECAUSE`, `ASSIGNED_TO` require an
  evidence sentence ≥10 chars.
- **Persons:** every `Person` node must exist in the person registry
  (no LLM-hallucinated names — fuzzy-matched via `rapidfuzz`).
- **Auto-correction:** flips known directional errors (e.g., reversed
  `DECIDED` subject/object).

**Outputs:** `_triples_validated.json` (approved) and
`_triples_rejected.json` (audit trail with reason codes).

### Stage 6.5 — Knowledge embedding (`pipeline/embed_knowledge.py`)

Embeds every validated knowledge node (Decisions, Tasks, Facts,
Events, Topics) with the configured embedding model
(`EMBEDDING_MODEL`, default `nvidia/llama-nemotron-embed-1b-v2`,
2048-dim, L2 normalised). Incremental: skips node IDs that are already in the
on-disk embedding index.

**Outputs:** `output/knowledge/embeddings/{org}_knowledge_embeddings.npy`
+ index JSON.

### Stage 6.6 — Deduplicator (`pipeline/deduplicator.py`)

Finds near-duplicate entities (e.g., two LLM-extracted "Decision"
nodes that mean the same thing) using embedding cosine similarity +
SciPy `connected_components`. Per-thread duplicates are merged
aggressively; cross-thread duplicates use a higher threshold.

**Output:** `output/knowledge/triples/{org}_triples_deduped.json`.

### Stage 7 — Unified builder (`builders/unified_builder.py`)

Materialises everything into Neo4j via four passes:

- **Pass 0** — schema (constraints + indexes, idempotent).
- **Pass 1** — Person, Organization nodes from the registry.
- **Pass 2** — all knowledge nodes + edges from the deduped triples.
- **Pass 3** — uploads the pre-computed NeMo embeddings to each
  node's `embedding` property.

All Cypher uses `MERGE`, so re-running is a no-op when nothing
changed. Aborts safely if Neo4j is unreachable; the pipeline can be
re-run once Neo4j is reachable.

### Stage 8 — Post-processor (`builders/post_processor.py`) — **cold loop**

The "second pass" that needs the *whole corpus* and is therefore not
part of the 6-hourly hot loop. Runs daily, ideally at 03:00.

- **9A — Topic taxonomy:** HDBSCAN on topic embeddings → LLM-labelled
  clusters.
- **9B — Person expertise:** PageRank-ish scoring of who talks about
  what.
- **9C — Process synthesis:** finds Decision/Task patterns that recur
  across threads in the same topic, materialises them as `Process`
  nodes with `HAS_STEP` edges to `Task` nodes.
- **9D — Cross-thread edges:** `SUPERSEDES` / `PRECEDED_BY` temporal
  relationships between decisions.

---

## 5. Knowledge ontology

Seven node types, nine predicates. Defined as Pydantic enums in
`common/schemas_extraction.py`.

**Node types:** `Person`, `Organization`, `Decision`, `Task`, `Fact`,
`Topic`, `Event`.

**Predicates:**

| Predicate         | Subject → Object         | Meaning                          |
|-------------------|--------------------------|----------------------------------|
| `DECIDED`         | Person → Decision        | Person made this decision        |
| `BECAUSE`         | Decision → Fact          | Rationale for a decision         |
| `STATED_BY`       | Fact/Decision → Person   | Who said this                    |
| `ASSIGNED_TO`     | Task → Person            | Who owns this task               |
| `REGARDING`       | Fact/Decision/Task → Topic | What it's about                |
| `SUBJECT_OF`      | Event → Topic            | Event concerns this topic        |
| `WORKS_AT`        | Person → Organization    | Employment relationship          |
| `PARTICIPATED_IN` | Person → Event           | Who was in meeting/call          |
| `MENTIONS`        | Fact/Decision → Person/Org | Referenced but not subject     |

Node IDs are deterministic SHA-256 hashes via
`common/hashing.py:node_id_for(node_type, name, scope=thread_id)`.
Scoping by `thread_id` keeps "send invoice" in thread A distinct from
"send invoice" in thread B — the deduplicator decides cross-thread.

---

## 6. Folder structure

```
lore-ingestion/
│
├── README.md                  ← you are here
├── CLAUDE.md                  ← project notes for Claude Code
├── pyproject.toml             ← dependencies (pip install .)
├── uv.lock                    ← uv-managed lock file
├── docker-compose.yml         ← local Neo4j for dev
├── Dockerfile                 ← single image, works on-prem + Fargate
├── .dockerignore              ← excludes output/, secrets, etc.
├── .env.example               ← copy to .env, fill in secrets
├── key.json                   ← Google service account (NEVER commit)
│
├── common/                    ┄┄ pure shared utilities, no IO at import
│   ├── config.py                pydantic Settings (single source of env)
│   ├── hashing.py               short_hash, node_id_for — deterministic IDs
│   ├── schemas.py               KnowledgeEvent, Actor, EventType
│   ├── schemas_extraction.py    Triple, NodeType, Predicate, ExtractionResult
│   ├── rbac.py                  dept classification, sensitivity labels
│   ├── org_config.py            per-org JSON config loader
│   ├── output_paths.py          KnowledgePaths — central path registry
│   ├── logger.py                colored console + file logging
│   ├── stage_gate.py            check_nonempty — stage-boundary gates
│   ├── stage_summary.py         log_summary — uniform end-of-stage logs
│   └── watermark.py             ★ per-user incremental sync watermark
│
├── infra/                     ┄┄ external-service adapters
│   ├── gmail/                   Gmail API (Stage 1)
│   │   ├── run.py                 user listing + per-user batch fetch
│   │   ├── fetcher.py             Gmail API client (DWD auth, batch, RL)
│   │   └── normalizer.py          raw MIME → KnowledgeEvent
│   ├── llm/                     LLM provider registry (Protocol-based)
│   │   ├── base.py                ChatProvider Protocol
│   │   ├── nvidia.py              NvidiaChatProvider (DeepSeek v3.2)
│   │   ├── openrouter.py          OpenrouterChatProvider (kept, opt-in)
│   │   └── registry.py            get_chat_provider(name=None)
│   └── embeddings/              Embedding provider registry
│       ├── base.py                EmbeddingProvider Protocol
│       ├── nvidia.py              NvidiaEmbeddingProvider (NeMo, 2048-d)
│       └── registry.py            get_embedding_provider(name=None)
│
├── pipeline/                  ┄┄ source-agnostic processing stages
│   ├── filter.py                Stage 2 — signal-based noise removal
│   ├── person_registry.py       Stage 3 — identity resolution
│   ├── comm_graph.py            Stage 4 — communication graph
│   ├── extraction/              Stage 5 — LLM triple extraction
│   │   ├── cli.py                 argparse entry (cli_entry, main)
│   │   ├── extract.py             extract_all + batch processing
│   │   ├── prompts.py             SYSTEM_PROMPT, CORRECTION_PROMPT
│   │   ├── grouping.py            group_by_thread + person registry loader
│   │   ├── rate_limit.py          AsyncRateLimiter (sliding-window)
│   │   ├── checkpoint.py          _load_checkpoint, _save_checkpoint
│   │   └── output.py              _save_output
│   ├── validation/              Stage 6 — deterministic quality gate
│   │   ├── cli.py                 argparse entry
│   │   ├── run.py                 run_validation orchestrator
│   │   ├── schema.py              predicate schema + content regexes
│   │   ├── rules.py               validate_triple, auto_correct_directions
│   │   ├── name_lookup.py         rapidfuzz person-name resolution
│   │   ├── structural.py          cross-triple orphan checks
│   │   ├── thread.py              per-thread orchestrator
│   │   └── id_minting.py          delegates to common.hashing.node_id_for
│   ├── embed_knowledge.py       Stage 6.5 — NVIDIA NeMo embeddings
│   └── deduplicator.py          Stage 6.6 — cosine + connected components
│
├── builders/                  ┄┄ database artefact producers
│   ├── unified_builder.py       Neo4j MERGE (4 passes) — main entry
│   ├── post_processor.py        topic clustering, processes, x-thread edges
│   ├── l0_ledger.py … l3_claims.py  4-layer substrate (L0 ledger → L3 claims)
│   ├── dedup_claims.py, project_claims.py  claim dedup + claims-Neo4j projection
│   └── slack_delta.py, slack_acl_emitter.py  Slack edit/delete + ACL artefacts
│
├── runners/                   ┄┄ entry points (16); the main ones:
│   ├── ledger_sync.py           Gmail → L0 ledger (substrate)
│   ├── zoho_ledger_sync.py      Zoho → L0 ledger (substrate)
│   ├── l0_sync.py               source-agnostic L0 sync core
│   ├── extract_claims.py        L3 claim extraction
│   ├── substrate_flow.py        daemon: re-runs L1/L2/dedup/projection
│   ├── sync.py                  legacy incremental Gmail delta loop
│   ├── slack_reconcile.py, slack_apply_delta.py, ingest_jira.py,
│   └── outlook_smoke.py, zoho_smoke.py, sync_status.py, ...
│
├── control/                   ┄┄ FastAPI control API on :8100 (see EXPLAIN.md)
│
├── chronograph/               ┄┄ GitLab code-intelligence (separate)
│   ├── config.py
│   ├── entities.py              Pydantic models: Snapshot, CodeEntity, ...
│   ├── ingestion/               fetch → parse → embed → PostgreSQL
│   ├── parsing/                 tree-sitter AST for 8+ languages
│   ├── diffing/                 semantic diff + commit classification
│   ├── query/                   PostgreSQL + pgvector + LLM queries
│   └── store/                   pg_store.py (PostgreSQL + pgvector)
│
├── evaluation/                ┄┄ 5-round QA framework for triple quality
│   ├── round1_structural.py     schema + predicate validation
│   ├── round2_content_quality.py garbage content, name format
│   ├── round3_edge_cases.py     nested decisions, ambiguous pronouns
│   ├── round4_hard_threads.py   long/conflicting threads
│   ├── round5_final_validation.py Neo4j queryable + RBAC checks
│   └── run_all.py               runs all five
│
├── knowledge_distillation/    ┄┄ research-only: train smaller LLMs on
│   ├── distill.py                production extraction model output
│   ├── models/                   TeacherConfig registry (10 teachers)
│   ├── build_sft_dataset.py
│   └── ...
│
├── scripts/                   ┄┄ one-off utilities (claim_gate.py, model_testing.py, ...)
│
├── docs/                      ┄┄ design + operational docs
│   ├── DEPLOY_CHECKLIST.md      deploy checklist (matches EXPLAIN.md)
│   ├── RBAC/SCOPE_FILTER_HANDOFF.md  canonical ACL (scope_ids) doc
│   ├── PIPELINE_THROUGHPUT.md, ZOHO_ON_SUBSTRATE.md  substrate docs
│   ├── Jira/, slack/, Outlook/  per-source docs
│   ├── modularity.md            (historical) pre-refactor audit
│   └── modularity-phases/       phase-by-phase refactor log (Steps 1-16)
│
└── output/                    ┄┄ generated artefacts (gitignored)
                                 see §7 for the layout inside this
```

★ = added/expanded during the incremental-sync work.

---

## 7. Output directory layout

Everything under `output/` is generated and gitignored. In Docker
deployments this directory maps to `/app/state` and is backed by a
bind-mounted volume (on-prem) or an EFS mount (Fargate). The
`LORE_STATE_DIR` env var lets you point it anywhere.

```
output/
├── raw/                              Stage 1 output — one file per fetch day
│   └── {org}_gmail_YYYY-MM-DD.jsonl    KnowledgeEvent per line
│
├── filtered/                         Stage 2 output (cumulative)
│   └── {org}_gmail_filtered.jsonl
│
├── knowledge/                        post-filter artefacts
│   ├── triples/
│   │   ├── {org}_triples_raw.json      Stage 5 — LLM output
│   │   ├── {org}_triples_validated.json Stage 6 — approved
│   │   ├── {org}_triples_rejected.json  Stage 6 — audit trail
│   │   └── {org}_triples_deduped.json   Stage 6.6 — final input to builder
│   ├── embeddings/
│   │   ├── {org}_knowledge_embeddings.npy        (N × 2048 float32)
│   │   ├── {org}_knowledge_embedding_index.json  node_id ↔ row map
│   │   └── {org}_knowledge_embeddings.ckpt.*     incremental checkpoint
│   ├── registry/
│   │   ├── {org}_person_registry.json   Stage 3
│   │   ├── {org}_email_lookup.json
│   │   ├── {org}_comm_graph.json        Stage 4
│   │   └── {org}_comm_summary.json
│   └── backups/                        timestamped .bak files
│
├── index/                            legacy vector index (build_index.py)
├── cypher/                           Cypher emitted by unified_builder
├── config/                           per-org config + stats JSON
│   ├── {org}_config.json               domains, functional mailboxes, noise
│   └── {org}_gmail_stats.json          last fetch summary
│
├── checkpoints/                      ★ resume-state files
│   ├── {org}_sync.json                 THE WATERMARK (incremental sync)
│   ├── {org}_gmail_checkpoint.json     Stage 1 checkpoint
│   ├── {org}_triple_extraction_checkpoint.json  Stage 5 checkpoint
│   ├── {org}_gmail_watermark_report.json scratch (per-run)
│   └── {org}_changed_threads.txt        scratch (per-run)
│
└── logs/                             per-stage rotating log files
    └── {org}_{stage}_YYYY-MM-DD.log
```

The **watermark file** (`checkpoints/{org}_sync.json`) is the only
file that absolutely must persist across container restarts for
incremental sync to work. Everything else is recoverable by re-running
the relevant stage.

---

## 8. Environment variables

Copy `.env.example` → `.env` and fill in. We use
`common/config.py` (Pydantic Settings) as the single source of truth
for everything; modules read from `get_settings()`, not `os.getenv`.

### Required for the main Gmail flow

| Variable | Used by | Purpose |
|----------|---------|---------|
| `NVIDIA_API_KEY` | extraction + embeddings | DeepSeek v3.2 LLM + NeMo retriever |
| `NEO4J_URI` | unified_builder | `neo4j+s://xxx.databases.neo4j.io` |
| `NEO4J_USERNAME` | unified_builder | usually `neo4j` |
| `NEO4J_PASSWORD` | unified_builder | Neo4j password |

### Optional

| Variable | Used by | Default / note |
|----------|---------|----------------|
| `EXTRACTION_MODEL` | extraction | `deepseek-ai/deepseek-v3.2` |
| `NVIDIA_BASE_URL` | extraction + embeddings | `https://integrate.api.nvidia.com/v1` |
| `LLM_PROVIDER` | extraction | `nvidia` (set `openrouter` to switch) |
| `EMBEDDING_PROVIDER` | embed + dedup | `nvidia` |
| `OPENROUTER_API_KEY` | post-processor (topic LLM labels) | optional |
| `ANTHROPIC_API_KEY` | filter (when NOT using `--skip-llm`) | optional |
| `GITLAB_PAT`, `GITLAB_BASE_URL`, `DATABASE_URL` (or `POSTGRES_URI`) | chronograph | only if you ingest GitLab |
| `ORG` | most stages | default org slug |
| `LORE_STATE_DIR` | runners.sync, watermark | persistent state root |

### Authentication files (not env vars, but required)

- `key.json` — Google service-account key with **domain-wide
  delegation** enabled and these OAuth scopes:
  - `https://www.googleapis.com/auth/gmail.readonly`
  - `https://www.googleapis.com/auth/admin.directory.user.readonly`

  Never commit this file. Stored in `/srv/lore/secrets/key.json` on
  on-prem; in AWS Secrets Manager on Fargate.

---

## 9. Running stages — full command reference

All commands assume `cd lore-ingestion/`. Use `../.venv/bin/python` if
the venv isn't activated.

### Full Gmail pipeline (in order)

```bash
# Stage 1 — Fetch
../.venv/bin/python -m infra.gmail.run \
    --org ezobooks --domain ezobooks.in \
    --admin-email admin@ezobooks.in --key key.json

# Stage 2 — Filter
../.venv/bin/python -m pipeline.filter \
    --input output/raw/ezobooks_gmail_*.jsonl --org ezobooks --skip-llm

# Stage 3 — Person registry
../.venv/bin/python -m pipeline.person_registry \
    --input output/filtered/ezobooks_gmail_filtered.jsonl --org ezobooks

# Stage 4 — Communication graph
../.venv/bin/python -m pipeline.comm_graph \
    --input output/filtered/ezobooks_gmail_filtered.jsonl --org ezobooks

# Stage 5 — Triple extraction
../.venv/bin/python -m pipeline.extraction.cli --org ezobooks --resume

# Stage 6 — Validate
../.venv/bin/python -m pipeline.validation.cli --org ezobooks

# Stage 6.5 — Embed
../.venv/bin/python -m pipeline.embed_knowledge --org ezobooks

# Stage 6.6 — Deduplicate
../.venv/bin/python -m pipeline.deduplicator --org ezobooks

# Stage 7 — Build (Neo4j MERGE)
../.venv/bin/python -m builders.unified_builder --org ezobooks

# Stage 8 — Post-process (cold loop, daily)
../.venv/bin/python -m builders.post_processor --org ezobooks
```

### One-shot incremental sync (every 6 hours)

```bash
../.venv/bin/python -m runners.sync \
    --org ezobooks --domain ezobooks.in \
    --admin-email admin@ezobooks.in --key key.json
```

### Common flags

| Flag | Where | What it does |
|------|-------|--------------|
| `--resume` | Stage 1, Stage 5 | continue from checkpoint |
| `--dry-run` | Stage 1 | count only, don't fetch full bodies |
| `--skip-llm` | Stage 2 | rule-based filtering only (default) |
| `--limit N` | Stage 5 | extract only first N threads (testing) |
| `--thread-ids FILE` | Stage 5 | re-extract only listed threads |
| `--discover-noise` | Stage 2 | auto-learn org-specific noise domains |
| `--users-file X` | Stage 1 | fetch only the users listed in X |
| `--since DATE` | Stage 1 | ISO date, day-resolution incremental |
| `--use-watermark` | Stage 1 | per-user lossless watermark (used by runners.sync) |
| `--watermark-out FILE` | Stage 1 | emit max(internalDate) report |
| `--allow-empty` | most stages | don't raise on zero output |
| `--rebuild` | builders/vector | force re-embed (legacy) |

### Evaluation (5-round QA framework)

```bash
../.venv/bin/python -m evaluation.run_all
# or one round at a time:
../.venv/bin/python -m evaluation.round1_structural \
    --file output/knowledge/triples/ezobooks_triples_raw.json
```

---

## 10. Incremental polling (cron-style scheduling)

For a customer in production, you don't run stages manually — you
schedule `runners.sync` to fire every 6 hours.

The watermark module (`common/watermark.py`) tracks
`max(internalDate)` **per Gmail user**. Gmail's `after:<epoch>` query
operator is server-side strict greater-than against `internalDate`,
so the next run picks up exactly from the message after the last one
processed — no overlap that re-bills LLM tokens, no gap that loses a
message.

**Atomicity rule:** the watermark advances only after the
**full** pipeline (fetch → filter → extract → validate → embed →
dedupe → Neo4j upsert) succeeds. Any failure → watermark unchanged →
next run re-fetches the same window → downstream dedup (message-ID
hash, node-ID hash, Neo4j `MERGE`) handles it idempotently.

The last real deploy was systemd on an EC2 box in the Loremind VPC,
with runs launched through the control API; see [`EXPLAIN.md`](EXPLAIN.md).
For Gmail and Zoho the substrate path (`runners.ledger_sync` /
`runners.zoho_ledger_sync` + `runners.substrate_flow`) replaced this loop.

---

## 11. Docker deployment

> The Fargate/EventBridge design described in older docs was never the
> real deploy. The last deploy was systemd on an EC2 box; see
> [`EXPLAIN.md`](EXPLAIN.md).

```bash
# Build
docker build -t loremind/lore-ingestion:1.1.0 .

# Run (on-prem; bind-mount a host dir for state)
docker run --rm \
    --env-file /etc/lore/lore.env \
    -v /srv/lore-state:/app/state \
    -v /srv/lore/secrets/key.json:/etc/lore/key.json:ro \
    loremind/lore-ingestion:1.1.0 \
    python -m runners.sync --org ezobooks --domain ezobooks.in \
        --admin-email admin@ezobooks.in --key /etc/lore/key.json
```

See `Dockerfile` for the image spec — multi-purpose, no `ENTRYPOINT`,
so the same image runs any stage by supplying the command.

---

## 12. Design principles

- **Org-agnostic.** No customer-specific logic in code. Everything
  per-customer lives in `output/config/{org}_config.json` and the
  org's environment.
- **Pure functions where possible.** Stages 2–6 are deterministic
  transforms of their input files. Re-running is free and produces
  identical output.
- **Deterministic IDs.** Same `(node_type, name, scope)` → same
  SHA-256 hash → same `node_id` in the vector index and Neo4j. No
  UUIDs, no auto-increments, no DB-assigned IDs in the pipeline.
- **Idempotent writes.** Neo4j writes use `MERGE` exclusively.
  Embedding writes skip already-cached IDs. The deduplicator is a
  fixed-point operation.
- **Atomic watermark.** The watermark is the only piece of mutable
  global state, and it advances exactly once per fully-successful
  run, written via `os.replace` so a crash never produces a
  half-written file.
- **Checkpoint everywhere there's batch work.** Stage 1 saves after
  every user; Stage 5 saves every 50 threads. A crash costs one
  batch, never the run.
- **Single image, single state dir.** The same Docker image and the
  same `LORE_STATE_DIR` path work on a Linux server, on Fargate
  with EFS, on a developer laptop, and in CI.
- **Provider-pluggable.** LLM and embedding providers are Protocols
  with a registry (`infra/llm`, `infra/embeddings`). Adding a
  provider = one new file + one line in the registry.
- **Graceful degradation.** If Neo4j is unreachable, the pipeline
  still produces validated triples and embeddings;
  `lore-engine` falls back to vector-only search until Neo4j is back
  up.
- **Logging is structured.** Every stage emits a `log_summary` line
  with stage name + key counts. End-of-stage gates (`check_nonempty`)
  refuse to silently produce zero output unless `--allow-empty` is
  passed.

---

## 13. Where to look when something breaks

| Symptom | First place to look |
|---------|---------------------|
| Gmail fetch returns 0 messages | `output/checkpoints/{org}_sync.json` — is the watermark too far forward? Gmail Workspace DWD scopes still granted? |
| Filter drops everything | Compare `_gmail_stats.json` (Stage 1) with `_gmail_filtered.jsonl` count. Most likely a misconfigured `--org-domains`. |
| Triple extraction stuck | Tail the log; if rate-limited, you'll see `AsyncRateLimiter` sleeping. NVIDIA cap is 40 RPM. |
| Validator rejects everything | Open `_triples_rejected.json` — every entry has a `reason` field. Top reasons: bad predicate direction, missing person, evidence too short. |
| Neo4j upload fails | Check Neo4j is reachable at `NEO4J_URI`; re-run `builders.unified_builder`. |
| Embedder out-of-memory | Reduce `--batch-size` (default 32) or move the embedding stage to a host with more RAM. |
| Incremental sync misses a message | This shouldn't happen by construction. Compare `output/checkpoints/{org}_sync.json:users` with the actual Gmail mailbox state via the Gmail web UI. File an issue with the watermark diff. |
| Container exits with code 137 | OOM. Set `--memory 6g` on `docker run` or bump Fargate task RAM to 8 GiB. |

For deeper investigation, every stage writes a per-day log file under
`output/logs/{org}_{stage}_YYYY-MM-DD.log` with full DEBUG output.

---

## 14. Contributing

We follow **GitHub Flow** with a protected `main`. The conventions
are in
[`/docs/git-best-practices.md`](../docs/git-best-practices.md) at the
monorepo root (one level up):

- Branch names: `<type>/<short-description>` — `feat/...`,
  `fix/...`, `refactor/...`, `pipeline/...`, `docs/...`, `chore/...`,
  `exp/...`.
- Commit messages: Conventional Commits
  (`feat(pipeline): add ...`, `fix(builders): ...`).
- One PR per concern. Refactors don't bundle bugfixes; features
  don't bundle docs.
- Open a **Draft PR** the moment you push your first commit, so the
  team can see what's in progress and which `main` branch it targets.

A typical change to this repo:

```bash
git checkout -b feat/<short-name>
# ... edits ...
git commit -m "feat(<scope>): <summary>"
git push -u origin feat/<short-name>
gh pr create --base main --title "feat(<scope>): <summary>" \
    --body "$(cat <<'EOF'
## Summary
- Bullet point what changed.
- Bullet point why.

## Test plan
- [ ] Stage 1 still runs on the EZO sample.
- [ ] No regression in validator reject rate.
EOF
)"
```

For deeper architectural notes, see `CLAUDE.md` (oriented at Claude
Code), `docs/modularity-phases/` (the phase-by-phase log of how the
code reached its current shape), and `EXPLAIN.md` for ops.
