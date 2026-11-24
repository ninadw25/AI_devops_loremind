"""
runners/slack_apply_delta.py
────────────────────────────
W6 orchestrator: take a Slack reconcile delta (edits + deletes) and update the
knowledge graph incrementally — re-extract only the affected SESSIONS, diff
their node sets against the live graph artifact, and emit/apply a delta Cypher.

Pipeline:
  reconcile_delta_*.jsonl  ─┐
  current Slack events ─────┤→ affected sessions → re-extract (LLM) + validate
  prior triples_deduped ────┘→ plan_delta → emit_delta_cypher → write/apply

The re-extractor is a pluggable seam (`reextract_fn`) so the orchestration is
unit-testable with a fake, and the live LLM path is isolated in
`_default_reextract`. Bounded to the changed sessions → never O(history).

Run (live; needs NVIDIA + Neo4j):
    ../.venv/bin/python -m runners.slack_apply_delta --org techtest
    ../.venv/bin/python -m runners.slack_apply_delta --org techtest --apply
"""

from __future__ import annotations

import argparse
import glob
import json
import sys
from pathlib import Path
from typing import Callable, Optional

from common.logger import get_logger, setup_logging
from common.output_paths import KnowledgePaths
from builders.slack_delta import (
    affected_sessions_from_delta,
    emit_delta_cypher,
    plan_delta,
    write_delta_cypher,
)

log = get_logger("slack.apply_delta")

# A re-extractor maps {session_id → session thread} to {session_id → validated thread}.
ReextractFn = Callable[[dict, str, Path], dict]


def _latest_delta(org: str, data_dir: Path) -> Optional[Path]:
    matches = sorted(glob.glob(str(data_dir / "slack" / f"{org}_reconcile_delta_*.jsonl")))
    return Path(matches[-1]) if matches else None


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def _prior_triples(org: str, data_dir: Path) -> dict:
    paths = KnowledgePaths(org=org, data_dir=data_dir, source="slack")
    src = paths.triples_deduped if paths.triples_deduped.exists() else paths.triples_validated
    return json.loads(src.read_text(encoding="utf-8")) if src.exists() else {}


def _current_sessions(org: str, data_dir: Path) -> dict:
    """Sessionize the latest Slack events file (assumed current post-fetch)."""
    from pipeline.extraction.grouping import group_by_thread
    from pipeline.input_selection import latest_nonempty_path
    spec = str(data_dir / "filtered" / f"{org}_slack_*.jsonl")
    try:
        path = latest_nonempty_path(spec, label="slack events", logger=log)
    except FileNotFoundError:
        path = latest_nonempty_path(str(data_dir / "raw" / f"{org}_slack_*.jsonl"),
                                    label="slack events", logger=log)
    return group_by_thread(path)


def run_apply_delta(
    org: str,
    data_dir: Path,
    reextract_fn: ReextractFn,
    apply: bool = False,
) -> dict:
    """Compute + emit (optionally apply) the graph delta for a reconcile delta."""
    delta_path = _latest_delta(org, data_dir)
    if not delta_path:
        log.warning("no reconcile delta for %s — nothing to apply", org)
        return {"affected": 0, "soft_delete": 0, "upsert_sessions": 0}
    delta_records = _read_jsonl(delta_path)
    log.info("delta: %s (%d records)", delta_path.name, len(delta_records))

    sessions = _current_sessions(org, data_dir)
    affected_ids = affected_sessions_from_delta(delta_records, sessions)
    log.info("affected sessions: %d", len(affected_ids))
    if not affected_ids:
        return {"affected": 0, "soft_delete": 0, "upsert_sessions": 0}

    affected_threads = {sid: sessions[sid] for sid in affected_ids if sid in sessions}
    new = reextract_fn(affected_threads, org, data_dir)   # {sid → validated thread}

    prior = _prior_triples(org, data_dir)
    plan = plan_delta(prior, new, set(affected_ids))

    # name_to_pid for edge cypher (Person resolution), from the resolved registry.
    from pipeline.identity.registry_view import registry_source
    reg_path = registry_source(org, data_dir)
    name_to_pid: dict[str, str] = {}
    if reg_path.exists():
        reg = json.loads(reg_path.read_text(encoding="utf-8"))
        for p in reg.get("persons", []):
            for nm in [p.get("canonical_name", "")] + p.get("all_names", []):
                if nm:
                    name_to_pid[nm.lower()] = p["person_id"]

    cypher = emit_delta_cypher(plan, new, name_to_pid, org=org)
    out = write_delta_cypher(org, data_dir, cypher)

    summary = {"affected": len(affected_ids), **plan.summary(), "cypher": str(out)}
    if apply:
        from common.config import neo4j_credentials_for
        from builders.slack_delta import apply_delta_cypher
        uri, user, password = neo4j_credentials_for(org)
        n = apply_delta_cypher(out, uri, user, password)
        summary["applied_statements"] = n
        log.info("applied %d statements to Neo4j", n)
    log.info("delta summary: %s", summary)
    return summary


