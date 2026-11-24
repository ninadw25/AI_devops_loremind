"""
evaluation/round1_structural.py
────────────────────────────────
Round 1: Structural validation of extracted triples.

Checks:
  1. All predicates from allowed set
  2. Subject/object types correct per predicate schema
  3. Every Decision has a DECIDED triple
  4. Every Task has an ASSIGNED_TO triple
  5. JSON integrity (all required fields present)

Usage:
    python -m evaluation.round1_structural
    python -m evaluation.round1_structural --file output/knowledge/triples/ezobooks_triples_validated.json
"""

import json
import sys
from pathlib import Path
from collections import Counter

from common.output_paths import KnowledgePaths

VALID_PREDICATES = {
    "DECIDED", "BECAUSE", "STATED_BY", "ASSIGNED_TO", "REGARDING",
    "SUBJECT_OF", "WORKS_AT", "PARTICIPATED_IN", "MENTIONS",
}

VALID_NODE_TYPES = {
    "Person", "Organization", "Decision", "Task", "Fact", "Topic", "Event",
}

# predicate → (allowed_subject_types, allowed_object_types)
# Kept in sync with pipeline/validator.py PREDICATE_SCHEMA exactly.
PREDICATE_SCHEMA = {
    # Decisions made by persons or collective actors (HR, orgs, mailboxes)
    "DECIDED":         ({"Person", "Organization"},                    {"Decision"}),
    # Rationale can ground both Decisions and Tasks
    "BECAUSE":         ({"Decision", "Task"},                          {"Fact"}),
    "STATED_BY":       ({"Fact"},                                      {"Person", "Organization"}),
    "ASSIGNED_TO":     ({"Task"},                                      {"Person", "Organization"}),
    # Any knowledge-bearing node can have a topic link
    "REGARDING":       ({"Decision", "Task", "Event", "Fact"},         {"Topic"}),
    # SUBJECT_OF: the affected party of a decision (person or org)
    "SUBJECT_OF":      ({"Decision"},                                  {"Person", "Organization"}),
    "WORKS_AT":        ({"Person"},                                    {"Organization"}),
    # Organizations participate in events too
    "PARTICIPATED_IN": ({"Person", "Organization"},                    {"Event"}),
    "MENTIONS":        ({"Decision", "Fact", "Task", "Event"},         {"Person", "Organization"}),
}


