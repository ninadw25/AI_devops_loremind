> **Historical (as of 2026-09-25)** — superseded by `docs/slack/SLACK_ONBOARDING.md` and `docs/slack/SLACK_PIPELINE.md` (IN_SCOPE wiring and several scripts named here are gone). Kept for context; do not treat as current.

# Slack Connector Integration README

This document summarizes the Slack connector integration work completed in this repository, including all files created or updated during the process and what they do.

## Status Summary

- Slack connector source code has been integrated under `infra/slack/`.
- Shared/common logic needed by Slack was moved into existing common modules.
- Environment templates were updated with Slack variables.
- Post-merge normalization output was generated and compared against pre-merge data.
- Original external source folder (`slack-connector/`) was removed after integration.

## Model & Env changes (important)

- **Extraction model/provider:** Stage 5 extraction was standardized to use OpenRouter with `google/gemini-2.5-flash` as the default LLM when `OPENROUTER_API_KEY` is present. This replaces the prior NVIDIA DeepSeek default used in some branches.
- **Embedding model/provider:** Stage 6.5 embeddings use the NVIDIA embeddings endpoint by default and we migrated from the deprecated `nvidia/llama-3.2-nemoretriever-300m-embed-v1` to the supported `nvidia/llama-nemotron-embed-1b-v2` (2048-dim). Provider lifecycle can force a model swap; the code reads the model from `EMBEDDING_MODEL` in settings so it is configurable via `.env`.
- **Env vars to set:** ensure the following are present in your local `.env` when running end-to-end with embedding upload:
  - `OPENROUTER_API_KEY` (for Stage 5 extraction using OpenRouter)
  - `OPENROUTER_LLM_MODEL` (optional override, default `google/gemini-2.5-flash`)
  - `EMBEDDING_PROVIDER` (default `nvidia`)
  - `EMBEDDING_MODEL` (default `nvidia/llama-nemotron-embed-1b-v2`)
  - `NVIDIA_API_KEY` (required for embedding upload when `EMBEDDING_PROVIDER=nvidia`)
  - `NEO4J_URI`, `NEO4J_USERNAME`, `NEO4J_PASSWORD` (for Pass 3 upload)

Note: If an embedding model is deprecated by the provider you will see an HTTP 410 error; the pipeline now surfaces a clear message and allows switching `EMBEDDING_MODEL` to a supported model.

## 1) New Slack Runtime Modules

### `infra/slack/__init__.py`
- Package marker for Slack integration modules.

### `infra/slack/normalizer.py`
- Converts Slack API message payloads into `KnowledgeEvent` records.
- Handles:
  - Noise filtering (`is_noise`)
  - Message body composition (`compose_message_body`)
  - Event type mapping (`determine_event_type`)
  - Thread classification (`classify_thread`)
  - Actor creation and final `KnowledgeEvent` construction (`build_knowledge_event`)

### `infra/slack/fetcher.py`
- Slack Web API client logic.
- Handles:
  - Auth checks and scope validation
  - User cache loading
  - Channel listing
  - Message and thread fetching with retry/rate-limit behavior
  - Event iteration per channel

### `infra/slack/connector.py`
- Connector wrapper/orchestrator around fetcher + normalizer.
- Provides ingestion/fetch workflow entry points.

### `infra/slack/run.py`
- CLI entrypoint for Slack ingestion.
- Supports polling/socket/hybrid modes and common options like:
  - `--org`, `--token`, `--app-token`, `--mode`
  - `--since`, `--resume`, `--dry-run`, `--channels`
- Handles output path setup, checkpointing, and run lifecycle.

### `infra/slack/socket_mode.py`
- Socket Mode ingestion support (realtime events) using Slack app token.

## 2) Shared/Common Module Updates for Slack

### `common/schemas.py` (updated)
- Added Slack event type constants under `EventType`:
  - `SLACK_MESSAGE`, `SLACK_DELETED`, `SLACK_REPLY`, `SLACK_DISCUSSION`, `SLACK_DM`, `SLACK_GDM`
- Updated `Actor` model to include:
  - `is_bot`
  - `is_deleted`
- This aligns common actor schema with Slack user/bot metadata.

### `common/hashing.py` (updated)
- Added `make_slack_id(org, channel_id, ts)` for deterministic Slack event IDs.
- ID strategy matches integration requirement for stable dedup-safe IDs.

## 3) Environment Files Added/Updated

