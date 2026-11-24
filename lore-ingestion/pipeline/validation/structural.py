"""
pipeline/validation/structural.py
─────────────────────────────────
Cross-triple structural checks within a single thread.

Catches orphan Decision/Task nodes — knowledge bearers that nobody
authorized or owns. These are signals of a half-extracted thread.
"""

from __future__ import annotations


def check_structural_rules(validated: dict) -> list[dict]:
    """Check cross-triple structural rules within a thread.

    Rules:
    - Every Decision node must have at least one DECIDED triple
    - Every Task node must have at least one ASSIGNED_TO triple

    Returns list of triples to reject (moved from valid → rejected).
    """
    triples = validated["triples"]
    tid = validated["thread_id"]

    decided_decisions: set[str] = set()
    assigned_tasks: set[str] = set()
    for t in triples:
        if t["predicate"] == "DECIDED":
            desc = ((t.get("object") or {}).get("description") or "").lower()
            if desc:
                decided_decisions.add(desc)
        elif t["predicate"] == "ASSIGNED_TO":
            desc = ((t.get("subject") or {}).get("description") or "").lower()
            if desc:
                assigned_tasks.add(desc)

    orphans: list[dict] = []
    for t in triples:
        # Decision as subject without a DECIDED link anywhere
        if t["subject"].get("type") == "Decision":
            desc = (t["subject"].get("description") or "").lower()
            if desc and desc not in decided_decisions:
                orphans.append({
                    "thread_id": tid,
                    "triple": t,
                    "reasons": ["orphan_decision_no_DECIDED_link"],
                })

        if (t.get("object") or {}).get("type") == "Decision":
            desc = ((t.get("object") or {}).get("description") or "").lower()
            if desc and desc not in decided_decisions:
                orphans.append({
                    "thread_id": tid,
                    "triple": t,
                    "reasons": ["orphan_decision_no_DECIDED_link"],
                })

        # Task as subject without an ASSIGNED_TO link anywhere
        if t["subject"].get("type") == "Task":
            desc = (t["subject"].get("description") or "").lower()
            if desc and desc not in assigned_tasks:
                orphans.append({
                    "thread_id": tid,
                    "triple": t,
                    "reasons": ["orphan_task_no_ASSIGNED_TO_link"],
                })

    return orphans
