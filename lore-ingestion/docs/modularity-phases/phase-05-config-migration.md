# Phase 5 — Migrate `os.getenv` callsites to Settings

**Status:** ✅ Completed (2026-05-19)
**Risk:** LOW (mechanical replacement; defaults preserved in `Settings`)
**Files touched:** 9

## What changed per file

| File | Before | After |
|------|--------|-------|
| `pipeline/embed_knowledge.py` | `os.getenv("NVIDIA_API_KEY", "")` in `main()` + manual check | `validate_for("embed_knowledge")` + `get_settings().nvidia_api_key`. Removed `import os`. |
| `pipeline/filter.py` | `os.environ.get("ANTHROPIC_API_KEY", "")` in `run_filter()` | `get_settings().anthropic_api_key`. Removed `import os`. |
| `pipeline/triple_extractor.py` | 3 inline `os.getenv` calls in `main()` (NVIDIA_BASE_URL / NVIDIA_API_KEY / EXTRACTION_MODEL) + commented-out OpenRouter trio | `validate_for("triple_extractor")` + `s.nvidia_base_url` / `s.nvidia_api_key` / `s.extraction_model`. Comment updated to point to `settings.openrouter_*` for the disabled path. Removed `import os`. |
| `builders/unified_builder.py` | 3 inline `os.getenv` calls passed to `run_builder()` | `validate_for("unified_builder")` (only when `--skip-upload` is false) + `s.neo4j_uri` / `s.neo4j_username` / `s.neo4j_password`. Removed `import os`. |
| `builders/post_processor.py` | Module-level `OPENROUTER_API_KEY = os.getenv(...)` reread inside `main()` with `global` + `load_dotenv()` | All 4 usages call `get_settings().openrouter_api_key` lazily. `main()` calls `validate_for("post_processor")` only when clustering is enabled. Removed `import os` and the `load_dotenv` block. |
| `builders/vector/build_index.py` | Module-level `OPENROUTER_API_KEY`, `os.environ["MONGODB_*"]` in `store_metadata_in_mongo`, `os.getenv("OUTPUT_DIR", ...)` default | `get_settings().openrouter_api_key`, `s.mongodb_uri` / `s.mongodb_db_name` with explicit guard, `s.output_dir or "./output"` for the CLI default. Removed `import os` and the orphan `load_dotenv()`. |
| `builders/graph/load_to_neo4j.py` | Module-level `os.getenv` for NEO4J_* + own `load_dotenv` block | Module-level reads now `_s = get_settings(); URI=_s.neo4j_uri.strip(); ...`. Existing sys.exit-on-missing behavior preserved. Removed `import os` and `load_dotenv`. |
| `scripts/model_testing.py` | `os.getenv("NVIDIA_API_KEY", "")` + orphan `load_dotenv()` | `get_settings().nvidia_api_key` + `base_url` now `s.nvidia_base_url` (was a hardcoded URL). Removed `import os` and `load_dotenv()`. |
| `chronograph/config.py` | Standalone module with 13 `os.getenv` calls + own `load_dotenv` | Compatibility shim: every legacy constant (`GITLAB_PAT`, `OPENROUTER_API_KEY`, `MONGODB_URI`, `CHRONOGRAPH_CONFIG`, etc.) now sourced from `common.config.get_settings()`. Public API unchanged so existing chronograph imports keep working. |

## Auditability gained

The lore-backend invariant now holds in lore-ingestion too:

```bash
$ grep -rn "os\.getenv\|os\.environ" --include="*.py" . \
    | grep -v common/config.py \
    | grep -v knowledge_distillation/models/
chronograph/config.py:8:Direct env reads (`os.getenv`) have been removed. New code in
```

The only remaining hit is a **docstring** in `chronograph/config.py` documenting the change. Zero live `os.getenv`/`os.environ` reads outside `common/config.py`.

`knowledge_distillation/models/` is excluded because Step 13 will collapse those 10 teacher files into a registry. Their env reads will be handled there.

## Fail-fast verification

```bash
$ env -i python -c "from common.config import validate_for; \
                    validate_for('triple_extractor')"
MissingConfigError: Stage 'triple_extractor' requires non-empty env vars:
                    NVIDIA_API_KEY. Check your .env file.

$ env -i python -c "from common.config import validate_for; \
                    validate_for('unified_builder')"
MissingConfigError: Stage 'unified_builder' requires non-empty env vars:
                    NEO4J_URI, NEO4J_PASSWORD. Check your .env file.
```

Each pipeline stage now fails immediately on startup if its required env is missing — before any LLM call, Neo4j connection attempt, or file IO.

## Side effects worth noting

1. **`builders/vector/build_index.py:store_metadata_in_mongo`** previously did `os.environ["MONGODB_URI"]` which raises `KeyError` on missing key. New code explicitly raises `RuntimeError` with a clear message. Same behavior, better diagnostic.
2. **`builders/post_processor.py`** dropped the `global OPENROUTER_API_KEY` + `os.getenv` reassignment hack. The four usages now call `get_settings().openrouter_api_key` lazily; semantics identical (Settings singleton reads `.env` at first access).
3. **`builders/unified_builder.py`** previously called `run_builder` with empty strings when env was missing; the Neo4j driver would then fail with a confusing connection error. Now `validate_for` catches it at process start. The `--skip-upload` path bypasses validation (Cypher-only generation needs no Neo4j).
4. **`scripts/model_testing.py`** now uses `settings.nvidia_base_url` instead of a hardcoded URL; behavior equivalent for default config but allows override via env.
5. **`load_dotenv()` calls removed** from `builders/vector/build_index.py`, `builders/post_processor.py`, `builders/graph/load_to_neo4j.py`, `scripts/model_testing.py`, `chronograph/config.py`. `pydantic-settings` already loads `.env` automatically when `Settings()` is constructed.

## Rollback

```bash
git checkout HEAD -- \
    pipeline/embed_knowledge.py pipeline/filter.py pipeline/triple_extractor.py \
    builders/unified_builder.py builders/post_processor.py \
    builders/vector/build_index.py builders/graph/load_to_neo4j.py \
    scripts/model_testing.py chronograph/config.py
```

No data on disk changed. No new external dependencies introduced (pydantic-settings was added in Step 4).

## Next phase

**Step 6 — Canonicalize embedding model (NVIDIA NeMo)**

Two sub-tasks per the locked decisions:

1. Comment out (do not delete) the OpenAI-specific code paths in `builders/vector/build_index.py`. The local-fallback path (`sentence-transformers`) may stay; user direction is to keep the file usable for future re-enablement.
2. Switch `builders/post_processor.py` topic-name clustering from OpenRouter `text-embedding-3-small` to NVIDIA `nemoretriever-300m`. Specifically:
   - `embed_topics()` at `builders/post_processor.py:60-90` currently posts to `openrouter.ai/v1/embeddings` with `openai/text-embedding-3-small`. Switch to NVIDIA endpoint + `settings.embedding_model`.
   - Update `REQUIRED_BY_STAGE["post_processor"]` in `common/config.py` from `("openrouter_api_key",)` to `("nvidia_api_key",)`.
   - Verify clustering quality is acceptable on EZO Books data (HDBSCAN runs on the new embedding space). Embeddings change dim from 1536 → 2048.

This is the first phase where existing artifacts (the topic taxonomy) may need to be regenerated. Discuss with user before running on production data.
