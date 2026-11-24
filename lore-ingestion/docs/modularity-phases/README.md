> Note: `lore-backend` in these phase docs is now **lore-engine** (renamed).

# Modularity Migration — Phase Tracker

This folder is the **resumable progress log** for the 16-step modular redesign of `lore-ingestion/`. If a session ends or context is lost, Claude (or a human) reads this folder and picks up where the previous session left off.

## How to use this folder

- Every step gets one file: `phase-NN-<slug>.md`.
- Each phase file contains: **Status**, **Scope**, **Changes made**, **Verification**, **Rollback notes**, **Next phase**.
- `STATUS.md` (this folder, sibling file) is the one-screen current state — read it first.
- The plan itself lives in `docs/modularity.md` (the original /modularity output).

## Decisions locked in (2026-05-18)

1. **Canonical embedding model:** NVIDIA NeMo `nvidia/llama-3.2-nemoretriever-300m-embed-v1` (2048-dim). Same model used by `lore-backend`.
2. **`builders/vector/build_index.py`:** OpenAI-specific code paths get commented out (not deleted) — kept for possible future re-enablement.
3. **`builders/post_processor.py` topic clustering:** switches from OpenRouter OpenAI to NVIDIA NeMo so the entire codebase uses one embedding model.
4. **Module splits (Steps 10–11):** old import paths (`pipeline.triple_extractor`, `pipeline.validator`) preserved via re-export shims for backwards compatibility with `knowledge_distillation/` and `scripts/`.
5. **Execution mode:** Steps 1→16 in order, one at a time, with user checkpoint between each.

## The 16 Steps (summary)

| # | Phase | Risk | Status |
|---|-------|------|--------|
| 1 | Fix CLAUDE.md doc drift | LOW | pending |
| 2 | Centralize Cypher `_esc()` | LOW | pending |
| 3 | Move node-ID formula into `common/hashing.py` | LOW | pending |
| 4 | Add `common/config.py` Settings module | LOW | pending |
| 5 | Migrate `os.getenv` callsites to Settings | LOW | pending |
| 6 | Canonicalize embedding model (NVIDIA NeMo) — comment out OpenAI in build_index, switch post_processor to NVIDIA | MEDIUM | pending |
| 7 | End-of-run summary logging at every stage | LOW | pending |
| 8 | Minimum-output gates at stage boundaries | LOW | pending |
| 9 | Replace silent checkpoint corruption with explicit recovery | LOW | pending |
| 10 | Split `pipeline/triple_extractor.py` (1273 LOC) into `pipeline/extraction/` subpackage + shim | MEDIUM | pending |
| 11 | Split `pipeline/validator.py` (755 LOC) into `pipeline/validation/` subpackage + shim | MEDIUM | pending |
| 12 | Introduce `infra/llm/` and `infra/embeddings/` provider registries | MEDIUM | pending |
| 13 | Collapse `knowledge_distillation/models/*` (10 files) into a registry | LOW | pending |
| 14 | Rename `connectors/` → `infra/gmail/` | LOW | pending |
| 15 | Introduce `runners/` with thin CLI wrappers | LOW | pending |
| 16 | (Optional) Pull `chronograph/` into `common/` infrastructure | MEDIUM | pending |

Full plan with scope/risk/test/rollback for each step: `docs/modularity.md`.

## Resume protocol

If you (Claude) are starting cold:
1. Read `STATUS.md` in this folder.
2. Read the most recent `phase-NN-*.md` to see what was last completed.
3. Read `docs/modularity.md` if you need full plan context.
4. Confirm the user's locked decisions above are still current; ask if anything changed.
5. Continue from the next pending step.
