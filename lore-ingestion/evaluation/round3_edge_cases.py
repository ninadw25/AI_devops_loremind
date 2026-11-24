"""
evaluation/round3_edge_cases.py
────────────────────────────────
Round 3: Edge case analysis.

Finds and categorizes challenging threads:
  - Single-email threads (minimal context)
  - Very long threads (30+ emails)
  - Forwarded email chains
  - Mixed-language threads (Hindi/Marathi + English)

Also checks extraction behavior on these edge cases.

Usage:
    python -m evaluation.round3_edge_cases
    python -m evaluation.round3_edge_cases --filtered output/filtered/ezobooks_gmail_filtered.jsonl
"""

import json
import re
import sys
from pathlib import Path
from collections import defaultdict, Counter

from common.output_paths import KnowledgePaths

# Keywords that legitimately justify PARTICIPATED_IN on a single email
# (e.g., a meeting invite or calendar notification)
MEETING_INDICATORS_RE = re.compile(
    r"meeting|call|standup|stand.up|sync|zoom|skype|teams|google meet|"
    r"discussion|interview|debrief|webinar|conference|video|agenda",
    re.I,
)


def find_edge_case_threads(filtered_path: Path) -> dict:
    """Scan filtered JSONL and identify edge case threads."""
    threads: dict[str, list[dict]] = defaultdict(list)

    with open(filtered_path, "r", encoding="utf-8") as f:
        for line in f:
            event = json.loads(line)
            tid = (event.get("raw", {}).get("thread_id")
                   or event.get("parent_id")
                   or event.get("source_id", event.get("id", "")))
            threads[tid].append(event)

    # Sort within threads by timestamp
    for tid in threads:
        threads[tid].sort(key=lambda e: e.get("timestamp", ""))

    # Categorize
    single_email = []
    long_threads = []
    forwarded = []
    mixed_language = []

    hindi_re = re.compile(r"[\u0900-\u097F]")  # Devanagari
    fwd_re = re.compile(r"^(fwd?:|forwarded)", re.I)

    for tid, emails in threads.items():
        subject = emails[0].get("title", "") if emails else ""
        body_sample = (emails[0].get("body", "") or "")[:500] if emails else ""

        if len(emails) == 1:
            single_email.append({
                "thread_id": tid,
                "subject": subject,
                "body_preview": body_sample[:400],
            })

        if len(emails) >= 15:
            long_threads.append({
                "thread_id": tid,
                "email_count": len(emails),
                "subject": subject,
            })

        if fwd_re.search(subject):
            forwarded.append({
                "thread_id": tid,
                "email_count": len(emails),
                "subject": subject,
            })

        # Check for Hindi/Marathi in any email body
        for e in emails[:3]:
            body = e.get("body", "") or ""
            if hindi_re.search(body[:500]):
                mixed_language.append({
                    "thread_id": tid,
                    "email_count": len(emails),
                    "subject": subject,
                    "language_sample": body[:100],
                })
                break

    return {
        "total_threads": len(threads),
        "single_email": single_email,
        "long_threads": sorted(long_threads,
                               key=lambda x: -x["email_count"]),
        "forwarded": forwarded,
        "mixed_language": mixed_language,
    }


