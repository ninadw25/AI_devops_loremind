"""
runners/ingest_jira.py
──────────────────────
One-shot, end-to-end Jira ingestion orchestrator.

Runs the FULL Jira pipeline for a single project, stage by stage, as a
sequence of `python -m <module>` subprocesses (mirroring how the rest of
the pipeline is invoked from the docs). Each stage's start/exit is logged
and a non-zero return code ABORTS the run with a clear error.

This is the production entry point spawned by client-admin's
`POST /api/v1/ingestion/run` with `source='jira'`. client-admin decrypts the
three Jira env-vars (JIRA_BASE_URL, JIRA_EMAIL, JIRA_API_TOKEN) and passes them
through the child process environment; this runner reads them via get_settings()
(through each stage's own config) and validates they are present up front.

Project scoping:
    `infra.jira.run` has no --project-key flag — it scopes ingestion via the
    org config `jira_projects` whitelist. So this runner writes --project-key
    into that whitelist (output/config/{org}_config.json) before running
    discovery/full, ensuring ONLY the requested project is ingested.

Usage:
    python -m runners.ingest_jira --org acme --project-key ENG
    python -m runners.ingest_jira --org acme --project-key ENG --limit 50
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.request
import uuid
from glob import glob
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

from common.config import get_settings
from common.logger import get_logger, setup_logging
from common.org_config import load_org_config, save_org_config

log = get_logger("runners.ingest_jira")

# Repo root — this file is runners/ingest_jira.py, so parents[1] is the
# lore-ingestion directory. All subprocess stages run with this cwd so that
# `python -m <module>` resolves the package layout and writes under output/.
REPO_ROOT = Path(__file__).resolve().parents[1]

OUTPUT_DIR = "output"


def _run_stage(label: str, module_args: list[str], env: dict) -> None:
    """Run one pipeline stage as `python -m <module> ...` and abort on failure.

    Logs the stage start and exit. Raises SystemExit(1) on a non-zero return
    code so the whole ingestion run fails loudly at the first broken stage.
    """
    cmd = [sys.executable, "-m", *module_args]
    log.info("─" * 60)
    log.info("STAGE START [%s]: %s", label, " ".join(cmd))
    log.info("─" * 60)

    result = subprocess.run(cmd, cwd=str(REPO_ROOT), env=env, check=False)

    if result.returncode != 0:
        log.error("STAGE FAILED [%s] — exit code %d. Aborting Jira ingestion run.",
                  label, result.returncode)
        raise SystemExit(1)

    log.info("STAGE OK [%s] — exit code 0", label)


def _latest_raw_jira_jsonl(org: str) -> Path:
    """Return the newest dated raw Jira JSONL written by the fetch stage.

    The fetch stage writes output/raw/{org}_jira_{YYYY-MM-DD}.jsonl. Glob for
    all of them and pick the newest by modification time. Aborts if none exist.
    """
    pattern = str(REPO_ROOT / OUTPUT_DIR / "raw" / f"{org}_jira_*.jsonl")
    matches = [Path(p) for p in glob(pattern)]
    # Exclude the noise sidecar ({org}_jira_noise.jsonl) — not a dated raw file.
    matches = [p for p in matches if p.name != f"{org}_jira_noise.jsonl"]
    if not matches:
        log.error("No raw Jira JSONL found matching %s — fetch stage produced no output.",
                  pattern)
        raise SystemExit(1)
    newest = max(matches, key=lambda p: p.stat().st_mtime_ns)
    log.info("Selected newest raw Jira file: %s", newest)
    return newest


def _set_project_whitelist(org: str, project_key: str) -> None:
    """Pin the org config jira_projects whitelist to the requested project key.

    `infra.jira.run` filters ingestion to projects listed in this whitelist, so
    writing it here is how we honor --project-key (the connector takes no such
    flag). Other config fields are preserved.
    """
    config_dir = REPO_ROOT / OUTPUT_DIR / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    config = load_org_config(org, config_dir)
    config["org"] = org
    config["jira_projects"] = [project_key]
    save_org_config(config, config_dir)
    log.info("Pinned org config jira_projects whitelist to [%s]", project_key)


def _notify_rbac(org: str, run_id: str) -> None:
    """Best-effort notify rbac so it loads the new scope_principals.

    POSTs {org, runId, artifactBase} to <rbac>/internal/ingestion-complete with
    a bearer secret. Never fails the run — any error is logged and swallowed
    (mirrors control/manager.py's terminal-callback behavior).
    """
    base = (
        os.environ.get("SERVICE_RBAC_SERVICE_URL")
        or os.environ.get("RBAC_URL")
        or "http://localhost:3000"
    ).rstrip("/")
    secret = (
        os.environ.get("INTERNAL_WEBHOOK_SECRET")
        or os.environ.get("INGESTION_CONTROL_SECRET")
        or ""
    )
    url = base + "/internal/ingestion-complete"
    payload = json.dumps({
        "org": org,
        "runId": run_id,
        "artifactBase": OUTPUT_DIR,
    }).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {secret}",
    }
    try:
        req = urllib.request.Request(url, data=payload, method="POST", headers=headers)
        with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310
            log.info("rbac ingestion-complete notified url=%s http=%s", url, resp.status)
    except Exception as exc:  # noqa: BLE001
        log.warning("rbac ingestion-complete notify failed (non-fatal) url=%s error=%s",
                    url, exc)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="End-to-end Jira ingestion pipeline (single project)")
    parser.add_argument("--org", required=True, help="Organization slug")
    parser.add_argument("--project-key", required=True, dest="project_key",
                        help="Jira project key to ingest (e.g. ENG)")
    parser.add_argument("--limit", type=int, default=None,
                        help="Optional cap on issues fetched / threads extracted")
    args = parser.parse_args()

    org = args.org
    project_key = args.project_key
    limit = args.limit

    logs_dir = REPO_ROOT / OUTPUT_DIR / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    setup_logging(org=org, log_dir=str(logs_dir), source="ingest_jira")

    run_id = uuid.uuid4().hex
    log.info("=" * 60)
    log.info("=== ingest_jira START org=%s project=%s limit=%s run_id=%s ===",
             org, project_key, limit, run_id)
    log.info("=" * 60)

    # ── Validate Jira credentials are present in the environment ──────────
    # client-admin injects JIRA_BASE_URL / JIRA_EMAIL / JIRA_API_TOKEN into the
    # child environment; get_settings() reads them. Fail fast with a clear
    # message rather than letting the fetch stage produce a cryptic auth error.
    settings = get_settings()
    missing = [
        name for name, value in (
            ("JIRA_BASE_URL", settings.jira_base_url),
            ("JIRA_EMAIL", settings.jira_email),
            ("JIRA_API_TOKEN", settings.jira_api_token),
        )
        if not value
    ]
    if missing:
        log.error("Missing required Jira env-var(s): %s. "
                  "These must be set in the environment (client-admin injects them).",
                  ", ".join(missing))
        raise SystemExit(1)

    # Child stages inherit the current environment (incl. the injected JIRA_*).
    env = dict(os.environ)

    # Scope ingestion to the requested project via the org config whitelist.
    _set_project_whitelist(org, project_key)

    # ── Stage 1: Discovery (ACL scopes / principals) ──────────────────────
    _run_stage(
        "1/10 discovery",
        ["infra.jira.run", "--org", org, "--stage", "discovery",
         "--output-dir", OUTPUT_DIR],
        env,
    )

    # ── Stage 2: Full fetch (issues + comments → raw JSONL + ACL artifact) ─
    full_args = ["infra.jira.run", "--org", org, "--stage", "full",
                 "--output-dir", OUTPUT_DIR]
    if limit is not None:
        full_args += ["--limit", str(limit)]
    _run_stage("2/10 fetch", full_args, env)

    # ── Stage 3: Filter (resolve newest raw dated file) ───────────────────
    raw_path = _latest_raw_jira_jsonl(org)
    _run_stage(
        "3/10 filter",
        ["pipeline.filter", "--org", org, "--input", str(raw_path),
         "--source", "jira", "--output-dir", OUTPUT_DIR],
        env,
    )

    filtered_path = (REPO_ROOT / OUTPUT_DIR / "filtered"
                     / f"{org}_jira_filtered.jsonl")

    # ── Stage 4: Person registry ──────────────────────────────────────────
    _run_stage(
        "4/10 person_registry",
        ["pipeline.person_registry", "--org", org,
         "--input", str(filtered_path), "--output-dir", OUTPUT_DIR],
        env,
    )

    # ── Stage 5: Communication graph ──────────────────────────────────────
    _run_stage(
        "5/10 comm_graph",
        ["pipeline.comm_graph", "--org", org,
         "--input", str(filtered_path), "--output-dir", OUTPUT_DIR],
        env,
    )

    # ── Stage 6: Triple extraction (LLM) ──────────────────────────────────
    extract_args = ["pipeline.triple_extractor", "--org", org,
                    "--source", "jira", "--data-dir", OUTPUT_DIR]
    if limit is not None:
        extract_args += ["--limit", str(limit)]
    _run_stage("6/10 triple_extractor", extract_args, env)

    # ── Stage 7: Validation gate ──────────────────────────────────────────
    _run_stage(
        "7/10 validator",
        ["pipeline.validator", "--org", org, "--source", "jira",
         "--data-dir", OUTPUT_DIR],
        env,
    )

    # ── Stage 8: Embed knowledge nodes ────────────────────────────────────
    _run_stage(
        "8/10 embed_knowledge",
        ["pipeline.embed_knowledge", "--org", org, "--source", "jira",
         "--data-dir", OUTPUT_DIR],
        env,
    )

    # ── Stage 9: Deduplication ────────────────────────────────────────────
    _run_stage(
        "9/10 deduplicator",
        ["pipeline.deduplicator", "--org", org, "--source", "jira",
         "--data-dir", OUTPUT_DIR],
        env,
    )

    # ── Stage 10: Unified builder (Neo4j Cypher + embeddings upload) ──────
    _run_stage(
        "10/10 unified_builder",
        ["builders.unified_builder", "--org", org, "--source", "jira"],
        env,
    )

    # ── Best-effort: notify rbac to load the new scope_principals ─────────
    _notify_rbac(org, run_id)

    log.info("=" * 60)
    log.info("=== ingest_jira DONE org=%s project=%s run_id=%s ===",
             org, project_key, run_id)
    log.info("=" * 60)


if __name__ == "__main__":
    main()
