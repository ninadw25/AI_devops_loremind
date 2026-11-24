"""Structural guard: connectors (Layer 1, infra/) must not import the
processing layer (pipeline/, builders/) at module load. Dependencies flow
one way: runners -> pipeline/builders -> infra -> common. A module-level
upward import (the LOR-40 Jira smoke regression) silently breaks that and
makes connectors un-packageable in isolation."""

import ast
import pathlib

INFRA = pathlib.Path(__file__).resolve().parents[2] / "infra"
FORBIDDEN_ROOTS = {"pipeline", "builders"}


def _module_level_imports(path: pathlib.Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    out: list[str] = []
    for node in tree.body:  # module body only — deferred imports inside functions are allowed
        if isinstance(node, ast.Import):
            out.extend(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            out.append(node.module)
    return out


def test_infra_has_no_module_level_upward_imports():
    violations: list[str] = []
    for py in INFRA.rglob("*.py"):
        if "tests" in py.parts:
            continue
        for mod in _module_level_imports(py):
            if mod.split(".")[0] in FORBIDDEN_ROOTS:
                violations.append(f"{py.relative_to(INFRA.parent)} imports {mod}")
    assert not violations, "infra/ must not import pipeline/ or builders/ at module level:\n" + "\n".join(violations)
