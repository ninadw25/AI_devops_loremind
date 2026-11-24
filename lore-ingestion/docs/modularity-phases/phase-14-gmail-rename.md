# Phase 14 — Rename `connectors/` → `infra/gmail/`

**Status:** ✅ Completed (2026-05-19)
**Risk:** LOW (pure relocation + shim; public CLI surface preserved)

## The problem

After Step 12, `infra/llm/` and `infra/embeddings/` exist as the home
for "external-service adapters." Gmail fetching is the canonical
external-service adapter for the ingestion pipeline yet lived in a
sibling top-level `connectors/` directory. Two names for the same
concept invites drift — future contributors will guess wrong about
where new source adapters belong (Slack, WhatsApp, GitLab, …).

## The fix

Move the three Gmail files under the unified `infra/` umbrella:

```
infra/gmail/
  __init__.py        re-exports the public surface
  fetcher.py         (was connectors/gmail/fetcher.py, 589 LOC, unchanged)
  normalizer.py      (was connectors/gmail/normalizer.py, 243 LOC, unchanged)
  run.py             (was connectors/gmail/run.py, 410 LOC)
                       └─ only change: imports rewired from
                          `connectors.gmail.X` → `infra.gmail.X`
```

`infra/gmail/__init__.py` exports `GmailFetcher`,
`is_functional_mailbox`, `normalize_email_message`,
`normalize_email_thread`, `get_message_id_header`.

## Backward compatibility

Per locked decision #4 (preserve historical import paths via shims),
`connectors/gmail/` is now a re-export shim package:

```
connectors/
  __init__.py        marker docstring only
  gmail/
    __init__.py      re-exports infra.gmail's public surface
    fetcher.py       `from infra.gmail.fetcher import *`
    normalizer.py    `from infra.gmail.normalizer import *`
    run.py           `from infra.gmail.run import *` + `if __name__ ...: main()`
```

The shim `run.py` reproduces the `if __name__ == "__main__": main()`
block so the documented CLI invocation
`python -m connectors.gmail.run --org … --domain …` continues to work
unchanged — verified below.

## What was verified

```
$ python -c "from infra.gmail import GmailFetcher, normalize_email_message"  → OK
$ python -c "
    from infra.gmail.fetcher import GmailFetcher, is_functional_mailbox
    from connectors.gmail.fetcher import GmailFetcher as Gs, is_functional_mailbox as ifm
    assert Gs is GmailFetcher and ifm is is_functional_mailbox
  "                                                                            → OK
$ python -c "
    from infra.gmail.run import main
    from connectors.gmail.run import main as m_shim
    assert m_shim is main
  "                                                                            → OK
$ python -m connectors.gmail.run --help                                        → usage banner
$ python -m infra.gmail.run --help                                             → identical banner
```

Shim symbols are identity-equal to canonical (`is` checks pass), so any
caller comparing types or holding references across import paths sees
the same objects.

## Surface map (what callers see)

| Old path                                  | New path                          | Status                  |
|-------------------------------------------|-----------------------------------|-------------------------|
| `connectors.gmail.fetcher.GmailFetcher`   | `infra.gmail.fetcher.GmailFetcher`| Both work (shim aliases)|
| `connectors.gmail.fetcher.is_functional_mailbox` | same in `infra.gmail`      | Both work               |
| `connectors.gmail.normalizer.*`           | `infra.gmail.normalizer.*`        | Both work               |
| `python -m connectors.gmail.run`          | `python -m infra.gmail.run`       | Both work (preferred: infra) |

## Open follow-up

- `CLAUDE.md` (both repo-root and `lore-ingestion/CLAUDE.md`) still
  documents the Stage 1 command as `python -m connectors.gmail.run`.
  That command continues to work via the shim. Updating the docs to
  the canonical `python -m infra.gmail.run` is a follow-up — needs
  user confirmation that the import-path migration is "blessed"
  before flipping documented commands.

## Rollback

```bash
git mv infra/gmail/fetcher.py connectors/gmail/fetcher.py
git mv infra/gmail/normalizer.py connectors/gmail/normalizer.py
git mv infra/gmail/run.py connectors/gmail/run.py
# then revert the import rewrite in run.py and delete infra/gmail/__init__.py
```

## Next phase

**Step 15 — Introduce `runners/` CLI wrappers**

Each pipeline stage currently mixes CLI parsing, orchestration, and
the underlying logic in one module. A `runners/` directory of thin
argparse-only entry points (one per stage) lets the canonical
modules become pure libraries and gives `lore-backend` a way to call
them in-process without `subprocess`.
