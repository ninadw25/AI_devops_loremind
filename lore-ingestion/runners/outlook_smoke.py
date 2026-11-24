"""
runners.outlook_smoke
─────────────────────
End-to-end MS365 smoke pipeline: filter → person registry → triple
extraction on already-fetched Outlook Mail and/or Teams raw JSONL.

Why this lives in `runners/` and not `infra/outlook/`:
    This orchestrates Layer-2 pipeline stages (filter, person_registry,
    extraction). Connectors live in Layer 1 (`infra/`) and must not import
    upward into `pipeline/`. Keeping the smoke orchestration here preserves
    the one-way dependency rule (runners → pipeline → infra → common).

This is a developer convenience for exercising the MS365 path locally against
already-fetched data. It is invoked from `infra.outlook.run --stage smoke` via
a deferred import so the connector keeps no module-level dependency on the
pipeline.

Architecture contract (approved LOR-67 Phase 2A):
    - NEVER calls graph_get(), get_access_token(), emit_ms365_identities(),
      or emit_teams_scope_map(). Zero network operations.
    - Discovery artifacts (identities_json, teams_scopes_json) are
      prerequisites produced by --stage discover. Smoke verifies their
      presence and logs a warning when absent — it does NOT generate them.
    - Required inputs: raw_mail_jsonl and/or raw_teams_jsonl from OutlookPaths
      (or explicit overrides via raw_mail_path / raw_teams_path). Missing
      required inputs raise FileNotFoundError immediately with a clear message.

Workflows:
    # Mock-data (no credentials needed)
    python -m infra.outlook.run --org demo --stage smoke

    # Real MS365 (run discover + fetch first)
    python -m infra.outlook.run --org demo --stage discover
    python -m infra.outlook.run --org demo --stage fetch
    python -m infra.outlook.run --org demo --stage smoke
"""

from __future__ import annotations

import asyncio
import argparse
from pathlib import Path

from common.logger import get_logger
from common.org_config import load_org_config
from common.output_paths import KnowledgePaths, OutlookPaths
from pipeline.filter import run_outlook_filter, run_ms_teams_filter
from pipeline.person_registry import build_person_registry, merge_by_name, save_registry

log = get_logger("runners.outlook_smoke")