### `.env.example` (updated)
- Added Slack variable placeholders and tuning keys:
  - `SLACK_TOKEN`
  - `SLACK_APP_TOKEN`
  - `SLACK_ORG`
  - `SLACK_INGEST_MODE`
  - `SLACK_APP_TIER`
  - `SLACK_REQUEST_DELAY`
  - `SLACK_WORKSPACE_ID`
  - `SLACK_SCANBACK_DAYS`
  - `SLACK_DEFAULT_RETRY_SLEEP`
  - `SLACK_BOT_ALLOWLIST`

### `slack.env.example` (created)
- Slack-only env template with non-secret placeholders and recommended defaults.
- Intended to copy values into local `.env`.

### `.env` (created locally)
- Local runtime secrets file copied from source env during migration.
- This file is local-only and must not be committed.

## 4) Validation / Comparison Scripts Created

### `scripts/generate_post_merge.py`
- Rebuilds Slack normalized events (post-merge style) from pre-merge JSONL raw payloads.
- Writes regenerated output to `output/ci_post_merge/`.

### `scripts/compare_jsonl.py`
- Compares pre/post JSONL files as normalized JSON multisets.
- Reports counts and only-in-pre / only-in-post differences.

### `scripts/test_build_event.py`
- Single-record debug runner to verify `build_knowledge_event` behavior.

### `scripts/debug_normalizer.py`
- Batch debug helper to inspect normalization failures across records.

## 5) Generated Validation Artifacts

### `output/ci_post_merge/ezobooks_slack_2026-05-26.jsonl`
- Regenerated post-merge Slack output used for comparison checks.

### `output/ci_pre_merge/ezobooks_slack_2026-05-24.jsonl`
- Local pre-merge reference copy created during validation workflow.

## 6) Source Folder Cleanup

### `slack-connector/` (deleted)
- External source folder removed after integration into `infra/slack`.
- All runtime Slack functionality required by this repo is now maintained in:
  - `infra/slack/*`
  - shared updates in `common/*`

## 7) Operational Notes

- Normalization parity check (pre vs post) was executed using the comparison tooling in this repo.
- Live Slack end-to-end validation (real API polling/socket) depends on valid local `.env` tokens and scopes.
- For runtime ingestion, use:
  - `python -m infra.slack.run --org <org> --token <xoxb...> --dry-run`
  - add `--app-token <xapp...>` for socket/hybrid modes.

### Required Slack bot token scopes (live discovery/testing)

For live Stage 0 discovery and Slack test runs, the bot token should include:

- `channels:read`
- `groups:read`
- `users:read`
- `users:read.email`

If one or more of these are missing, Slack API calls may fail with `missing_scope` errors and discovery can be partial or fail fast depending on the endpoint and auth check path.

## Stage 0 — ACL Discovery (what I ran and verified)

- Purpose: discover workspace + channel scopes and private-channel memberships to emit an ACL artifact and idempotent Cypher for Neo4j ingestion.
- Command used (live run against Slack; token provided locally — token not echoed):
  - `python -m infra.slack.run --org ezobooks --stage discovery --token <REDACTED>`

- Artifacts produced:
  - `output/slack/ezobooks_scope_principals.json` — contains `scopes`, `principals`, and `scope_principals` arrays.
  - `output/cypher/ezobooks_slack_acl.cypher` — MERGE statements for `:AclScope` nodes (workspace + channels).
  - Example counts from the live run: `5 scopes`, `2 principals`, `2 scope_principals` (see `output/slack/ezobooks_scope_principals.json`).

  - Live run details: the discovery run produced 5 scopes, 2 principals and 2 scope_principals for the `ezobooks` workspace. Only the private channel `#work-to-do` (scope_id `slack:channel:C0B5LTQP2GJ`) contained `scope_principals`; public channels have no membership entries in `scope_principals`.

  - Idempotency: discovery was re-run into a second output directory (`output/run2`) and the artifacts (`output/slack/ezobooks_scope_principals.json` and `output/cypher/ezobooks_slack_acl.cypher`) were byte-identical between runs, confirming deterministic and idempotent output.

