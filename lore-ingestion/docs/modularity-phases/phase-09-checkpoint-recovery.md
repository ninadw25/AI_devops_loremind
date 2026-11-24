# Phase 9 — Explicit checkpoint corruption recovery

**Status:** ✅ Completed (2026-05-19)
**Risk:** LOW
**Files touched:**
- `common/stage_gate.py` — added `CheckpointCorrupt(RuntimeError)` exception.
- `pipeline/embed_knowledge.py` — `load_checkpoint()` now raises by default; gained `force_restart` parameter; CLI gained `--force-restart` flag.
- `pipeline/triple_extractor.py` — `_load_checkpoint()` now raises by default; `extract_all()` gained `force_restart` kwarg; CLI gained `--force-restart` flag.

## The change in one diff (conceptual)

**Before:**
```python
except (json.JSONDecodeError, OSError):
    log.warning("Corrupt checkpoint, starting fresh")
    return {}
```

**After:**
```python
except (json.JSONDecodeError, OSError) as e:
    if force_restart:
        log.warning(
            "Corrupt checkpoint (%s) — deleting and starting fresh "
            "because --force-restart was set", e,
        )
        path.unlink(missing_ok=True)
        return {}
    raise CheckpointCorrupt(
        f"Extraction checkpoint at {path} could not be parsed "
        f"({type(e).__name__}: {e}). Re-run with --force-restart "
        f"to discard and start fresh."
    ) from e
```

## Why this matters

The Stage 5 LLM extractor saves a checkpoint every 500 threads. On the EZO Books dataset that's a multi-hour run that costs LLM tokens. Before this change, if hour-25 produced a half-written checkpoint (process killed mid-write, disk full, etc.), the next run silently logged "Corrupt checkpoint, starting fresh" and re-billed every prior thread.

After this change, the run aborts loudly. The operator decides:
- Restore from a backup (the run's actual output is still on disk in `_triples_raw.json`).
- Pass `--force-restart` to acknowledge the loss and re-run.

The default is now **safe**: there is no silent way to lose LLM-paid work.

## Behavior matrix (verified)

| Scenario | Old behavior | New behavior |
|----------|--------------|--------------|
| Checkpoint file absent | Return empty (correct) | Return empty (unchanged) |
| Valid checkpoint | Loads (correct) | Loads (unchanged) |
| Corrupt checkpoint, no flag | **Silent restart from zero** | **Raises `CheckpointCorrupt`, exits non-zero** |
| Corrupt checkpoint, `--force-restart` | (no such flag existed) | Logs WARNING, **deletes the broken file**, returns empty |

The deletion in the `force_restart` path is important: leaving a known-corrupt file around just so the next run can also fail to parse it is worthless.

## Smoke test results

```
OK triple: missing checkpoint → {} (no raise)
OK triple: valid checkpoint loads
OK triple: corrupt raises (CheckpointCorrupt)
OK triple: --force-restart deletes corrupt checkpoint, returns {}
OK embed: missing checkpoint → ([],0)
OK embed: valid checkpoint loads
OK embed: corrupt raises (CheckpointCorrupt)
OK embed: --force-restart deletes corrupt checkpoint
```

All four behavior modes verified for both checkpoint formats.

## Note on existing `--resume` semantics

`pipeline/triple_extractor.py:main()` already deletes the checkpoint when `--resume` is **not** passed (`if not args.resume and checkpoint_path.exists(): checkpoint_path.unlink()`). That logic is unchanged. The new `--force-restart` flag only kicks in if `--resume` *is* passed AND the existing file turns out to be unreadable. In other words:
- `(no flag)`: nuke old checkpoint, start fresh. Same as before.
- `--resume`: try to load checkpoint. If unreadable → abort. New, safer.
- `--resume --force-restart`: try to load checkpoint. If unreadable → delete and start fresh. Same as the old silent behavior, now explicit.

## Rollback

```bash
git checkout HEAD -- \
    common/stage_gate.py \
    pipeline/embed_knowledge.py pipeline/triple_extractor.py
```

## Next phase

**Step 10 — Split `pipeline/triple_extractor.py` (1273 LOC) into `pipeline/extraction/` subpackage + shim**

The largest god module in the codebase. Per the locked decision, the file path stays valid via a re-export shim so `knowledge_distillation/*` and `scripts/model_testing.py` keep working unchanged.

Planned new layout:

```
pipeline/extraction/
  __init__.py       — re-exports the public API (validate_thread, etc.)
  prompts.py        — SYSTEM_PROMPT + user-prompt builders (~200 lines)
  rate_limit.py     — AsyncRateLimiter (~30 lines)
  grouping.py       — thread-size bucket logic (~80 lines)
  checkpoint.py     — _load/_save_checkpoint helpers (~30 lines after Step 9)
  output.py         — _save_output / failures file (~20 lines)
  extract.py        — extract_all() + _process_batch() + retry loop
  cli.py            — argparse + entry point
pipeline/triple_extractor.py
  → one-line shim: `from pipeline.extraction.cli import *`
                   `from pipeline.extraction import *`
```

External imports that must keep working:
- `from pipeline.triple_extractor import THIS_THAT_AND_THE_OTHER` — used by `knowledge_distillation/allocator.py`, `knowledge_distillation/build_sft_dataset.py`, `knowledge_distillation/distill.py`, `scripts/model_testing.py`. Need to enumerate exactly which symbols they import, before moving anything.

Action before edits: `grep -rn "from pipeline.triple_extractor import" --include='*.py' .` and lock that list as the shim's `__all__`.
