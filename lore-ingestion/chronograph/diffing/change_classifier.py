"""
Change classifier — enrich DiffRecords with additional classification.

Adds metrics like:
  - Lines added/removed
  - Complexity delta
  - Temporal coupling detection
  - Commit-level change classification (feature/bugfix/refactor/etc.)
"""

import logging
from chronograph.entities import DiffRecord, Symbol, ComplexityMetrics

log = logging.getLogger("chronograph.change_classifier")


def classify_commit(message: str) -> str:
    """
    Classify a commit by its message using conventional commit patterns.

    Returns one of: merge, feature, bugfix, refactor, performance, docs, test, style, chore, modification
    """
    msg = message.lower().strip()

    # Merge commits
    if msg.startswith("merge"):
        return "merge"

    # Conventional commit prefixes take priority
    if msg.startswith("feat"):
        return "feature"
    if msg.startswith("fix"):
        return "bugfix"
    if msg.startswith("refactor"):
        return "refactor"
    if msg.startswith("perf"):
        return "performance"
    if msg.startswith("docs") or msg.startswith("doc"):
        return "docs"
    if msg.startswith("test"):
        return "test"
    if msg.startswith("style"):
        return "style"
    if msg.startswith("chore") or msg.startswith("build") or msg.startswith("ci"):
        return "chore"

    # Keyword fallback
    if any(kw in msg for kw in ["fix", "bug", "patch", "hotfix", "resolve", "issue"]):
        return "bugfix"
    if any(kw in msg for kw in ["feat", "add", "new", "implement", "introduce"]):
        return "feature"
    if any(kw in msg for kw in ["refactor", "restructure", "reorganize", "extract", "split", "rename", "move"]):
        return "refactor"
    if any(kw in msg for kw in ["perf", "performance", "optimize", "speed", "cache", "faster"]):
        return "performance"
    if any(kw in msg for kw in ["doc", "readme", "comment", "docstring", "jsdoc"]):
        return "docs"
    if any(kw in msg for kw in ["test", "spec", "coverage"]):
        return "test"
    if any(kw in msg for kw in ["style", "format", "lint", "prettier", "eslint"]):
        return "style"
    if any(kw in msg for kw in ["chore", "build", "ci", "deps", "bump", "upgrade", "config"]):
        return "chore"

    return "modification"


def enrich_diff(
    diff: DiffRecord,
    old_sym: Symbol | None,
    new_sym: Symbol | None,
) -> DiffRecord:
    """Add enrichment details to a DiffRecord."""
    details = dict(diff.details)

    if old_sym and new_sym:
        # Lines delta
        old_lines = old_sym.end_line - old_sym.start_line + 1
        new_lines = new_sym.end_line - new_sym.start_line + 1
        details["lines_delta"] = new_lines - old_lines

        # Complexity delta
        if old_sym.complexity and new_sym.complexity:
            details["cyclomatic_delta"] = new_sym.complexity.cyclomatic - old_sym.complexity.cyclomatic
            details["cognitive_delta"] = new_sym.complexity.cognitive - old_sym.complexity.cognitive
            details["nesting_delta"] = new_sym.complexity.max_nesting - old_sym.complexity.max_nesting

        # Params delta
        details["params_added"] = [p for p in new_sym.params if p not in old_sym.params]
        details["params_removed"] = [p for p in old_sym.params if p not in new_sym.params]

        # New calls
        old_calls = set(old_sym.calls)
        new_calls = set(new_sym.calls)
        details["calls_added"] = list(new_calls - old_calls)
        details["calls_removed"] = list(old_calls - new_calls)

    elif new_sym:
        # Added symbol
        details["lines"] = new_sym.end_line - new_sym.start_line + 1
        if new_sym.complexity:
            details["cyclomatic"] = new_sym.complexity.cyclomatic
            details["cognitive"] = new_sym.complexity.cognitive

    elif old_sym:
        # Removed symbol
        details["lines"] = old_sym.end_line - old_sym.start_line + 1

    diff.details = details
    return diff


def compute_temporal_coupling(
    file_change_history: dict[str, list[str]],
) -> list[dict]:
    """
    Compute temporal coupling between files.

    Args:
        file_change_history: {commit_sha: [file_paths_changed]}

    Returns:
        List of {file_a, file_b, coupling_score, co_change_count}
    """
    # Count how many commits each file appears in
    file_commits: dict[str, set[str]] = {}
    for sha, files in file_change_history.items():
        for f in files:
            file_commits.setdefault(f, set()).add(sha)

    # Count co-changes
    co_changes: dict[tuple[str, str], int] = {}
    for sha, files in file_change_history.items():
        sorted_files = sorted(set(files))
        for i in range(len(sorted_files)):
            for j in range(i + 1, len(sorted_files)):
                pair = (sorted_files[i], sorted_files[j])
                co_changes[pair] = co_changes.get(pair, 0) + 1

    # Compute coupling score
    couplings = []
    for (a, b), count in co_changes.items():
        min_commits = min(len(file_commits[a]), len(file_commits[b]))
        if min_commits == 0:
            continue
        score = count / min_commits
        if score >= 0.3:  # Only report meaningful coupling
            couplings.append({
                "file_a": a,
                "file_b": b,
                "coupling_score": round(score, 3),
                "co_change_count": count,
                "commits_a": len(file_commits[a]),
                "commits_b": len(file_commits[b]),
            })

    couplings.sort(key=lambda x: -x["coupling_score"])
    return couplings
