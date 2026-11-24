# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

> **Dormant repo (as of 2026-09).** lore-ingestion is not deployed in prod since the CA-firm pivot; it is kept for possible reuse. Treat run/deploy instructions below as how it last worked, not as a live service.

## Project

**lore-ingestion** — multi-source knowledge ingestion pipeline for Loremind's Lore platform. Fetches organizational communications (Gmail, Slack, Jira; GitLab via chronograph), filters noise, extracts structured knowledge as graph triples via LLM, builds Neo4j graph and vector index. Each source carries ACL scope metadata so the knowledge graph can enforce per-user visibility.

- Python 3.12, shared venv at `../.venv/` (system Python is 3.14)
- **Always use `../.venv/bin/python`** from repo root

## Commands

```bash
# ── Gmail Pipeline (run in order from lore-ingestion/ dir) ──

# Stage 1: Fetch raw emails (checkpoint/resume per user)
../.venv/bin/python -m infra.gmail.run --org ezobooks --domain ezobooks.in --admin-email admin@ezobooks.in --key key.json

# Stage 2: Noise filtering (signal-based; no LLM needed for ~70% removal)
../.venv/bin/python -m pipeline.filter --input output/raw/ezobooks_gmail_*.jsonl --org ezobooks --skip-llm

# Stage 3: Identity resolution (cross-domain alias merging)
../.venv/bin/python -m pipeline.person_registry --input output/filtered/ezobooks_gmail_filtered.jsonl --org ezobooks

# Stage 4: Communication graph (directed edges, Union-Find communities)
../.venv/bin/python -m pipeline.comm_graph --input output/filtered/ezobooks_gmail_filtered.jsonl --org ezobooks

# Stage 5: LLM triple extraction (async, per-provider concurrency, checkpoint/resume)
../.venv/bin/python -m pipeline.triple_extractor --org ezobooks --resume

# Stage 6: Deterministic validation gate (schema + content + fuzzy person name resolution via rapidfuzz)
../.venv/bin/python -m pipeline.validator --org ezobooks

# Stage 6.5: Embed knowledge nodes (EMBEDDING_MODEL, default nvidia/llama-nemotron-embed-1b-v2; shared by dedup + builder)
../.venv/bin/python -m pipeline.embed_knowledge --org ezobooks

# Stage 6.6: Entity deduplication (embedding cosine + connected components)
../.venv/bin/python -m pipeline.deduplicator --org ezobooks

# ── Outlook Mail + MS Teams Connector (MS365, mirrors Gmail/Slack/Jira) ──
# All three stages share the OutlookPaths registry (output/raw, output/filtered,
# output/outlook). Fetch NORMALIZES raw Graph payloads into KnowledgeEvents
# before writing — downstream stages are source-agnostic.

# Discover: enumerate MS365 users + Teams channel ACLs (writes ACL artifacts only)
../.venv/bin/python -m infra.outlook.run --org ezobooks --stage discover

# Fetch: delta-sync mail + Teams → normalize → output/raw/{org}_outlook_mail.jsonl
#                                              output/raw/{org}_ms_teams.jsonl
../.venv/bin/python -m infra.outlook.run --org ezobooks --stage fetch --limit 100

# Smoke: end-to-end filter → person_registry → triple extraction on fetched raw
../.venv/bin/python -m infra.outlook.run --org ezobooks --stage smoke --source both

# Then run the shared downstream stages source-namespaced (per source):
../.venv/bin/python -m pipeline.validator     --org ezobooks --source outlook_mail
../.venv/bin/python -m pipeline.embed_knowledge --org ezobooks --source outlook_mail
../.venv/bin/python -m pipeline.deduplicator  --org ezobooks --source outlook_mail
../.venv/bin/python -m builders.unified_builder --org ezobooks --source outlook_mail
# (repeat with --source ms_teams)

# ── Zoho Mail + Cliq Connector (mirrors Outlook; self-client OAuth) ──
# All stages share the ZohoPaths registry (output/raw, output/filtered,
# output/zoho). Fetch NORMALIZES raw Mail/Cliq payloads into KnowledgeEvents
# before writing — downstream stages are source-agnostic. Zoho has no native
# delta-link, so incremental sync uses a client-side epoch-ms watermark
# cursor (output/checkpoints/{org}_zoho_cursors.json) instead of a true delta token.

# Discover: enumerate Zoho users + Cliq channel ACLs (writes ACL artifacts only)
../.venv/bin/python -m infra.zoho.run --org ezobooks --stage discover

# Fetch: watermark-sync mail + Cliq → normalize → output/raw/{org}_zoho_mail.jsonl
#                                                  output/raw/{org}_zoho_cliq.jsonl
../.venv/bin/python -m infra.zoho.run --org ezobooks --stage fetch --limit 100

# Smoke: end-to-end filter → person_registry → triple extraction on fetched raw
../.venv/bin/python -m infra.zoho.run --org ezobooks --stage smoke --source both

# Then run the shared downstream stages source-namespaced (per source):
../.venv/bin/python -m pipeline.validator     --org ezobooks --source zoho_mail
../.venv/bin/python -m pipeline.embed_knowledge --org ezobooks --source zoho_mail
../.venv/bin/python -m pipeline.deduplicator  --org ezobooks --source zoho_mail
../.venv/bin/python -m builders.unified_builder --org ezobooks --source zoho_mail
# (repeat with --source zoho_cliq)

# ── Database Builders (produce artifacts for lore-engine) ──

# Unified builder: Neo4j Cypher (passes 0-2) + NVIDIA NeMo embedding upload (pass 3) in one pass
../.venv/bin/python -m builders.unified_builder --org ezobooks

# Post-processing: topic clustering, person expertise, process synthesis, cross-thread edges
../.venv/bin/python -m builders.post_processor --org ezobooks

# ── 4-layer substrate (primary path for Gmail + Zoho) ──
# L0 Postgres ledger (source_payloads + cursors) → L1 registries → L2 edges →
# L3 claims → projection to a SEPARATE claims Neo4j (CLAIMS_NEO4J_*).
../.venv/bin/python -m runners.ledger_sync ...           # Gmail → L0 ledger
../.venv/bin/python -m runners.zoho_ledger_sync --org X  # Zoho → L0 ledger
../.venv/bin/python -m runners.extract_claims ...        # L3 claim extraction
../.venv/bin/python -u -m runners.substrate_flow --org X # daemon: keeps L1/L2/dedup/projection flowing

# ── Control API (FastAPI; elysia-rbac launches runs through it) ──
../.venv/bin/uvicorn control.app:app --host 0.0.0.0 --port 8100   # see EXPLAIN.md

# ── Evaluation (5-round QA framework) ──
../.venv/bin/python -m evaluation.round1_structural --file output/knowledge/ezobooks_triples_raw.json
../.venv/bin/python -m evaluation.round2_content_quality
../.venv/bin/python -m evaluation.round3_edge_cases
../.venv/bin/python -m evaluation.round4_hard_threads
../.venv/bin/python -m evaluation.round5_final_validation

# ── Key flags ──
--resume          # Continue from checkpoint (stages 1 and 5)
--dry-run         # Preview without writing
--skip-llm        # Rule-based classification only (filter stage)
--limit N         # Process only first N threads (triple_extractor; useful for testing)
--thread-ids FILE # Extract only specific thread IDs listed in file
--discover-noise  # Auto-learn org noise domains (filter stage)
--users-file X    # Fetch specific users only (stage 1)
--since DATE      # Incremental fetch since ISO date (stage 1)
```