- Verification performed:
  1. Ran discovery twice into separate output directories to check idempotency:
     - `python -m infra.slack.run --org ezobooks --stage discovery --token <REDACTED> --output-dir output/run1`
     - `python -m infra.slack.run --org ezobooks --stage discovery --token <REDACTED> --output-dir output/run2`
  2. Compared artifacts with the included helper script:
     - `python scripts/idempotency_check.py`
     - This script loads and deterministically sorts `scopes`, `principals`, and `scope_principals`, then diffs the JSON and the Cypher text.
  3. Result: `IDEMPOTENCY CHECK: PASSED` — JSON and Cypher files were identical between runs.

- Notes and next steps:
  - Idempotency ensures the generated Cypher uses `MERGE` correctly and can be applied multiple times without creating duplicates.
  - If you want to include archived channels or a channel subset, re-run discovery with `--include-archived` or `--channels <name|id>` and re-check artifacts.
  - Recommended next action: run Stage 1 (fetch + normalize) to produce `output/raw/{org}_slack_*.jsonl` and validate normalized event output with the existing comparison tooling.

  
### Files created for Stage 0 (discovery)

- `infra/slack/discovery.py` — Stage 0 implementation: lists channels, enumerates private-channel members, builds the `ScopePrincipalArtifact`, and writes JSON + Cypher artifacts.
- `infra/slack/tests/test_discovery.py` — Unit smoke test using a `FakeSlackFetcher` to assert scope/principal shapes and private/public membership handling.
- `infra/slack/tests/test_discovery_full.py` — Offline pytest using `FakeSlackFetcher` validating JSON artifact shape, Cypher contents, public/private behavior, and idempotency.
- `scripts/idempotency_check.py` — Helper script that compares two discovery runs: normalizes and diffs `scopes/principals/scope_principals` JSON and the generated Cypher text; exits non-zero on differences.
- `output/slack/{org}_scope_principals.json` — JSON artifact produced by discovery (example: `output/slack/ezobooks_scope_principals.json`).
- `output/cypher/{org}_slack_acl.cypher` — Cypher file with `MERGE` statements for `:AclScope` nodes created by discovery (example: `output/cypher/ezobooks_slack_acl.cypher`).

These files collectively implement Stage 0, support unit testing, and provide idempotency verification for safe application of ACL Cypher to Neo4j.

## Step 2 — Fetcher + Normalizer Wiring

- Purpose: fetch Slack history/replies, normalize raw Slack messages into `KnowledgeEvent`, and keep Slack-specific noise handling inside the Slack layer.
- Implemented behavior:
  - `conversations.history` and `conversations.replies` are used for channel and thread ingestion.
  - Tier 3 pacing is enforced for history/replies at ~50 requests/min (`SLACK_APP_TIER=tier3` => 1.2s minimum delay).
  - `Retry-After` headers are honored on 429 responses and the delay is raised when Slack tells us to back off.
  - `raw.slack_meta.channel_id` is stamped onto each raw Slack message.
  - `raw.scope_id = slack:channel:{channel_id}` is written during normalization.
  - `source_ref` and `source_url` are generated for each normalized record.
  - Slack noise is filtered in the Slack normalizer path, not in the Gmail filter stage.
  - `SLACK_WORKSPACE_DOMAIN` is used to generate workspace-correct deep links.

### Files updated/used in Step 2 and their purpose

- `infra/slack/fetcher.py` - Slack Web API client for channel history, thread replies, user caching, and rate-limit-aware retry/backoff.
- `infra/slack/normalizer.py` - Converts raw Slack payloads into `KnowledgeEvent`, applies Slack noise rules, and writes `raw.scope_id`, `source_ref`, and `source_url`.
- `infra/slack/run.py` - CLI Slack ingestion runner updated to use the Slack normalizer path for message handling.
- `infra/slack/connector.py` - Async Slack connector wrapper updated to use the same Slack normalization path.
- `infra/slack/socket_mode.py` - Realtime Socket Mode path updated to use the same Slack normalization path.
- `scripts/run_stage1_manual.py` - Manual Stage 1 runner used to verify normalized Slack output on selected and full channel sets.

### Files produced in Step 2 and their purpose

- `output/raw/ezobooks_slack_2026-05-26.jsonl` - Sample normalized Slack output for selected channels, used to verify `raw.scope_id`, `source_ref`, `source_url`, and `raw.slack_meta.channel_id`.
- `output/raw/ezobooks_slack_2026-05-27.jsonl` - All-channel normalized Slack output, used to verify the same Step 2 fields across the full accessible channel set.

### Verification result

- The sample and full runs both produced normalized records with the required Step 2 fields.
- Final integrity check on the all-channel output reported `records 18` and `missing 0`.

