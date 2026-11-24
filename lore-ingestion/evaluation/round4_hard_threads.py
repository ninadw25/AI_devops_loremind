"""
evaluation/round4_hard_threads.py
──────────────────────────────────
Round 4: Find and test hard/important threads.

Finds threads that contain:
  - PIP / Performance Improvement content
  - Salary / CTC / Revision content
  - Vendor selection / procurement decisions
  - Compliance / regulatory content
  - Technical architecture decisions

These are the threads that matter most for query quality.

Usage:
    python -m evaluation.round4_hard_threads --find
    python -m evaluation.round4_hard_threads --evaluate
"""

import json
import re
import sys
from pathlib import Path
from collections import defaultdict, Counter


TOPIC_PATTERNS = {
    "pip": [
        re.compile(r"\bpip\b", re.I),
        re.compile(r"performance improvement", re.I),
        re.compile(r"observation period", re.I),
        re.compile(r"warning letter", re.I),
    ],
    "salary": [
        re.compile(r"\bsalary\b", re.I),
        re.compile(r"\bctc\b", re.I),
        re.compile(r"revision|increment|appraisal", re.I),
        re.compile(r"compensation", re.I),
    ],
    "vendor": [
        re.compile(r"vendor\b", re.I),
        re.compile(r"procurement|rfp|rfq", re.I),
        re.compile(r"(selected|choosing|replaced|switching).*(vendor|provider|platform)", re.I),
    ],
    "compliance": [
        re.compile(r"\bgst\b", re.I),
        re.compile(r"\btds\b", re.I),
        re.compile(r"compliance|regulatory|statutory", re.I),
        re.compile(r"audit\b", re.I),
    ],
    "technical": [
        re.compile(r"(api|integration|webhook|deployment)", re.I),
        re.compile(r"(architecture|migration|database|server)", re.I),
        re.compile(r"(razorpay|servetel|clickup|dialer)", re.I),
    ],
}


def find_hard_threads(filtered_path: Path) -> dict[str, list[dict]]:
    """Find threads matching important topic patterns."""
    threads: dict[str, list[dict]] = defaultdict(list)

    with open(filtered_path, "r", encoding="utf-8") as f:
        for line in f:
            event = json.loads(line)
            tid = (event.get("raw", {}).get("thread_id")
                   or event.get("parent_id")
                   or event.get("source_id", event.get("id", "")))
            threads[tid].append(event)

    topic_threads: dict[str, list[dict]] = defaultdict(list)

    for tid, emails in threads.items():
        subject = emails[0].get("title", "") if emails else ""
        body_combined = " ".join(
            (e.get("body", "") or "")[:500] for e in emails[:3])
        text = f"{subject} {body_combined}"

        for topic, patterns in TOPIC_PATTERNS.items():
            for pat in patterns:
                if pat.search(text):
                    topic_threads[topic].append({
                        "thread_id": tid,
                        "email_count": len(emails),
                        "subject": subject,
                        "matched_pattern": pat.pattern,
                    })
                    break

    return dict(topic_threads)


def evaluate_hard_threads(topic_threads: dict,
                          triples_data: dict) -> dict:
    """Check extraction quality on hard threads."""
    issues = []
    stats = {}

    for topic, thread_list in topic_threads.items():
        extracted = 0
        total_triples = 0
        decisions = 0
        facts = 0

        sample_size = min(50, len(thread_list))
        for t in thread_list[:sample_size]:
            tid = t["thread_id"]
            if tid not in triples_data:
                continue
            thread = triples_data[tid]
            triples = thread.get("triples", [])
            extracted += 1
            total_triples += len(triples)

            for tr in triples:
                if tr.get("object", {}).get("type") == "Decision":
                    decisions += 1
                elif tr.get("object", {}).get("type") == "Fact":
                    facts += 1

            if not triples:
                issues.append({
                    "check": f"hard_{topic}_empty",
                    "thread_id": tid,
                    "detail": f"{topic} thread with 0 triples: '{t['subject'][:50]}'",
                })

        stats[topic] = {
            "found_threads": len(thread_list),
            "extracted": extracted,
            "total_triples": total_triples,
            "decisions": decisions,
            "facts": facts,
        }

    return {
        "topic_stats": stats,
        "total_issues": len(issues),
        "issues": issues,
    }


def print_report(topic_threads: dict, eval_results: dict):
    """Print report."""
    print("=" * 60)
    print("ROUND 4: HARD THREAD ANALYSIS")
    print("=" * 60)

    for topic, thread_list in topic_threads.items():
        count = len(thread_list)
        stats = eval_results["topic_stats"].get(topic, {})
        print(f"\n  [{topic.upper()}] {count} threads found, "
              f"{stats.get('extracted', 0)} extracted")
        print(f"    Triples: {stats.get('total_triples', 0)}, "
              f"Decisions: {stats.get('decisions', 0)}, "
              f"Facts: {stats.get('facts', 0)}")
        print(f"    Sample threads:")
        for t in thread_list[:5]:
            print(f"      {t['thread_id'][:12]} ({t['email_count']} emails): "
                  f"{t['subject'][:50]}")

    if eval_results["issues"]:
        print(f"\n  HARD THREAD ISSUES: {eval_results['total_issues']}")
        for issue in eval_results["issues"][:10]:
            print(f"    [{issue['check']}] {issue['thread_id'][:12]}: "
                  f"{issue['detail']}")

    print("=" * 60)


def main():
    import argparse
    from common.output_paths import KnowledgePaths
    _kp = KnowledgePaths(org="ezobooks")
    parser = argparse.ArgumentParser(
        description="Round 4: Hard thread analysis")
    parser.add_argument("--filtered",
                        default="output/filtered/ezobooks_gmail_filtered.jsonl")
    parser.add_argument("--triples", default=str(_kp.triples_validated))
    parser.add_argument("--find", action="store_true",
                        help="Just find threads, don't evaluate extraction")
    args = parser.parse_args()

    filtered_path = Path(args.filtered)
    if not filtered_path.exists():
        print(f"ERROR: {filtered_path} not found")
        sys.exit(1)

    print("Finding hard threads...")
    topic_threads = find_hard_threads(filtered_path)

    triples_data = {}
    eval_results = {"topic_stats": {}, "total_issues": 0, "issues": []}

    if not args.find:
        triples_path = Path(args.triples)
        if triples_path.exists():
            triples_data = json.loads(
                triples_path.read_text(encoding="utf-8"))
            eval_results = evaluate_hard_threads(topic_threads, triples_data)

    print_report(topic_threads, eval_results)

    out_path = Path("evaluation") / "round4_results.json"
    out_path.write_text(json.dumps({
        "topics": {k: v[:20] for k, v in topic_threads.items()},
        "evaluation": eval_results,
    }, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nResults saved to {out_path}")


if __name__ == "__main__":
    main()