Tests (200+ across pipeline + connectors):

```bash
../.venv/bin/python -m pytest pipeline/tests/ infra/ tests/ -q
```

For live-data validation of pipeline stages use `--dry-run` or `--limit 5`.

## Architecture

### Module Layout

```
infra/              Layer 1 — source connectors + external providers.
                    Each connector exposes the SAME contract: fetcher → normalizer
                    (→ KnowledgeEvent) → optional discovery (→ scope_principals)
                    → run.py (thin, fetch-only, gated). Connectors import ONLY
                    common.* and their own package — never pipeline/ or builders/
                    (enforced by pipeline/tests/test_layering.py).
  gmail/              run.py / fetcher.py (DWD auth, batch 50/req) / normalizer.py
  slack/              run.py / fetcher.py / normalizer.py / discovery.py (ACL) /
                      socket_mode.py (realtime polling alternative)
  jira/               run.py / fetcher.py / normalizer.py / discovery.py (ACL +
                      security crawl) / metrics.py
  outlook/ zoho/      MS365 (Outlook + Teams) and Zoho (Mail + Cliq) connectors
  github/ gitlab/     code-host connectors (chronograph)
  llm/                ChatProvider registry — NVIDIA / OpenRouter / Bedrock
  embeddings/         EmbeddingProvider registry — NVIDIA / OpenAI / Bedrock

pipeline/           Layer 2 — processing stages (source-agnostic; per-source
                    data-shape differences come from common/sources.py, not
                    inline `if source ==`)
  filter.py           Stage 2 — signal-based noise removal + optional LLM classification
  person_registry.py  Stage 3 — cross-domain identity resolution, alias merging
  comm_graph.py       Stage 4 — directed communication graph, Union-Find communities
  triple_extractor.py Stage 5 — thin shim; implementation in extraction/extract.py
  validator.py        Stage 6 — deterministic quality gate: schema, content, person names

builders/           Produce database artifacts for lore-engine
  l0_ledger.py … l3_claims.py  Substrate layers (L0 ledger, L1 registries,
                      L2 edges, L3 claims); dedup_claims.py, project_claims.py
                      (projection to the claims Neo4j)
  unified_builder.py  Recommended entry point. Passes 0-2 emit Cypher
                      (schema, persons, knowledge); pass 3 uploads
                      pre-computed NVIDIA NeMo embeddings to Neo4j nodes.
  post_processor.py   Cross-thread post-processing: topic clustering (HDBSCAN +
                      LLM labeling), person expertise, process synthesis,
                      SUPERSEDES/PRECEDED_BY temporal edges.

common/             Layer 0 — contracts/utilities.
                    No dependencies on infra/pipeline/builders.
  config.py           SINGLE source of env config (Pydantic Settings). All
                      Gmail/Slack/Jira/Neo4j/LLM vars live here; validate_for()
                      fails fast per stage. No os.getenv elsewhere.
  hashing.py          Canonical short_hash(), make_id(), node_id_for() — every
                      source (incl. Jira) routes IDs through here.
  scopes.py           SINGLE owner of ACL scope/principal ID formats
                      (slack:channel:, jira:project:, jira:U…, etc.).
  sources.py          SOURCE_CAPS — per-source data-shape table (thread key,
                      whether scope/citation live in `raw`).
  rbac.py             Department classification, sensitivity labeling.
  schemas.py          KnowledgeEvent, Actor, EventType — unified event format.
  schemas_extraction.py  Pydantic: NodeType, Predicate, Triple, ExtractionResult.
  org_config.py       Per-org JSON config (domains, functional mailboxes, noise).
  output_paths.py     Artifact path registry — KnowledgePaths, IdentityPaths, JiraPaths,
                      SlackPaths, OutlookPaths, ZohoPaths.
  vault_client.py     Per-tenant creds from vault-proxy; fails closed.
  email_canon.py      Email address canonicalization.
  stage_gate.py       check_nonempty() — fail loudly on 0-output stages.
  stage_summary.py    log_summary() — one greppable STAGE_SUMMARY line per stage.
  watermark.py        Per-user incremental-sync state (Gmail fetch).
  logger.py           Colored console + file logging.

runners/            Layer 3 — orchestration (may import every layer below).
                    16 entry points; the main ones:
  ledger_sync.py / zoho_ledger_sync.py / l0_sync.py  Substrate L0 sync (primary
                      path for Gmail + Zoho).
  extract_claims.py   L3 claim extraction.
  substrate_flow.py   Daemon that re-runs L1/L2/dedup/projection as data lands.
  sync.py             Legacy incremental Gmail delta loop (triple pipeline).
  slack_reconcile.py / slack_apply_delta.py / ingest_jira.py / outlook_smoke.py
  jira_smoke.py       End-to-end Jira fetch→pipeline (invoked by infra.jira.run
                      --stage smoke via a deferred import; keeps infra one-way).

chronograph/        GitLab code intelligence (separate pipeline)
  ingestion/          Fetch → parse → embed → PostgreSQL
  parsing/            Tree-sitter AST for 8+ languages
  diffing/            Semantic diff, commit classification
  query/              PostgreSQL + pgvector + LLM queries
  store/              PostgreSQL + pgvector (pg_store.py)

control/            FastAPI control API on :8100 (runs, pipeline dashboard, logs);
                    X-Internal-Auth, fail-closed. See EXPLAIN.md.
agent/              LangGraph agent (chronograph).
health/             Health checks (see health/README.md).

evaluation/         5-round QA framework for triple extraction quality
  round1_structural.py    Schema + predicate validation, orphan node checks
  round2_content_quality.py  Rubber stamps, garbage content, name format issues
  round3_edge_cases.py    Complex scenarios: nested decisions, ambiguous pronouns
  round4_hard_threads.py  Difficult cases: long threads, conflicting information
  round5_final_validation.py  Integration test: Neo4j queryable + RBAC checks

docs/               Architecture, pipeline, setup documentation
  modularity-phases/  Resumable phase tracker for the modular redesign migration
scripts/            Utility scripts (model_testing.py)
```

