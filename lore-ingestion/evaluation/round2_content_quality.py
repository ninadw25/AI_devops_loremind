"""
evaluation/round2_content_quality.py
─────────────────────────────────────
Round 2: Content quality validation.

Checks:
  1. Rubber stamp decisions (too vague / generic)
  2. Sales offers misclassified as decisions
  3. Garbage facts (HTML, chat pastes, pleasantries)
  4. Missing evidence for critical predicates only
  5. Person names (email addresses used as names)
  6. Too-short descriptions

Usage:
    python -m evaluation.round2_content_quality
    python -m evaluation.round2_content_quality --file output/knowledge/triples/ezobooks_triples_validated.json
"""

import json
import re
import sys
from pathlib import Path
from collections import Counter

from common.output_paths import KnowledgePaths


RUBBER_STAMPS = {
    # Generic acknowledgements
    "noted", "ok", "okay", "done", "approved", "rejected",
    "your request is approved", "proceed accordingly",
    "acknowledged", "will do", "thanks", "thank you",
    "sure", "agreed", "confirmed", "received",
    # Indian business communication patterns
    "please do the needful", "kindly do the needful", "do the needful",
    "as discussed", "as per discussion", "as discussed over call",
    "as per our discussion", "as discussed on call",
    "please proceed", "kindly proceed",
    "please revert", "kindly revert", "please revert back",
    "please find attached", "please find the attached",
    "noted with thanks", "noted and understood",
    "your request has been approved", "request approved",
    "action taken", "shall be done", "will be done",
}

GARBAGE_PATTERNS = [
    re.compile(r"font-size|margin|padding|color\s*:", re.I),
    re.compile(r"<div|<span|<table|<td|<br", re.I),
    re.compile(r"\[\d{1,2}/\d{1,2},?\s+\d{1,2}:\d{2}"),  # WhatsApp timestamps
    re.compile(r"dear sir|dear madam|dear team|to whom", re.I),
    re.compile(r"kind regards|warm regards|best regards|cheers", re.I),
    re.compile(r"thank you for your support|thanks for your cooperation", re.I),
    re.compile(r"^(hi|hello|hey|good morning|good evening)\s", re.I),
    re.compile(r"sent from my (iphone|android|samsung)", re.I),
    re.compile(r"unsubscribe|opt.out|manage.subscription", re.I),
]

SALES_INDICATORS = [
    re.compile(r"we are (providing|offering|committing)", re.I),
    re.compile(r"(special|limited) offer", re.I),
    re.compile(r"(buy|purchase|order) now", re.I),
    re.compile(r"free (trial|demo|consultation)", re.I),
    re.compile(r"pricing|quotation|proposal", re.I),
]

EMAIL_AS_NAME_RE = re.compile(r"^[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}$", re.I)


