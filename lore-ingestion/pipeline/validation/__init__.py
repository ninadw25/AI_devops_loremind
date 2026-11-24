"""
pipeline.validation
───────────────────
Stage 6 — deterministic quality gate, split into focused modules:

  schema        ALLOWED_NODE_TYPES, PREDICATE_SCHEMA, content regexes,
                MIN_DESCRIPTION_LENGTH / MIN_EVIDENCE_LENGTH
  name_lookup   build_name_lookup, resolve_person_name,
                resolve_person_names_in_triple, FUZZY_* tunables
  rules         validate_triple, auto_correct_directions
  structural    check_structural_rules (cross-triple orphan checks)
  thread        validate_thread (orchestrates the per-thread pipeline)
  id_minting    _assign_node_ids (delegates to common.hashing.node_id_for)
  run           run_validation (Stage 6 driver + stats + gate)
  cli           argparse + entry point

`pipeline/validator.py` is now a re-export shim preserving the
historical import surface for `knowledge_distillation/*`,
`scripts/model_testing.py`, and `pipeline/extraction/extract.py`.
"""

from pipeline.validation.id_minting import (
    _NAME_KEYED_NODE_TYPES,
    _SCOPED_NODE_TYPES,
    _assign_node_ids,
)
from pipeline.validation.name_lookup import (
    FUZZY_AMBIGUITY_GAP,
    FUZZY_SCORE_CUTOFF,
    build_name_lookup,
    resolve_person_name,
    resolve_person_names_in_triple,
)
from pipeline.validation.rules import (
    auto_correct_directions,
    validate_triple,
)
from pipeline.validation.run import run_validation
from pipeline.validation.schema import (
    ALLOWED_NODE_TYPES,
    MIN_DESCRIPTION_LENGTH,
    MIN_EVIDENCE_LENGTH,
    PREDICATE_SCHEMA,
)
from pipeline.validation.structural import check_structural_rules
from pipeline.validation.thread import validate_thread

__all__ = [
    # Public — used by external callers
    "build_name_lookup",
    "validate_thread",
    "check_structural_rules",
    # Triple-level rules and helpers
    "validate_triple",
    "auto_correct_directions",
    "resolve_person_name",
    "resolve_person_names_in_triple",
    # Driver
    "run_validation",
    # Constants
    "ALLOWED_NODE_TYPES",
    "PREDICATE_SCHEMA",
    "MIN_DESCRIPTION_LENGTH",
    "MIN_EVIDENCE_LENGTH",
    "FUZZY_SCORE_CUTOFF",
    "FUZZY_AMBIGUITY_GAP",
]
