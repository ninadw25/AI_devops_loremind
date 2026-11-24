#!/usr/bin/env python
"""Live progress for the Gmail replication run.

Reads the two things that actually know what's happening: the ledger (durable
counts — the truth about what's safe on disk) and the tail of the driver log
(where the in-flight mailbox is right now). Redraws in place until you Ctrl-C.

    ../.venv/bin/python scripts/fetch_progress.py
    ../.venv/bin/python scripts/fetch_progress.py --interval 5

Deliberately read-only: it never touches the run, so it is safe to start,
stop, and restart while a fetch is going.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import time
from datetime import timedelta

import sqlalchemy as sa

# Run directly from scripts/ as well as via -m, so the repo root is importable.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from builders.l0_ledger import SCHEMA, LedgerStore, _resolve_dsn  # noqa: E402

LOG = "output/logs/full_sync_driver.log"
ORG, SOURCE = "ezobooks", "gmail"

ANSI = re.compile(r"\x1b\[[0-9;]*m")
# "[52/62] [87/1505] Batches done... (1740 msgs fetched, 294.4s elapsed)"
BATCH = re.compile(
    r"\[(\d+)/(\d+)\]\s+\[(\d+)/(\d+)\]\s+Batches done\.\.\.\s+"
    r"\((\d+) msgs fetched, ([\d.]+)s elapsed\)"
)
# "[52/62] someone@ezobooks.in [FUNC] — starting message fetch"
START = re.compile(r"\[(\d+)/(\d+)\]\s+(\S+@\S+?)\s+(?:\[FUNC\]\s+)?[—-]\s+starting")
# "[52/62] someone@ezobooks.in — 6992 messages found"
FOUND = re.compile(r"\[(\d+)/(\d+)\]\s+(\S+@\S+?)\s+[—-]\s+(\d+) messages found")
SKIP = re.compile(r"(\d+) already replicated, (\d+) to fetch")


def tail(path: str, nbytes: int = 200_000) -> list[str]:
    """Last chunk of the log, ANSI stripped. Empty list if it isn't there yet."""
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            fh.seek(max(0, fh.tell() - nbytes))
            raw = fh.read().decode("utf-8", "replace")
    except FileNotFoundError:
        return []
    return [ANSI.sub("", ln) for ln in raw.splitlines()]


def last(pattern: re.Pattern, lines: list[str]):
    for ln in reversed(lines):
        m = pattern.search(ln)
        if m:
            return m
    return None


def bar(done: int, total: int, width: int = 42) -> str:
    if total <= 0:
        return "─" * width
    filled = min(width, round(width * done / total))
    return "█" * filled + "░" * (width - filled)


def hms(seconds: float) -> str:
    if seconds < 0 or seconds != seconds or seconds > 86_400 * 7:
        return "—"
    return str(timedelta(seconds=int(seconds)))


def running() -> bool:
    return subprocess.run(
        ["pgrep", "-f", "runners.ledger_sync"], capture_output=True
    ).returncode == 0


def ledger_counts(store: LedgerStore) -> tuple[int, int]:
    """(durable payloads, mailboxes with a cursor) — i.e. fully-finished ones."""
    with store._session() as s:
        payloads = s.execute(
            sa.text(
                f"SELECT count(*) FROM {SCHEMA}.source_payloads "
                "WHERE org=:o AND source=:s"
            ),
            {"o": ORG, "s": SOURCE},
        ).scalar_one()
        done = s.execute(
            sa.text(
                f"SELECT count(*) FROM {SCHEMA}.fetch_cursors "
                "WHERE org=:o AND source=:s"
            ),
            {"o": ORG, "s": SOURCE},
        ).scalar_one()
    return payloads, done


def render(store: LedgerStore, started_at: float, baseline: int) -> str:
    lines = tail(LOG)
    payloads, mailboxes_done = ledger_counts(store)

    b = last(BATCH, lines)
    s = last(START, lines)
    f = last(FOUND, lines)
    k = last(SKIP, lines)

    mb_i, mb_n = (int(b.group(1)), int(b.group(2))) if b else (
        (int(s.group(1)), int(s.group(2))) if s else (0, 0)
    )
    who = s.group(3) if s else "—"

    out = []
    live = running()
    dot = "\033[32m●\033[0m" if live else "\033[31m●\033[0m"
    out.append(f"  {dot} Gmail replication — {'running' if live else 'STOPPED'}")
    out.append("")

    # Mailboxes: finished ones are those with a cursor; the current one is extra.
    out.append(f"  Mailboxes   {bar(mailboxes_done, mb_n)}  {mailboxes_done}/{mb_n or '?'} complete")

    if b:
        bi, bn = int(b.group(3)), int(b.group(4))
        msgs, elapsed = int(b.group(5)), float(b.group(6))
        rate = msgs / elapsed if elapsed else 0
        eta = (bn - bi) * (elapsed / bi) if bi else -1
        out.append(f"  This inbox  {bar(bi, bn)}  {bi}/{bn} batches")
        out.append(f"              {who}")
        detail = f"              {msgs:,} fetched · {rate:.1f} msg/s · ~{hms(eta)} left"
        if f and f.group(3) == who:
            detail += f" · {int(f.group(4)):,} in mailbox"
        out.append(detail)
        if k:
            out.append(
                f"              skipped {int(k.group(1)):,} already replicated"
            )
    elif s:
        out.append(f"  This inbox  {bar(0, 1)}  listing messages…")
        out.append(f"              {who}")

    out.append("")
    gained = payloads - baseline
    wall = time.time() - started_at
    out.append(f"  Stored      {payloads:,} unique messages durable")
    # New rows grow slower than "fetched" and that is the system working: a
    # message already pulled from another mailbox is one row gaining a second
    # sighting, not a new row. A big gap here means heavy internal cc'ing.
    out.append(
        f"              +{gained:,} new in {hms(wall)} "
        "(rest were already-seen copies from other mailboxes)"
    )
    if not live:
        out.append("")
        out.append("  \033[33mNo fetch process running.\033[0m "
                   "Relaunch: setsid nohup bash scripts/full_sync_overnight.sh &")
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--interval", type=float, default=3.0, help="refresh seconds")
    ap.add_argument("--once", action="store_true", help="print once and exit")
    args = ap.parse_args()

    store = LedgerStore(_resolve_dsn())
    if not store.connect():
        print("Cannot reach the ledger database. Is lore-postgres up?", file=sys.stderr)
        return 1

    baseline, _ = ledger_counts(store)
    started = time.time()

    if args.once:
        print(render(store, started, baseline))
        return 0

    print("\033[?25l", end="")  # hide cursor
    prev = 0
    try:
        while True:
            frame = render(store, started, baseline)
            # Redraw in place: jump home, clear each old line as we go.
            sys.stdout.write("\033[H\033[J" if prev == 0 else f"\033[{prev}A")
            for ln in frame.split("\n"):
                sys.stdout.write("\033[2K" + ln + "\n")
            prev = frame.count("\n") + 1
            sys.stdout.flush()
            time.sleep(args.interval)
    except KeyboardInterrupt:
        return 0
    finally:
        print("\033[?25h", end="")  # restore cursor


if __name__ == "__main__":
    raise SystemExit(main())
