"""
pipeline/triple_extractor.py
─────────────────────────────
Stage 5 — Triple extraction from email threads.

NOTE: This file is now a re-export shim. The implementation lives in
`pipeline/extraction/` (split into prompts, rate_limit, grouping,
checkpoint, output, extract, cli — see `pipeline/extraction/__init__.py`).
This shim preserves the historical import paths used by external
callers (`knowledge_distillation/*`, `scripts/model_testing.py`) so
they keep working unchanged.

Usage (CLI is unchanged):
    python -m pipeline.triple_extractor --org ezobooks --limit 5
    python -m pipeline.triple_extractor --org ezobooks --resume
"""

from pipeline.extraction import (  # noqa: F401  (re-export)
    AsyncRateLimiter,
    BATCH_SIZE,
    CHECKPOINT_EVERY,
    CORRECTION_PROMPT,
    MAX_RETRIES,
    RETRY_BACKOFF,
    SYSTEM_PROMPT,
    build_org_context,
    build_thread_prompt,
    correct_one,
    extract_all,
    extract_one,
    group_by_thread,
    load_person_registry,
    strip_quoted_replies,
)
from pipeline.extraction.checkpoint import (  # noqa: F401
    _load_checkpoint,
    _save_checkpoint,
)
from pipeline.extraction.extract import (  # noqa: F401
    _make_error_result,
    _process_batch,
)
from pipeline.extraction.output import _save_output  # noqa: F401
from pipeline.extraction.prompts import _get_sender_name  # noqa: F401
from pipeline.extraction.cli import build_parser, main  # noqa: F401


if __name__ == "__main__":
    from pipeline.extraction.cli import cli_entry
    cli_entry()