## Step 3 — Slack Grouping Tweak

- Purpose: make extraction grouping source-aware so Slack threads are grouped by `thread_ts` while preserving the Slack channel `scope_id`.
- Implemented behavior:
  - Slack records use `thread_ts` as the grouping key.
  - `raw.scope_id` is carried into grouped units.
  - Participant union is not applied for Slack, because ACL comes from channel membership discovered in Stage 0.

### Files created in Step 3 and their purpose

- `scripts/verify_slack_step3.py` - Small PASS/FAIL verifier that runs `pipeline/extraction/grouping.py` against Slack raw output and checks grouping, scope propagation, and participant-union absence.

### Files modified in Step 3 and their purpose

- `pipeline/extraction/grouping.py` - Added the Slack branch that groups by `raw.thread_ts`/`ts`, keeps `raw.scope_id`, and leaves Gmail grouping unchanged.

### Verification result

- Verified successfully against `output/raw/ezobooks_slack_2026-05-27.jsonl`.
- The verifier reported: `PASS: 13 Slack threads grouped correctly`.

### Why this matters

- This keeps Slack extraction thread-based for LLM processing while preserving channel-scope ACL semantics for downstream validation and building.


## Step 4 — Builder Source-Aware ACL Behavior

- Purpose: ensure the builder creates/merges Slack `:AclScope` nodes (identity-only), attaches `IN_SCOPE` edges from knowledge nodes to the correct channel scope, and does not write mutable `participants` data onto scope nodes.
- Key change: `builders/unified_builder.py` now sets `scope_id` on knowledge node merges and emits `_cypher_for_in_scope()` blocks to MERGE `:AclScope` and the `IN_SCOPE` relation.
- Verification: generated Pass-2 Cypher contains `t.scope_id = 'slack:channel:...'` and `MERGE (n)-[:IN_SCOPE]->(s)`. Quick verifier `scripts/test_in_scope_cypher.py` reports IN_SCOPE occurrences.

## Changes Implemented (summary — May 27, 2026)

### What we did
- **Builder ACL**: Added source-aware ACL wiring in `builders/unified_builder.py` so knowledge nodes retain `scope_id` and the builder emits `IN_SCOPE` edges to `:AclScope` (builder does not write mutable membership).
- **Extraction & Validation plumbing**: Preserved `scope_id`, `source_ref`, and `source_url` through extract → validate → build; made the extractor tolerant to fenced/prefixed JSON and added an OpenRouter fallback when `NVIDIA_API_KEY` is not present (for test environments).
- **Grouping**: Added Slack branch in `pipeline/extraction/grouping.py` to group threads by `thread_ts` and propagate channel scope metadata.
- **Verification & helpers**: Added small scripts to generate sample cypher, scan generated cypher for `IN_SCOPE`, and to convert Slack raw JSONL to minimal validated triples for local testing.
- **End-to-end run**: Executed extract → validate → build on the ezobooks Slack raw JSONL and verified `scope_id` and `IN_SCOPE` appear in the generated Pass-2 Cypher.

### New files created (purpose)
- `scripts/generate_fake_cypher.py` — generate small sample cypher to exercise builder output quickly.
- `scripts/test_in_scope_cypher.py` — scan `output/cypher` files and report `IN_SCOPE` edge occurrences (used as a quick verifier).
- `scripts/convert_slack_raw_to_validated.py` — temporary helper to create minimal validated triples and a person registry from Slack raw JSONL for local validator runs.

### Modified files (high level)
- `builders/unified_builder.py` — added `_cypher_for_in_scope()` and set `scope_id` on knowledge node merges.
- `pipeline/extraction/extract.py` — added OpenRouter fallback, improved JSON parsing robustness, and ensured metadata (scope/citation) propagation.
- `pipeline/extraction/grouping.py` — Slack grouping by `thread_ts` and scope preservation.
- `pipeline/validation/*` — patched to propagate `scope_id`/`source_ref`/`source_url` through validation output.
- `common/config.py` — allowed non-NVIDIA provider fallback configuration for test runs.

### Notes & next steps
- **Stage 0 discovery (`infra/slack/discovery.py`) is still pending.** The builder currently MERGEs `:AclScope` when a `scope_id` is present, but the formal discovery artifact `output/slack/{org}_scope_principals.json` and idempotent discovery cypher must be implemented to satisfy the contract in Section 4.
- Recommended immediate next work: implement `infra/slack/discovery.py` and add smoke tests under `infra/slack/tests/` so Stage 0 acceptance gates can be verified.