# ── default (live) re-extractor ─────────────────────────────────────────────
def _default_reextract(affected_threads: dict, org: str, data_dir: Path) -> dict:
    """Re-extract + validate the affected sessions with the real LLM engine.

    Isolated here so the orchestration above stays testable without creds.
    """
    import asyncio
    from openai import AsyncOpenAI  # noqa: F401  (type only; client from provider)

    from common.config import get_settings, validate_for
    from infra.llm import get_chat_provider
    from pipeline.extraction.extract import extract_one
    from pipeline.extraction.rate_limit import AsyncRateLimiter
    from pipeline.extraction.grouping import load_person_registry
    from pipeline.validation.name_lookup import build_name_lookup
    from pipeline.validation.thread import validate_thread
    from pipeline.validation.id_minting import _assign_node_ids
    from pipeline.identity.registry_view import registry_source

    validate_for("triple_extractor")
    settings = get_settings()
    provider = get_chat_provider(settings.llm_provider)
    model = settings.extraction_model if settings.llm_provider == "nvidia" else provider.default_model
    client = provider.make_async_client(max_retries=0, timeout=120.0)
    rate_limiter = AsyncRateLimiter(max_calls=38, period=60.0)
    semaphore = asyncio.Semaphore(4)

    email_to_person, persons_by_id = load_person_registry(org, data_dir)
    registry = json.loads(registry_source(org, data_dir).read_text(encoding="utf-8"))
    name_lookup = build_name_lookup(registry)

    async def _run() -> dict:
        tasks = [extract_one(client, model, semaphore, rate_limiter, sid, th,
                             email_to_person, persons_by_id)
                 for sid, th in affected_threads.items()]
        return dict(await asyncio.gather(*tasks))

    raw_results = asyncio.run(_run())

    new: dict = {}
    for sid, raw_result in raw_results.items():
        validated, _rejected = validate_thread(raw_result, name_lookup, persons_by_id)
        for triple in validated.get("triples", []):
            _assign_node_ids(triple, sid)
        # carry session scope/citation so the delta cypher can stamp scope_ids
        src = affected_threads.get(sid, {})
        for k in ("scope_id", "source_ref", "source_url", "extra_scope_ids",
                  "department_tags", "sensitivity", "sensitivity_score", "thread_id"):
            if src.get(k) is not None:
                validated[k] = src[k]
        new[sid] = validated
    return _jsonable(new)


def _jsonable(obj):
    """Launder through JSON exactly like the full pipeline's file write does.

    NodeType/Predicate are str-enums; the normal pipeline persists triples to
    JSON (where a str-enum serializes to its value, e.g. "Decision") and reads
    them back as plain strings. The delta path keeps triples in memory, so
    without this the enum object survives and the delta Cypher emits a broken
    label `MATCH (s:NodeType.DECISION …)` instead of `(s:Decision …)`.
    """
    return json.loads(json.dumps(obj))


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="W6: apply a Slack reconcile delta to the graph")
    ap.add_argument("--org", required=True)
    ap.add_argument("--data-dir", default="output", type=Path)
    ap.add_argument("--apply", action="store_true", help="apply the delta Cypher to Neo4j")
    args = ap.parse_args(argv)

    setup_logging(org=args.org, log_dir=str(args.data_dir / "logs"), source="slack.apply_delta")
    result = run_apply_delta(args.org, args.data_dir, _default_reextract, apply=args.apply)
    return 0 if result is not None else 1


if __name__ == "__main__":
    sys.exit(main())
