import json
from pathlib import Path
import sys
from difflib import unified_diff


def load_and_normalize(json_path: Path):
    data = json_path.read_text(encoding="utf-8")
    obj = json.loads(data)
    # Sort lists deterministically
    obj["scopes"] = sorted(obj.get("scopes", []), key=lambda x: x.get("scope_id"))
    obj["principals"] = sorted(obj.get("principals", []), key=lambda x: x.get("principal_id"))
    obj["scope_principals"] = sorted(obj.get("scope_principals", []), key=lambda x: (x.get("scope_id"), x.get("principal_id")))
    return obj


def compare_json(p1: Path, p2: Path):
    a = load_and_normalize(p1)
    b = load_and_normalize(p2)
    if a == b:
        print("JSON artifacts: IDENTICAL")
        return True
    else:
        print("JSON artifacts: DIFFER")
        print("--- only in first / only in second ---")
        # crude diff printing
        sa = json.dumps(a, indent=2, ensure_ascii=False).splitlines()
        sb = json.dumps(b, indent=2, ensure_ascii=False).splitlines()
        for line in unified_diff(sa, sb, fromfile=str(p1), tofile=str(p2), lineterm=""):
            print(line)
        return False


def compare_text(p1: Path, p2: Path):
    t1 = p1.read_text(encoding="utf-8").splitlines()
    t2 = p2.read_text(encoding="utf-8").splitlines()
    if t1 == t2:
        print("Cypher files: IDENTICAL")
        return True
    else:
        print("Cypher files: DIFFER")
        for line in unified_diff(t1, t2, fromfile=str(p1), tofile=str(p2), lineterm=""):
            print(line)
        return False


def main():
    base = Path("output")
    run1_json = base / "run1" / "slack" / "ezobooks_scope_principals.json"
    run2_json = base / "run2" / "slack" / "ezobooks_scope_principals.json"
    run1_cy = base / "run1" / "cypher" / "ezobooks_slack_acl.cypher"
    run2_cy = base / "run2" / "cypher" / "ezobooks_slack_acl.cypher"

    ok = True
    if not run1_json.exists() or not run2_json.exists():
        print("Missing JSON artifacts for comparison:")
        print(run1_json, "exists?", run1_json.exists())
        print(run2_json, "exists?", run2_json.exists())
        sys.exit(2)

    if not compare_json(run1_json, run2_json):
        ok = False

    if not run1_cy.exists() or not run2_cy.exists():
        print("Missing Cypher artifacts for comparison:")
        print(run1_cy, "exists?", run1_cy.exists())
        print(run2_cy, "exists?", run2_cy.exists())
        sys.exit(2)

    if not compare_text(run1_cy, run2_cy):
        ok = False

    if ok:
        print("IDEMPOTENCY CHECK: PASSED — artifacts identical")
        sys.exit(0)
    else:
        print("IDEMPOTENCY CHECK: FAILED — artifacts differ")
        sys.exit(3)


if __name__ == "__main__":
    main()
