# Phase 10 — Split `pipeline/triple_extractor.py` into `pipeline/extraction/`

**Status:** ✅ Completed (2026-05-19)
**Risk:** MEDIUM (largest file in the codebase; external callers depend on it)

## What changed

The 1339-line god module `pipeline/triple_extractor.py` was split into a
focused subpackage. The original path now contains a re-export shim, so
every external caller (`knowledge_distillation/build_sft_dataset.py`,
`knowledge_distillation/distill.py`, `knowledge_distillation/allocator.py`,
`scripts/model_testing.py`) keeps working with zero edits.

### New layout

```
pipeline/extraction/
  __init__.py       — public surface + re-exports (single import boundary)
  rate_limit.py     — AsyncRateLimiter (~30 lines)
  prompts.py        — SYSTEM_PROMPT, CORRECTION_PROMPT, strip_quoted_replies,
                       build_org_context, _get_sender_name, build_thread_prompt
                       (~430 lines — dominated by the long prompt string)
  grouping.py       — group_by_thread, load_person_registry (~60 lines)
  checkpoint.py     — _load_checkpoint (raises CheckpointCorrupt; force_restart
                       opt-in), _save_checkpoint (~40 lines)
  output.py         — _save_output (~12 lines)
  extract.py        — extract_one, correct_one, _process_batch, extract_all,
                       MAX_RETRIES / RETRY_BACKOFF / BATCH_SIZE / CHECKPOINT_EVERY
                       (~430 lines — actual extraction logic)
  cli.py            — argparse + main() + cli_entry() (~95 lines)
pipeline/triple_extractor.py
  → 50-line shim that re-exports the public surface and forwards CLI calls
    to `pipeline.extraction.cli.cli_entry`.
```

The shim is the only file in `pipeline/` allowed to know which symbols
live in which submodule. Everything else imports either from
`pipeline.triple_extractor` (legacy callers) or from
`pipeline.extraction` directly (new code should prefer this).

## External imports preserved (the shim contract)

Grepped before touching anything:

```
knowledge_distillation/build_sft_dataset.py:
    SYSTEM_PROMPT, build_org_context, build_thread_prompt,
    group_by_thread, load_person_registry
knowledge_distillation/allocator.py:
    group_by_thread
knowledge_distillation/distill.py:
    AsyncRateLimiter, SYSTEM_PROMPT, group_by_thread, build_thread_prompt,
    build_org_context, load_person_registry
scripts/model_testing.py:
    AsyncRateLimiter, SYSTEM_PROMPT, group_by_thread, load_person_registry,
    build_thread_prompt, build_org_context
```

All six symbols are exported by the shim, and `pipeline.extraction.SYSTEM_PROMPT
is pipeline.triple_extractor.SYSTEM_PROMPT` (verified — single object,
not two copies).

## Behavioral change: `_process_batch` rate_limiter arg

The original loop body at `pipeline/triple_extractor.py:1152` called
`_process_batch` with 8 positional args — **missing `rate_limiter`**:

```python
# OLD (in-loop call, 8 args — would TypeError on first full batch)
await _process_batch(
    batch, client, model, semaphore,
    email_to_person, persons_by_id,        # rate_limiter slot
    org_context, name_lookup)
```

The very next call (the flush of the trailing partial batch at line 1184)
was correct (9 args, with `rate_limiter`). This is an obvious copy/paste
typo: `_process_batch` declares 9 required positional args, and a full
batch of 50 threads would have raised `TypeError: _process_batch() missing
1 required positional argument: 'name_lookup'` on every multi-batch run.

The refactor fixes the in-loop call to match the flush call (and the
function's actual signature). Both calls now pass `rate_limiter`
consistently. This is the only intentional behavioral change in this
phase.

> Why fix during a refactor: the typo would have crashed every full
> production run the moment it hit 50 threads. Leaving it broken under
> the new structure would make the split look like the cause when
> someone next runs the pipeline. Flagged here so the change is
> auditable.

## Smoke tests (all green)

```
$ python -c "from pipeline.triple_extractor import (
      SYSTEM_PROMPT, build_org_context, build_thread_prompt,
      group_by_thread, load_person_registry, AsyncRateLimiter)"          → OK
$ python -c "import pipeline.triple_extractor as te
      for s in ['extract_all','extract_one','correct_one','_process_batch',
                '_load_checkpoint','_save_checkpoint','_save_output',
                'CORRECTION_PROMPT','strip_quoted_replies','main','BATCH_SIZE']:
          assert hasattr(te, s)"                                          → OK
$ python -c "from pipeline.triple_extractor import SYSTEM_PROMPT
              from pipeline.extraction.prompts import SYSTEM_PROMPT as S2
              assert SYSTEM_PROMPT is S2"                                 → OK
$ python -m pipeline.triple_extractor --help                              → OK
```

## Rollback

```bash
git checkout HEAD -- pipeline/triple_extractor.py
git rm -r pipeline/extraction/
```

Callers continue to work unchanged because the shim's import names match
the old module's top-level names exactly.

## Next phase

**Step 11 — Split `pipeline/validator.py` (755 LOC) into `pipeline/validation/` subpackage + shim**

Same pattern. External callers of `validator.py` (will need a grep
before edits, but at minimum `pipeline/triple_extractor.py:28` via the
shim and `scripts/model_testing.py`) need their symbols preserved.

Likely split:
```
pipeline/validation/
  __init__.py         — re-exports
  name_lookup.py      — build_name_lookup (rapidfuzz machinery)
  schema_rules.py     — schema/predicate direction checks
  content_rules.py    — description length, HTML/placeholder checks
  structural_rules.py — orphan-decision/orphan-task checks (check_structural_rules)
  id_minting.py       — _assign_node_ids (delegates to common.hashing.node_id_for)
  validate.py         — validate_thread driver
  cli.py              — argparse + main()
```

The pre-existing Task-ID-scope mismatch already flagged in
[[phase-03-node-ids]] is **not** addressed here either — that's a
behavioral fix that needs its own ticket.
