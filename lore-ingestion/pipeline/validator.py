"""
pipeline/validator.py
─────────────────────
Stage 6 — Deterministic quality gate for extracted triples.

NOTE: This file is now a re-export shim. Implementation lives in
`pipeline/validation/` (split into schema, name_lookup, rules,
structural, thread, id_minting, run, cli — see
`pipeline/validation/__init__.py`). The shim preserves the historical
import surface for external callers (`knowledge_distillation/*`,
`scripts/model_testing.py`, `pipeline/extraction/extract.py`).

Usage (CLI unchanged):
    python -m pipeline.validator --org ezobooks
"""

from pipeline.validation import (  # noqa: F401  (re-export)
    ALLOWED_NODE_TYPES,
    FUZZY_AMBIGUITY_GAP,
    FUZZY_SCORE_CUTOFF,
    MIN_DESCRIPTION_LENGTH,
    MIN_EVIDENCE_LENGTH,
    PREDICATE_SCHEMA,
    auto_correct_directions,
    build_name_lookup,
    check_structural_rules,
    resolve_person_name,
    resolve_person_names_in_triple,
    run_validation,
    validate_thread,
    validate_triple,
)
from pipeline.validation.id_minting import (  # noqa: F401
    _NAME_KEYED_NODE_TYPES,
    _SCOPED_NODE_TYPES,
    _assign_node_ids,
)
from pipeline.validation.schema import (  # noqa: F401
    _CHAT_PASTE_RE,
    _CSS_RE,
    _HTML_RE,
    _PLACEHOLDER_RE,
)
from pipeline.validation.cli import build_parser, main  # noqa: F401


if __name__ == "__main__":
    main()