### Pipeline Flow

```
Gmail API (DWD)
  → Fetch (infra/gmail/run.py)
      → output/raw/{org}_gmail_{date}.jsonl
  → Filter (pipeline/filter.py)
      → output/filtered/{org}_gmail_filtered.jsonl
  → Person Registry (pipeline/person_registry.py)
      → output/knowledge/{org}_person_registry.json
  → Comm Graph (pipeline/comm_graph.py)
      → output/knowledge/{org}_comm_graph.json
  → Triple Extractor (pipeline/triple_extractor.py)
      → output/knowledge/{org}_triples_raw.json        [LLM output]
  → Validator (pipeline/validator.py)
      → output/knowledge/{org}_triples_validated.json  [approved]
      → output/knowledge/{org}_triples_rejected.json   [audit trail]
  → Unified Builder (builders/unified_builder.py)
      → output/knowledge/embeddings/{org}_knowledge_embeddings.npy (pipeline/embed_knowledge.py)
      → output/cypher/pass*.cypher → Neo4j
```

All output goes to `output/` subdirectories: `raw/`, `filtered/`, `knowledge/`, `cypher/`, `logs/`, `checkpoints/`, `config/` (`index/` is legacy).

### Triple Extraction (Stage 5 — pipeline/extraction/extract.py)

