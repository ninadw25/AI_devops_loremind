from __future__ import annotations

import json
import os
import re
from pathlib import Path


SOURCE_REF_RE = re.compile(r"^[CDG][0-9A-Z]+:[0-9]+\.[0-9]+$")
SOURCE_URL_RE = re.compile(r"^https://[a-z0-9.-]+\.slack\.com/archives/[CDG][0-9A-Z]+/p[0-9]{16,}$")


def _fail(message: str) -> None:
    print(f"FAIL: {message}")
    raise SystemExit(1)


def _latest_non_empty_raw_file(org: str) -> Path:
    candidates = sorted(Path("output/raw").glob(f"{org}_slack_*.jsonl"))
    candidates = [path for path in candidates if path.stat().st_size > 0]
    if not candidates:
        _fail("no non-empty Slack raw jsonl files found")
    return candidates[-1]


def main() -> None:
    org = os.getenv("ORG", "ezobooks")
    raw_file = _latest_non_empty_raw_file(org)

    checked = 0
    missing_fields = 0
    bad_ref = 0
    bad_url = 0

    for line in raw_file.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        source_ref = record.get("source_ref") or (record.get("raw") or {}).get("source_ref")
        source_url = record.get("source_url") or (record.get("raw") or {}).get("source_url")
        if not source_ref and not source_url:
            continue

        checked += 1
        if not source_ref or not source_url:
            missing_fields += 1
            continue
        if not SOURCE_REF_RE.match(source_ref):
            bad_ref += 1
        if not SOURCE_URL_RE.match(source_url):
            bad_url += 1

    print("=== Citation Test ===")
    print(f"Raw file used: {raw_file}")
    print(f"Events with citation fields checked: {checked}")
    print()

    if checked == 0:
        _fail("no events with source_ref/source_url found")
    if missing_fields:
        _fail(f"events missing source_ref/source_url pair: {missing_fields}")
    print("PASS: source_ref and source_url fields are present")

    if bad_ref:
        _fail(f"events with invalid source_ref format: {bad_ref}")
    print("PASS: source_ref format is valid")

    if bad_url:
        _fail(f"events with invalid source_url format: {bad_url}")
    print("PASS: source_url format is valid")

    print("\nSTATUS: PASS")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:
        _fail(str(exc))