## Step 5 — Filter Bypass

- Purpose: ensure Slack events bypass the Gmail-centric noise/drop rules so Slack records are not incorrectly dropped by email-specific heuristics.
- Implemented behavior:
  - `pipeline/filter.py` now detects Slack events via `event.get("source") == "slack"` (case-insensitive) and skips Gmail-only noise checks.
  - Slack empty-body retention: Slack events are allowed to pass even when the Gmail flow would drop empty bodies; Slack-specific noise filtering remains in `infra/slack/normalizer.py`.
- Acceptance gate:
  - Slack raw input should not be dropped by `pipeline/filter.py` (verified on real input: `18/18 kept, 0 dropped`).

## Blockers encountered during runbook execution and fixes

During the first full runbook execution on real data we encountered three blockers; each was fixed and re-tested:

- Blocker 1 — incomplete full-mode orchestration in `infra/slack/run.py`:
  - Symptom: `python -m infra.slack.run --org <org>` stopped early after user-cache load and never processed channels.
  - Fix: restored the channel discovery → per-channel `process_channel()` loop, checkpointing, output writing, and summary logging so full ingestion runs to completion.

- Blocker 2 — extraction CLI hardcoded to Gmail filtered input:
  - Symptom: `pipeline.triple_extractor` failed when Gmail filtered files were absent even for Slack runs.
  - Fix: added a fallback in `pipeline/extraction/cli.py` to use the latest non-empty `output/raw/{org}_slack_*.jsonl` when Gmail filtered input is missing; extractor resumes from checkpoint when present.

- Blocker 4 — empty latest Slack raw files were selected by downstream stages:
  - Symptom: incremental sync runs could produce a new empty Slack raw file, and downstream stages sometimes tried to process it.
  - Fix: centralized Slack raw-file selection in a shared helper and updated `pipeline.filter` + `pipeline.triple_extractor` to automatically skip empty files and reuse the latest valid non-empty file.

- Blocker 3 — builder hard-failed when Neo4j/Aura env missing:
  - Symptom: `builders.unified_builder` raised `MissingConfigError` and aborted when Neo4j credentials were not present (blocking cypher-only runs).
  - Fix: map `NEO4J_AURA_*` into legacy `NEO4J_*` names and gracefully degrade to cypher-only mode when upload creds are absent (sets `--skip-upload` and emits cypher files). Also update `.env` guidance for `NEO4J_AURA_URI/USER/PASSWORD` aliases.

Each fix was applied in-place and validated by re-running the affected stage(s) on the `ezobooks` workspace.

## End-to-end runbook (real-data) — summary

I executed the full runbook on the `ezobooks` Slack workspace (real tokens in local `.env`) and validated the pipeline end-to-end except for embedding upload (credentials for embedding provider were not present):

### Implementation notes

- Stage 2 and Stage 5 now automatically ignore empty `output/raw/{org}_slack_*.jsonl` files and select the newest non-empty Slack raw file.
- The selection logic is centralized in a shared helper and is used by both `pipeline.filter` and `pipeline.triple_extractor`, so glob input like `output/raw/ezobooks_slack_*.jsonl` works consistently on Windows and Linux.
- The helper also handles an explicit empty Slack raw file path by falling back to sibling Slack raw files in the same directory.
- If the newest Slack raw file is empty, the pipeline skips it, walks backward through older matches, and chooses the latest valid non-empty file instead.
- If all matching Slack raw files are empty, the pipeline fails with a clear error instead of selecting an invalid input.
- This removes the need for the manual rename workaround when an ingestion run creates an empty latest dump alongside older real data.
- Observed model split after the final rerun:
  - Stage 5 extraction: `openrouter` + `google/gemini-2.5-flash`
  - Stage 6.5 embeddings: `nvidia` + `nvidia/llama-nemotron-embed-1b-v2`