The core LLM stage. Reads email threads grouped by `thread_id`, calls LLM to extract structured knowledge as graph triples.

**Model:** DeepSeek v3.2. `LLM_PROVIDER` defaults to `bedrock` (`BEDROCK_MODEL`, default `deepseek.v3.2`); with `nvidia` it is `deepseek-ai/deepseek-v3.2` via NVIDIA Build (env: `EXTRACTION_MODEL`, `NVIDIA_BASE_URL`, `NVIDIA_API_KEY`; 40 RPM cap). `LLM_PROVIDER` selects nvidia / openrouter / bedrock (see the comment block in `pipeline/extraction/extract.py`).
**Concurrency:** set per provider in `pipeline/extraction/extract.py` (`_PROVIDER_LIMITS`): nvidia 4 concurrent / 38 RPM, openrouter 8 / 60 RPM, bedrock from `bedrock_rpm`/`bedrock_concurrency` (plus a TPM-aware limiter). Batches of 50 threads.
**Checkpointing:** Saves after every batch (50 threads). Resume with `--resume`.

**Thread grouping strategy** (by email count):
- ≤6 emails → full body for all
- ≤15 emails → full body for first 2 + last 2, subjects only for middle
- \>15 emails → first 3 + last 3 full, middle subjects only

**Ontology (9 predicates, 7 node types):**

