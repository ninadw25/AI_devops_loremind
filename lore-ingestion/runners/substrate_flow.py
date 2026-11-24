"""
runners/substrate_flow.py
─────────────────────────
The pipeline-as-a-river daemon: keeps every derived layer flowing while the
expensive stages (fetch, claim extraction) run, instead of waiting for them
to finish.

Every layer in the substrate is a cheap, idempotent, re-runnable projection —
data presence IS the work queue (a conversation without a digest row is
pending extraction; a claim not yet in Neo4j gets MERGEd next cycle). So
"orchestration" is just: notice what changed, re-run the projections that
depend on it.

    events changed (a fetch landed)   → L1 registries + L2 edges + dedup +
                                        FULL graph projection
    claims changed (extraction ran)   → dedup + claims-only graph projection
    nothing changed                   → sleep

The genuinely sequential barriers, and why they stay:
  - a conversation must be fully fetched before extraction reads it (fetch
    writes events atomically per batch; extraction only sees complete rows)
  - the L1 registry must exist before extraction prompts are built (it's a
    ~10s rebuild, refreshed here whenever events change; the long-running
    extraction process reloads it per restart)
Everything else runs simultaneously with extraction.

Usage (from lore-ingestion/):
    ../.venv/bin/python -u -m runners.substrate_flow --org ezobooks
    ../.venv/bin/python -u -m runners.substrate_flow --org ezobooks --interval 120
"""

from __future__ import annotations

import argparse
import os
import time

import sqlalchemy as sa
from dotenv import load_dotenv

from builders.l0_ledger import SCHEMA, LedgerStore, _resolve_dsn, safe_scalar
from common.logger import get_logger, setup_logging

log = get_logger("runners.substrate_flow")


def _apply_tenant_env(org: str) -> None:
    """Point this daemon's databases at `org`'s own, resolved from Vault.

    Unlike a fetch, the daemon is not launched by the control API, so it never
    passed through `RunManager._child_env` and would otherwise run every
    projection against whatever DATABASE_URL / NEO4J_* / CLAIMS_NEO4J_* the box
    happens to hold. On a box serving one client that is correct by accident; on
    the second client it writes one tenant's knowledge into another's graph,
    looking like a clean run.

    No VAULT_PROXY_URL → leave the environment alone (local/single-tenant, same
    behaviour as before). Set → the creds MUST resolve; the error propagates and
    the daemon refuses to start rather than starting on the wrong databases.

    Must run before the first get_settings(), which caches os.environ.
    """
    from common.config import _reset_settings, get_settings

    if not get_settings().vault_proxy_url:
        return
    from common.vault_client import resolve_run_env

    env = resolve_run_env(org)
    os.environ.update(env)
    _reset_settings()  # re-read with the tenant's values
    log.info("[flow] tenant env resolved from vault org=%s keys=%s", org, sorted(env))


def counts(store: LedgerStore, org: str) -> tuple[int, int, int]:
    # claim (and, for a tenant with only ever L0 having run, even events) may
    # not exist yet — each later layer creates its own table on its own first
    # run. safe_scalar treats "no table yet" as zero instead of crashing the
    # daemon before a brand-new tenant has produced anything to react to.
    with store._session() as s:
        payloads = safe_scalar(s, f"SELECT count(*) FROM {SCHEMA}.source_payloads WHERE org=:o", o=org)
        events = safe_scalar(s, f"SELECT count(*) FROM {SCHEMA}.events WHERE org=:o", o=org)
        claims = safe_scalar(s, f"SELECT count(*) FROM {SCHEMA}.claim WHERE org=:o", o=org)
    return payloads, events, claims


def _sources(store: LedgerStore, org: str) -> list[str]:
    """Distinct sources present in the replica — the daemon is source-agnostic,
    so it derives whatever is there (gmail, zoho_mail, …)."""
    with store._session() as s:
        return list(s.execute(sa.text(
            f"SELECT DISTINCT source FROM {SCHEMA}.source_payloads WHERE org=:o"),
            {"o": org}).scalars().all())


def cycle(org: str, payloads_changed: bool, events_changed: bool,
          claims_changed: bool) -> None:
    """One refresh pass. Imports inside so a crash in one builder never takes
    the daemon down with an import-time error, and each run uses fresh code.

    The river, in dependency order:
      new payloads (a fetch landed) → per-source normalize → classify → group,
        which bumps the events count and cascades below on the next tick;
      new events → L1 + L2 (both span ALL sources) → dedup → full projection;
      new claims → dedup → claims-only projection.
    """
    from builders import (classify_noise, dedup_claims, group_conversations,
                          l1_registry, l2_edges, normalize_events, project_claims)

    if payloads_changed:
        store = LedgerStore(_resolve_dsn())
        store.connect()
        try:
            srcs = _sources(store, org)
        finally:
            store.disconnect()
        log.info("[flow] payloads changed — deriving events for sources %s", srcs)
        for src in srcs:
            normalize_events.normalize_events(org, source=src)
            classify_noise.classify_noise(org, source=src)
            group_conversations.group_conversations(org, source=src)

    if events_changed:
        log.info("[flow] events changed — full refresh (L1 + L2 + dedup + projection)")
        l1_registry.build(org)                         # all sources — merges spine
        l2_edges.build(org)                            # all sources
        dedup_claims.build(org)
        project_claims.build(org)                      # full: nodes + L2 + claims
    elif claims_changed:
        log.info("[flow] new claims — dedup + claims-only projection")
        dedup_claims.build(org)
        project_claims.build(org, claims_only=True)


def main() -> None:
    ap = argparse.ArgumentParser(description="Continuous downstream refresh daemon")
    ap.add_argument("--org", required=True)
    ap.add_argument("--interval", type=int, default=300, help="seconds between checks")
    ap.add_argument("--once", action="store_true", help="one cycle and exit")
    args = ap.parse_args()

    load_dotenv(".env")
    # Before setup_logging and before anything reads Settings: this rewrites the
    # environment every later component resolves its databases from.
    _apply_tenant_env(args.org)
    setup_logging(org=args.org, log_dir="./output/logs", source="substrate_flow")
    store = LedgerStore(_resolve_dsn())
    if not store.connect():
        raise RuntimeError("substrate_flow: could not connect to Postgres.")
    start_payloads, start_events, start_claims = counts(store, args.org)
    store.disconnect()
    log.info("[flow] watching org=%s from payloads=%d events=%d claims=%d (interval %ds)",
             args.org, start_payloads, start_events, start_claims, args.interval)

    # Sentinel, not the real counts: "data presence IS the work queue" (module
    # docstring) means a tenant with a pre-existing backlog — fetched before
    # this daemon ever started — must be caught up on the FIRST cycle, not
    # only react to deltas that occur after launch. Seeding last_* from the
    # startup counts made the first cycle compare 131205 payloads against a
    # baseline of 131205 and conclude nothing had changed, so a tenant whose
    # fetch finished before the daemon was ever started never got processed.
    last_payloads = last_events = last_claims = -1

    while True:
        try:
            store = LedgerStore(_resolve_dsn())
            if not store.connect():
                raise RuntimeError("db unreachable")
            payloads, events, claims = counts(store, args.org)
            store.disconnect()

            cycle(args.org, payloads_changed=payloads != last_payloads,
                  events_changed=events != last_events,
                  claims_changed=claims != last_claims)
            last_payloads, last_events, last_claims = payloads, events, claims
        except Exception as exc:  # noqa: BLE001 — daemon must survive DB blips
            log.warning("[flow] cycle failed (%s) — retrying next interval", exc)
        if args.once:
            break
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
