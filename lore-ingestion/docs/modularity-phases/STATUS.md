# Modularity Migration — Current Status

**Last updated:** 2026-05-19
**Current phase:** ✅ All 16 phases complete (final step: Step 16).

## Completed phases

- ✅ **Step 1 — Fix CLAUDE.md doc drift** (see `phase-01-claude-md-drift.md`)
- ✅ **Step 2 — Centralize Cypher `_esc()`** (see `phase-02-cypher-esc.md`)
- ✅ **Step 3 — Move node-ID formula into `common/hashing.py`** (see `phase-03-node-ids.md`)
- ✅ **Step 4 — Add `common/config.py` Pydantic Settings module** (see `phase-04-config-module.md`)
- ✅ **Step 5 — Migrate `os.getenv` callsites to Settings** (see `phase-05-config-migration.md`)
- ✅ **Step 6 — Canonicalize embedding model (NVIDIA NeMo)** (see `phase-06-canonical-embedding.md`)
- ✅ **Step 7 — End-of-run summary logging at every stage** (see `phase-07-stage-summaries.md`)
- ✅ **Step 8 — Minimum-output gates at stage boundaries** (see `phase-08-output-gates.md`)
- ✅ **Step 9 — Explicit checkpoint corruption recovery** (see `phase-09-checkpoint-recovery.md`)
- ✅ **Step 10 — Split `pipeline/triple_extractor.py` into `pipeline/extraction/`** (see `phase-10-extraction-split.md`)
- ✅ **Step 11 — Split `pipeline/validator.py` into `pipeline/validation/`** (see `phase-11-validation-split.md`)
- ✅ **Step 12 — `infra/llm/` + `infra/embeddings/` provider registries** (see `phase-12-infra-registries.md`)
- ✅ **Step 13 — Collapse `knowledge_distillation/models/*` into a TeacherConfig registry** (see `phase-13-teacher-registry.md`)
- ✅ **Step 14 — Rename `connectors/` → `infra/gmail/`** (see `phase-14-gmail-rename.md`)
- ✅ **Step 15 — Introduce `runners/` CLI wrappers** (see `phase-15-runners.md`)
- ✅ **Step 16 — Chronograph → common infrastructure** (see `phase-16-chronograph-common.md`)

## In progress

_None — all 16 phases complete._

## Open follow-up (not blocking)

- Re-run `builders.post_processor --org ezobooks` to regenerate topic taxonomy in the new 2048-dim space.
- Legacy `builders.vector.build_index` produces 2048-dim now; existing standalone index needs `--rebuild` if used downstream.
- (From Step 10) `pipeline/extraction/extract.py` now passes `rate_limiter` to the in-loop `_process_batch` call. Pre-split code was missing it and would have raised `TypeError` on the first full 50-thread batch.
- ~~(From `phase-03-node-ids.md`) `builders/post_processor.py` scopes Task IDs by topic while `pipeline/validation/id_minting.py` scopes by `thread_id`.~~ **FIXED 2026-05-19** in `builders/post_processor.py:synthesize_processes` — task descriptions now carry their originating `thread_id` through `topic_decisions`, and `HAS_STEP` edges mint `Task` IDs with `scope=thread_id` to match the validator. Re-run `runners.post_process` on existing data to materialise the previously-missing `HAS_STEP` edges.
- (From Step 12) `pipeline/embed_knowledge.py:embed_texts` retains its `api_key` parameter (now ignored — `del api_key`). Cosmetic cleanup deferred.
- (From Step 13) `qwen_openrouter` teacher exists in the registry but is not wired into `distill.py:TEACHERS`. Add one line if the OpenRouter free-tier route is needed.
- (From Step 14) `CLAUDE.md` (repo root + `lore-ingestion/CLAUDE.md`) still documents `python -m connectors.gmail.run`. Works via shim; canonical path is `python -m infra.gmail.run`. Doc flip is a separate user-blessed change.

- (From Step 15) `CLAUDE.md` still documents `python -m pipeline.<stage>` / `python -m builders.<x>` for every command. All historical paths still work; flipping documented commands to `python -m runners.<stage>` is a separate doc PR.
- (From Step 15) `pipeline.extraction.cli:cli_entry` is the only entry function not named `main()`. Cosmetic alignment with other stages is a tiny follow-up.
- (From Step 16) `chronograph/query/query_engine.py` still talks to the OpenRouter embeddings endpoint via raw `requests.post`. Needs `OpenrouterEmbeddingProvider` added to `infra/embeddings/` (≈80 LOC) to route through the registry.
- (From Step 16) `chronograph/ingestion/` is a self-contained GitLab adapter that could live under `infra/gitlab/` mirroring `infra/gmail/`. Pure relocation, not consolidation.

## Next pending

_None — migration is complete. All open items above are non-blocking follow-ups._

## Blockers / open questions

_None._

## Locked decisions (from 2026-05-18 session)

1. Canonical embedding model: NVIDIA NeMo `nvidia/llama-3.2-nemoretriever-300m-embed-v1` (2048-dim).
2. `builders/vector/build_index.py`: comment out OpenAI-specific code, don't delete.
3. `builders/post_processor.py` topic clustering: switch to NVIDIA NeMo.
4. Module splits: preserve old import paths via re-export shims.
5. Execute steps 1→16 in order, user checkpoints between each.
6. (Step 12) `pipeline/filter.py`'s Anthropic SDK path stays outside the LLM registry — different SDK, optional path.

## Phase log

| # | Step | Status | File |
|---|------|--------|------|
| 1 | Fix CLAUDE.md doc drift | ✅ | `phase-01-claude-md-drift.md` |
| 2 | Centralize Cypher `_esc()` | ✅ | `phase-02-cypher-esc.md` |
| 3 | Move node-ID formula into `common/hashing.py` | ✅ | `phase-03-node-ids.md` |
| 4 | Add `common/config.py` Settings module | ✅ | `phase-04-config-module.md` |
| 5 | Migrate `os.getenv` callsites to Settings | ✅ | `phase-05-config-migration.md` |
| 6 | Canonicalize embedding model (NVIDIA NeMo) | ✅ | `phase-06-canonical-embedding.md` |
| 7 | End-of-run summary logging | ✅ | `phase-07-stage-summaries.md` |
| 8 | Minimum-output gates | ✅ | `phase-08-output-gates.md` |
| 9 | Explicit checkpoint corruption recovery | ✅ | `phase-09-checkpoint-recovery.md` |
| 10 | Split `pipeline/triple_extractor.py` | ✅ | `phase-10-extraction-split.md` |
| 11 | Split `pipeline/validator.py` | ✅ | `phase-11-validation-split.md` |
| 12 | `infra/llm/` + `infra/embeddings/` registries | ✅ | `phase-12-infra-registries.md` |
| 13 | Collapse `knowledge_distillation/models/*` | ✅ | `phase-13-teacher-registry.md` |
| 14 | Rename `connectors/` → `infra/gmail/` | ✅ | `phase-14-gmail-rename.md` |
| 15 | Introduce `runners/` CLI wrappers | ✅ | `phase-15-runners.md` |
| 16 | Chronograph → common infrastructure | ✅ | `phase-16-chronograph-common.md` |
