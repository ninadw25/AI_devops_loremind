"""
normalize_gitlab_export.py
──────────────────────────
The gitlab_export/*.json files were written with a UTF-8 BOM and a
double-escaped-quote bug (PowerShell ConvertTo-Json): a literal `"` inside a
text field was emitted as `\\"` (escaped-backslash + closing-quote) instead of
`\"`, which terminates the JSON string early and makes 5/8 files invalid.

This normalizer:
  1. reads each file as utf-8-sig (drops BOM),
  2. repairs the corruption: `\\"`  ->  `\"`,
  3. validates the result parses,
  4. writes clean UTF-8 (no BOM) JSON to gitlab_export/clean/.

Safety: the repair is only accepted if the file parses as valid JSON afterward
(JSON is strict, so an over-broad replace would fail the parse). Reports record
counts so the result can be sanity-checked against expected table sizes.
"""
from __future__ import annotations
import json
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "gitlab_export"
OUT = SRC / "clean"
FILES = ["repos", "commits", "members", "commit_files",
         "source_files", "symbols", "edges", "snapshots"]


def main() -> int:
    OUT.mkdir(exist_ok=True)
    print(f"{'file':14} {'status':10} {'records':>8}  repaired")
    print("-" * 48)
    all_ok = True
    for name in FILES:
        src = SRC / f"{name}.json"
        raw = src.read_text(encoding="utf-8-sig")
        repaired = raw.replace('\\\\"', '\\"')
        was_repaired = repaired != raw
        try:
            data = json.loads(repaired)
        except json.JSONDecodeError as e:
            print(f"{name:14} {'FAIL':10} {'-':>8}  ({e})")
            all_ok = False
            continue
        n = len(data) if isinstance(data, list) else 1
        (OUT / f"{name}.json").write_text(
            json.dumps(data, ensure_ascii=False), encoding="utf-8"
        )
        print(f"{name:14} {'OK':10} {n:>8}  {'yes' if was_repaired else 'no'}")
    print("-" * 48)
    print(f"clean files -> {OUT}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
