# Phase 4 — Add `common/config.py` Pydantic Settings module

**Status:** ✅ Completed (2026-05-19)
**Risk:** LOW (additive only — no existing call site modified)
**Files touched:**
- `common/config.py` (NEW)
- `pyproject.toml` — added `pydantic-settings>=2.0` to deps (already installed in shared `.venv`; this just records the direct dependency).

## Design choice — mirror `lore-backend/core/settings.py`

The backend already has a working Settings pattern. Same conventions adopted here for consistency:

- `pydantic-settings.BaseSettings` v2.
- `SettingsConfigDict(env_file=".env", case_sensitive=False, extra="ignore")` — uppercase env vars in `.env` populate lowercase fields.
- Module-level `get_settings()` returns a lazy singleton; `_reset_settings()` is the test seam.
- `embedding_provider`, `embedding_model`, `embedding_dim` field names mirror the backend so the same `.env` works for both subprojects.

## What's new

| Symbol | Purpose |
|--------|---------|
| `Settings` (BaseSettings) | All 26 config fields, every env var from the audit. |
| `REQUIRED_BY_STAGE: dict[str, tuple[str, ...]]` | Per-stage required-env contract. 13 stages. |
| `validate_for(stage)` | Raises `MissingConfigError` if any required field for `stage` is empty. Call at the top of each runner. |
| `MissingConfigError(RuntimeError)` | Typed exception for the missing-env case. |
| `env_audit()` | Returns `{VAR_NAME: bool}` — secret-safe (booleans only). Useful for a future `--print-config` flag. |
| `get_settings()` / `_reset_settings()` | Singleton + test seam. |

## Stages registered

```
gmail_fetch       — ()
filter            — ()
filter_llm        — ("anthropic_api_key",)
person_registry   — ()
comm_graph        — ()
triple_extractor  — ("nvidia_api_key",)
validator         — ()
embed_knowledge   — ("nvidia_api_key",)
deduplicator      — ()
unified_builder   — ("neo4j_uri", "neo4j_password")
post_processor    — ("openrouter_api_key",)    # moves to nvidia_api_key in Step 6
chronograph_ingest— ("gitlab_pat", "mongodb_uri")
chronograph_query — ("mongodb_uri", "openrouter_api_key")
```

## Smoke test results

All 9 checks pass:

1. Defaults instantiate (`embedding_model='nvidia/llama-3.2-nemoretriever-300m-embed-v1'`, `embedding_dim=2048`, `extraction_model='deepseek-ai/deepseek-v3.2'`).
2. Env vars override defaults case-insensitively.
3. `validate_for("triple_extractor")` passes when `NVIDIA_API_KEY` is set.
4. `validate_for("unified_builder")` raises `MissingConfigError: Stage 'unified_builder' requires non-empty env vars: NEO4J_PASSWORD` when password is unset.
5. Stages with empty requirements pass trivially.
6. Unknown stage → `KeyError` listing known stages.
7. `env_audit()` returns booleans only — no secret values leak.
8. `get_settings()` returns the same object on repeated calls.
9. `output_root` resolves to `<repo>/output` when `OUTPUT_DIR` is unset.

All existing modules (`pipeline.validator`, `builders.unified_builder`, `builders.post_processor`, `pipeline.triple_extractor`, `pipeline.embed_knowledge`) still import cleanly — Step 4 changed zero existing call sites.

## Rollback

```bash
git checkout HEAD -- pyproject.toml
rm common/config.py
```

## Why this is safe to land alone

Step 4 is intentionally additive. Every `os.getenv(...)` in the codebase still runs unchanged. New code can opt into `get_settings()` whenever it wants; Step 5 will do the mechanical migration of existing callsites.

## Next phase

**Step 5 — Migrate `os.getenv` callsites to Settings**

Files with `os.getenv` / `os.environ` reads to migrate (excluding the new `common/config.py`):

- `pipeline/triple_extractor.py` — `NVIDIA_BASE_URL`, `NVIDIA_API_KEY`, `EXTRACTION_MODEL`
- `pipeline/embed_knowledge.py` — `NVIDIA_API_KEY`
- `pipeline/filter.py` — `ANTHROPIC_API_KEY`
- `builders/unified_builder.py` — `NEO4J_URI`, `NEO4J_USERNAME`, `NEO4J_PASSWORD`
- `builders/graph/load_to_neo4j.py` — `NEO4J_URI`, `NEO4J_USERNAME`, `NEO4J_PASSWORD`
- `builders/vector/build_index.py` — `OPENROUTER_API_KEY`, `MONGODB_URI`, `MONGODB_DB_NAME`, `OUTPUT_DIR`
- `builders/post_processor.py` — `OPENROUTER_API_KEY` (twice — both removed)
- `chronograph/config.py` — replace the whole module by re-exporting from `common.config`, or keep as thin compatibility layer.

For each CLI entrypoint (`main()`), add `validate_for("<stage>")` immediately after argparse so failures happen before any IO.

Verification target: `grep -rn "os\.getenv\|os\.environ" --include="*.py" . | grep -v common/config.py` returns zero results (matches the lore-backend audit command).
