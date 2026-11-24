"""
runners.jira_smoke
──────────────────
End-to-end Jira smoke pipeline: filter → person registry → triple
extraction on an already-fetched Jira raw JSONL.

Why this lives in `runners/` and not `infra/jira/`:
    This orchestrates Layer-2 pipeline stages (filter, person_registry,
    extraction). Connectors live in Layer 1 (`infra/`) and must not import
    upward into `pipeline/`. Keeping the smoke orchestration here preserves
    the one-way dependency rule (runners → pipeline → infra → common).

This is a developer convenience for exercising the Jira path locally; the
production path is `runners.sync` (Gmail) and its future multi-source
extension. It is invoked from `infra.jira.run --stage smoke` via a deferred
import so the connector keeps no module-level dependency on the pipeline.
"""

from __future__ import annotations

from pathlib import Path

from common.logger import get_logger
from common.org_config import load_org_config
from common.output_paths import KnowledgePaths
from pipeline.filter import run_jira_filter
from pipeline.jira.identity import emit_identities, emit_person_scope_map
from pipeline.person_registry import build_person_registry, merge_by_name, save_registry

log = get_logger("runners.jira_smoke")


async def run_jira_smoke_pipeline(
    *,
    org: str,
    output_dir: Path,
    raw_path: Path,
    limit: int | None = None,
    allow_empty: bool = False,
    force_restart: bool = False,
    extract_limit: int | None = None,
    extract_all_fn=None,
) -> dict:
    """Run filter -> registry -> extract for a Jira smoke test."""
    if extract_all_fn is None:
        from pipeline.extraction.extract import extract_all as extract_all_fn

    filtered_path = output_dir / "filtered" / f"{org}_jira_filtered.jsonl"
    config_dir = output_dir / "config"
    config_dir.mkdir(parents=True, exist_ok=True)

    # P5 — emit identity facts + pre-computed person→scope map.
    # Both run before the filter stage so raw event actors are captured.
    identity_path = emit_identities(org=org, data_dir=output_dir, raw_path=raw_path)
    log.info("Jira identities written to %s", identity_path)
    scope_map_path = emit_person_scope_map(org=org, data_dir=output_dir)
    log.info("Jira person scope map written to %s", scope_map_path)

    filter_stats = run_jira_filter(
        input_path=raw_path,
        output_dir=output_dir,
        org=org,
        allow_empty=allow_empty,
    )

    org_config = load_org_config(org, config_dir)
    org_domains = org_config.get("org_domains", [])
    functional = set(org_config.get("extra_functional_mailboxes", []))

    registry = build_person_registry(
        input_path=filtered_path,
        org_domains=org_domains,
        functional_emails=functional,
    )
    before_merge = len(registry.get("persons", []))
    registry, merge_log = merge_by_name(registry, internal_domains=set(org_domains))

    paths = KnowledgePaths(org=org, data_dir=output_dir, source="jira")
    paths.makedirs()
    save_registry(registry, paths.registry_dir, org)
    checkpoint_path = output_dir / "checkpoints" / f"{org}_jira_triple_extraction_checkpoint.json"
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)

    effective_limit = extract_limit if extract_limit is not None else limit
    await extract_all_fn(
        filtered_path=filtered_path,
        org=org,
        data_dir=output_dir,
        output_path=paths.triples_raw,
        checkpoint_path=checkpoint_path,
        limit=effective_limit,
        offset=0,
        thread_ids=None,
        allow_empty=allow_empty,
        force_restart=force_restart,
    )

    return {
        "filter": filter_stats,
        "registry_persons_before_merge": before_merge,
        "registry_merge_groups": len(merge_log),
        "filtered_path": str(filtered_path),
        "checkpoint_path": str(checkpoint_path),
        "triples_path": str(paths.triples_raw),
        "identity_path": str(identity_path),
        "scope_map_path": str(scope_map_path),
    }