def evaluate_edge_cases(edge_cases: dict, triples_data: dict) -> dict:
    """Check extraction quality on edge case threads."""
    issues = []

    # Single-email: check for hallucinated PARTICIPATED_IN
    # Only flag if subject/body contain no meeting-context keywords
    single_by_tid = {t["thread_id"]: t for t in edge_cases["single_email"]}
    for tid, t_info in single_by_tid.items():
        if tid not in triples_data:
            continue
        thread = triples_data[tid]
        search_text = t_info.get("subject", "") + " " + t_info.get("body_preview", "")
        has_meeting_context = bool(MEETING_INDICATORS_RE.search(search_text))
        for tr in thread.get("triples", []):
            if tr["predicate"] == "PARTICIPATED_IN" and not has_meeting_context:
                issues.append({
                    "check": "single_email_event_hallucination",
                    "thread_id": tid,
                    "detail": "PARTICIPATED_IN on single email with no meeting context — possible hallucination",
                })

    # Long threads: check if middle decisions are captured
    long_tids = {t["thread_id"] for t in edge_cases["long_threads"]}
    for tid in long_tids:
        if tid in triples_data:
            thread = triples_data[tid]
            triple_count = len(thread.get("triples", []))
            email_count = thread.get("email_count", 0)
            if email_count > 15 and triple_count == 0:
                issues.append({
                    "check": "long_thread_empty",
                    "thread_id": tid,
                    "detail": f"{email_count} emails but 0 triples extracted",
                })

    # Forwarded: check attribution
    fwd_tids = {t["thread_id"] for t in edge_cases["forwarded"]}
    for tid in fwd_tids:
        if tid in triples_data:
            thread = triples_data[tid]
            for tr in thread.get("triples", []):
                if tr["predicate"] == "DECIDED":
                    decider = tr["subject"].get("name", "")
                    if not decider:
                        issues.append({
                            "check": "forwarded_no_attribution",
                            "thread_id": tid,
                            "detail": "Decision without clear attribution in forwarded thread",
                        })

    issue_counts = Counter(i["check"] for i in issues)

    return {
        "edge_case_counts": {
            "single_email": len(edge_cases["single_email"]),
            "long_threads_15plus": len(edge_cases["long_threads"]),
            "forwarded": len(edge_cases["forwarded"]),
            "mixed_language": len(edge_cases["mixed_language"]),
        },
        "total_issues": len(issues),
        "issue_breakdown": dict(issue_counts.most_common()),
        "issues": issues,
    }


def print_report(edge_cases: dict, eval_results: dict):
    """Print human-readable report."""
    print("=" * 60)
    print("ROUND 3: EDGE CASE ANALYSIS")
    print("=" * 60)
    print(f"  Total threads: {edge_cases['total_threads']}")
    print()
    print("  Edge case distribution:")
    for category, count in eval_results["edge_case_counts"].items():
        print(f"    {category:30s} {count}")
    print()

    print("  Top 5 longest threads:")
    for t in edge_cases["long_threads"][:5]:
        print(f"    {t['thread_id'][:12]}: {t['email_count']} emails — "
              f"{t['subject'][:50]}")
    print()

    print("  Sample forwarded threads:")
    for t in edge_cases["forwarded"][:5]:
        print(f"    {t['thread_id'][:12]}: {t['subject'][:60]}")
    print()

    print("  Sample mixed-language threads:")
    for t in edge_cases["mixed_language"][:5]:
        print(f"    {t['thread_id'][:12]}: {t['subject'][:40]} — "
              f"'{t['language_sample'][:40]}...'")
    print()

    if eval_results["total_issues"]:
        print(f"  EDGE CASE ISSUES: {eval_results['total_issues']}")
        for check, count in eval_results["issue_breakdown"].items():
            print(f"    {check:40s} {count}")
        for issue in eval_results["issues"][:10]:
            print(f"    [{issue['check']}] {issue['thread_id'][:12]}: "
                  f"{issue['detail']}")
    else:
        print("  No edge case issues found!")
    print("=" * 60)


def main():
    import argparse
    _kp = KnowledgePaths(org="ezobooks")
    parser = argparse.ArgumentParser(
        description="Round 3: Edge case analysis")
    parser.add_argument("--filtered",
                        default="output/filtered/ezobooks_gmail_filtered.jsonl")
    parser.add_argument("--triples", default=str(_kp.triples_validated))
    args = parser.parse_args()

    filtered_path = Path(args.filtered)
    triples_path = Path(args.triples)

    if not filtered_path.exists():
        print(f"ERROR: {filtered_path} not found")
        sys.exit(1)

    print("Scanning filtered emails for edge cases...")
    edge_cases = find_edge_case_threads(filtered_path)

    triples_data = {}
    if triples_path.exists():
        triples_data = json.loads(triples_path.read_text(encoding="utf-8"))

    eval_results = evaluate_edge_cases(edge_cases, triples_data)
    print_report(edge_cases, eval_results)

    out_path = Path("evaluation") / "round3_results.json"
    out_path.write_text(
        json.dumps({"edge_cases": {
            k: v[:20] if isinstance(v, list) else v
            for k, v in edge_cases.items()
        }, "evaluation": eval_results}, indent=2, ensure_ascii=False),
        encoding="utf-8")
    print(f"\nResults saved to {out_path}")


if __name__ == "__main__":
    main()
