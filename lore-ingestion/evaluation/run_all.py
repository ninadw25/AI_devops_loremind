"""
evaluation/run_all.py
─────────────────────
Combined structural + content quality scorecard.
Replaces the old round5_final_validation.py.

Pass/fail thresholds:
  Structural: < 0.5% of total triples flagged
  Quality:    < 5.0% issue rate

Usage:
    python -m evaluation.run_all
    python -m evaluation.run_all --file output/knowledge/triples/ezobooks_triples_validated.json
    python -m evaluation.run_all --org myorg
"""

import json
import sys
from pathlib import Path

from common.output_paths import KnowledgePaths
from evaluation.round1_structural import evaluate as eval_structural
from evaluation.round2_content_quality import evaluate as eval_quality


# Structural issues below this fraction of total triples → pass
STRUCTURAL_PASS_THRESHOLD = 0.005   # 0.5%
# Quality issue rate below this percentage → pass
QUALITY_PASS_THRESHOLD = 5.0        # 5%


def run_all_checks(data: dict) -> dict:
    """Run structural + content quality checks and produce a combined scorecard."""
    structural = eval_structural(data)
    quality = eval_quality(data)

    total_triples = structural["total_triples"]
    total_issues = structural["total_issues"] + quality["total_issues"]

    structural_issue_rate = structural["total_issues"] / max(total_triples, 1)
    quality_issue_rate = (quality["total_issues"] / max(total_triples, 1)) * 100

    structural_pass = structural_issue_rate < STRUCTURAL_PASS_THRESHOLD
    quality_pass = quality_issue_rate < QUALITY_PASS_THRESHOLD

    return {
        "structural": {
            "issues": structural["total_issues"],
            "issue_rate_pct": round(structural_issue_rate * 100, 2),
            "breakdown": structural["issue_breakdown"],
            "pass": structural_pass,
            "threshold_pct": STRUCTURAL_PASS_THRESHOLD * 100,
        },
        "quality": {
            "issues": quality["total_issues"],
            "issue_rate_pct": round(quality_issue_rate, 1),
            "evidence_pct": quality["evidence_coverage_pct"],
            "breakdown": quality["issue_breakdown"],
            "pass": quality_pass,
            "threshold_pct": QUALITY_PASS_THRESHOLD,
        },
        "summary": {
            "threads": structural["threads_total"],
            "threads_with_triples": structural["threads_with_triples"],
            "empty_threads": structural["empty_threads"],
            "total_triples": total_triples,
            "avg_per_thread": structural["avg_triples_per_thread"],
            "total_issues": total_issues,
            "overall_pass": structural_pass and quality_pass,
        },
        "predicate_distribution": structural["predicate_distribution"],
        "node_type_distribution": structural["node_type_distribution"],
        "sample_structural_issues": structural["issues"][:30],
        "sample_quality_issues": quality["issues"][:30],
    }


def print_report(results: dict):
    """Print final scorecard."""
    s = results["summary"]
    st = results["structural"]
    q = results["quality"]

    print("=" * 65)
    print("EVALUATION SCORECARD")
    print("=" * 65)
    print()
    print(f"  Threads:              {s['threads']:,}")
    print(f"  With triples:         {s['threads_with_triples']:,}")
    print(f"  Empty threads:        {s['empty_threads']:,}")
    print(f"  Total triples:        {s['total_triples']:,}")
    print(f"  Avg triples/thread:   {s['avg_per_thread']}")
    print()
    print("  ── STRUCTURAL ──")
    print(f"  Issues:    {st['issues']:,}  ({st['issue_rate_pct']}% of triples, "
          f"threshold < {st['threshold_pct']}%)  "
          f"{'✓ PASS' if st['pass'] else '✗ FAIL'}")
    if st["breakdown"]:
        for check, count in st["breakdown"].items():
            print(f"    {check:40s} {count:,}")
    print()
    print("  ── CONTENT QUALITY ──")
    print(f"  Issues:    {q['issues']:,}  ({q['issue_rate_pct']}% of triples, "
          f"threshold < {q['threshold_pct']}%)  "
          f"{'✓ PASS' if q['pass'] else '✗ FAIL'}")
    print(f"  Evidence coverage: {q['evidence_pct']}%")
    if q["breakdown"]:
        for check, count in q["breakdown"].items():
            print(f"    {check:40s} {count:,}")
    print()
    print("  ── PREDICATE DISTRIBUTION ──")
    for pred, count in results["predicate_distribution"].items():
        print(f"    {pred:25s} {count:,}")
    print()
    print("  ── NODE TYPE DISTRIBUTION ──")
    for ntype, count in results["node_type_distribution"].items():
        print(f"    {ntype:25s} {count:,}")
    print()

    verdict = "READY FOR PIPELINE" if s["overall_pass"] else "NEEDS ATTENTION"
    print(f"  {'─' * 45}")
    print(f"  VERDICT: {verdict}")
    print(f"  {'─' * 45}")

    if not s["overall_pass"]:
        all_issues = (results["sample_structural_issues"]
                      + results["sample_quality_issues"])
        print()
        print("  Top issues:")
        for issue in all_issues[:15]:
            print(f"    [{issue['check']}] {issue.get('detail', '')[:65]}")

    print("=" * 65)


def main():
    import argparse
    _kp = KnowledgePaths(org="ezobooks")
    parser = argparse.ArgumentParser(
        description="Evaluation scorecard: structural + content quality")
    parser.add_argument("--file", default=str(_kp.triples_validated),
                        help="Path to triples JSON")
    parser.add_argument("--org", default="ezobooks")
    args = parser.parse_args()

    path = Path(args.file)
    if not path.exists():
        print(f"ERROR: {path} not found")
        sys.exit(1)

    data = json.loads(path.read_text(encoding="utf-8"))
    results = run_all_checks(data)
    print_report(results)

    out_path = Path("evaluation") / "scorecard_results.json"
    out_path.write_text(json.dumps(results, indent=2, ensure_ascii=False),
                        encoding="utf-8")
    print(f"\nResults saved to {out_path}")


if __name__ == "__main__":
    main()
