"""
runners.zoho_smoke
───────────────────
End-to-end Zoho smoke pipeline: filter → person registry → triple
extraction on already-fetched Zoho Mail and/or Cliq raw JSONL.

Why this lives in `runners/` and not `infra/zoho/`:
    This orchestrates Layer-2 pipeline stages (filter, person_registry,
    extraction). Connectors live in Layer 1 (`infra/`) and must not import
    upward into `pipeline/`. Keeping the smoke orchestration here preserves
    the one-way dependency rule (runners → pipeline → infra → common).

This is a developer convenience for exercising the Zoho path locally against
already-fetched data. It is invoked from `infra.zoho.run --stage smoke` via
a deferred import so the connector keeps no module-level dependency on the
pipeline.

Architecture contract (mirrors runners/outlook_smoke.py):
    - NEVER calls get_access_token(), zoho_get(), emit_zoho_identities(),
      or emit_cliq_scope_map(). Zero network operations.
    - Discovery artifacts (identities_json, cliq_scopes_json) are
      prerequisites produced by --stage discover. Smoke verifies their
      presence and logs a warning when absent — it does NOT generate them.
    - Required inputs: raw_mail_jsonl and/or raw_cliq_jsonl from ZohoPaths
      (or explicit overrides via raw_mail_path / raw_cliq_path). Missing
      required inputs raise FileNotFoundError immediately with a clear message.

Workflows:
    # Mock-data (no credentials needed)
    python -m infra.zoho.run --org demo --stage smoke

    # Real Zoho (run discover + fetch first)
    python -m infra.zoho.run --org demo --stage discover
    python -m infra.zoho.run --org demo --stage fetch
    python -m infra.zoho.run --org demo --stage smoke
"""

from __future__ import annotations

import asyncio
import argparse
from pathlib import Path

from common.logger import get_logger
from common.org_config import load_org_config
from common.output_paths import KnowledgePaths, ZohoPaths
from pipeline.filter import run_zoho_mail_filter, run_zoho_cliq_filter
from pipeline.person_registry import build_person_registry, merge_by_name, save_registry

log = get_logger("runners.zoho_smoke")


