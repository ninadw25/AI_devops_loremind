"""
runners/l0_sync.py
──────────────────
The source-agnostic L0 sync core — the one implementation of "replicate each
principal's messages once into `source_payloads`, then advance its cursor."

Every source (Gmail, Zoho, and the next one) reduces to two source-specific
pieces plus this shared loop:

  1. a **fetch closure** `fetch(principal, since_ms, emit)` that pulls the
     principal's messages and calls `emit(batch)` per batch — this is where the
     source's own API/auth/paging lives (infra/<src>/fetcher.py);
  2. an **L0 module** exposing the fixed contract `canonical_id`,
     `canonical_payload`, `sighting`, `internal_date_ms`, `L0_PAYLOAD_KIND`
     (infra/<src>/l0.py or normalizer.py).

This module owns everything that is identical across sources: per-principal
cursor resume, per-batch durable writes, cursor-advance-last-on-clean, the
parallel fan-out, per-principal failure isolation, and run totals. There is no
per-source branching here — a new source adds files, never edits this loop.

`docs/revised_ingestion_pipeline/05_SYNC_AND_LEDGER.md` §3 defines the frame;
this is its single implementation.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Callable, Protocol

from builders.l0_ledger import LedgerStore
from common.hashing import make_id
from common.logger import get_logger

log = get_logger("runners.l0_sync")


class L0Module(Protocol):
    """The fixed contract each source's L0 layer provides (structural typing —
    infra/gmail/normalizer and infra/zoho/l0 already satisfy it)."""
    L0_PAYLOAD_KIND: str
    def canonical_id(self, msg: dict) -> str: ...
    def canonical_payload(self, msg: dict) -> dict: ...
    def sighting(self, msg: dict, principal: str) -> dict: ...
    def internal_date_ms(self, msg: dict) -> int: ...


# A source's fetch closure: pull `principal`'s messages newer than `since_ms`
# (None = full backfill) and call `emit(batch)` for each batch as it lands.
FetchPrincipal = Callable[[str, "int | None", Callable[[list[dict]], None]], None]


def _payload_row(l0: L0Module, msg: dict, org: str, source: str,
                 principal: str, run_id: str) -> dict:
    cid = l0.canonical_id(msg)
    return {
        "payload_id": make_id(l0.L0_PAYLOAD_KIND, f"{org}:{cid}"),
        "org": org, "source": source, "canonical_id": cid,
        "payload": l0.canonical_payload(msg),
        "sightings": {principal: l0.sighting(msg, principal)},
        "fetch_run_id": run_id,
    }


def run_l0_sync(
    org: str,
    source: str,
    principals: list[dict],
    fetch_principal: FetchPrincipal,
    l0: L0Module,
    *,
    store: LedgerStore | None,
    label: Callable[[dict], str],
    dry_run: bool = False,
    parallel: int = 8,
) -> dict:
    """Replicate every principal into L0, in parallel, durably, resumably.

    `store` is None only for a dry run. `label(principal)` → the principal
    string used as the mailbox/account key (also the sightings key). Writes are
    per batch; the cursor advances only after a principal is fully replicated,
    so an interruption re-lists that principal and re-writes at most one batch
    (idempotent upsert). One bad principal is isolated — it fails, its cursor
    stays put, and the rest of the run continues.
    """
    run_id = f"{source}-sync-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    totals = {"principals": len(principals), "done": 0, "failed": 0,
              "fetched": 0, "inserted": 0, "merged": 0}
    failed: list[str] = []
    tot_lock = threading.Lock()

    def _one(i: int, principal: dict) -> None:
        who = label(principal)
        try:
            since_ms = store.get_fetch_cursor(org, source, who) if store else None
            stats = {"fetched": 0, "inserted": 0, "merged": 0, "max_ms": since_ms or 0}

            def _emit(batch: list[dict]) -> None:
                if not batch:
                    return
                rows = [_payload_row(l0, m, org, source, who, run_id) for m in batch]
                stats["fetched"] += len(rows)
                stats["max_ms"] = max(stats["max_ms"],
                                      max((l0.internal_date_ms(m) for m in batch), default=0))
                if dry_run or store is None:
                    return
                st = store.upsert_payloads(rows)
                stats["inserted"] += st["inserted"]
                stats["merged"] += st["merged"]

            fetch_principal(who, since_ms, _emit)

            # Cursor LAST, only on a clean principal: sources list newest-first,
            # so advancing mid-fetch would skip older unfetched messages.
            if store is not None and stats["max_ms"] and stats["max_ms"] != (since_ms or 0):
                store.set_fetch_cursor(org, source, who, last_internal_date_ms=stats["max_ms"])

            with tot_lock:
                for k in ("fetched", "inserted", "merged"):
                    totals[k] += stats[k]
                if not dry_run:
                    totals["done"] += 1
                done, new = totals["done"], totals["inserted"]
            log.info("[%s-sync] [%d/%d] %s — %d fetched, %d new, %d merged | %d done, %d new total",
                     source, i, len(principals), who, stats["fetched"], stats["inserted"],
                     stats["merged"], done, new)
        except Exception as exc:  # noqa: BLE001 — one bad principal must not end the run
            with tot_lock:
                totals["failed"] += 1
                failed.append(who)
            log.error("[%s-sync] [%d/%d] %s FAILED: %s — cursor unchanged, retries next run",
                      source, i, len(principals), who, exc)

    workers = max(1, min(parallel, len(principals)))
    log.info("[%s-sync] replicating %d principals with %d workers", source, len(principals), workers)
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix=f"{source}-l0") as pool:
        list(pool.map(lambda ip: _one(*ip), enumerate(principals, 1)))

    if store is not None:
        totals["payloads_total"] = store.count_payloads(org, source)
    totals["failed_principals"] = failed
    log.info("[%s-sync] STAGE_SUMMARY stage=l0_sync source=%s org=%s principals=%d done=%d "
             "failed=%d payloads_new=%d payloads_total=%s",
             source, source, org, totals["principals"], totals["done"], totals["failed"],
             totals["inserted"], totals.get("payloads_total"))
    log.info("[%s-sync] DONE %s", source, totals)
    return totals
