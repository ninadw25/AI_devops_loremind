"""
pipeline/validation/rules.py
────────────────────────────
Triple-level validation rules and pre-validation auto-correction
for common LLM directional mistakes.
"""

from __future__ import annotations

from pipeline.validation.schema import (
    ALLOWED_NODE_TYPES,
    MIN_DESCRIPTION_LENGTH,
    MIN_EVIDENCE_LENGTH,
    PREDICATE_SCHEMA,
    _CHAT_PASTE_RE,
    _CSS_RE,
    _HTML_RE,
    _PLACEHOLDER_RE,
)


def validate_triple(triple: dict, name_lookup: dict) -> list[str]:
    """Validate a single triple. Returns list of rejection reasons (empty = valid).

    Triples tagged `derived="field"` (Jira deterministic) bypass LLM-quality
    checks entirely — they come from authoritative structured fields, not from
    a model that can hallucinate. They still pass through auto_correct_directions
    so the schema entries are consistent, but content/evidence/name checks are
    skipped.
    """
    if triple.get("derived") in ("field", "provenance"):
        return []

    errors: list[str] = []

    subject = triple.get("subject", {})
    predicate = triple.get("predicate", "")
    obj = triple.get("object", {})
    evidence = triple.get("evidence_sentence", "")

    s_type = subject.get("type", "")
    o_type = obj.get("type", "")
    s_desc = subject.get("name") or subject.get("description") or ""
    o_desc = obj.get("name") or obj.get("description") or ""

    # ── Schema validation ─────────────────────────────────────────
    if s_type not in ALLOWED_NODE_TYPES:
        errors.append(f"invalid_subject_type:{s_type}")

    if o_type not in ALLOWED_NODE_TYPES:
        errors.append(f"invalid_object_type:{o_type}")

    if predicate not in PREDICATE_SCHEMA:
        errors.append(f"invalid_predicate:{predicate}")
    else:
        allowed_s, allowed_o = PREDICATE_SCHEMA[predicate]
        if s_type not in allowed_s:
            errors.append(
                f"wrong_subject_type:{predicate} expects "
                f"{allowed_s} got {s_type}")
        if o_type not in allowed_o:
            errors.append(
                f"wrong_object_type:{predicate} expects "
                f"{allowed_o} got {o_type}")

    # ── Content quality ───────────────────────────────────────────
    for label, desc, ntype in [("subject", s_desc, s_type),
                                ("object", o_desc, o_type)]:
        # Person/Organization/Topic use "name" — shorter is valid
        # (e.g., "EZO", "IBM", "HR"). Knowledge nodes use
        # "description" — need more substance.
        min_len = (3 if ntype in ("Person", "Organization", "Topic")
                   else MIN_DESCRIPTION_LENGTH)
        if not desc or len(desc.strip()) < min_len:
            errors.append(f"too_short_{label}:{desc!r}")
            continue
        if _HTML_RE.search(desc):
            errors.append(f"html_garbage_{label}")
        if _CSS_RE.search(desc):
            errors.append(f"css_garbage_{label}")
        if _CHAT_PASTE_RE.search(desc):
            errors.append(f"chat_paste_{label}")
        if _PLACEHOLDER_RE.match(desc.strip()):
            errors.append(f"placeholder_{label}:{desc!r}")

    # ── Evidence sentence ─────────────────────────────────────────
    if predicate in ("DECIDED", "BECAUSE", "ASSIGNED_TO"):
        if not evidence or len(evidence.strip()) < MIN_EVIDENCE_LENGTH:
            errors.append("missing_evidence_for_critical_predicate")

    # ── Person name resolution ────────────────────────────────────
    if s_type == "Person":
        if s_desc.lower() not in name_lookup:
            errors.append(f"unresolved_person_subject:{s_desc}")

    if o_type == "Person":
        if o_desc.lower() not in name_lookup:
            errors.append(f"unresolved_person_object:{o_desc}")

    return errors


def auto_correct_directions(triple: dict) -> tuple[dict, str | None]:
    """Fix known directional errors the model makes.

    Returns (triple, correction_type) where correction_type identifies
    which pattern was corrected (e.g., "DECIDED_reversed") or None if
    no correction applied. Per-pattern tracking lets us measure how
    often the model makes each direction mistake.
    """
    pred = triple.get("predicate", "")
    subj_type = triple.get("subject", {}).get("type", "")
    obj_type = triple.get("object", {}).get("type", "")
    correction: str | None = None

    # DECIDED should be (Person|Organization) -> Decision
    if (pred == "DECIDED"
            and subj_type == "Decision"
            and obj_type in ("Person", "Organization")):
        triple["subject"], triple["object"] = triple["object"], triple["subject"]
        triple["_auto_corrected"] = True
        correction = "DECIDED_reversed"

    # ASSIGNED_TO should be Task -> (Person|Organization)
    elif (pred == "ASSIGNED_TO"
            and subj_type in ("Person", "Organization")
            and obj_type == "Task"):
        triple["subject"], triple["object"] = triple["object"], triple["subject"]
        triple["_auto_corrected"] = True
        correction = "ASSIGNED_TO_reversed"

    # BECAUSE should be (Decision|Task) -> Fact
    elif (pred == "BECAUSE"
            and subj_type == "Fact"
            and obj_type in ("Decision", "Task")):
        triple["subject"], triple["object"] = triple["object"], triple["subject"]
        triple["_auto_corrected"] = True
        correction = "BECAUSE_reversed"

    # SUBJECT_OF should be Decision -> (Person|Organization)
    elif (pred == "SUBJECT_OF"
            and subj_type in ("Person", "Organization")
            and obj_type == "Decision"):
        triple["subject"], triple["object"] = triple["object"], triple["subject"]
        triple["_auto_corrected"] = True
        correction = "SUBJECT_OF_reversed"

    # STATED_BY should be Fact -> Person|Organization
    elif (pred == "STATED_BY"
            and subj_type in ("Person", "Organization")
            and obj_type == "Fact"):
        triple["subject"], triple["object"] = triple["object"], triple["subject"]
        triple["_auto_corrected"] = True
        correction = "STATED_BY_reversed"

    # PARTICIPATED_IN should be (Person|Organization) -> Event
    elif (pred == "PARTICIPATED_IN"
            and subj_type == "Event"
            and obj_type in ("Person", "Organization")):
        triple["subject"], triple["object"] = triple["object"], triple["subject"]
        triple["_auto_corrected"] = True
        correction = "PARTICIPATED_IN_reversed"

    return triple, correction
