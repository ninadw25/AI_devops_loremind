# Phase 7 — End-of-run summary logging at every stage

**Status:** ✅ Completed (2026-05-19)
**Risk:** LOW (additive only — every change is a new log line or counter)
**Files touched:**
- `common/stage_summary.py` (NEW) — `log_summary(stage, **fields)` emits one structured line.
- 10 runners now end with `log_summary(...)`.
- Audit-gap targeted improvements: F2, F3, F4, F6, F7, F10 closed.

## The summary helper

`common.stage_summary.log_summary` produces one greppable line per stage:

```
STAGE_SUMMARY stage=<name> key=value key=value ...
```

- Quoted with `"…"` when a value contains spaces.
- `None` values are dropped so callers can pass optional fields uniformly.
- Logged under logger name `stage` so operators can filter with `grep '\[stage\]' run.log`.

## Per-stage summary line emitted

| Stage | Key fields |
|-------|-----------|
| `gmail_fetch` | users, messages, duplicates, events, thread_events, **normalize_failures (F3)**, **failed_users (F2)**, duration_s |
| `filter` | input, kept, dropped, drop_pct, classification, **llm_requested + llm_fallback (F4)**, duration_s |
| `person_registry` | persons, internal, external, functional, multi_email, events, merged_by_name, duration_s |
| `comm_graph` | nodes, internal_nodes, edges, communities, duration_s |
| `triple_extractor` | threads_processed, triples_total, errors, corrections, tokens, model, duration_s, **failures_path (F10)** |
| `validator` | threads, triples_in, triples_valid, triples_rejected, rejection_pct (F6), auto_corrections, duration_s |
| `embed_knowledge` | nodes, dim, size_mb, model, duration_s |
| `deduplicator` | nodes, clusters_merged, nodes_remapped, triples_in, duplicates_dropped, duration_s |
| `unified_builder` | cypher_files, upload (status enum), nodes_uploaded, duration_s |
| `post_processor` | clusters, label_calls, **label_fallbacks (F7)**, skip_clustering, duration_s |

## Audit gaps closed

- **F2** — `connectors/gmail/run.py` now writes `output/config/<org>_gmail_failed_users.json` listing every user with `events_written == 0`. A `WARNING` is logged when the list is non-empty; the path is referenced in the summary line.
- **F3** — `connectors/gmail/run.py` now tracks `normalize_failures` (was already caught but uncounted). Surfaced in both a warning and the summary line.
- **F4** — `pipeline/filter.py` now records `llm_requested` and `llm_fallback_reason` in the stats dict and the summary line. Operators can grep for `llm_fallback=missing_anthropic_api_key`.
- **F6** — `pipeline/validator.py` was already comprehensive in its end-block logging; added the one-line summary so the rejection percentage is greppable.
- **F7** — `builders/post_processor.py` now counts `label_calls` and `label_fallbacks` (LLM cluster-naming) in a module-level `_label_stats` dict that resets per-run.
- **F10** — `pipeline/triple_extractor.py` now collects every thread that ended with `_error` in the result and persists them as `{thread_id, error}` records to `<org>_extraction_failures.json` (file is written even when empty, so its existence is the success signal).

## Verification

```
$ grep -rln "log_summary(" --include="*.py" .
common/stage_summary.py
builders/post_processor.py
builders/unified_builder.py
connectors/gmail/run.py
pipeline/deduplicator.py
pipeline/validator.py
pipeline/embed_knowledge.py
pipeline/comm_graph.py
pipeline/person_registry.py
pipeline/filter.py
pipeline/triple_extractor.py
```

All 10 runners + the helper. Example output:

```
stage INFO STAGE_SUMMARY stage=validator org=ezobooks threads=14213
                          triples_in=58213 triples_valid=49000
                          triples_rejected=9213 rejection_pct=15.8
                          duration_s=421.5 note="nominal run"
```

All modules import cleanly after the changes.

## Rollback

```bash
git checkout HEAD -- \
    common/stage_summary.py \
    pipeline/filter.py pipeline/validator.py pipeline/triple_extractor.py \
    pipeline/embed_knowledge.py pipeline/person_registry.py \
    pipeline/comm_graph.py pipeline/deduplicator.py \
    builders/unified_builder.py builders/post_processor.py \
    connectors/gmail/run.py
rm common/stage_summary.py
```

No data on disk has changed except: `_gmail_failed_users.json` and `_extraction_failures.json` are now written on every run. Both are diagnostic artifacts; their absence does not affect downstream stages.

## Next phase

**Step 8 — Minimum-output gates at stage boundaries**

Add an assertion at the end of each stage runner that the produced artifact has more than zero rows (or a configurable threshold). Today, if a stage produces 0 outputs because of an upstream bug, downstream stages just run on the empty artifact and the pipeline reports "success" 6 times. The fix is one `if len(output) == 0: raise StageOutputEmpty(...)` per stage.

Specific places that need the gate (working forward through the pipeline):

- `pipeline/filter.py` — at end of `run_filter`, check `kept > 0` (or > threshold).
- `pipeline/person_registry.py` — check `len(registry["persons"]) > 0`.
- `pipeline/comm_graph.py` — check `total_nodes > 0`.
- `pipeline/triple_extractor.py` — check `len(results) > 0`.
- `pipeline/validator.py` — check `triples_valid > 0` (else likely a schema or person-registry mismatch).
- `pipeline/embed_knowledge.py` — check `len(nodes) > 0`.
- `pipeline/deduplicator.py` — check `total_triples_out > 0`.
- `builders/unified_builder.py` — check `cypher_files > 0`.

Add an `--allow-empty` flag to each runner so the gate can be bypassed when intended (e.g., dry-run / single-thread debugging).
