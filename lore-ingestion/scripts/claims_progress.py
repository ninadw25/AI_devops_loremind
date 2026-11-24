#!/usr/bin/env python
"""Live progress for the claim extraction run (build step 4).

Reads the DB only — the driver log is not reliable for this: Python
block-buffers stdout when it isn't a TTY, so `extract_claims_driver.log` can
sit stale for a long time even while the run is actively writing claims.
`claim_digest.created_at` is the ground truth for both progress and rate.

    ../.venv/bin/python scripts/claims_progress.py
    ../.venv/bin/python scripts/claims_progress.py --once

Deliberately read-only: safe to start/stop/restart while extraction runs.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from datetime import timedelta

import sqlalchemy as sa

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from builders.l0_ledger import SCHEMA, LedgerStore, _resolve_dsn  # noqa: E402

ORG = "ezobooks"
MIN_MESSAGES = 2   # matches the overnight driver's default (skips 1-message notifications)


def hms(seconds: float) -> str:
    return str(timedelta(seconds=int(seconds)))


def is_running() -> bool:
    out = subprocess.run(["pgrep", "-f", "runners.extract_claims"],
                         capture_output=True, text=True)
    return bool(out.stdout.strip())


def counts(store: LedgerStore) -> dict:
    with store._session() as s:
        pending = s.execute(sa.text(f"""
            SELECT count(*) FROM (
              SELECT e.conversation_id FROM {SCHEMA}.events e
              LEFT JOIN {SCHEMA}.claim_digest d ON d.conversation_id = e.conversation_id
              WHERE e.org=:o AND e.noise_class='signal' AND e.conversation_id IS NOT NULL
                AND d.conversation_id IS NULL
              GROUP BY e.conversation_id HAVING count(*) >= :m) x
        """), {"o": ORG, "m": MIN_MESSAGES}).scalar()
        digests = s.execute(sa.text(
            f"SELECT count(*) FROM {SCHEMA}.claim_digest WHERE org=:o"), {"o": ORG}).scalar()
        with_claims = s.execute(sa.text(
            f"SELECT count(*) FROM {SCHEMA}.claim_digest WHERE org=:o AND claim_count>0"),
            {"o": ORG}).scalar()
        claims = s.execute(sa.text(
            f"SELECT count(*) FROM {SCHEMA}.claim WHERE org=:o"), {"o": ORG}).scalar()
        grounded = s.execute(sa.text(
            f"SELECT count(*) FROM {SCHEMA}.claim WHERE org=:o AND evidence_grounded"),
            {"o": ORG}).scalar()
        kinds = dict(s.execute(sa.text(
            f"SELECT kind, count(*) FROM {SCHEMA}.claim WHERE org=:o GROUP BY 1 ORDER BY 2 DESC"),
            {"o": ORG}).all())
        recent = s.execute(sa.text(f"""
            SELECT conversation_id, digest, claim_count, created_at
            FROM {SCHEMA}.claim_digest WHERE org=:o ORDER BY created_at DESC LIMIT 3
        """), {"o": ORG}).all()
        # rate over the last 5 minutes of actual writes
        rate = s.execute(sa.text(f"""
            SELECT count(*) FROM {SCHEMA}.claim_digest
            WHERE org=:o AND created_at > now() - interval '5 minutes'
        """), {"o": ORG}).scalar()
    return {"pending": pending, "digests": digests, "with_claims": with_claims,
            "claims": claims, "grounded": grounded, "kinds": kinds, "recent": recent,
            "rate_per_min": rate / 5.0}


def render(store: LedgerStore) -> str:
    c = counts(store)
    live = is_running()
    total = c["digests"] + c["pending"]
    pct = 100 * c["digests"] / total if total else 0
    out = [
        "═" * 64,
        f"  CLAIM EXTRACTION — {ORG}  {'●  live' if live else '○  NOT RUNNING'}",
        "═" * 64,
        f"  Conversations   {c['digests']:,} / {total:,}  ({pct:.1f}%)  "
        f"— {c['with_claims']:,} produced >=1 claim",
        f"  Claims          {c['claims']:,} total, {c['grounded']:,} evidence-grounded "
        f"({100*c['grounded']/max(c['claims'],1):.0f}%)",
        f"  Kinds           " + ", ".join(f"{k}:{v}" for k, v in c["kinds"].items()),
        f"  Rate            {c['rate_per_min']:.1f} conversations/min (last 5 min)",
    ]
    if c["rate_per_min"] > 0:
        eta_min = c["pending"] / c["rate_per_min"]
        out.append(f"  ETA             ~{hms(eta_min*60)} at current rate")
    out.append("")
    out.append("  Most recent conversations processed:")
    for r in c["recent"]:
        out.append(f"    [{r.created_at.strftime('%H:%M:%S')}] {r.claim_count} claim(s) — "
                   f"{(r.digest or '')[:90]}")
    if not live:
        out.append("")
        out.append("  \033[33mNo extraction process running.\033[0m "
                   "Relaunch: setsid nohup bash scripts/extract_claims_overnight.sh 2 &")
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--interval", type=float, default=5.0, help="refresh seconds")
    ap.add_argument("--once", action="store_true", help="print once and exit")
    args = ap.parse_args()

    store = LedgerStore(_resolve_dsn())
    if not store.connect():
        print("Cannot reach the ledger database. Is lore-postgres up?", file=sys.stderr)
        return 1

    if args.once:
        print(render(store))
        return 0

    print("\033[?25l", end="")
    prev = 0
    try:
        while True:
            frame = render(store)
            sys.stdout.write("\033[H\033[J" if prev == 0 else f"\033[{prev}A")
            for ln in frame.split("\n"):
                sys.stdout.write("\033[2K" + ln + "\n")
            prev = frame.count("\n") + 1
            sys.stdout.flush()
            time.sleep(args.interval)
    except KeyboardInterrupt:
        return 0
    finally:
        print("\033[?25h", end="")


if __name__ == "__main__":
    main()
