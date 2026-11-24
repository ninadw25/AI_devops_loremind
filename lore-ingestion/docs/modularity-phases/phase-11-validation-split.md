# Phase 11 — Split `pipeline/validator.py` into `pipeline/validation/`

**Status:** ✅ Completed (2026-05-19)
**Risk:** MEDIUM (consumed by extraction's correction pass on every batch, plus external callers)

## What changed

The 773-line `pipeline/validator.py` god module was split into a
focused subpackage. The original path is now a re-export shim. Every
external caller continues to work with zero edits:

```
pipeline.validator.build_name_lookup       (knowledge_distillation/distill.py)
pipeline.validator.validate_thread         (knowledge_distillation/distill.py,
                                            scripts/model_testing.py,
                                            pipeline/extraction/extract.py)
pipeline.validator.check_structural_rules  (scripts/model_testing.py,
                                            pipeline/extraction/extract.py)
```

Verified by import + identity check + a functional smoke test
(`build_name_lookup` + `validate_thread` on a hand-crafted thread).

### New layout

```
pipeline/validation/
  __init__.py        — public surface + re-exports (single import boundary)
  schema.py          — ALLOWED_NODE_TYPES, PREDICATE_SCHEMA, content regexes,
                        MIN_DESCRIPTION_LENGTH / MIN_EVIDENCE_LENGTH (~70 lines)
  name_lookup.py     — build_name_lookup + rapidfuzz resolution
                        (resolve_person_name, resolve_person_names_in_triple,
                         FUZZY_SCORE_CUTOFF, FUZZY_AMBIGUITY_GAP) (~135 lines)
  rules.py           — validate_triple, auto_correct_directions (~155 lines)
  structural.py      — check_structural_rules (~70 lines)
  thread.py          — validate_thread (per-thread orchestrator) (~100 lines)
  id_minting.py      — _assign_node_ids + the two node-type sets
                        (delegates the formula to common.hashing.node_id_for)
  run.py             — run_validation: full Stage-6 driver + stats + gate
  cli.py             — argparse + main()
pipeline/validator.py
  → ~55-line shim re-exporting the public surface (plus the
    `_HTML_RE`/`_CSS_RE`/etc. internal regexes and the `_assign_node_ids`
    private-but-imported helper, so any tool that reached in still works).
```

Each module has a single reason to change:
- The ontology evolves → edit `schema.py`.
- A new fuzzy-resolution edge case → edit `name_lookup.py`.
- A new triple-level rule or auto-correction → edit `rules.py`.
- A new orphan check → edit `structural.py`.
- Stage driver / stats / artifacts → edit `run.py`.
- A new CLI flag → edit `cli.py`.

## Behavioral parity

This phase is a **pure refactor** — no behavioral changes. Every
function body is byte-for-byte the same as the pre-split version (modulo
imports moving to the top of each new file). Verified by:

- `import pipeline.validator as v; v.PREDICATE_SCHEMA is
  pipeline.validation.schema.PREDICATE_SCHEMA` → True (single object,
  not a copy).
- `build_name_lookup` returns the same dict for a synthetic registry.
- `validate_thread` accepts a small thread through cleanly and returns
  zero rejections.
- CLI `python -m pipeline.validator --help` unchanged.

## Known carry-over (not addressed here)

The Task-ID scope mismatch first surfaced in `phase-03-node-ids.md`
still holds: `post_processor.py:363` mints Task IDs scoped by topic,
while `_assign_node_ids` (now `pipeline/validation/id_minting.py`)
scopes by `thread_id`. The two ID spaces are still disjoint. Splitting
the file did not, and should not, touch behavior — this needs its own
fix ticket.

## Rollback

```bash
git checkout HEAD -- pipeline/validator.py
git rm -r pipeline/validation/
```

## Next phase

**Step 12 — `infra/llm/` + `infra/embeddings/` provider registries**

The pipeline currently constructs `AsyncOpenAI` directly inside
`pipeline/extraction/extract.py` and embedding clients inside
`pipeline/embed_knowledge.py` / `builders/unified_builder.py` /
`builders/post_processor.py` / `builders/vector/build_index.py`. Each
site re-reads its own settings and instantiates its own client. The
plan is the same shape as `lore-backend/infra/` — a thin
`ChatProvider`/`EmbeddingProvider` Protocol pair, one module per
provider implementation, and a tiny registry that the call sites
resolve through `get_settings().llm_provider` /
`get_settings().embedding_provider`.

Locked decision (carry-over): NVIDIA NeMo remains the canonical
embedding model; OpenAI/OpenRouter code stays commented for reference.
