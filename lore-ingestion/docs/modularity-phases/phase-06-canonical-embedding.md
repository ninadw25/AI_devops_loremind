# Phase 6 — Canonicalize embedding model (NVIDIA NeMo)

**Status:** ✅ Completed (2026-05-19)
**Risk:** MEDIUM — changes embedding-space dimension for the topic-clustering and legacy-builder paths (1536 → 2048). Code is safe; existing artifacts may need regeneration. Not yet re-run on production data.
**Files touched:**
- `builders/post_processor.py` — `embed_topics()` rewritten to use NVIDIA NeMo; gate switched from `openrouter_api_key` to `nvidia_api_key`.
- `builders/vector/build_index.py` — OpenRouter path commented out (preserved per locked decision), new `_nvidia_embed()` added, `embed_documents()` now uses NVIDIA when available with local sentence-transformers fallback. `requests as _requests` import commented (only the OpenRouter path used it).
- `common/config.py` — `REQUIRED_BY_STAGE["post_processor"]` changed from `("openrouter_api_key",)` to `("nvidia_api_key",)`.

## Locked decisions implemented

1. ✅ Switch `post_processor.embed_topics` to NVIDIA NeMo.
2. ✅ Comment out (don't delete) the OpenAI/OpenRouter code in `build_index.py`.
3. ✅ Keep local sentence-transformers fallback in `build_index.py` (still useful for offline / dev).
4. ✅ Update `REQUIRED_BY_STAGE["post_processor"]` to reflect new requirement.

## Code shape (after)

### `builders/post_processor.py:embed_topics`

```python
def embed_topics(topics: list[str]) -> np.ndarray:
    from openai import OpenAI
    s = get_settings()
    client = OpenAI(api_key=s.nvidia_api_key, base_url=s.nvidia_base_url)

    all_embeddings: list[list[float]] = []
    for i in range(0, len(topics), 32):
        batch = topics[i:i + 32]
        resp = client.embeddings.create(
            input=batch,
            model=s.embedding_model,                # nvidia/llama-3.2-nemoretriever-300m-embed-v1
            encoding_format="float",
            extra_body={"input_type": "query", "truncate": "NONE"},
        )
        all_embeddings.extend(item.embedding for item in resp.data)
    return np.array(all_embeddings, dtype=np.float32)
```

This mirrors `pipeline/embed_knowledge.py:embed_texts` exactly so the vectors land in the same space.

### `builders/vector/build_index.py:embed_documents`

```python
s = get_settings()
if s.nvidia_api_key:
    embeddings = _nvidia_embed(texts)            # NVIDIA NeMo, 2048-dim
else:
    model = _get_embed_model()                   # sentence-transformers, 384-dim
    embeddings = model.encode(texts, batch_size=256, normalize_embeddings=True)
```

The OpenRouter path is preserved verbatim as a comment block above `_get_embed_model`, ready to re-enable if needed.

## What the LLM cluster labeling still uses (intentional)

`label_clusters_llm()` in `post_processor.py` calls `google/gemini-2.0-flash-001` via OpenRouter chat completions to give clusters nicer canonical names. That is a *chat call*, not an *embedding call*. It is optional — when `openrouter_api_key` is unset, the function returns the identity mapping (clusters keep their raw, most-frequent-member names). So:

- NVIDIA_API_KEY: **required** for post_processor (drives embeddings).
- OPENROUTER_API_KEY: **optional** for post_processor (nicer cluster labels only).

If you eventually want to move the labeling LLM call onto NVIDIA too, that is a separate, smaller change.

## Smoke test results

```
OK imports
OK embed_topics references nvidia_api_key + embedding_model, no openrouter
OK _nvidia_embed present; _openrouter_embed and OPENROUTER_* constants removed
   from module namespace
OK validate_for('post_processor') -> Stage 'post_processor' requires non-empty
   env vars: NVIDIA_API_KEY. Check your .env file.
OK validate_for passes with NVIDIA_API_KEY set (OpenRouter not required)
```

## Data implication — needs user attention before next production run

Topic-clustering embeddings now have **2048 dimensions** instead of 1536. The existing `output/knowledge/<org>_topic_taxonomy.json` (if any) was computed in the old 1536-dim space, so HDBSCAN clusters there are not directly comparable. You will want to:

```bash
# Re-run topic clustering on the new embedding space:
../.venv/bin/python -m builders.post_processor --org ezobooks

# This regenerates _topic_taxonomy.json and the pass3_post_processing.cypher
# file. The Cypher file is idempotent (MERGE statements), but the cluster
# *labels* will likely differ. Audit before re-applying to Neo4j.
```

The legacy standalone vector index from `builders/vector/build_index.py` will also need a `--rebuild` to switch from OpenRouter 1536-dim or local 384-dim to NVIDIA 2048-dim. **The main flow** (`pipeline/embed_knowledge.py` → `builders/unified_builder.py`) was already on NVIDIA 2048-dim — those artifacts are unaffected.

## Rollback

```bash
git checkout HEAD -- builders/post_processor.py builders/vector/build_index.py common/config.py
```

No data on disk was modified.

## Next phase

**Step 7 — End-of-run summary logging at every stage**

Add a structured summary log line at the end of every CLI runner. Specific gaps from the audit:

- `connectors/gmail/normalizer.py` (F3) — count parse failures, surface at end.
- `pipeline/validator.py` (F6) — log `validated N / rejected M (%)` at end.
- `connectors/gmail/fetcher.py` (F2) — persist network-failed users to `failed_users.json` + log.
- `pipeline/triple_extractor.py` (F10) — persist permanently-failed threads + log.
- `pipeline/filter.py` (F4) — count LLM-fallback events.
- `builders/post_processor.py` (F7) — count cluster-labeling fallbacks.

Format suggestion (matches `lore-backend` JSON log style):

```
INFO stage=triple_extractor input=14213 output=14206 dropped=7
     duration_s=3421.5 model=deepseek-ai/deepseek-v3.2 rate_limited=2
```

Step 7 is purely additive (no behavior change).