- Stage 1 (ingest): `python -m infra.slack.run --org ezobooks` → produced `output/raw/ezobooks_slack_2026-05-27.jsonl` (3 channels, 18 events).
- Stage 2–3 (normalize → filter): Slack records preserved `raw.scope_id`, `source_ref`, and `source_url` and passed the filter (Slack bypass in place).
- Stage 5 (extraction): `python -m pipeline.triple_extractor --org ezobooks --resume` → fell back to Slack raw, grouped 13 threads (checkpoint resumed), nothing new to extract.
- Stage 6 (validator): `python -m pipeline.validator --org ezobooks` → validated triples: 13 threads, 1 triple valid, 0 rejected (`output/knowledge/triples/ezobooks_triples_validated.json`).
- Stage 6.5 (embed): skipped — `pipeline/embed_knowledge` requires an embedding API key (`NVIDIA_API_KEY` or `OPENROUTER_API_KEY`); no key in local `.env`, so embeddings not generated.
- Stage 7 / Builder (passes 0–2): `python -m builders.unified_builder --org ezobooks` → Cypher generated (`output/cypher/pass0_schema.cypher`, `pass1_persons.cypher`, pass2 chunks). Pass 3 upload skipped due to missing embeddings.

Result: end-to-end runbook (discovery → ingestion → extraction → validation → cypher generation) works on real Slack data; embedding/upload (Pass 3) requires embedding credentials and embeddings artifact to verify Neo4j upload.

## Final Addendum — Embeddings Issue, Fix, and Final Rerun

This addendum starts from the embeddings problem we hit during the first local end-to-end attempt and records how I resolved it, then how I re-ran the pipeline end to end.

### What was broken

- The first Neo4j container I used was a Community image without vector-index support, so the generated `CREATE VECTOR INDEX` statements in the builder output could not be applied.
- The generated Pass-2 Cypher also had a syntax issue in the `IN_SCOPE` block: `MERGE (s:AclScope ...)` was immediately followed by `MATCH (...)` without a `WITH`, which Neo4j rejected.
- Because the Cypher passes could not be applied cleanly, the embedding upload step could not find the expected nodes in Neo4j.

### How I resolved it

1. I replaced the local Neo4j container with Neo4j `5.26`, which supports the vector-index statements generated by this repository.
2. I fixed the builder Cypher generation so the `IN_SCOPE` block uses `WITH s` between the scope `MERGE` and the node `MATCH`.
3. I added the NVIDIA API key to the local environment so `pipeline/embed_knowledge.py` could generate the embedding artifact successfully.
4. I regenerated the Cypher output.
5. I loaded the generated passes into Neo4j in order:
  - pass 0: schema
  - pass 1: persons
  - pass 2: knowledge chunks
6. I re-ran the builder so it could upload embeddings against the nodes that now existed in Neo4j.
7. I verified the final graph state with a Neo4j query and confirmed embeddings were present on `Person`, `Task`/`KnowledgeNode`, and `AclScope` nodes.

### Final end-to-end runbook I executed

1. Discovery:
  - `python -m infra.slack.run --org ezobooks --stage discovery`
2. Full Slack ingestion:
  - `python -m infra.slack.run --org ezobooks`
3. Triple extraction resume:
  - `python -m pipeline.triple_extractor --org ezobooks --resume`
4. Validation:
  - `python -m pipeline.validator --org ezobooks`
5. Builder:
  - `python -m builders.unified_builder --org ezobooks`
6. Neo4j application of generated Cypher:
  - load `output/cypher/pass0_schema.cypher`
  - load `output/cypher/pass1_persons.cypher`
  - load `output/cypher/pass2_chunk_000_knowledge.cypher`
  - confirm embeddings with a Neo4j query

### Files created in the final run and their purpose

- [output/slack/ezobooks_scope_principals.json](output/slack/ezobooks_scope_principals.json) — final Slack ACL discovery artifact with scopes, principals, and scope-principal grants.
- [output/cypher/ezobooks_slack_acl.cypher](output/cypher/ezobooks_slack_acl.cypher) — Cypher for Slack `:AclScope` nodes and their MERGE statements.
- [output/raw/ezobooks_slack_2026-05-28.jsonl](output/raw/ezobooks_slack_2026-05-28.jsonl) — normalized Slack ingestion output from the real workspace run.
- [output/knowledge/triples/ezobooks_triples_raw.json](output/knowledge/triples/ezobooks_triples_raw.json) — raw extraction output before validation.
- [output/knowledge/triples/ezobooks_triples_validated.json](output/knowledge/triples/ezobooks_triples_validated.json) — validated triples that passed the stage 6 gate.
- [output/knowledge/triples/ezobooks_triples_rejected.json](output/knowledge/triples/ezobooks_triples_rejected.json) — rejected triples kept for auditability.
- [output/knowledge/config/ezobooks_validation_stats.json](output/knowledge/config/ezobooks_validation_stats.json) — validator summary and counts for the run.
- [output/cypher/pass0_schema.cypher](output/cypher/pass0_schema.cypher) — schema constraints and indexes for Neo4j.
- [output/cypher/pass1_persons.cypher](output/cypher/pass1_persons.cypher) — person node MERGE Cypher.
- [output/cypher/pass2_chunk_000_knowledge.cypher](output/cypher/pass2_chunk_000_knowledge.cypher) — knowledge node and relationship Cypher, including `IN_SCOPE` edges.
- [output/knowledge/embeddings/ezobooks_knowledge_embeddings.npy](output/knowledge/embeddings/ezobooks_knowledge_embeddings.npy) — generated embeddings used for the Neo4j upload step.

