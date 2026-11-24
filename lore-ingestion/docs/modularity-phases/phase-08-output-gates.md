# Phase 8 — Minimum-output gates at stage boundaries

**Status:** ✅ Completed (2026-05-19)
**Risk:** LOW
**Files touched:**
- `common/stage_gate.py` (NEW) — `check_nonempty()` + `StageOutputEmpty` exception.
- 9 runners now call `check_nonempty(...)` after their summary line and accept a `--allow-empty` flag.

## What `check_nonempty()` does

```python
from common.stage_gate import check_nonempty, StageOutputEmpty

check_nonempty(stage="filter", count=kept, what="events kept",
               allow_empty=allow_empty)
```

- If `count > 0`: silent no-op.
- If `count == 0` and `allow_empty=False`: raises `StageOutputEmpty` with a precise message naming the stage.
- If `count == 0` and `allow_empty=True`: logs a WARNING and returns (operator-acknowledged empty run).

The exception is a subclass of `RuntimeError` — uncaught, it exits the process with a non-zero status. The next pipeline stage will not run.

## Stage → what gets gated

| Stage | Gate count | Reasoning |
|-------|-----------|-----------|
| `gmail_fetch` | `total_events_written` | If 0 events landed in JSONL, every downstream stage is poisoned. |
| `filter` | `kept` | If filter dropped everything, the signal-classifier is broken or the input is wrong. |
| `person_registry` | `stats["total_persons"]` | If 0 persons identified, identity resolution failed. |
| `comm_graph` | `stats["total_nodes"]` | If 0 nodes, registry handoff failed. |
| `triple_extractor` | `len(results)` | Counts the full artifact (includes resumed completed threads), so `--resume` runs with nothing new still pass. |
| `validator` | `stats["triples_valid"]` | If 0 valid triples, extraction quality collapsed. |
| `embed_knowledge` | `len(nodes)` | If 0 nodes embedded, no semantic data to upload. |
| `deduplicator` | `triples_in - duplicates_dropped` | If everything was a duplicate, something is wrong upstream. |
| `unified_builder` | `cypher_files + nodes_uploaded` | A run with neither Cypher nor uploads accomplished nothing. |

`post_processor` is not gated — it's an enhancement stage on top of validated triples and a clean run can legitimately produce an empty taxonomy when there are too few unique topics for HDBSCAN. The label-fallback counter from Step 7 already surfaces degraded behavior.

## CLI flag

Every gated runner now accepts `--allow-empty`. Use cases:
- `--limit 0` style dry-runs.
- Debugging on a tiny test slice that legitimately yields zero outputs.
- Calibration runs (`pipeline/deduplicator.py --calibrate` already exits early; the gate never sees that path).

## Verification

```bash
$ grep -rln "from common.stage_gate import" --include="*.py" .
common/stage_gate.py
pipeline/deduplicator.py pipeline/embed_knowledge.py pipeline/filter.py
pipeline/validator.py pipeline/person_registry.py pipeline/comm_graph.py
pipeline/triple_extractor.py
builders/unified_builder.py
connectors/gmail/run.py

# Behaviour:
check_nonempty('test', count=5, what='widgets')   # silent pass
check_nonempty('test', count=0, what='widgets')   # raises StageOutputEmpty
check_nonempty('test', count=0, ..., allow_empty=True)   # warns, returns
```

All 10 modules (9 runners + helper) import cleanly. Gate logic round-trip-verified.

## Rollback

```bash
git checkout HEAD -- \
    pipeline/filter.py pipeline/validator.py pipeline/triple_extractor.py \
    pipeline/embed_knowledge.py pipeline/person_registry.py \
    pipeline/comm_graph.py pipeline/deduplicator.py \
    builders/unified_builder.py connectors/gmail/run.py
rm common/stage_gate.py
```

## What this gives operators

If an LLM run loses 100% of threads to a JSON parsing regression, the operator now sees:

```
ERROR ... StageOutputEmpty: Stage 'triple_extractor' produced 0 threads in
       output. This is almost always an upstream bug; pass --allow-empty
       to override if you intended an empty run.
```

…instead of a "VALIDATION COMPLETE" message six stages later on an empty validated.json artifact.

## Next phase

**Step 9 — Replace silent checkpoint corruption with explicit recovery**

Two call sites currently swallow checkpoint corruption and silently start fresh:

- `pipeline/embed_knowledge.py:158-160` — `except Exception as e: log.warning("Corrupt checkpoint (%s) — starting fresh", e); return [], 0`
- `pipeline/triple_extractor.py:649-651` — `except (json.JSONDecodeError, OSError): log.warning("Corrupt checkpoint, starting fresh"); return {}`

Both should default to **exiting non-zero with an explicit error** and require a new `--force-restart` flag to opt into the current behavior. A 30-hour LLM run silently restarting because a checkpoint got corrupted at hour 25 is exactly the failure mode this codebase was set up to avoid.

Risk: LOW. Pure local change at two call sites, plus one new CLI flag in each.
