"""
builders/classify_noise.py
───────────────────────────
Noise classifier worker — build step 1c of 05_SYNC_AND_LEDGER.md.

Fills the `noise_class` + `filter_version` columns on lore.events instead of
copying the survivors to output/filtered/ (kills F8). A noise verdict is an
*opinion about* an event, so it lives as a column — never as the presence or
absence of the row. Downstream consumers read `WHERE noise_class = 'signal'`;
tuning a threshold or fixing a mis-classified sender is an UPDATE (re-run this),
not a file-to-file re-run of everything.

Reuses `pipeline.filter.is_noise` verbatim — the signal heuristics (universal
noise domains, org-discovered noise, empty-body-external) are unchanged; only
their *output sink* moves from a file copy to a column.

    lore.events  --is_noise-->  noise_class ∈ {'signal', '<reason>'} + filter_version

Usage (from lore-ingestion/):
    ../.venv/bin/python -m builders.classify_noise --org ezobooks [--source gmail]
"""

from __future__ import annotations

import argparse
from pathlib import Path

from builders.l0_ledger import LedgerStore, _resolve_dsn
from common.config import get_settings
from common.logger import get_logger, setup_logging
from common.org_config import load_org_config
from pipeline.filter import FILTER_VERSION, is_noise

log = get_logger("builders.classify_noise")

SIGNAL = "signal"


def _org_noise(org: str) -> tuple[set[str], set[str]]:
    """(discovered noise domains, allowlisted domains) from the org config."""
    config_dir = get_settings().output_root / "config"
    cfg = load_org_config(org, config_dir)
    return (set(cfg.get("discovered_noise_domains", [])),
            set(cfg.get("allowlisted_domains", [])))


def _event_dict(row) -> dict:
    """Only the fields is_noise reads — tags, primary_actor, body."""
    return {"tags": row.tags or [], "primary_actor": row.primary_actor or "",
            "body": row.body or ""}


def classify_noise(org: str, source: str = "gmail", batch: int = 1000) -> dict:
    org_noise, org_allowlist = _org_noise(org)
    store = LedgerStore(_resolve_dsn())
    if not store.connect():
        raise RuntimeError("classify_noise: could not connect to Postgres.")
    try:
        seen = 0
        for events in store.iter_events(org, source, batch=batch):
            updates = []
            for row in events:
                junk, reason = is_noise(_event_dict(row), org_noise, org_allowlist)
                updates.append({"id": row.id,
                                "noise_class": reason if junk else SIGNAL,
                                "filter_version": FILTER_VERSION})
            store.update_noise_class(updates)
            seen += len(events)
            log.info("[noise] classified %d events", seen)
        dist = store.count_events_by_noise(org, source)
        signal = dist.get(SIGNAL, 0)
        result = {"classified": seen, "signal": signal,
                  "noise": seen - signal, "filter_version": FILTER_VERSION,
                  "distribution": dist}
        log.info("[noise] DONE %s", result)
        return result
    finally:
        store.disconnect()


def main() -> None:
    ap = argparse.ArgumentParser(description="Classify events as signal/noise into a column (step 1c)")
    ap.add_argument("--org", required=True)
    ap.add_argument("--source", default="gmail")
    args = ap.parse_args()
    setup_logging(org=args.org, source="classify_noise")
    classify_noise(args.org, source=args.source)


if __name__ == "__main__":
    main()
