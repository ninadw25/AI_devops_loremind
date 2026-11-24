# Phase 15 — Introduce `runners/` CLI wrappers

**Status:** ✅ Completed (2026-05-19)
**Risk:** LOW (additive — historical `python -m pipeline.<stage>` invocations untouched)

## The problem

Every pipeline stage is invoked as
`python -m pipeline.<stage> ...` / `python -m builders.<x> ...`. That
works, but two things are missing:

1. **No central CLI surface.** A new contributor has to grep three
   different package roots (`pipeline/`, `builders/`, `infra/gmail/`)
   to discover the runnable entry points. `ls runners/` should
   answer the question instead.
2. **Library / CLI boundary is implicit.** Calling a stage in-process
   from `lore-backend` is possible today (`from pipeline import
   filter; filter.main()`), but the convention isn't spelled out
   anywhere — the next contributor reaches for `subprocess`.

## The fix

A new top-level `runners/` package with one 3–5 line wrapper per
stage. Each wrapper does nothing but import the canonical module's
entry function and call it under `__main__`:

```python
# runners/filter.py
"""runners.filter — Stage 2 (noise filtering). See `pipeline.filter`."""
from pipeline.filter import main

if __name__ == "__main__":
    main()
```

The `runners/__init__.py` docstring is the authoritative map from
stage name → canonical module → runner, so the package itself
documents the CLI surface.

## What landed

```
runners/
  __init__.py             rationale + canonical → runner map
  gmail_fetch.py          → infra.gmail.run:main
  filter.py               → pipeline.filter:main
  person_registry.py      → pipeline.person_registry:main
  comm_graph.py           → pipeline.comm_graph:main
  triple_extract.py       → pipeline.extraction.cli:cli_entry
  validate.py             → pipeline.validation.cli:main
  embed_knowledge.py      → pipeline.embed_knowledge:main
  deduplicate.py          → pipeline.deduplicator:main
  build.py                → builders.unified_builder:main
  post_process.py         → builders.post_processor:main
```

10 stage runners; covers every step of the documented Gmail pipeline
plus the two builders.

## What was verified

```
$ python -m runners.gmail_fetch --help       → usage banner
$ python -m runners.filter --help            → usage banner
$ python -m runners.person_registry --help   → usage banner
$ python -m runners.comm_graph --help        → usage banner
$ python -m runners.triple_extract --help    → usage banner
$ python -m runners.validate --help          → usage banner
$ python -m runners.embed_knowledge --help   → usage banner
$ python -m runners.deduplicate --help       → usage banner
$ python -m runners.build --help             → usage banner
$ python -m runners.post_process --help      → usage banner
```

All 10 runners surface the same argparse `--help` text as the
canonical entry points — confirming no flag drift was introduced.

## What was NOT done (and why)

The Step 15 design note hinted at pulling argparse out of the canonical
modules so they become "pure libraries." That's a much larger churn
(8 stage modules × ~50 LOC of argparse code each), and the immediate
goal — uniform CLI surface + a documented library boundary — is
achieved by the wrappers alone. Importing a stage module today does
**not** trigger argparse (the parser lives inside `main()`, not at
import time), so `lore-backend` can already do
`from pipeline import filter; filter.main()` without side effects.

Leaving the canonical modules' `main()` functions in place also
preserves the historical
`python -m pipeline.<stage>` invocations documented in CLAUDE.md
— no breakage for any user already wired into those paths.

If a future contributor wants to fully purify the library/CLI split
(extract argparse to `runners/<stage>.py`, leave only a `run(...)` in
the canonical module), the runner files are the obvious home for the
argparse — that work can land incrementally, stage by stage.

## Open follow-up

- `CLAUDE.md` documents `python -m pipeline.<stage>` / `python -m
  builders.<x>` / `python -m connectors.gmail.run` everywhere. All
  three remain valid. Migrating documented commands to `python -m
  runners.<stage>` is a separate, user-blessed doc PR.
- The `pipeline.extraction.cli:cli_entry` indirection (vs. all other
  stages exposing `main()`) is a Step-10 quirk — `triple_extract`
  runner calls `cli_entry()` directly. Unifying the entry function
  name (`main` everywhere) would be a tiny cosmetic follow-up.

## Rollback

```bash
rm -rf runners/
```

Pure addition — nothing else depends on `runners/`.

## Next phase

**Step 16 (optional) — chronograph → common infrastructure**

`chronograph/` (GitLab code-intelligence) duplicates several common
utilities (hashing, schemas, embedding clients) inline rather than
pulling from `common/` and `infra/`. Step 16 will audit the
duplication and consolidate where the surface is genuinely shared.
