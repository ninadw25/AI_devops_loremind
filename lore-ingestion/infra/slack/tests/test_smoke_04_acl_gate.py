from __future__ import annotations

import json
import os
from pathlib import Path


def _allowed_scopes(scopes: list[dict], scope_principals: list[dict], principal_id: str, tenant_id: str) -> set[str]:
    member_scopes = {
        row["scope_id"]
        for row in scope_principals
        if row.get("principal_id") == principal_id
    }
    public_scopes = {
        row["scope_id"]
        for row in scopes
        if row.get("tenant_id") == tenant_id and bool(row.get("is_public"))
    }
    return member_scopes | public_scopes


def _query_time_visible(node: dict, allowed: set[str]) -> bool:
    scope_id = node.get("scope_id")
    extra_scopes = node.get("extra_scope_ids") or []
    return scope_id in allowed and all(s in allowed for s in extra_scopes)


def _fail(message: str) -> None:
    print(f"FAIL: {message}")
    raise SystemExit(1)


def main() -> None:
    org = os.getenv("ORG", "ezobooks")
    scope_file = Path("output/slack") / f"{org}_scope_principals.json"
    if not scope_file.exists():
        _fail(f"missing discovery artifact at {scope_file}")

    payload = json.loads(scope_file.read_text(encoding="utf-8"))
    scopes = payload.get("scopes", [])
    scope_principals = payload.get("scope_principals", [])
    principals = [p.get("principal_id") for p in payload.get("principals", []) if p.get("principal_id")]

    private_scopes = [s for s in scopes if s.get("kind") == "channel" and not bool(s.get("is_public"))]
    public_scopes = [s for s in scopes if s.get("kind") == "channel" and bool(s.get("is_public"))]

    if not private_scopes:
        _fail("no private channel scopes found")
    if not public_scopes:
        _fail("no public channel scopes found")

    target_private = private_scopes[0]["scope_id"]
    private_members = {r["principal_id"] for r in scope_principals if r.get("scope_id") == target_private}
    outsider_principal = next((pid for pid in principals if pid not in private_members), None)
    if not outsider_principal:
        _fail("could not find a principal outside private channel membership")

    allowed = _allowed_scopes(scopes, scope_principals, outsider_principal, org)

    public_node = {"id": "node_public", "scope_id": public_scopes[0]["scope_id"], "extra_scope_ids": []}
    private_node = {"id": "node_private", "scope_id": target_private, "extra_scope_ids": []}

    print("=== ACL Gate Test ===")
    print(f"Principal tested: {outsider_principal}")
    print(f"Private scope tested: {target_private}")
    print(f"Allowed scopes computed: {len(allowed)}")
    print()

    if not _query_time_visible(public_node, allowed):
        _fail("public scope should be visible to tested principal")
    print("PASS: public channel scope remains visible")

    if _query_time_visible(private_node, allowed):
        _fail("private scope should be filtered out for non-member principal")
    print("PASS: private channel scope is filtered for non-members")

    print("\nSTATUS: PASS")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:
        _fail(str(exc))