async def run_outlook_smoke_pipeline(
    *,
    org: str,
    output_dir: Path,
    raw_mail_path: Path | None = None,
    raw_teams_path: Path | None = None,
    source: str = "both",
    limit: int | None = None,
    allow_empty: bool = False,
    force_restart: bool = False,
    extract_limit: int | None = None,
    extract_all_fn=None,
) -> dict:
    """Run filter → registry → extract for an Outlook / Teams smoke test.

    Args:
        org:            Organisation slug.
        output_dir:     Root output directory (e.g. Path("output")).
        raw_mail_path:  Explicit path to Outlook Mail raw JSONL. When None,
                        falls back to OutlookPaths(org, output_dir).raw_mail_jsonl.
        raw_teams_path: Explicit path to Teams raw JSONL. When None, falls back
                        to OutlookPaths(org, output_dir).raw_teams_jsonl.
        source:         Which source(s) to process.
                        "outlook_mail" | "ms_teams" | "both" (default).
        limit:          Total thread limit passed to extract_all (both sources).
        allow_empty:    If True, do not raise when a stage produces 0 events.
        force_restart:  If True, ignore existing extraction checkpoints.
        extract_limit:  Per-source extraction limit (overrides limit when set).
        extract_all_fn: Injectable async function with the same signature as
                        pipeline.extraction.extract.extract_all. Set in tests
                        to avoid requiring an LLM API key.

    Returns:
        Summary dict with paths and counts for all completed stages.
    """
    if extract_all_fn is None:
        from pipeline.extraction.extract import extract_all as extract_all_fn

    output_dir = Path(output_dir)
    paths = OutlookPaths(org=org, data_dir=output_dir)
    paths.makedirs()

    config_dir = output_dir / "config"
    config_dir.mkdir(parents=True, exist_ok=True)

    run_mail = source in ("both", "outlook_mail")
    run_teams = source in ("both", "ms_teams")

    # ── Resolve raw input paths ───────────────────────────────────────────────
    mail_raw = Path(raw_mail_path) if raw_mail_path else paths.raw_mail_jsonl
    teams_raw = Path(raw_teams_path) if raw_teams_path else paths.raw_teams_jsonl

    # ── Step 1: Verify required raw JSONL files ───────────────────────────────
    if run_mail and not mail_raw.exists():
        raise FileNotFoundError(
            f"Outlook Mail raw JSONL not found: {mail_raw}\n"
            "Run '--stage fetch' first, or supply --raw-mail <path>."
        )
    if run_teams and not teams_raw.exists():
        raise FileNotFoundError(
            f"Teams raw JSONL not found: {teams_raw}\n"
            "Run '--stage fetch' first, or supply --raw-teams <path>."
        )

    # ── Step 2: Verify optional discovery artifacts (warn only) ──────────────
    identities_present = paths.identities_json.exists()
    scopes_present = paths.teams_scopes_json.exists()

    if not identities_present:
        log.warning(
            "MS365 identities artifact not found: %s — "
            "run '--stage discover' to generate it. Continuing without ACL enrichment.",
            paths.identities_json,
        )
    else:
        log.info("MS365 identities artifact: %s", paths.identities_json)

    if not scopes_present:
        log.warning(
            "Teams scope map artifact not found: %s — "
            "run '--stage discover' to generate it. Continuing without Teams ACL.",
            paths.teams_scopes_json,
        )
    else:
        log.info("Teams scope map artifact: %s", paths.teams_scopes_json)

    # ── Org config ────────────────────────────────────────────────────────────
    org_config = load_org_config(org, config_dir)
    org_domains = org_config.get("org_domains", [])
    functional = set(org_config.get("extra_functional_mailboxes", []))

    # ── Step 3: Filter — Outlook Mail ─────────────────────────────────────────
    filter_mail_stats: dict | None = None
    if run_mail:
        log.info("=" * 55)
        log.info("Step 3: Filtering Outlook Mail — %s", org)
        filter_mail_stats = run_outlook_filter(
            input_path=mail_raw,
            output_dir=output_dir,
            org=org,
            allow_empty=allow_empty,
        )

    # ── Step 4: Filter — Teams ────────────────────────────────────────────────
    filter_teams_stats: dict | None = None
    if run_teams:
        log.info("=" * 55)
        log.info("Step 4: Filtering Teams — %s", org)
        filter_teams_stats = run_ms_teams_filter(
            input_path=teams_raw,
            output_dir=output_dir,
            org=org,
            allow_empty=allow_empty,
        )

    # ── Step 5: Person registry ───────────────────────────────────────────────
    # Build from Outlook Mail filtered events only — mail actors carry email
    # addresses which drive identity resolution. Teams actors carry Azure AD
    # object IDs with no email field and are correctly excluded here; Teams
    # ACL is channel-scoped (scope_in_raw=True) and does not depend on the
    # person registry.
    log.info("=" * 55)
    log.info("Step 5: Building person registry — %s", org)

    registry_input = paths.filtered_mail_jsonl if run_mail else paths.filtered_teams_jsonl
    registry = build_person_registry(
        input_path=registry_input,
        org_domains=org_domains,
        functional_emails=functional,
    )

    # ── Step 6: Name merge ────────────────────────────────────────────────────
    before_merge = len(registry.get("persons", []))
    registry, merge_log = merge_by_name(registry, internal_domains=set(org_domains))
    log.info(
        "Registry: %d persons before merge, %d merge groups",
        before_merge, len(merge_log),
    )

    # ── Step 7: Save registry ─────────────────────────────────────────────────
    # Use outlook_mail source key for KnowledgePaths so registry goes into the
    # shared knowledge/registry/ directory alongside Gmail and Jira registries.
    reg_paths = KnowledgePaths(org=org, data_dir=output_dir, source="outlook_mail")
    reg_paths.makedirs()
    save_registry(registry, reg_paths.registry_dir, org)
    log.info("Registry saved → %s", reg_paths.registry_dir)

    # ── Step 8: Extract — Outlook Mail ────────────────────────────────────────
    effective_limit = extract_limit if extract_limit is not None else limit
    mail_triples_path: Path | None = None
    mail_checkpoint_path: Path | None = None

    if run_mail:
        log.info("=" * 55)
        log.info("Step 8: Extracting Outlook Mail triples — %s", org)
        mail_kpaths = KnowledgePaths(org=org, data_dir=output_dir, source="outlook_mail")
        mail_kpaths.makedirs()
        mail_checkpoint_path = (
            output_dir / "checkpoints"
            / f"{org}_outlook_mail_triple_extraction_checkpoint.json"
        )
        mail_checkpoint_path.parent.mkdir(parents=True, exist_ok=True)

        await extract_all_fn(
            filtered_path=paths.filtered_mail_jsonl,
            org=org,
            data_dir=output_dir,
            output_path=mail_kpaths.triples_raw,
            checkpoint_path=mail_checkpoint_path,
            limit=effective_limit,
            offset=0,
            thread_ids=None,
            allow_empty=allow_empty,
            force_restart=force_restart,
        )
        mail_triples_path = mail_kpaths.triples_raw

    # ── Step 9: Extract — Teams ───────────────────────────────────────────────
    teams_triples_path: Path | None = None
    teams_checkpoint_path: Path | None = None

    if run_teams:
        log.info("=" * 55)
        log.info("Step 9: Extracting Teams triples — %s", org)
        teams_kpaths = KnowledgePaths(org=org, data_dir=output_dir, source="ms_teams")
        teams_kpaths.makedirs()
        teams_checkpoint_path = (
            output_dir / "checkpoints"
            / f"{org}_ms_teams_triple_extraction_checkpoint.json"
        )
        teams_checkpoint_path.parent.mkdir(parents=True, exist_ok=True)

        await extract_all_fn(
            filtered_path=paths.filtered_teams_jsonl,
            org=org,
            data_dir=output_dir,
            output_path=teams_kpaths.triples_raw,
            checkpoint_path=teams_checkpoint_path,
            limit=effective_limit,
            offset=0,
            thread_ids=None,
            allow_empty=allow_empty,
            force_restart=force_restart,
        )
        teams_triples_path = teams_kpaths.triples_raw

    # ── Step 10: Summary ──────────────────────────────────────────────────────
    log.info("=" * 55)
    log.info("SMOKE PIPELINE COMPLETE — %s (source=%s)", org, source)
    if filter_mail_stats:
        log.info(
            "  Outlook Mail filter: %d kept / %d total (%.1f%% dropped)",
            filter_mail_stats.get("kept", 0),
            filter_mail_stats.get("total_input", 0),
            filter_mail_stats.get("drop_rate_pct", 0.0),
        )
    if filter_teams_stats:
        log.info(
            "  Teams filter:        %d kept / %d total (%.1f%% dropped)",
            filter_teams_stats.get("kept", 0),
            filter_teams_stats.get("total_input", 0),
            filter_teams_stats.get("drop_rate_pct", 0.0),
        )
    log.info(
        "  Registry:            %d persons (%d merge groups)",
        len(registry.get("persons", [])), len(merge_log),
    )
    log.info(
        "  Discovery artifacts: identities=%s scopes=%s",
        "present" if identities_present else "absent (warned)",
        "present" if scopes_present else "absent (warned)",
    )
    log.info("=" * 55)

    return {
        "org": org,
        "source": source,
        # Discovery artifact presence
        "identities_artifact_present": identities_present,
        "scopes_artifact_present": scopes_present,
        # Filter stats
        "filter_mail": filter_mail_stats,
        "filter_teams": filter_teams_stats,
        # Registry
        "registry_persons_before_merge": before_merge,
        "registry_merge_groups": len(merge_log),
        # Paths
        "filtered_mail_path": str(paths.filtered_mail_jsonl) if run_mail else None,
        "filtered_teams_path": str(paths.filtered_teams_jsonl) if run_teams else None,
        "triples_mail_path": str(mail_triples_path) if mail_triples_path else None,
        "triples_teams_path": str(teams_triples_path) if teams_triples_path else None,
        "identities_path": str(paths.identities_json),
        "scopes_path": str(paths.teams_scopes_json),
    }


def run(args: argparse.Namespace) -> None:
    """Entry point called by infra.outlook.run --stage smoke.

    Receives the full argparse.Namespace from run.py main() so all
    CLI arguments are available without additional plumbing.
    """
    asyncio.run(run_outlook_smoke_pipeline(
        org=args.org,
        output_dir=Path(args.output_dir),
        raw_mail_path=Path(getattr(args, "raw_mail", None) or "") or None,
        raw_teams_path=Path(getattr(args, "raw_teams", None) or "") or None,
        source=getattr(args, "source", "both") or "both",
        limit=int(args.limit) if getattr(args, "limit", None) else None,
        allow_empty=getattr(args, "allow_empty", False),
        force_restart=getattr(args, "force_restart", False),
    ))
