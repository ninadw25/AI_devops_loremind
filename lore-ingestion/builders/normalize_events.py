"""
builders/normalize_events.py
────────────────────────────
Normalize worker — build step 1b of 05_SYNC_AND_LEDGER.md.

Projects the L0 payload replica (source_payloads) into normalized events
(knowledge_events / lore.events). Normalize is a PURE function of the stored
payload, so this whole step is re-runnable as an UPDATE over the replica when
the mapping improves — a normalizer bug is a re-projection, never a re-fetch.

    source_payloads (canonical raw) --event_from_payload--> lore.events
                                     (+ normalizer_version stamp)

Unlike the fetch, this touches no external API. Run it whenever the payloads
change (new sync) or the normalizer changes (new NORMALIZER_VERSION).

Usage (from lore-ingestion/):
    ../.venv/bin/python -m builders.normalize_events --org ezobooks [--source gmail]
"""

from __future__ import annotations

import argparse

from builders.l0_ledger import LedgerStore, _event_to_row, _resolve_dsn
from common.logger import get_logger, setup_logging
from infra.gmail.normalizer import NORMALIZER_VERSION, event_from_payload
from infra.zoho.l0 import (ZOHO_MAIL_NORMALIZER_VERSION,
                          event_from_payload as zoho_event_from_payload)

log = get_logger("builders.normalize_events")

# Per-source projection: canonical payload dict + a representative mailbox/account
# → event. Each new source is one entry here; the projector signature is fixed
# (payload, principal, org) so the loop below stays source-agnostic.
_PROJECTORS = {
    "gmail": (event_from_payload, NORMALIZER_VERSION),
    "zoho_mail": (zoho_event_from_payload, ZOHO_MAIL_NORMALIZER_VERSION),
}


def _mailbox_of(sightings: dict) -> str:
    """Any inbox that held the message — used only for the internal/external tag;
    scope/identity are participant-derived and mailbox-independent."""
    return next(iter(sightings), "") if sightings else ""


def normalize_events(org: str, source: str = "gmail", batch: int = 500) -> dict:
    project = _PROJECTORS.get(source)
    if not project:
        raise ValueError(f"No normalizer for source {source!r} yet.")
    projector, version = project

    store = LedgerStore(_resolve_dsn())
    if not store.connect():
        raise RuntimeError("normalize_events: could not connect to Postgres.")

    read = written = 0
    try:
        for payloads in store.iter_payloads(org, source, batch=batch):
            rows = []
            for p in payloads:
                ev = projector(p.payload, _mailbox_of(p.sightings), org)
                rows.append(_event_to_row(ev.to_dict(), org, normalizer_version=version))
            written += store.upsert_events_projection(rows)
            read += len(payloads)
            log.info("[normalize] projected %d payloads (%d events upserted so far)",
                     read, written)
        result = {"payloads": read, "events": written, "normalizer_version": version}
        log.info("[normalize] DONE %s", result)
        return result
    finally:
        store.disconnect()


def main() -> None:
    ap = argparse.ArgumentParser(description="Project L0 payloads → normalized events (step 1b)")
    ap.add_argument("--org", required=True)
    ap.add_argument("--source", default="gmail")
    args = ap.parse_args()
    setup_logging(org=args.org, source="normalize_events")
    normalize_events(args.org, source=args.source)


if __name__ == "__main__":
    main()
