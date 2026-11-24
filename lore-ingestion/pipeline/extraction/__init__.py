"""
pipeline.extraction
───────────────────
Stage 5 — LLM triple extraction, split into focused modules:

  rate_limit  AsyncRateLimiter (sliding-window throttle)
  prompts     SYSTEM_PROMPT / CORRECTION_PROMPT, prompt builders,
              quote-strip helper, organizational context helper
  grouping    group_by_thread, load_person_registry
  checkpoint  _load_checkpoint, _save_checkpoint (raises
              CheckpointCorrupt by default; --force-restart opts in
              to deleting corrupt files)
  output      _save_output
  extract     extract_one, correct_one, _process_batch, extract_all
  cli         argparse + entry point

`pipeline/triple_extractor.py` is now a re-export shim that preserves
the historical import paths used by `knowledge_distillation/*` and
`scripts/model_testing.py`.

The public surface of this subpackage is everything re-exported below.
Internal helpers prefixed with `_` (e.g. `_load_checkpoint`) are
re-exported only because external code already imports them by that
name.
"""

from pipeline.extraction.checkpoint import (
    _load_checkpoint,
    _save_checkpoint,
)
from pipeline.extraction.extract import (
    BATCH_SIZE,
    CHECKPOINT_EVERY,
    MAX_RETRIES,
    RETRY_BACKOFF,
    _make_error_result,
    _process_batch,
    correct_one,
    extract_all,
    extract_one,
)
from pipeline.extraction.grouping import (
    group_by_thread,
    load_person_registry,
)
from pipeline.extraction.output import _save_output
from pipeline.extraction.prompts import (
    CORRECTION_PROMPT,
    SYSTEM_PROMPT,
    _get_sender_name,
    build_org_context,
    build_thread_prompt,
    strip_quoted_replies,
)
from pipeline.extraction.rate_limit import (
    AsyncRateLimiter,
    TokenAwareRateLimiter,
    estimate_request_tokens,
)

__all__ = [
    # Public — used by external callers (knowledge_distillation, scripts)
    "AsyncRateLimiter",
    "TokenAwareRateLimiter",
    "estimate_request_tokens",
    "SYSTEM_PROMPT",
    "CORRECTION_PROMPT",
    "build_org_context",
    "build_thread_prompt",
    "group_by_thread",
    "load_person_registry",
    "strip_quoted_replies",
    # Pipeline driver + helpers
    "extract_all",
    "extract_one",
    "correct_one",
    # Constants (some tests / tools depend on these)
    "BATCH_SIZE",
    "CHECKPOINT_EVERY",
    "MAX_RETRIES",
    "RETRY_BACKOFF",
]
