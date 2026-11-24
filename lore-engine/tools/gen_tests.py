"""AI test generation loop (the Qodo-Cover algorithm, which is archived upstream):
   generate tests with an LLM → run them → keep a file only if it passes AND raises coverage.

    python tools/gen_tests.py domain/context.py domain/sources.py domain/retrieval/person.py
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

from benchmarks.rag_eval.common import LLM_MODEL, nvidia

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable
PROMPT = """You write pytest unit tests. Target module `{mod}` (import path `{imp}`).
Currently UNCOVERED lines: {missing}.
Rules: pure unit tests, no network/DB/LLM calls (use monkeypatch or small fakes); pytest-asyncio is in auto mode so
`async def test_x()` works without decorators; assert real behaviour, not just that code runs; only import what exists below.
Return ONLY one python file, no prose.

SOURCE (numbered):
{src}
{feedback}"""


def run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([PY, "-m", *args], cwd=ROOT, capture_output=True, text=True)


def coverage_of(mod: str, tests: list[str]) -> tuple[float, list[int]]:
    run("pytest", *tests, "-q", "-p", "no:cacheprovider", f"--cov={mod.replace('/', '.')[:-3]}", "--cov-report=json:/tmp/_cov.json")
    d = json.load(open("/tmp/_cov.json"))["files"]
    f = next((v for k, v in d.items() if k.endswith(mod)), None)
    return (f["summary"]["percent_covered"], f["missing_lines"]) if f else (0.0, [])


def generate(mod: str) -> dict:
    existing = [str(p.relative_to(ROOT)) for p in (ROOT / "tests").rglob("test_*.py") if "generated" not in p.parts]
    before, missing = coverage_of(mod, existing)
    src = "\n".join(f"{i+1:4} {l}" for i, l in enumerate((ROOT / mod).read_text().splitlines()))
    out = ROOT / "tests" / "generated" / f"test_{Path(mod).stem}_gen.py"
    feedback, client, kept = "", nvidia(), False
    for attempt in range(1, 4):
        text = client.chat.completions.create(
            model=LLM_MODEL, temperature=0.2, max_tokens=8000,
            messages=[{"role": "user", "content": PROMPT.format(
                mod=mod, imp=mod[:-3].replace("/", "."), missing=missing, src=src[:24000], feedback=feedback)}],
        ).choices[0].message.content or ""
        text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
        m = re.search(r"```(?:python)?\n(.*?)```", text, re.S)
        out.write_text((m.group(1) if m else text).strip() + "\n")
        r = run("pytest", str(out), "-q", "-p", "no:cacheprovider", "--tb=short", "-x")
        if r.returncode == 0:
            after, _ = coverage_of(mod, existing + [str(out)])
            kept = after > before
            break
        feedback = f"\nYour previous attempt failed with:\n{(r.stdout + r.stderr)[-2500:]}\nFix it."
    if not kept:
        out.unlink(missing_ok=True); after = before
    return {"module": mod, "before": round(before, 1), "after": round(after, 1), "kept": kept, "attempts": attempt}


if __name__ == "__main__":
    rows = [generate(m) for m in sys.argv[1:]]
    Path(ROOT / "benchmarks/rag_eval/results/testgen.json").write_text(json.dumps(rows, indent=1))
    for r in rows:
        print(r)
