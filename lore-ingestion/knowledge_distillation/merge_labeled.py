"""
knowledge_distillation/merge_labeled.py
───────────────────────────────────────
Consolidate all teacher labeled.jsonl files into a single triples_raw.json
in the exact format pipeline.validator (Stage 6) expects.

Input  : knowledge_distillation/output/{teacher}/labeled.jsonl
Output : output/knowledge/{org}_triples_raw.json  (dict keyed by thread_id)

Usage (from lore-ingestion/):
    ../.venv/bin/python -m knowledge_distillation.merge_labeled
    ../.venv/bin/python -m knowledge_distillation.merge_labeled --org ezobooks
    ../.venv/bin/python -m knowledge_distillation.merge_labeled --dry-run
"""

import argparse
import json
import shutil
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

TEACHERS_DIR = Path("knowledge_distillation/output")
from common.output_paths import KnowledgePaths

# Keys copied verbatim from each labeled.jsonl record.
PASS_THROUGH = (
    "thread_id",
    "email_count",
    "triples",
    "thread_summary",
    "department_tags",
    "sensitivity",
)


def _to_raw_record(rec: dict) -> dict:
    """Convert a labeled.jsonl row to the triples_raw.json per-thread schema."""
    out = {k: rec.get(k) for k in PASS_THROUGH}
    # Flatten tokens.total → _tokens to match existing raw format
    tokens = rec.get("tokens") or {}
    if isinstance(tokens, dict) and "total" in tokens:
        out["_tokens"] = tokens["total"]
    # Keep teacher provenance (validator ignores extra keys)
    if rec.get("teacher"):
        out["_teacher"] = rec["teacher"]
    return out


def merge(org: str, dry_run: bool) -> None:
    if not TEACHERS_DIR.exists():
        raise FileNotFoundError(f"Teachers dir not found: {TEACHERS_DIR}")

    merged: dict[str, dict] = {}
    per_teacher = Counter()
    dupes: list[tuple[str, str, str]] = []     # (tid, first_teacher, dup_teacher)
    empty_triples = 0
    total_triples = 0

    teacher_dirs = sorted(p for p in TEACHERS_DIR.iterdir() if p.is_dir())

    print(f"Scanning {len(teacher_dirs)} teacher directories …")
    for tdir in teacher_dirs:
        labeled = tdir / "labeled.jsonl"
        if not labeled.exists():
            print(f"  skip {tdir.name:12s}  (no labeled.jsonl)")
            continue

        count = 0
        with labeled.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                tid = rec.get("thread_id")
                if not tid:
                    continue

                if tid in merged:
                    dupes.append((tid, merged[tid].get("_teacher", "?"), tdir.name))
                    continue

                raw = _to_raw_record(rec)
                merged[tid] = raw
                count += 1

                triples = raw.get("triples") or []
                if triples:
                    total_triples += len(triples)
                else:
                    empty_triples += 1

        per_teacher[tdir.name] = count
        print(f"  read {tdir.name:12s}  {count:>6,} threads")

    print("─" * 56)
    print(f"  Unique threads   : {len(merged):,}")
    print(f"  Empty-triple     : {empty_triples:,}")
    print(f"  Total triples    : {total_triples:,}")
    print(f"  Duplicate skips  : {len(dupes):,}")
    if dupes[:5]:
        for tid, a, b in dupes[:5]:
            print(f"    dup {tid}  kept={a}  dropped={b}")

    per_teacher_summary = dict(sorted(per_teacher.items()))
    print(f"  Per-teacher      : {per_teacher_summary}")

    if dry_run:
        print("\n[dry-run] no file written")
        return

    paths = KnowledgePaths(org=org)
    paths.makedirs()
    out_path = paths.triples_raw

    if out_path.exists():
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup = paths.backups_dir / f"{org}_triples_raw.{stamp}.bak.json"
        shutil.copy2(out_path, backup)
        print(f"  Backed up existing → backups/{backup.name}")

    out_path.write_text(
        json.dumps(merged, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    size_mb = out_path.stat().st_size / 1024**2
    print(f"\nWrote {out_path}  ({size_mb:.1f} MB, {len(merged):,} threads)")


def main() -> None:
    p = argparse.ArgumentParser(
        description="Merge KD teacher labeled.jsonl into triples_raw.json"
    )
    p.add_argument("--org", default="ezobooks")
    p.add_argument("--dry-run", action="store_true",
                   help="Print stats without writing output")
    args = p.parse_args()
    merge(args.org, args.dry_run)


if __name__ == "__main__":
    main()
