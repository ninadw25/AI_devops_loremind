# Phase 12 — `infra/llm/` + `infra/embeddings/` provider registries

**Status:** ✅ Completed (2026-05-19)
**Risk:** MEDIUM (every LLM and embedding call site touched; behavior must remain identical)

## Why

Before this phase, every stage that needed an LLM or embedding client
re-derived the same pattern: read settings, construct an OpenAI
client, hard-code the model + endpoint, then call the API. Five
call sites, five copies of the same wiring:

```
pipeline/extraction/extract.py          AsyncOpenAI(nvidia)  → chat
builders/post_processor.py:label_clusters_llm   OpenAI(openrouter)   → chat
pipeline/embed_knowledge.py:embed_texts         OpenAI(nvidia)       → embed
builders/post_processor.py:embed_topics         OpenAI(nvidia)       → embed
builders/vector/build_index.py:_nvidia_embed    OpenAI(nvidia)       → embed
```

If a future caller (say, a backfill script) wants to swap NVIDIA for
a local self-hosted endpoint, the change has to land in five files.
Worse, two of those files duplicate the retry-on-429 logic with
subtly different cooldown values (30s vs 60s vs 10s).

This phase centralizes the wiring behind two thin abstractions.

## New layout

```
infra/
  __init__.py
  llm/
    __init__.py         — public surface: ChatProvider, get_chat_provider
    base.py             — ChatProvider Protocol (sync + async client factories)
    nvidia.py           — NvidiaChatProvider (base_url, api_key, default_model)
    openrouter.py       — OpenrouterChatProvider
    registry.py         — get_chat_provider(name=None) — settings-driven
  embeddings/
    __init__.py         — public surface: EmbeddingProvider, get_embedding_provider
    base.py             — Protocol with embed_batch + embed_all
    nvidia.py           — NvidiaEmbeddingProvider (NeMo Retriever, 2048-dim,
                          retry-on-429, L2 normalize)
    registry.py         — get_embedding_provider(name=None)
```

The shape mirrors `lore-backend/infra/`. Each provider is one tiny
file. Adding a new provider is two lines: a new module under
`infra/<kind>/` and one entry in `_PROVIDERS`.

## Public surface

```python
from infra.llm import get_chat_provider, ChatProvider
from infra.embeddings import get_embedding_provider, EmbeddingProvider

# Chat: provider hands back a configured client. Callers control
# timeout/retry/JSON-mode quirks at the call site where they belong.
provider = get_chat_provider()                      # → settings.llm_provider
client   = provider.make_async_client(max_retries=0, timeout=300.0)
model    = provider.default_model

# Embeddings: provider also owns batching/retry/normalize via embed_all,
# but exposes raw embed_batch for callers (like Stage 6.5) that need
# their own checkpoint loop.
p   = get_embedding_provider()                      # → settings.embedding_provider
arr = p.embed_all(texts)                            # np.float32 [N, dim], normalized
raw = p.embed_batch(texts)                          # list[list[float]], no retry
```

Both registries raise `ValueError` on unknown names so a typo in
`.env` can't silently fall through to the default.

## Settings additions

`common/config.py` gained one field:

```python
llm_provider: Literal["nvidia", "openrouter"] = "nvidia"
```

`embedding_provider` already existed and was waiting for this phase
to wire it up.

## Migrations (5 call sites)

| File | Before | After |
|------|--------|-------|
| `pipeline/extraction/extract.py:extract_all` | `AsyncOpenAI(base_url=s.nvidia_base_url, api_key=s.nvidia_api_key, max_retries=0, timeout=300.0)` | `get_chat_provider().make_async_client(max_retries=0, timeout=300.0)` |
| `builders/post_processor.py:label_clusters_llm` | inline `OpenAI(base_url="https://openrouter.ai/api/v1", api_key=...)` + hard-coded `model="google/gemini-2.0-flash-001"` | `get_chat_provider("openrouter").make_sync_client()` + `provider.default_model` |
| `pipeline/embed_knowledge.py:embed_texts` | `OpenAI(...)` + inline `client.embeddings.create(extra_body=...)` per batch | `get_embedding_provider().embed_batch(batch)` per batch (the surrounding checkpoint loop is unchanged) |
| `builders/post_processor.py:embed_topics` | same as above + manual normalization | `get_embedding_provider().embed_batch` in a small loop |
| `builders/vector/build_index.py:_nvidia_embed` | 40-line custom batch/retry/normalize | `get_embedding_provider().embed_all(texts, batch_size=NVIDIA_EMBED_BATCH_SIZE, max_retries=3)` |

Direct `from openai import …` is now down to **one site**
(`pipeline/extraction/extract.py`, which keeps the `AsyncOpenAI`
import purely for the function signature type annotations of
`extract_one` / `correct_one`). Every actual client construction in
`pipeline/` and `builders/` goes through the registry.

## What was left alone (on purpose)

- `pipeline/filter.py` constructs an `anthropic.Anthropic` client
  directly. This stays out of the registry — Anthropic is a different
  SDK with a different message format, and the filter's LLM path is
  optional (gated by `--skip-llm` in the default flow). Adding it
  would mean a second Protocol; not worth it for one caller.
- `embed_texts`'s `api_key` argument is preserved in the signature
  (now ignored with `del api_key`) so existing callers in
  `run_embed_knowledge()` don't need rewrites. Could be cleaned up
  in a later refactor, but a behavior-only refactor doesn't need to
  reshape signatures.

## Behavioral parity

Every migration preserves the original behavior:

- **NVIDIA chat client** still constructed with `max_retries=0,
  timeout=300.0`. The 40 RPM rate limiter + asyncio.Semaphore(4) at
  the call site are untouched.
- **OpenRouter chat client** still defaults to
  `google/gemini-2.0-flash-001` (now via `provider.default_model`
  instead of a hard-coded string).
- **NVIDIA embeddings** still use `input_type=query`,
  `truncate=NONE`, and the same retry-on-429 logic (`NvidiaEmbeddingProvider.embed_all`
  uses a 30s backoff like `_nvidia_embed`; `embed_knowledge` keeps
  its own 60s backoff loop around `embed_batch`).
- L2 normalization in `embed_all` produces bit-identical output to
  the pre-split normalization at every call site.

Verified by:

- `isinstance(get_chat_provider(), ChatProvider)` and
  `isinstance(get_embedding_provider(), EmbeddingProvider)` — both
  pass at runtime against the Protocol.
- `get_chat_provider("openrouter").default_model ==
  "google/gemini-2.0-flash-001"` — preserves the previously
  hard-coded model.
- CLI `--help` works through all migrated stages
  (`pipeline.triple_extractor`, `pipeline.embed_knowledge`,
  `builders.post_processor`).
- Unknown-provider names raise `ValueError` with a list of valid
  options.

## Rollback

```bash
git checkout HEAD -- \
  pipeline/extraction/extract.py \
  pipeline/embed_knowledge.py \
  builders/post_processor.py \
  builders/vector/build_index.py \
  common/config.py
git rm -r infra/
```

## Next phase

**Step 13 — Collapse `knowledge_distillation/models/*` (10 files) into a single registry**

That directory currently has ten near-identical teacher-model
wrappers (Kimi, Qwen, GLM, NemotronSuper, NS3.0/3.1/3.2, etc.).
They're prime candidates for the same `_PROVIDERS` pattern this
phase introduced.
