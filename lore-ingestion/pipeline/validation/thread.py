"""
pipeline/validation/thread.py
─────────────────────────────
Thread-level validation: runs auto-correction → fuzzy person
resolution → per-triple validation, then enriches the thread with
RBAC fields (access_groups, sensitivity).
"""

from __future__ import annotations

from common.rbac import (
    ALLOWED_DEPARTMENTS,
    ALLOWED_SENSITIVITY,
    compute_access_groups,
    compute_sensitivity,
)

from pipeline.validation.name_lookup import resolve_person_names_in_triple
from pipeline.validation.rules import auto_correct_directions, validate_triple


def validate_thread(
    thread_result: dict,
    name_lookup: dict,
    persons_by_id: dict | None = None,
) -> tuple[dict, list[dict]]:
    """Validate all triples in a thread result.

    Returns (validated_thread, list_of_rejected_triples).
    The validated_thread has RBAC fields enriched.

    If persons_by_id is provided, applies fuzzy person name
    resolution before validation — handles casing variants,
    parenthetical annotations, and partial names (first-name only).
    """
    tid = thread_result.get("thread_id", "")
    raw_triples = thread_result.get("triples", [])
    rejected: list[dict] = []
    valid_triples: list[dict] = []
    auto_corrections: dict[str, int] = {}

    for triple in raw_triples:
        # ── Auto-correct reversed directions ──────────────────────
        # Note: patterns like Fact→REGARDING or Org→MENTIONS→Org are
        # NOT silently removed — they flow through as schema errors
        # so the correction pass can fix them.
        triple, correction_type = auto_correct_directions(triple)
        if correction_type:
            auto_corrections[correction_type] = (
                auto_corrections.get(correction_type, 0) + 1)

        # ── Fuzzy-resolve Person names to canonical form ──────────
        if persons_by_id is not None:
            triple = resolve_person_names_in_triple(
                triple, name_lookup, persons_by_id)

        errors = validate_triple(triple, name_lookup)
        if errors:
            rejected.append({
                "thread_id": tid,
                "triple": triple,
                "reasons": errors,
            })
        else:
            valid_triples.append(triple)

    # ── Validate department tags ──────────────────────────────────
    dept_tags = thread_result.get("department_tags", [])
    dept_tags = [d for d in dept_tags if d in ALLOWED_DEPARTMENTS]
    if not dept_tags:
        dept_tags = ["general"]

    # ── Validate sensitivity ──────────────────────────────────────
    sensitivity = thread_result.get("sensitivity", "internal")
    if sensitivity not in ALLOWED_SENSITIVITY:
        sensitivity = "internal"

    # ── Compute RBAC from department tags ─────────────────────────
    access_groups = compute_access_groups(dept_tags)
    sens_score, sens_label = compute_sensitivity(dept_tags)

    # Build validated thread
    validated = {
        "thread_id": tid,
        "triples": valid_triples,
        "thread_summary": thread_result.get("thread_summary", ""),
        "email_count": thread_result.get("email_count", 0),
        "scope_id": thread_result.get("scope_id"),
        "source_ref": thread_result.get("source_ref"),
        "source_url": thread_result.get("source_url"),
        "department_tags": dept_tags,
        "sensitivity": sens_label,
        "sensitivity_score": sens_score,
        "access_groups": access_groups,
    }
    # Preserve source/scoping fields from the extraction/normalizer stage so
    # downstream builders can emit scope links and citations.
    for _k in ("scope_id", "scope_ids", "extra_scope_ids", "source_ref", "source_url", "url", "project", "raw"):
        if _k in thread_result and thread_result.get(_k) is not None:
            validated[_k] = thread_result.get(_k)
    if auto_corrections:
        validated["_auto_corrections"] = auto_corrections

    return validated, rejected
