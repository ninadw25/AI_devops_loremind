"""
pipeline/validation/run.py
──────────────────────────
Stage-6 driver: loads raw triples + person registry, runs
`validate_thread` and `check_structural_rules` over every thread,
mints node IDs on the survivors, writes validated + rejected
artifacts, emits stats and the stage summary, and trips the
empty-output gate when nothing comes through.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from common.logger import get_logger
from common.stage_gate import check_nonempty
from common.stage_summary import log_summary

from pipeline.validation.id_minting import _assign_node_ids
from pipeline.validation.name_lookup import build_name_lookup
from pipeline.validation.structural import check_structural_rules
from pipeline.validation.thread import validate_thread

# LLM-extracted placeholder values that should never become person:manual nodes.
# These are semantic placeholders, not real people.
_PLACEHOLDER_PERSON_NAMES: set[str] = {
    "unknown", "external, unknown", "external unknown",
    "tbd", "to be determined", "n/a", "na",
    "anonymous", "someone", "user", "person",
    "not specified", "not provided", "placeholder",
    "the team", "the user", "everyone", "anybody",
    "assignee", "reporter", "owner", "author",
}


def _is_placeholder_person(name: str) -> bool:
    """Return True if the name is a known LLM placeholder, not a real person."""
    from pipeline.person_registry import _is_garbage_name
    if _is_garbage_name(name):
        return True
    return name.strip().lower() in _PLACEHOLDER_PERSON_NAMES

log = get_logger("pipeline.validator")


def _copy_thread_metadata(validated: dict, thread_result: dict) -> dict:
    for _k in ("scope_id", "scope_ids", "extra_scope_ids", "source_ref", "source_url", "url", "project", "raw"):
        if thread_result.get(_k) is not None:
            validated[_k] = thread_result.get(_k)
    return validated


def run_validation(
    raw_path: Path,
    registry_path: Path,
    output_validated: Path,
    output_rejected: Path,
    allow_empty: bool = False,
) -> dict:
    """Run the full validation pipeline.

    Returns stats dict.
    """
    start = time.time()

    log.info("=" * 55)
    log.info("TRIPLE VALIDATION — Stage 6")
    log.info("  Input:     %s", raw_path)
    log.info("  Registry:  %s", registry_path)
    log.info("=" * 55)

    # Load inputs
    raw_data = json.loads(raw_path.read_text(encoding="utf-8"))
    registry = json.loads(registry_path.read_text(encoding="utf-8"))

    # Build name lookup from registry
    name_lookup = build_name_lookup(registry)
    persons_by_id = {p["person_id"]: p
                     for p in registry.get("persons", [])}
    log.info("Name lookup: %d name variants, %d persons",
             len(name_lookup), len(persons_by_id))

    # Validate each thread
    all_validated: dict[str, dict] = {}
    all_rejected: list[dict] = []

    total_triples_in = 0
    total_triples_valid = 0
    total_triples_rejected = 0
    empty_threads = 0
    auto_corrections_total: dict[str, int] = {}

    for tid, thread_result in raw_data.items():
        raw_count = len(thread_result.get("triples", []))
        total_triples_in += raw_count

        # Skip error threads from extraction
        if "_error" in thread_result:
            all_validated[tid] = _copy_thread_metadata({
                "thread_id": tid,
                "triples": [],
                "thread_summary": thread_result.get("thread_summary", ""),
                "email_count": thread_result.get("email_count", 0),
                "scope_id": thread_result.get("scope_id"),
                "source_ref": thread_result.get("source_ref"),
                "source_url": thread_result.get("source_url"),
                "department_tags": ["general"],
                "sensitivity": "internal",
                "sensitivity_score": 0.3,
                "access_groups": ["general", "management", "c_suite"],
                "_extraction_error": thread_result["_error"],
            }, thread_result)
            empty_threads += 1
            continue

        # Per-triple validation (with fuzzy person resolution)
        validated, rejected = validate_thread(
            thread_result, name_lookup, persons_by_id)
        all_rejected.extend(rejected)

        # Aggregate auto-correction stats
        for pattern, count in validated.get(
                "_auto_corrections", {}).items():
            auto_corrections_total[pattern] = (
                auto_corrections_total.get(pattern, 0) + count)

        # Structural validation (cross-triple checks)
        orphans = check_structural_rules(validated)
        if orphans:
            # Remove orphaned triples from validated
            orphan_set = {id(o["triple"]) for o in orphans}
            validated["triples"] = [
                t for t in validated["triples"]
                if id(t) not in orphan_set
            ]
            all_rejected.extend(orphans)

        valid_count = len(validated["triples"])
        total_triples_valid += valid_count
        total_triples_rejected += raw_count - valid_count

        if valid_count == 0:
            empty_threads += 1

        all_validated[tid] = validated

    # ── Assign node IDs to validated triples ──────────────────────
    for tid, thread in all_validated.items():
        for triple in thread.get("triples", []):
            _assign_node_ids(triple, tid)

    # ── If many unresolved-person rejections occurred, auto-generate
    #     fallback registry entries and re-run validation once. This
    #     replaces the previous manual edit workflow where users added
    #     fallback persons to `output/knowledge/registry/*.json`.
    unresolved_names = set()
    for r in all_rejected:
        for reason in r.get("reasons", []):
            if reason.startswith("unresolved_person_"):
                # reason format: unresolved_person_object:Name or _subject:Name
                parts = reason.split(":", 1)
                if len(parts) == 2 and parts[1].strip():
                    unresolved_names.add(parts[1].strip())

    if unresolved_names:
        # Filter out placeholder/garbage names — these should stay rejected,
        # not become person:manual nodes that pollute the graph.
        garbage_names = {n for n in unresolved_names if _is_placeholder_person(n)}
        if garbage_names:
            log.info(
                "Skipping %d placeholder person name(s) — will not create person:manual nodes: %s",
                len(garbage_names), ", ".join(sorted(garbage_names)),
            )
        unresolved_names -= garbage_names

        log.info("Found %d unresolved person names — adding fallback registry entries",
                 len(unresolved_names))
        # Create simple person records for each unresolved name
        def _slugify(name: str) -> str:
            import re
            s = name.lower()
            s = re.sub(r"[^a-z0-9]+", "_", s).strip("_")
            return s or "unknown"

        added = 0
        existing_ids = {p.get("person_id") for p in registry.get("persons", [])}
        for name in sorted(unresolved_names):
            pid = f"person:manual:{_slugify(name)}"
            # Ensure unique id
            suffix = 1
            base_pid = pid
            while pid in existing_ids:
                suffix += 1
                pid = f"{base_pid}_{suffix}"
            rec = {
                "person_id": pid,
                "canonical_name": name,
                "primary_email": "",
                "all_emails": [],
                "all_names": [name],
                "is_internal": False,
                "is_functional": False,
                "domains": [],
                "stats": {
                    "emails_sent": 0,
                    "emails_received": 0,
                    "total_activity": 0,
                    "threads_participated": 0,
                    "unique_contacts": 0,
                },
            }
            registry.setdefault("persons", []).append(rec)
            existing_ids.add(pid)
            added += 1

        if added:
            # Save augmented registry back to disk and rebuild lookups
            registry_path.write_text(json.dumps(registry, ensure_ascii=False, indent=2), encoding="utf-8")
            log.info("Wrote %d fallback persons into registry: %s", added, registry_path)

            # Rebuild lookups and re-run validation once
            name_lookup = build_name_lookup(registry)
            persons_by_id = {p["person_id"]: p
                             for p in registry.get("persons", [])}

            # Clear previous outputs and re-run validation loop
            all_validated = {}
            all_rejected = []
            total_triples_in = 0
            total_triples_valid = 0
            total_triples_rejected = 0
            empty_threads = 0
            auto_corrections_total = {}

            for tid, thread_result in raw_data.items():
                raw_count = len(thread_result.get("triples", []))
                total_triples_in += raw_count

                if "_error" in thread_result:
                    all_validated[tid] = _copy_thread_metadata({
                        "thread_id": tid,
                        "triples": [],
                        "thread_summary": thread_result.get("thread_summary", ""),
                        "email_count": thread_result.get("email_count", 0),
                        "department_tags": ["general"],
                        "sensitivity": "internal",
                        "sensitivity_score": 0.3,
                        "access_groups": ["general", "management", "c_suite"],
                        "_extraction_error": thread_result["_error"],
                    }, thread_result)
                    empty_threads += 1
                    continue

                validated, rejected = validate_thread(
                    thread_result, name_lookup, persons_by_id)
                all_rejected.extend(rejected)

                for pattern, count in validated.get(
                        "_auto_corrections", {}).items():
                    auto_corrections_total[pattern] = (
                        auto_corrections_total.get(pattern, 0) + count)

                orphans = check_structural_rules(validated)
                if orphans:
                    orphan_set = {id(o["triple"]) for o in orphans}
                    validated["triples"] = [
                        t for t in validated["triples"] if id(t) not in orphan_set
                    ]
                    all_rejected.extend(orphans)

                valid_count = len(validated["triples"])
                total_triples_valid += valid_count
                total_triples_rejected += raw_count - valid_count
                if valid_count == 0:
                    empty_threads += 1
                all_validated[tid] = validated

            # Re-assign node IDs after re-validation
            for tid, thread in all_validated.items():
                for triple in thread.get("triples", []):
                    _assign_node_ids(triple, tid)

    # ── Save outputs ──────────────────────────────────────────────
    output_validated.parent.mkdir(parents=True, exist_ok=True)
    output_validated.write_text(
        json.dumps(all_validated, ensure_ascii=False, indent=2),
        encoding="utf-8")

    output_rejected.parent.mkdir(parents=True, exist_ok=True)
    output_rejected.write_text(
        json.dumps(all_rejected, ensure_ascii=False, indent=2),
        encoding="utf-8")

    org = output_validated.stem.replace("_triples_validated", "")
    stats_path = output_validated.parent.parent / "config" / f"{org}_validation_stats.json"

    elapsed = time.time() - start

    # ── Rejection breakdown ───────────────────────────────────────
    reason_counts: dict[str, int] = {}
    for r in all_rejected:
        for reason in r.get("reasons", []):
            key = reason.split(":")[0]
            reason_counts[key] = reason_counts.get(key, 0) + 1

    stats = {
        "threads_total": len(raw_data),
        "threads_with_triples": len(raw_data) - empty_threads,
        "triples_in": total_triples_in,
        "triples_valid": total_triples_valid,
        "triples_rejected": total_triples_rejected,
        "rejection_rate_pct": round(
            total_triples_rejected * 100 / max(total_triples_in, 1), 1),
        "rejection_reasons": dict(sorted(
            reason_counts.items(), key=lambda x: -x[1])),
        "auto_corrections_by_pattern": dict(sorted(
            auto_corrections_total.items(), key=lambda x: -x[1])),
        "auto_corrections_total": sum(auto_corrections_total.values()),
        "duration_seconds": round(elapsed, 1),
    }

    log.info("")
    log.info("=" * 55)
    log.info("VALIDATION COMPLETE")
    log.info("  Threads:          %d (%d with triples)",
             stats["threads_total"], stats["threads_with_triples"])
    log.info("  Triples in:       %d", stats["triples_in"])
    log.info("  Triples valid:    %d", stats["triples_valid"])
    log.info("  Triples rejected: %d (%.1f%%)",
             stats["triples_rejected"], stats["rejection_rate_pct"])
    if reason_counts:
        log.info("  Rejection reasons:")
        for reason, count in sorted(reason_counts.items(), key=lambda x: -x[1]):
            log.info("    %-40s %d", reason, count)
    if auto_corrections_total:
        log.info("  Auto-corrections (direction fixes):")
        for pattern, count in sorted(
                auto_corrections_total.items(), key=lambda x: -x[1]):
            log.info("    %-40s %d", pattern, count)

    # Write stats file for cross-run comparison
    stats_path.parent.mkdir(parents=True, exist_ok=True)
    stats_path.write_text(
        json.dumps(stats, ensure_ascii=False, indent=2),
        encoding="utf-8")
    log.info("  Stats:            %s", stats_path)
    log.info("  Output:           %s", output_validated)
    log.info("  Rejected:         %s", output_rejected)
    log.info("  Duration:         %.1fs", elapsed)
    log.info("=" * 55)

    log_summary(
        "validator",
        org=org,
        threads=stats["threads_total"],
        threads_with_triples=stats["threads_with_triples"],
        triples_in=stats["triples_in"],
        triples_valid=stats["triples_valid"],
        triples_rejected=stats["triples_rejected"],
        rejection_pct=stats["rejection_rate_pct"],
        auto_corrections=stats["auto_corrections_total"],
        duration_s=stats["duration_seconds"],
    )
    check_nonempty("validator", count=stats["triples_valid"],
                   what="valid triples", allow_empty=allow_empty)
    return stats
