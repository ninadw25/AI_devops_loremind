from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def _fail(message: str) -> None:
    print(f"FAIL: {message}")
    raise SystemExit(1)


def main() -> None:
    org = os.getenv("ORG", "ezobooks")
    artifact = Path("output/slack") / f"{org}_scope_principals.json"
    if not artifact.exists():
        _fail(f"missing discovery artifact at {artifact}")

    payload = json.loads(artifact.read_text(encoding="utf-8"))
    scopes = payload.get("scopes", [])
    principals = payload.get("principals", [])
    scope_principals = payload.get("scope_principals", [])

    print("=== Discovery Test ===")
    print(f"Scopes found: {len(scopes)}")
    print(f"Principals found: {len(principals)}")
    print(f"Scope-principal mappings: {len(scope_principals)}")
    print()

    expected_keys = {"scopes", "principals", "scope_principals"}
    if set(payload.keys()) != expected_keys:
        _fail("scope_principals.json missing required top-level keys")
    print("PASS: scope_principals.json structure valid")

    public_channel_ids = {
        row.get("scope_id")
        for row in scopes
        if row.get("kind") == "channel" and bool(row.get("is_public"))
    }
    rows_for_public = [r for r in scope_principals if r.get("scope_id") in public_channel_ids]
    if rows_for_public:
        _fail("public channels should not have member rows")
    print("PASS: public channels have no member rows")

    private_channel_ids = {
        row.get("scope_id")
        for row in scopes
        if row.get("kind") == "channel" and not bool(row.get("is_public"))
    }
    if not private_channel_ids:
        _fail("no private channels found in discovery artifact")

    private_with_rows = {r.get("scope_id") for r in scope_principals if r.get("scope_id") in private_channel_ids}
    if private_with_rows != private_channel_ids:
        missing = sorted(private_channel_ids - private_with_rows)
        _fail(f"private channels missing member rows: {missing}")
    print("PASS: private channels contain member rows")

    print("\nSTATUS: PASS")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:
        _fail(str(exc))