| Predicate | Subject → Object | Meaning |
|-----------|-----------------|---------|
| DECIDED | Person → Decision | Person made this decision |
| BECAUSE | Decision → Fact | Rationale for a decision |
| STATED_BY | Fact/Decision → Person | Who said this |
| ASSIGNED_TO | Task → Person | Who owns this task |
| REGARDING | Fact/Decision/Task → Topic | What it's about |
| SUBJECT_OF | Event → Topic | Event concerns this topic |
| WORKS_AT | Person → Organization | Employment relationship |
| PARTICIPATED_IN | Person → Event | Who was in meeting/call |
| MENTIONS | Fact/Decision → Person/Org | Referenced entity |

**Retry + self-correction:** 3 retries with exponential backoff; validation failures trigger self-correcting re-prompt (error message appended to conversation).

**Output per thread:**
```json
{
  "thread_id": "...",
  "triples": [{"subject": {...}, "predicate": "DECIDED", "object": {...}, "evidence_sentence": "..."}],
  "thread_summary": "Two-sentence summary",
  "department_tags": ["hr"],
  "sensitivity": "confidential"
}
```

### Validator (Stage 6 — validator.py)

Deterministic quality gate before database builders. Checks:
- Schema: valid predicate, correct subject/object node types per predicate direction
- Content: descriptions ≥8 chars, no HTML/CSS, no placeholders
- Evidence: DECIDED/BECAUSE/ASSIGNED_TO require evidence ≥10 chars
- Persons: all Person nodes must exist in person registry (no phantom names)
- Auto-correction: fixes known directional errors (e.g., reversed DECIDED subject/object)

Produces `_triples_validated.json` (approved) and `_triples_rejected.json` (audit trail).

### Key Data Structures

**KnowledgeEvent** (`common/schemas.py`): Unified format across all sources. Fields: `id` (SHA-256), `source`, `event_type`, `timestamp`, `title`, `body`, `actors`, `primary_actor`, `project`, `tags`, `raw`. The `id` is a stable hash of `(source, source_id)` for deduplication.

**ExtractionResult** (`common/schemas_extraction.py`): Pydantic model for LLM output validation. Contains `triples: list[Triple]`, `thread_summary`, `department_tags`, `sensitivity`.

**Org Config** (`output/config/{org}_config.json`): Per-org settings — `org_domains`, `extra_functional_mailboxes`, `discovered_noise_domains`, `allowlisted_domains`. Loaded/saved by `common/org_config.py`.

**Node IDs**: Deterministic SHA-256 hashes via `common/hashing.py` (`short_hash()`, `make_id()`, `node_id_for()`). Format: `dec:{hash}`, `fact:{hash}`, `evt:{hash}`, `task:{hash}`, `topic:{hash}`, `person:{hash}`, `org:{hash}`. Shared between Neo4j graph and vector index for cross-referencing. All sources (incl. Jira) route through `common/hashing.py` — no source defines its own ID formula.

**ACL scope/principal IDs**: Format strings (`slack:channel:`, `jira:project:`, `jira:seclevel:`, `slack:{user}`, `jira:U{account}`, `jira:G{group}`) are owned solely by `common/scopes.py`. These strings are persisted in Neo4j and `*_scope_principals.json` — changing one is a breaking data-format change (locked by `pipeline/tests/test_scopes.py`).

## Key Design Patterns