### Final result

- Discovery completed successfully.
- Full Slack ingestion completed successfully.
- Triple extraction resumed cleanly.
- Validation completed successfully.
- Builder completed successfully after the Neo4j/vector-index fix and Cypher correction.
- Embeddings were written to Neo4j and verified with a post-run query.

## Windows Setup + End-to-End Runbook

Use the steps below on a Windows machine in a VS Code PowerShell terminal.

### 1) Prerequisites

- Python 3.12 or newer
- Git
- Optional but recommended: Docker Desktop if you want to run Neo4j locally
- Access to your Slack app token and bot token
- OpenRouter API key for Stage 5 extraction
- NVIDIA API key for Stage 6.5 embeddings if you want to generate embeddings locally
- Neo4j credentials if you want the builder to upload into a live graph

### 2) Create and activate the environment

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip setuptools wheel
```

### 3) Install dependencies

The project dependencies are defined in `pyproject.toml`, so the main install is:

```powershell
pip install -e .
```

Install the Neo4j Python driver separately if it is not already present:

```powershell
pip install neo4j
```

If `hdbscan` is missing on Windows, install it explicitly:

```powershell
python -m pip install --prefer-binary hdbscan
```

If the pip wheel fails, use conda-forge instead:

```powershell
conda install -c conda-forge hdbscan -y
```

verify through this
python -c "import pydantic, openai, numpy, hdbscan, neo4j; print('ok')"

### 4) Configure `.env`

Copy the sample file and fill in your values:

```powershell
copy .env.example .env
notepad .env
```

Required values for the full run:

- `SLACK_TOKEN`
- `OPENROUTER_API_KEY`
- `NVIDIA_API_KEY` if you want embeddings
- `NEO4J_URI`
- `NEO4J_USERNAME`
- `NEO4J_PASSWORD`

Optional overrides:

- `OPENROUTER_LLM_MODEL=google/gemini-2.5-flash`
- `EMBEDDING_PROVIDER=nvidia`
- `EMBEDDING_MODEL=nvidia/llama-nemotron-embed-1b-v2`

### 5) Run the Slack end-to-end runbook

Run the stages in order from the repository root:

```powershell
python -m infra.slack.run --org ezobooks --stage discovery
python -m infra.slack.run --org ezobooks

python -m pipeline.filter --input output\raw\ezobooks_slack_*.jsonl --org ezobooks --skip-llm    (replace with the file that generated at above stage )

python -m pipeline.person_registry --input output\filtered\ezobooks_gmail_filtered.jsonl --org ezobooks  (inside this file we have slack data only)