def evaluate(data: dict) -> dict:
    """Run content quality checks."""
    issues = []
    total_triples = 0
    triples_with_evidence = 0

    for tid, thread in data.items():
        if "_error" in thread:
            continue

        for i, tr in enumerate(thread.get("triples", [])):
            total_triples += 1
            subj = tr.get("subject", {})
            obj = tr.get("object", {})
            pred = tr.get("predicate", "")
            evidence = tr.get("evidence_sentence", "")
            s_desc = subj.get("name") or subj.get("description") or ""
            o_desc = obj.get("name") or obj.get("description") or ""

            # CHECK 1: Rubber stamp decisions
            if obj.get("type") == "Decision":
                desc_lower = o_desc.lower().strip()
                if desc_lower in RUBBER_STAMPS:
                    issues.append({
                        "check": "rubber_stamp_decision",
                        "thread_id": tid,
                        "detail": f"Vague decision: '{o_desc}'",
                    })
                elif len(o_desc.split()) < 4:
                    issues.append({
                        "check": "too_short_decision",
                        "thread_id": tid,
                        "detail": f"Decision too short ({len(o_desc.split())} words): '{o_desc}'",
                    })

            # CHECK 2: Sales offers as decisions
            if obj.get("type") == "Decision":
                for pat in SALES_INDICATORS:
                    if pat.search(o_desc):
                        issues.append({
                            "check": "sales_as_decision",
                            "thread_id": tid,
                            "detail": f"Sales language in decision: '{o_desc[:60]}'",
                        })
                        break

            # CHECK 3: Garbage facts
            if obj.get("type") == "Fact":
                for pat in GARBAGE_PATTERNS:
                    if pat.search(o_desc):
                        issues.append({
                            "check": "garbage_fact",
                            "thread_id": tid,
                            "detail": f"Garbage in fact: '{o_desc[:60]}'",
                        })
                        break
            if subj.get("type") == "Fact":
                for pat in GARBAGE_PATTERNS:
                    if pat.search(s_desc):
                        issues.append({
                            "check": "garbage_fact",
                            "thread_id": tid,
                            "detail": f"Garbage in fact subject: '{s_desc[:60]}'",
                        })
                        break

            # CHECK 4: Missing evidence (only critical predicates)
            if evidence and len(evidence.strip()) >= 10:
                triples_with_evidence += 1
            else:
                if pred in ("DECIDED", "BECAUSE", "ASSIGNED_TO"):
                    issues.append({
                        "check": "missing_evidence_critical",
                        "thread_id": tid,
                        "detail": f"No evidence for {pred}: '{o_desc[:40]}'",
                    })
                # Non-critical predicates (WORKS_AT, MENTIONS, REGARDING, etc.)
                # don't require evidence — skip them

            # CHECK 5: Email address used as person name
            for node_key, node in [("subject", subj), ("object", obj)]:
                if node.get("type") == "Person":
                    name = node.get("name", "")
                    if EMAIL_AS_NAME_RE.match(name):
                        issues.append({
                            "check": "email_as_person_name",
                            "thread_id": tid,
                            "detail": f"Email used as name: '{name}'",
                        })

            # CHECK 6: Very short descriptions
            for node_key, desc in [("subject", s_desc), ("object", o_desc)]:
                ntype = tr.get(node_key, {}).get("type", "")
                if ntype in ("Decision", "Task", "Fact") and len(desc) < 10:
                    issues.append({
                        "check": "too_short_description",
                        "thread_id": tid,
                        "detail": f"Short {ntype} ({len(desc)} chars): '{desc}'",
                    })

    issue_counts = Counter(i["check"] for i in issues)
    evidence_pct = round(
        triples_with_evidence * 100 / max(total_triples, 1), 1)

    return {
        "total_triples": total_triples,
        "triples_with_evidence": triples_with_evidence,
        "evidence_coverage_pct": evidence_pct,
        "total_issues": len(issues),
        "issue_breakdown": dict(issue_counts.most_common()),
        "issues": issues,
    }


def print_report(results: dict):
    """Print a human-readable report."""
    print("=" * 60)
    print("ROUND 2: CONTENT QUALITY VALIDATION")
    print("=" * 60)
    print(f"  Total triples:        {results['total_triples']}")
    print(f"  With evidence:        {results['triples_with_evidence']} "
          f"({results['evidence_coverage_pct']}%)")
    print()
    print(f"  TOTAL QUALITY ISSUES: {results['total_issues']}")
    if results["issue_breakdown"]:
        print("  Issue breakdown:")
        for check, count in results["issue_breakdown"].items():
            print(f"    {check:35s} {count}")
        print()
        print("  Issue details (first 20):")
        for issue in results["issues"][:20]:
            print(f"    [{issue['check']}] {issue['thread_id'][:12]}: "
                  f"{issue['detail']}")
    else:
        print("  No content quality issues found!")
    print("=" * 60)


def main():
    import argparse
    _kp = KnowledgePaths(org="ezobooks")
    parser = argparse.ArgumentParser(
        description="Round 2: Content quality validation")
    parser.add_argument("--file", default=str(_kp.triples_validated))
    args = parser.parse_args()

    path = Path(args.file)
    if not path.exists():
        print(f"ERROR: {path} not found")
        sys.exit(1)

    data = json.loads(path.read_text(encoding="utf-8"))
    results = evaluate(data)
    print_report(results)

    out_path = Path("evaluation") / "round2_results.json"
    out_path.write_text(json.dumps(results, indent=2, ensure_ascii=False),
                        encoding="utf-8")
    print(f"\nResults saved to {out_path}")


if __name__ == "__main__":
    main()
