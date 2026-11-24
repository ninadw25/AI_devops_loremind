# Phase 16 — Chronograph → common infrastructure consolidation

**Status:** ✅ Completed (2026-05-19) — partial scope; remainder deferred as open follow-ups
**Risk:** LOW (ID outputs proven bit-identical to pre-refactor)

## Audit findings

I walked `chronograph/` looking for duplication against `common/`,
`common/config.py`, `infra/llm/`, `infra/embeddings/`. Three classes
of duplication exist; this phase tackled #1, the others are documented
follow-ups.

### 1. Inline SHA-256 hashing (FIXED)

Four call sites re-implemented the canonical
`hashlib.sha256(raw.encode()).hexdigest()[:16]` idiom inline instead of
calling `common.hashing.short_hash()`:

| File                                | Use                |
|-------------------------------------|--------------------|
| `chronograph/entities.py:96`        | `TemporalEdge.id`  |
| `chronograph/entities.py:117`       | `DiffRecord.id`    |
| `chronograph/store/mongo_store.py:345` | `create_edge`       |
| `chronograph/store/mongo_store.py:372` | `create_edges_bulk` |

Two of these (`mongo_store.py`) used a function-local
`import hashlib` — a smell that the canonical helper wasn't visible
from the call site.

**Fix:** module-level `from common.hashing import short_hash`, and
each call site rewritten to `short_hash(raw, length=16)`. Hash output
is identical (verified with a side-by-side assertion on multiple
inputs and via a `TemporalEdge` round-trip).

`Symbol.body_hash` and `CodeEntityVersion.body_hash` keep their
full-length `sha256().hexdigest()` — those are content-addressing
hashes, not IDs, and `short_hash` is the wrong tool. They stay inline.

### 2. OpenRouter embeddings via raw `requests.post` (DEFERRED)

`chronograph/query/query_engine.py:116-130` calls the OpenRouter
embeddings endpoint with `requests.post(...)` directly. The unified
embedding registry from Step 12 (`infra/embeddings/`) currently has
only `NvidiaEmbeddingProvider` — adding `OpenrouterEmbeddingProvider`
would let chronograph delete its inline HTTP code and select via
`get_embedding_provider("openrouter")`.

This is real consolidation work (~80 LOC of new provider, plus
plumbing for headers/auth differences between OpenRouter and
NVIDIA), and crosses into Step 12's locked decision (NVIDIA NeMo
canonical, OpenRouter commented-not-deleted in the main pipeline).
Chronograph is on the GitLab/code path, which has historically used
OpenRouter embeddings for code-symbol vectors. Switching that to
NVIDIA NeMo is a content-addressing change (vectors are not
interchangeable across embedding models) — needs an org-wide
re-embed.

**Recommendation:** move when (a) chronograph is being re-embedded
anyway, or (b) we decide to standardize chronograph on NVIDIA NeMo.

### 3. Inline `requests.get` for GitLab API (DEFERRED, out of scope)

`chronograph/ingestion/code_fetcher.py` and `ingest_pipeline.py`
make many `requests.get(...)` calls to the GitLab API. This is a
GitLab adapter that doesn't have a sibling in `infra/` — it's not
duplicated, it's just not yet moved under the `infra/` umbrella.

A future step could create `infra/gitlab/` analogous to
`infra/gmail/`, but that's an organizational move (Step 14 pattern),
not a "common infrastructure consolidation." Out of scope for Step
16.

## What landed

```diff
chronograph/entities.py
+ from common.hashing import short_hash
- self.id = f"edge:{hashlib.sha256(raw.encode()).hexdigest()[:16]}"
+ self.id = f"edge:{short_hash(raw, length=16)}"
- self.id = f"diff:{hashlib.sha256(raw.encode()).hexdigest()[:16]}"
+ self.id = f"diff:{short_hash(raw, length=16)}"

chronograph/store/mongo_store.py
+ from common.hashing import short_hash
- import hashlib  (× 2, function-local)
- edge_id = f"edge:{hashlib.sha256(raw.encode()).hexdigest()[:16]}"   (× 2)
+ edge_id = f"edge:{short_hash(raw, length=16)}"                       (× 2)
```

`hashlib` is still imported in `entities.py` for the full-length
content hashes on `Symbol` / `CodeEntityVersion` (intentional).

`chronograph/config.py` was already delegating to
`common/config.py:get_settings()` after Step 5 — no change needed.

## What was verified

```
$ python -c "
    import hashlib
    from common.hashing import short_hash
    for s in ['abc', 'foo:bar:CALLS:2020-01-01', '', 'long' * 50]:
        assert short_hash(s, length=16) == hashlib.sha256(s.encode()).hexdigest()[:16]
  "                                                                        → OK

$ python -c "
    from chronograph.entities import TemporalEdge
    from datetime import datetime
    e = TemporalEdge(source_id='a', target_id='b', rel_type='CALLS',
                     valid_from=datetime(2020,1,1))
    assert e.id == 'edge:43e483b83c78fdaf'   # bit-identical to pre-refactor
  "                                                                        → OK

$ python -c "from chronograph.store.mongo_store import MongoStore"          → OK
$ python -c "from chronograph.entities import (
                TemporalEdge, DiffRecord, Symbol, CodeEntityVersion)"        → OK
```

Edge / diff IDs are deterministic functions of their input strings,
and `short_hash(s, length=16) == sha256(s)[:16]` by construction, so
no existing edges in MongoDB get re-keyed.

## Open follow-up

- **OpenRouter embedding provider** in `infra/embeddings/` —
  `chronograph/query/query_engine.py` still calls
  `https://openrouter.ai/api/v1/embeddings` via raw `requests.post`.
  Add `OpenrouterEmbeddingProvider` (≈80 LOC) and switch the two
  call sites in `query_engine.py:_embed_text` /
  `query_engine.py:251` to `get_embedding_provider("openrouter")`.
  Holds because the NVIDIA-canonical decision (Step 6/12) hasn't
  been extended to the chronograph corpus.
- **GitLab adapter under `infra/`** — `chronograph/ingestion/`
  (`code_fetcher.py`, `ingest_pipeline.py`) is a self-contained
  GitLab adapter. A future organizational step could move it to
  `infra/gitlab/` mirroring the Step 14 `infra/gmail/` pattern. Not
  bundled here because it's a relocation, not a consolidation.

## Rollback

```bash
git checkout HEAD -- chronograph/entities.py chronograph/store/mongo_store.py
```

## End of migration

Step 16 was the final step in the modularity migration plan (Steps
1→16). All 16 phases are now complete; see `STATUS.md` for the
roll-up and the consolidated open-follow-up list.