python -m pipeline.triple_extractor --org ezobooks --source slack --resume
python -m pipeline.validator --org ezobooks
python -m pipeline.embed_knowledge --org ezobooks
python -m pipeline.deduplicator --org ezobooks
python -m builders.unified_builder --org ezobooks
```

`python -m infra.slack.run --org ezobooks` now includes both public and private channels visible to the authenticated token by default. No separate `--include-private` flag is required for normal ingestion.

`python -m builders.unified_builder --org ezobooks` now automatically applies the generated Cypher passes to Neo4j, uploads embeddings, and prints a graph summary. No manual `docker exec cypher-shell` step is needed anymore.

### 6) What to verify after the run

- `output/slack/ezobooks_scope_principals.json`
- `output/raw/ezobooks_slack_*.jsonl`
- `output/knowledge/triples/ezobooks_triples_raw.json`
- `output/knowledge/triples/ezobooks_triples_validated.json`
- `output/knowledge/embeddings/ezobooks_knowledge_embeddings.npy`
- `output/cypher/pass0_schema.cypher`
- `output/cypher/pass1_persons.cypher`
- `output/cypher/pass2_chunk_000_knowledge.cypher`

### 7) Verify Neo4j state

If you have Neo4j running locally, use this quick check:

```powershell
@'
from neo4j import GraphDatabase
import os
uri = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
user = os.environ.get("NEO4J_USERNAME", "neo4j")
pwd = os.environ.get("NEO4J_PASSWORD", "lorepassword")
driver = GraphDatabase.driver(uri, auth=(user, pwd))
queries = [
  "MATCH (n:KnowledgeNode) RETURN count(n) AS total, count(n.embedding) AS embedded",
  "MATCH (p:Person) RETURN count(p) AS total, count(p.embedding) AS embedded",
  "MATCH (:AclScope) RETURN count(*) AS scopes",
  "MATCH ()-[r:IN_SCOPE]->() RETURN count(r) AS rels",
]
with driver.session() as session:
  for query in queries:
    print(session.run(query).single())
driver.close()
'@ | python -
```

Verify Neo4j & artifacts (quick checks):

# list generated cypher / embeddings
ls output\cypher
ls output\knowledge\embeddings

# quick Neo4j verification via python snippet
@'
from neo4j import GraphDatabase
import os
uri=os.environ.get("NEO4J_URI","bolt://localhost:7687")
user=os.environ.get("NEO4J_USERNAME","neo4j")
pwd=os.environ.get("NEO4J_PASSWORD","lorepassword")
driver=GraphDatabase.driver(uri, auth=(user,pwd))
queries=[
 "MATCH (n:KnowledgeNode) RETURN count(n) AS total, count(n.embedding) AS embedded",
 "MATCH (p:Person) RETURN count(p) AS total, count(p.embedding) AS embedded",
 "MATCH (:AclScope) RETURN count(*) AS scopes",
 "MATCH ()-[r:IN_SCOPE]->() RETURN count(r) AS rels",
]
with driver.session() as s:
    for q in queries:
        print(s.run(q).single())
driver.close()
'@ | python -

### Notes for Windows users

- If PowerShell blocks activation, run `Set-ExecutionPolicy -Scope Process RemoteSigned` first.
- If you see an empty Slack raw file as the newest file, the pipeline now automatically falls back to the latest non-empty Slack raw file.
- If every matching Slack raw file is empty, the run fails clearly instead of selecting a bad input.
- `python -m builders.unified_builder --org ezobooks` now prints a visible build summary in the terminal because the CLI initializes logging on startup.
- The same builder command also applies the Cypher files to Neo4j automatically before uploading embeddings.

## **Smoke Tests**

  - **Purpose:** Quick verifications that the Slack pipeline stages and ACL plumbing behave correctly on real or CI-provided artifacts.
  - **Run from repo root:** the commands below assume your Python environment is active and you are at the repository root.
  - **Tests & Commands:**
    - **Discovery (shape & idempotency):** `python infra/slack/tests/test_smoke_01_discovery.py`
      - **Expected:** Generates `[output/slack/ezobooks_scope_principals.json](output/slack/ezobooks_scope_principals.json)` and `[output/cypher/ezobooks_slack_acl.cypher](output/cypher/ezobooks_slack_acl.cypher)`; run twice to confirm idempotency.
    - **Scope plumbing (builder ACL propagation):** `python infra/slack/tests/test_smoke_02_scope_plumbing.py`
      - **Expected:** Knowledge nodes in the graph include `scope_id` properties and the generated Pass-2 Cypher contains `IN_SCOPE` edges (verify `output/cypher/pass2_chunk_000_knowledge.cypher`).
    - **Citation fields (source_ref/source_url):** `python infra/slack/tests/test_smoke_03_citation.py`
      - **Expected:** Validated knowledge triples and built nodes include `source_ref` and `source_url` properties.
    - **ACL gate (public/private membership behavior):** `python infra/slack/tests/test_smoke_04_acl_gate.py`
      - **Expected:** Public channels have no `scope_principals` in the discovery artifact; private channels contain membership entries and Neo4j ACL behavior matches the discovery artifact.
  - **Notes:**
    - Some smoke tests require a running Neo4j instance for Cypher application checks; others operate on generated JSONL/Cypher artifacts only.
    - If a test needs tokens or a live Slack workspace, set your `.env` per the earlier Env vars section before running.
