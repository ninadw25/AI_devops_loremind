# Phase 1 — Fix CLAUDE.md doc drift

**Status:** ✅ Completed (2026-05-18)
**Risk:** LOW
**Files touched:** `lore-ingestion/CLAUDE.md`, `lore/CLAUDE.md` (root)

## Drift items fixed

| # | Location | Was (wrong) | Now (correct) |
|---|----------|-------------|---------------|
| 1 | `lore-ingestion/CLAUDE.md` `## Commands` | `builders/graph/generate_chunk.py --pass schema --pass persons --pass knowledge`, `builders/graph/utils/assign_node_ids.py` (4 nonexistent files) | Removed. Added `builders/post_processor.py` (real, was undocumented). Marked `builders/vector/build_index.py` as legacy. |
| 2 | `lore-ingestion/CLAUDE.md` Stage 6.5 line | "OpenRouter text-embedding-3-small" | "NVIDIA NeMo nemoretriever-300m, 2048-dim" |
| 3 | `lore-ingestion/CLAUDE.md` Module Layout `builders/` tree | Listed `graph/generate_chunk.py`, `graph/dataset/`, `graph/utils/{assign_node_ids,build_ontology_summary,run_name_merge}.py` (none exist) | Removed; documents what's actually on disk: `unified_builder.py`, `post_processor.py`, `graph/load_to_neo4j.py`, `vector/build_index.py` (legacy). |
| 4 | `lore-ingestion/CLAUDE.md` Triple Extraction section | "Model: `google/gemini-2.5-flash` via OpenRouter" | "Model: `deepseek-ai/deepseek-v3.2` via NVIDIA Build, 40 RPM. OpenRouter/Gemini path kept commented out." |
| 5 | `lore-ingestion/CLAUDE.md` Environment Variables | "Required: OPENROUTER_API_KEY" | Required: `NVIDIA_API_KEY`, `NEO4J_*`. Optional: OPENROUTER (post_processor only, planned to move). Notes that env reads are scattered and will be consolidated in Steps 4–5. |
| 6 | `lore-ingestion/CLAUDE.md` `scripts/` line | `(extract_test_threads.py)` (doesn't exist) | `(model_testing.py)` |
| 7 | `lore-ingestion/CLAUDE.md` `docs/` line | unchanged | added pointer to `docs/modularity-phases/` |
| 8 | `lore-ingestion/CLAUDE.md` Node IDs paragraph | Did not mention the multi-source duplication | Lists all 7 prefixes; notes consolidation planned in Step 3. |
| 9 | `lore-ingestion/CLAUDE.md` "Async concurrency" pattern | Did not mention the 40-RPM provider cap | Mentions sliding-window rate limiter. |
| 10 | `lore/CLAUDE.md` (root) — pipeline summary | "async Gemini, 20 concurrent" | "async, 20 concurrent" |
| 11 | `lore/CLAUDE.md` (root) — extraction model | "google/gemini-2.5-flash via OpenRouter" | "deepseek-ai/deepseek-v3.2 via NVIDIA Build (40 RPM cap)" |
| 12 | `lore/CLAUDE.md` (root) — builders summary | "vector index (OpenRouter embeddings, 101k docs, 1536-dim)" | "NVIDIA NeMo embeddings (nemoretriever-300m, 2048-dim). Same embedding model as lore-backend." |

## Verification

- `grep -r "generate_chunk\|extract_test_threads\|google/gemini-2.5-flash via OpenRouter" lore-ingestion/CLAUDE.md lore/CLAUDE.md` returns empty.
- All file paths referenced in CLAUDE.md now exist on disk.

## Rollback

`git diff HEAD -- 'lore-ingestion/CLAUDE.md' 'lore/CLAUDE.md'` shows only the doc-drift fixes. `git checkout HEAD -- ...` reverts cleanly. No code changed in this phase.

## Next phase

**Step 2 — Centralize Cypher `_esc()`**

Two definitions exist with different semantics:
- `builders/unified_builder.py:37-40` — also strips `;`
- `builders/post_processor.py:38-41` — does not strip `;`

Action: create a single helper (likely `builders/_cypher.py` or `builders/cypher_utils.py`), import from both call sites. Pick the stricter version (`;` → `,`). Verify by regenerating Cypher for one org and diffing against last known-good output.