- **Org-agnostic**: No company-specific logic in code. Everything driven by org config JSON.
- **Checkpoint/resume**: Fetch saves after each user, LLM extraction after every 50-thread batch. Use `--resume` to continue safely.
- **Signal-based filtering before LLM**: Gmail categories, noreply headers, unsubscribe footers, domain ratio analysis — eliminates ~70% noise without LLM.
- **Thread-level LLM extraction**: Groups by thread_id; 3 size buckets reduce API calls from ~35k emails to ~1-3k LLM calls.
- **Async concurrency**: per-provider semaphore + rate limiter for LLM calls (nvidia 4 / 38 RPM, openrouter 8 / 60 RPM, bedrock config-driven). Rate-limited Gmail fetch (0.1s/req, 2s/user cooldown).
- **Cross-domain alias merging**: Identity resolution matches `user@domain1` = `user@domain2` by local part within org domains.
- **Ingestion ↔ Engine separation**: lore-ingestion produces artifacts (validated triples, embeddings, Neo4j Cypher). lore-engine consumes them for serving queries.
- **5-round evaluation**: `evaluation/` contains structured QA tests covering structural, content quality, edge cases, hard threads, and integration checks.

## Environment Variables (.env)

All config is read in ONE place: `common/config.py` (Pydantic `Settings`). Code
calls `get_settings()` — do not add `os.getenv` elsewhere. `validate_for("<stage>")`
fails fast when a stage's required vars are missing. See `.env.example` for the
full template.

**Required for the main flow:**
- `NVIDIA_API_KEY` — LLM extraction (`deepseek-ai/deepseek-v3.2`) + NVIDIA embeddings (or `LLM_PROVIDER=bedrock` + `BEDROCK_*`).
- `NEO4J_URI`, `NEO4J_USERNAME`, `NEO4J_PASSWORD` — `builders/unified_builder.py` pass 3.

**Source connectors:**
- Slack: `SLACK_TOKEN` (xoxp- admin user token preferred; xoxb- bot token also accepted), `SLACK_APP_TOKEN`, `SLACK_ORG`, `SLACK_INGEST_MODE`
  (polling|socket|hybrid), `SLACK_APP_TIER`, `SLACK_REQUEST_DELAY`,
  `SLACK_SCANBACK_DAYS`, `SLACK_DEFAULT_RETRY_SLEEP`, `SLACK_BOT_ALLOWLIST`,
  `SLACK_WORKSPACE_DOMAIN`.
- Jira: `JIRA_BASE_URL`, `JIRA_EMAIL`, `JIRA_API_TOKEN` (or `JIRA_OAUTH_TOKEN` / `JIRA_PAT`).

**Optional / per-stage:**
- `EXTRACTION_MODEL` (default `deepseek-ai/deepseek-v3.2`), `NVIDIA_BASE_URL`.
- `OPENROUTER_API_KEY` — `builders/post_processor.py` topic-name clustering.
- `ANTHROPIC_API_KEY` — only when `pipeline/filter.py` runs without `--skip-llm`.
- `GITLAB_PAT`, `GITLAB_BASE_URL`, `DATABASE_URL` (or `POSTGRES_URI`) — chronograph (GitLab) pipeline.
- `ORG` — default org slug for stages that read it.

Google service account key in `key.json` with domain-wide delegation scopes: `gmail.readonly`, `admin.directory.user.readonly`.

<!-- LOREKEEPER:BEGIN (generated - do not edit by hand) -->
## Lorekeeper decision context
_Generated from `lorekeeper/decisions/`. Records are the source of truth; edit them, not this block. Run `lorekeeper compile` to refresh._

### lore-ingestion

**Load-bearing invariants:**
- **[D-INGEST-001]** MUST give every knowledge node `scope_ids`; a node with empty scopes is invisible

**Decisions:**
- `D-INGEST-001` [accepted] Fail-closed scopes and deterministic SHA-256 node IDs ⭐ — `lore-ingestion/common/hashing.py, lore-ingestion/common/scopes.py`

### cross-cutting

**Load-bearing invariants:**
- **[D-CROSS-001]** lore-ingestion MUST write artifacts (Neo4j graph, embeddings, scope/identity files).

**Decisions:**
- `D-CROSS-001` [accepted] Ingestion produces artifacts; engine consumes them (no shared runtime) ⭐ — `lore-ingestion/**, lore-engine/**`
<!-- LOREKEEPER:END -->
