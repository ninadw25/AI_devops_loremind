"""
runners.sync
────────────
Incremental ingestion delta loop. The "hot path" of the schedule.

What it does
────────────
Reads the per-user Gmail watermark, fetches only mail with
`internalDate > last_seen`, runs the delta through filter → person
merge → comm-graph merge → triple extraction (only on changed threads)
→ validate → embed (skip cached) → dedupe → unified builder. On
success, advances the watermark atomically.

Run cadence (recommended)
─────────────────────────
    EventBridge cron `0 0,6,12,18 * * ? *`  →  ECS RunTask  →  this script
    (every 6 hours)

The "cold loop" — `runners.post_process` (topic clustering, process
synthesis, cross-thread edges) — runs on its own less-frequent
schedule (e.g. daily at 03:00). It's a full-corpus operation, not a
delta operation, so do NOT call it from here.

Atomicity guarantee
───────────────────
The watermark advances ONLY when the entire pipeline completes
successfully. A crash anywhere → watermark stays where it was → next
run re-fetches the same window → downstream stages dedupe by
message-id / node-id and Neo4j MERGE handles idempotency. The cost of
a crash is one re-processed window; the cost of advancing too early
would be permanently lost messages, which is unacceptable.

Container deployment
────────────────────
Set LORE_STATE_DIR to a persistent path (EFS mount on Fargate, bind
volume on Docker-on-prem). Everything under that dir survives across
container restarts:
    $LORE_STATE_DIR/checkpoints/{org}_sync.json    ← THE watermark
    $LORE_STATE_DIR/raw/                           ← daily fetch files
    $LORE_STATE_DIR/filtered/
    $LORE_STATE_DIR/knowledge/                     ← cumulative triples + embeddings

CLI
───
    python -m runners.sync --org ezobooks \\
        --domain ezobooks.in \\
        --admin-email admin@ezobooks.in \\
        --key /etc/lore/key.json

The first three Gmail args are forwarded to `infra.gmail.run`. Any
that are env-driven in your deployment (NVIDIA_API_KEY, NEO4J_*, etc.)
are read by the underlying stages — no orchestration concern here.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

from common.logger import get_logger, setup_logging
from common import watermark
from infra.gitlab.polling import start_polling_scheduler as _start_gitlab_poller
from infra.github.polling import start_polling_scheduler as _start_github_poller
from common.output_paths import IdentityPaths


def start_polling_scheduler():
    """Start polling for all configured code-host sources."""
    _start_gitlab_poller()
    _start_github_poller()

log = get_logger("sync")


# ══════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════

def _python() -> str:
    """The Python executable to invoke sub-stages with.
    """
    return sys.executable


def _run(cmd: list[str], stage_name: str) -> None:
    """Run a sub-stage. Raises SystemExit on non-zero exit (the
    orchestrator's contract is "every stage MUST succeed, else fail loud
    and leave the watermark unchanged").
    """
    log.info("─" * 60)
    log.info("STAGE %s", stage_name)
    log.info("$ %s", " ".join(cmd))
    t0 = time.time()
    result = subprocess.run(cmd, check=False)
    dt = time.time() - t0
    if result.returncode != 0:
        raise RuntimeError(
            f"Stage '{stage_name}' failed with exit code "
            f"{result.returncode} after {dt:.1f}s"
        )
    log.info("STAGE %s — OK (%.1fs)", stage_name, dt)


def _read_filtered_thread_ids(filtered_path: Path,
                              since_ms_by_user: dict[str, int]) -> set[str]:
    """Identify thread_ids that have at least one message newer than the
    per-user watermark in the just-written filtered JSONL.

    The filter stage preserves source-message metadata, so each event
    catches the originating user's email and the message's internalDate
    (via timestamp). We tag a thread as "changed" if any of its messages
    are newer than the corresponding user's watermark.

    Conservative: if we can't determine the user/timestamp, the thread
    is included (re-process is cheap, miss is not).
    """
    changed: set[str] = set()
    if not filtered_path.exists():
        return changed

    with filtered_path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue

            raw = ev.get("raw") or {}
            thread_id = raw.get("thread_id") or ev.get("thread_id")
            if not thread_id:
                continue

            # Owning mailbox of this event (whose inbox surfaced it).
            owner = raw.get("mailbox_owner") or raw.get("user_email")
            ts_ms = raw.get("internal_date_ms")
            if ts_ms is None:
                # Fall back to event.timestamp (ISO) → ms
                ts_iso = ev.get("timestamp")
                if ts_iso:
                    try:
                        ts_ms = int(datetime.fromisoformat(
                            ts_iso.replace("Z", "+00:00")
                        ).timestamp() * 1000)
                    except ValueError:
                        ts_ms = None

            cutoff = since_ms_by_user.get(owner or "", 0)
            # No cutoff (first run / new user) OR newer than cutoff → changed
            if ts_ms is None or cutoff == 0 or ts_ms > cutoff:
                changed.add(thread_id)

    return changed


# ══════════════════════════════════════════════════════════════════════
# Orchestrator
# ══════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="Incremental ingestion delta loop (every 6h).")
    parser.add_argument("--org", required=True)
    parser.add_argument("--domain", required=True,
                        help="Google Workspace domain (e.g. ezobooks.in)")
    parser.add_argument("--admin-email", required=True,
                        help="Super Admin email for DWD")
    parser.add_argument("--key", required=True,
                        help="Path to Google service account key.json")
    parser.add_argument("--org-domains", default=None, nargs="*")
    parser.add_argument("--extra-functional", default=None, nargs="*")
    parser.add_argument("--data-dir", default=None,
                        help="Overrides LORE_STATE_DIR for testing")
    args = parser.parse_args()

    org = args.org
    data_dir = Path(args.data_dir or os.environ.get("LORE_STATE_DIR", "output"))
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "logs").mkdir(parents=True, exist_ok=True)
    (data_dir / "checkpoints").mkdir(parents=True, exist_ok=True)

    # Make the watermark module see the same path
    os.environ["LORE_STATE_DIR"] = str(data_dir)

    setup_logging(org=org, log_dir=str(data_dir / "logs"), source="sync")

    log.info("═" * 60)
    log.info("Lore Incremental Sync — %s", org)
    log.info("Data dir: %s", data_dir)
    log.info("Started:  %s", datetime.now(timezone.utc).isoformat())
    log.info("═" * 60)

    # Start the GitLab polling scheduler
    start_polling_scheduler()

    # Record run-start (clears any "running" state if previous run crashed
    # without writing a final status — diagnostic only, doesn't affect
    # watermark per-user values).
    watermark.mark_run_started(org)
    prev_state = watermark.load(org)

    # Snapshot per-user "since_ms" BEFORE the run, so we can later
    # identify which threads have new messages.
    since_ms_by_user = {
        email: (u.get("last_internal_date_ms") or 0)
        for email, u in (prev_state.get("users") or {}).items()
    }
    first_run = not any(since_ms_by_user.values())
    log.info(
        "Watermark snapshot: %s",
        "FIRST RUN (full ingestion)" if first_run
        else f"{len(since_ms_by_user)} users tracked"
    )

    # File path the fetcher will write its per-user max(internalDate) into.
    fetch_report_path = data_dir / "checkpoints" / f"{org}_gmail_watermark_report.json"
    if fetch_report_path.exists():
        fetch_report_path.unlink()  # ensure stale report doesn't bleed in

    try:
        # ── STAGE 1: Fetch (incremental via per-user watermark) ─────
        fetch_cmd = [
            _python(), "-m", "infra.gmail.run",
            "--org", org,
            "--domain", args.domain,
            "--admin-email", args.admin_email,
            "--key", args.key,
            "--output-dir", str(data_dir),
            "--use-watermark",
            "--watermark-out", str(fetch_report_path),
            "--allow-empty",
        ]
        if args.org_domains:
            fetch_cmd += ["--org-domains", *args.org_domains]
        if args.extra_functional:
            fetch_cmd += ["--extra-functional", *args.extra_functional]
        _run(fetch_cmd, "1/8 — Gmail fetch (incremental)")

        # ── Read the per-user max(internalDate) report ──────────────
        if not fetch_report_path.exists():
            raise RuntimeError(
                f"Gmail fetch did not emit watermark report at {fetch_report_path}. "
                "Refusing to advance watermark."
            )
        fetch_report = json.loads(fetch_report_path.read_text(encoding="utf-8"))
        user_max_ms: dict[str, int] = fetch_report.get("user_max_internal_date_ms") or {}
        log.info("Fetch report: %d users observed", len(user_max_ms))

        # ── STAGE 2: Filter ────────────────────────────────────────
        # The filter is a pure function of the raw JSONL it sees.
        # Today's raw file is dated; pick it up by glob.
        run_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        raw_glob = data_dir / "raw" / f"{org}_gmail_{run_date}.jsonl"
        if raw_glob.exists():
            _run([
                _python(), "-m", "pipeline.filter",
                "--input", str(raw_glob),
                "--org", org,
                "--skip-llm",
            ], "2/8 — Filter")
        else:
            log.info("No raw file today (no new mail) — skipping filter")

        # ── STAGE 3: Person registry (merge mode) ──────────────────
        filtered_path = data_dir / "filtered" / f"{org}_gmail_filtered.jsonl"
        if filtered_path.exists():
            # TODO(incremental-merge): pipeline.person_registry currently
            # rebuilds from the full filtered file. For a true delta, add
            # a `--merge` mode that union-merges with the existing
            # output/knowledge/registry/{org}_person_registry.json. For
            # now, the rebuild is idempotent — slower but correct.
            _run([
                _python(), "-m", "pipeline.person_registry",
                "--input", str(filtered_path),
                "--org", org,
            ], "3/8 — Person registry")

            # ── STAGE 4: Comm graph ────────────────────────────────
            # Same TODO as above: re-builds from full filtered file.
            _run([
                _python(), "-m", "pipeline.comm_graph",
                "--input", str(filtered_path),
                "--org", org,
            ], "4/8 — Comm graph")

            # ── STAGE 4.5: Centralized identity resolution ─────────
            # Cross-source: folds the Gmail registry + any Slack/Jira ACL
            # artifacts into one surrogate-id person map (output/identity/
            # {org}_identity.json) for the backend's RBAC. Seeds from the
            # prior run so person_ids stay stable across syncs. Idempotent.
            _run([
                _python(), "-m", "pipeline.identity.resolve",
                "--org", org,
                "--data-dir", str(data_dir),
                "--sources", "gmail,slack,jira",
                "--seed", str(IdentityPaths(org, data_dir).identity),
            ], "4.5/8 — Identity resolution")

        # ── STAGE 5: Triple extraction (only on changed threads) ───
        changed_threads = _read_filtered_thread_ids(filtered_path, since_ms_by_user)
        if first_run:
            log.info("First run — extracting ALL threads (no thread-id filter)")
            extract_cmd = [
                _python(), "-m", "pipeline.extraction.cli",
                "--org", org, "--resume", "--allow-empty",
            ]
        elif changed_threads:
            changed_file = data_dir / "checkpoints" / f"{org}_changed_threads.txt"
            changed_file.write_text("\n".join(sorted(changed_threads)), encoding="utf-8")
            log.info("Targeting %d changed threads", len(changed_threads))
            extract_cmd = [
                _python(), "-m", "pipeline.extraction.cli",
                "--org", org, "--resume", "--allow-empty",
                "--thread-ids", str(changed_file),
            ]
        else:
            log.info("No changed threads — skipping extraction")
            extract_cmd = None

        if extract_cmd:
            _run(extract_cmd, "5/8 — Triple extraction")

        # ── STAGE 6: Validate ──────────────────────────────────────
        _run([
            _python(), "-m", "pipeline.validation.cli",
            "--org", org, "--allow-empty",
        ], "6/8 — Validate")

        # ── STAGE 6.5: Embed (skips already-cached node_ids) ───────
        _run([
            _python(), "-m", "pipeline.embed_knowledge",
            "--org", org, "--allow-empty",
        ], "6.5/8 — Embed knowledge")

        # ── STAGE 6.6: Deduplicate ─────────────────────────────────
        _run([
            _python(), "-m", "pipeline.deduplicator",
            "--org", org,
        ], "6.6/8 — Deduplicate")

        # ── STAGE 7: Unified builder (Neo4j upsert; MERGE everywhere) ─
        _run([
            _python(), "-m", "builders.unified_builder",
            "--org", org,
        ], "7/8 — Unified builder (Neo4j MERGE)")

        # ── STAGE 8 (the actual write of the watermark) ────────────
        # ONLY now — after every stage above succeeded — do we advance
        # the watermark. If anything above raised, the except handler
        # records "failed" and leaves per-user watermarks untouched.
        watermark.commit_run(
            org=org,
            user_internal_dates_ms=user_max_ms,
            messages_processed=fetch_report.get("messages_processed", 0)
                or sum(1 for _ in (filtered_path.open(encoding="utf-8")
                                   if filtered_path.exists() else [])),
            threads_processed=len(changed_threads),
        )
        log.info("═" * 60)
        log.info("Sync complete — watermark ADVANCED for %d users",
                 sum(1 for ms in user_max_ms.values() if ms > 0))
        log.info("═" * 60)

    except Exception as exc:
        log.exception("Sync FAILED — watermark NOT advanced")
        watermark.mark_run_failed(org, error=f"{exc.__class__.__name__}: {exc}")
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