async def run_zoho_smoke_pipeline(
    *,
    org: str,
    output_dir: Path,
    raw_mail_path: Path | None = None,
    raw_cliq_path: Path | None = None,
    source: str = "both",
    limit: int | None = None,
    allow_empty: bool = False,
    force_restart: bool = False,
    extract_limit: int | None = None,
    extract_all_fn=None,
) -> dict:
    """Run filter → registry → extract for a Zoho Mail / Cliq smoke test.

    Args:
        org:            Organisation slug.
        output_dir:     Root output directory (e.g. Path("output")).
        raw_mail_path:  Explicit path to Zoho Mail raw JSONL. When None,
                        falls back to ZohoPaths(org, output_dir).raw_mail_jsonl.
        raw_cliq_path:  Explicit path to Cliq raw JSONL. When None, falls back
                        to ZohoPaths(org, output_dir).raw_cliq_jsonl.
        source:         Which source(s) to process.
                        "zoho_mail" | "zoho_cliq" | "both" (default).
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
    paths = ZohoPaths(org=org, data_dir=output_dir)
    paths.makedirs()

    config_dir = output_dir / "config"
    config_dir.mkdir(parents=True, exist_ok=True)

    run_mail = source in ("both", "zoho_mail")
    run_cliq = source in ("both", "zoho_cliq")

    # ── Resolve raw input paths ───────────────────────────────────────────────
    mail_raw = Path(raw_mail_path) if raw_mail_path else paths.raw_mail_jsonl
    cliq_raw = Path(raw_cliq_path) if raw_cliq_path else paths.raw_cliq_jsonl

    # ── Step 1: Verify required raw JSONL files ───────────────────────────────
    if run_mail and not mail_raw.exists():
        raise FileNotFoundError(
            f"Zoho Mail raw JSONL not found: {mail_raw}\n"
            "Run '--stage fetch' first, or supply --raw-mail <path>."
        )
    if run_cliq and not cliq_raw.exists():
        raise FileNotFoundError(
            f"Cliq raw JSONL not found: {cliq_raw}\n"
            "Run '--stage fetch' first, or supply --raw-cliq <path>."
        )

    # ── Step 2: Verify optional discovery artifacts (warn only) ──────────────
    identities_present = paths.identities_json.exists()
    scopes_present = paths.cliq_scopes_json.exists()

    if not identities_present:
        log.warning(
            "Zoho identities artifact not found: %s — "
            "run '--stage discover' to generate it. Continuing without ACL enrichment.",
            paths.identities_json,
        )
    else:
        log.info("Zoho identities artifact: %s", paths.identities_json)

    if not scopes_present:
        log.warning(
            "Cliq scope map artifact not found: %s — "
            "run '--stage discover' to generate it. Continuing without Cliq ACL.",
            paths.cliq_scopes_json,
        )
    else:
        log.info("Cliq scope map artifact: %s", paths.cliq_scopes_json)

    # ── Org config ────────────────────────────────────────────────────────────
    org_config = load_org_config(org, config_dir)
    org_domains = org_config.get("org_domains", [])
    functional = set(org_config.get("extra_functional_mailboxes", []))

    # ── Step 3: Filter — Zoho Mail ─────────────────────────────────────────────
    filter_mail_stats: dict | None = None
    if run_mail:
        log.info("=" * 55)
        log.info("Step 3: Filtering Zoho Mail — %s", org)
        filter_mail_stats = run_zoho_mail_filter(
            input_path=mail_raw,
            output_dir=output_dir,
            org=org,
            allow_empty=allow_empty,
        )

    # ── Step 4: Filter — Cliq ─────────────────────────────────────────────────
    filter_cliq_stats: dict | None = None
    if run_cliq:
        log.info("=" * 55)
        log.info("Step 4: Filtering Cliq — %s", org)
        filter_cliq_stats = run_zoho_cliq_filter(
            input_path=cliq_raw,
            output_dir=output_dir,
            org=org,
            allow_empty=allow_empty,
        )

    # ── Step 5: Person registry ───────────────────────────────────────────────
    # Build from Zoho Mail filtered events only — mail actors carry email
    # addresses which drive identity resolution. Cliq actors carry ZUIDs with
    # no email field and are correctly excluded here; Cliq ACL is
    # channel-scoped (scope_in_raw=True) and does not depend on the person
    # registry.
    log.info("=" * 55)
    log.info("Step 5: Building person registry — %s", org)

    registry_input = paths.filtered_mail_jsonl if run_mail else paths.filtered_cliq_jsonl
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
    # Use zoho_mail source key for KnowledgePaths so registry goes into the
    # shared knowledge/registry/ directory alongside Gmail, Jira, Outlook.
    reg_paths = KnowledgePaths(org=org, data_dir=output_dir, source="zoho_mail")
    reg_paths.makedirs()
    save_registry(registry, reg_paths.registry_dir, org)
    log.info("Registry saved → %s", reg_paths.registry_dir)

    # ── Step 8: Extract — Zoho Mail ───────────────────────────────────────────
    effective_limit = extract_limit if extract_limit is not None else limit
    mail_triples_path: Path | None = None
    mail_checkpoint_path: Path | None = None

    if run_mail:
        log.info("=" * 55)
        log.info("Step 8: Extracting Zoho Mail triples — %s", org)
        mail_kpaths = KnowledgePaths(org=org, data_dir=output_dir, source="zoho_mail")
        mail_kpaths.makedirs()
        mail_checkpoint_path = (
            output_dir / "checkpoints"
            / f"{org}_zoho_mail_triple_extraction_checkpoint.json"
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

    # ── Step 9: Extract — Cliq ────────────────────────────────────────────────
    cliq_triples_path: Path | None = None
    cliq_checkpoint_path: Path | None = None

    if run_cliq:
        log.info("=" * 55)
        log.info("Step 9: Extracting Cliq triples — %s", org)
        cliq_kpaths = KnowledgePaths(org=org, data_dir=output_dir, source="zoho_cliq")
        cliq_kpaths.makedirs()
        cliq_checkpoint_path = (
            output_dir / "checkpoints"
            / f"{org}_zoho_cliq_triple_extraction_checkpoint.json"
        )
        cliq_checkpoint_path.parent.mkdir(parents=True, exist_ok=True)

        await extract_all_fn(
            filtered_path=paths.filtered_cliq_jsonl,
            org=org,
            data_dir=output_dir,
            output_path=cliq_kpaths.triples_raw,
            checkpoint_path=cliq_checkpoint_path,
            limit=effective_limit,
            offset=0,
            thread_ids=None,
            allow_empty=allow_empty,
            force_restart=force_restart,
        )
        cliq_triples_path = cliq_kpaths.triples_raw

    # ── Step 10: Summary ──────────────────────────────────────────────────────
    log.info("=" * 55)
    log.info("SMOKE PIPELINE COMPLETE — %s (source=%s)", org, source)
    if filter_mail_stats:
        log.info(
            "  Zoho Mail filter: %d kept / %d total (%.1f%% dropped)",
            filter_mail_stats.get("kept", 0),
            filter_mail_stats.get("total_input", 0),
            filter_mail_stats.get("drop_rate_pct", 0.0),
        )
    if filter_cliq_stats:
        log.info(
            "  Cliq filter:      %d kept / %d total (%.1f%% dropped)",
            filter_cliq_stats.get("kept", 0),
            filter_cliq_stats.get("total_input", 0),
            filter_cliq_stats.get("drop_rate_pct", 0.0),
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
        "filter_cliq": filter_cliq_stats,
        # Registry
        "registry_persons_before_merge": before_merge,
        "registry_merge_groups": len(merge_log),
        # Paths
        "filtered_mail_path": str(paths.filtered_mail_jsonl) if run_mail else None,
        "filtered_cliq_path": str(paths.filtered_cliq_jsonl) if run_cliq else None,
        "triples_mail_path": str(mail_triples_path) if mail_triples_path else None,
        "triples_cliq_path": str(cliq_triples_path) if cliq_triples_path else None,
        "identities_path": str(paths.identities_json),
        "scopes_path": str(paths.cliq_scopes_json),
    }


def run(args: argparse.Namespace) -> None:
    """Entry point called by infra.zoho.run --stage smoke.

    Receives the full argparse.Namespace from run.py main() so all
    CLI arguments are available without additional plumbing.
    """
    asyncio.run(run_zoho_smoke_pipeline(
        org=args.org,
        output_dir=Path(args.output_dir),
        raw_mail_path=Path(getattr(args, "raw_mail", None) or "") or None,
        raw_cliq_path=Path(getattr(args, "raw_cliq", None) or "") or None,
        source=getattr(args, "source", "both") or "both",
        limit=int(args.limit) if getattr(args, "limit", None) else None,
        allow_empty=getattr(args, "allow_empty", False),
        force_restart=getattr(args, "force_restart", False),
    ))