def evaluate(data: dict) -> dict:
    """Run structural checks. Returns results dict."""
    issues = []
    predicate_counts = Counter()
    node_type_counts = Counter()
    threads_with_triples = 0
    total_triples = 0
    empty_threads = 0
    error_threads = 0

    for tid, thread in data.items():
        if "_error" in thread:
            error_threads += 1
            continue

        triples = thread.get("triples", [])
        total_triples += len(triples)

        if not triples:
            empty_threads += 1
            continue

        threads_with_triples += 1

        # Track Decision and Task nodes + their linking predicates
        decision_descs = set()
        decided_descs = set()
        task_descs = set()
        assigned_descs = set()

        for i, tr in enumerate(triples):
            pred = tr.get("predicate", "")
            subj = tr.get("subject", {})
            obj = tr.get("object", {})
            s_type = subj.get("type", "")
            o_type = obj.get("type", "")
            s_desc = subj.get("name") or subj.get("description") or ""
            o_desc = obj.get("name") or obj.get("description") or ""
            evidence = tr.get("evidence_sentence", "")

            predicate_counts[pred] += 1
            node_type_counts[s_type] += 1
            node_type_counts[o_type] += 1

            # CHECK 1: Valid predicate
            if pred not in VALID_PREDICATES:
                issues.append({
                    "check": "unknown_predicate",
                    "thread_id": tid,
                    "detail": f"Predicate '{pred}' not in allowed set",
                    "triple_index": i,
                })

            # CHECK 2: Valid node types
            if s_type not in VALID_NODE_TYPES:
                issues.append({
                    "check": "invalid_subject_type",
                    "thread_id": tid,
                    "detail": f"Subject type '{s_type}' not valid",
                    "triple_index": i,
                })
            if o_type not in VALID_NODE_TYPES:
                issues.append({
                    "check": "invalid_object_type",
                    "thread_id": tid,
                    "detail": f"Object type '{o_type}' not valid",
                    "triple_index": i,
                })

            # CHECK 3: Correct subject/object types per predicate
            if pred in PREDICATE_SCHEMA:
                allowed_s, allowed_o = PREDICATE_SCHEMA[pred]
                if s_type not in allowed_s:
                    issues.append({
                        "check": "wrong_subject_type",
                        "thread_id": tid,
                        "detail": f"{pred} expects subject {allowed_s}, got '{s_type}'",
                        "triple_index": i,
                    })
                if o_type not in allowed_o:
                    issues.append({
                        "check": "wrong_object_type",
                        "thread_id": tid,
                        "detail": f"{pred} expects object {allowed_o}, got '{o_type}'",
                        "triple_index": i,
                    })

            # CHECK 4: Required fields
            if not s_desc:
                issues.append({
                    "check": "empty_subject",
                    "thread_id": tid,
                    "detail": f"Triple {i}: subject has no name/description",
                    "triple_index": i,
                })
            if not o_desc:
                issues.append({
                    "check": "empty_object",
                    "thread_id": tid,
                    "detail": f"Triple {i}: object has no name/description",
                    "triple_index": i,
                })

            # Track Decision/Task for orphan check
            if o_type == "Decision":
                decision_descs.add(o_desc.lower())
            if s_type == "Decision":
                decision_descs.add(s_desc.lower())
            if pred == "DECIDED":
                decided_descs.add(o_desc.lower())

            if s_type == "Task":
                task_descs.add(s_desc.lower())
            if pred == "ASSIGNED_TO":
                assigned_descs.add(s_desc.lower())

        # CHECK 5: Orphan Decisions (no DECIDED link)
        orphan_decisions = decision_descs - decided_descs
        for od in orphan_decisions:
            issues.append({
                "check": "orphan_decision",
                "thread_id": tid,
                "detail": f"Decision with no DECIDED triple: '{od[:60]}'",
            })

        # CHECK 6: Orphan Tasks (no ASSIGNED_TO link)
        orphan_tasks = task_descs - assigned_descs
        for ot in orphan_tasks:
            issues.append({
                "check": "orphan_task",
                "thread_id": tid,
                "detail": f"Task with no ASSIGNED_TO triple: '{ot[:60]}'",
            })

    # Compile results
    issue_counts = Counter(i["check"] for i in issues)

    return {
        "threads_total": len(data),
        "threads_with_triples": threads_with_triples,
        "empty_threads": empty_threads,
        "error_threads": error_threads,
        "total_triples": total_triples,
        "avg_triples_per_thread": round(
            total_triples / max(threads_with_triples, 1), 1),
        "predicate_distribution": dict(predicate_counts.most_common()),
        "node_type_distribution": dict(node_type_counts.most_common()),
        "total_issues": len(issues),
        "issue_breakdown": dict(issue_counts.most_common()),
        "issues": issues,
    }


def print_report(results: dict):
    """Print a human-readable report."""
    print("=" * 60)
    print("ROUND 1: STRUCTURAL VALIDATION")
    print("=" * 60)
    print(f"  Threads total:        {results['threads_total']}")
    print(f"  Threads with triples: {results['threads_with_triples']}")
    print(f"  Empty threads:        {results['empty_threads']}")
    print(f"  Error threads:        {results['error_threads']}")
    print(f"  Total triples:        {results['total_triples']}")
    print(f"  Avg triples/thread:   {results['avg_triples_per_thread']}")
    print()
    print("  Predicate distribution:")
    for pred, count in results["predicate_distribution"].items():
        print(f"    {pred:25s} {count}")
    print()
    print("  Node type distribution:")
    for ntype, count in results["node_type_distribution"].items():
        print(f"    {ntype:25s} {count}")
    print()
    print(f"  TOTAL STRUCTURAL ISSUES: {results['total_issues']}")
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
        print("  No structural issues found!")
    print("=" * 60)


def main():
    import argparse
    _kp = KnowledgePaths(org="ezobooks")
    parser = argparse.ArgumentParser(
        description="Round 1: Structural validation")
    parser.add_argument("--file", default=str(_kp.triples_validated),
                        help="Path to triples JSON")
    args = parser.parse_args()

    path = Path(args.file)
    if not path.exists():
        print(f"ERROR: {path} not found")
        sys.exit(1)

    data = json.loads(path.read_text(encoding="utf-8"))
    results = evaluate(data)
    print_report(results)

    # Save results
    out_path = Path("evaluation") / "round1_results.json"
    out_path.write_text(json.dumps(results, indent=2, ensure_ascii=False),
                        encoding="utf-8")
    print(f"\nResults saved to {out_path}")


if __name__ == "__main__":
    main()
