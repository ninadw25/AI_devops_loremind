"""
pipeline/identity/registry_view.py
──────────────────────────────────
Bridge: centralized identity (identity.json) → the legacy person_registry shape
that the validator (`validation/run.py`) and extractor
(`extraction/grouping.py:load_person_registry`) already consume.

Why a bridge instead of rewiring both consumers: the validator and extractor each
load a `{"persons": [...], "email_to_person_id": {...}}` dict from disk, and the
validator even writes `person:manual` fallbacks back to it. Producing that exact
shape from identity.json is the smallest, lowest-risk cutover — the consumers
barely change, and the Gmail-only path is untouched (no identity.json ⇒ fall back
to the Gmail person_registry).

Flow:
    pipeline.identity.resolve  → identity.json + identity_review.json
                               → write_resolved_registry() → resolved_registry.json
    validator / extractor      → registry_source() picks resolved_registry.json
                                 when present, else the Gmail person_registry.json

⚠️ UNTESTED end-to-end against a real org. The Grid (paid) path is wired but cannot
be exercised without a Grid token (see docs/slack/SLACK_PIPELINE.md §0.1, W4).
Validated only structurally here + on whatever identity.json a run produces.
"""

from __future__ import annotations

import json
from pathlib import Path

from common.logger import get_logger
from common.output_paths import IdentityPaths, KnowledgePaths

log = get_logger("identity.registry_view")

_ZERO_STATS = {
    "emails_sent": 0, "emails_received": 0, "total_activity": 0,
    "threads_participated": 0, "unique_contacts": 0,
}


def identity_to_registry(artifact: dict) -> dict:
    """Convert an identity.json artifact to the legacy person_registry dict.

    Legacy persons use `all_names` / `all_emails` / `is_functional` / `stats`;
    identity persons use `names` / `emails` / `kind`. `email_to_person_id` is
    rebuilt from the alias_table so the extractor's actor→person lookup keeps
    working for every source (email:, slack:U…, jira:U…).
    """
    persons_out: list[dict] = []
    for p in artifact.get("persons", []):
        if p.get("merged_into"):
            continue
        emails = list(p.get("emails", []) or [])
        kind = p.get("kind", "")
        persons_out.append({
            "person_id":      p["person_id"],
            "canonical_name": p.get("canonical_name", ""),
            "primary_email":  emails[0] if emails else "",
            "all_emails":     emails,
            "all_names":      list(p.get("names", []) or []),
            "is_internal":    bool(p.get("is_internal")),
            "is_functional":  kind == "functional",
            "kind":           kind,
            "domains":        sorted({e.split("@", 1)[1] for e in emails if "@" in e}),
            # No per-message activity counts in identity.json — org-context ranking
            # (build_org_context) degrades to "no volume signal" on this path. TODO:
            # carry Gmail stats through the resolver if richer org-context is needed.
            "stats":          dict(_ZERO_STATS),
            "sources":        dict(p.get("sources", {}) or {}),
            "native_keys":    list(p.get("native_keys", []) or []),
        })

    # actor → person_id. The extractor lowercases the actor string before lookup,
    # so store both the canonical and a lowercased variant of every key.
    e2p: dict[str, str] = {}
    for alias_key, pid in artifact.get("alias_table", {}).items():
        if not alias_key or not pid:
            continue
        if alias_key.startswith("email:"):
            bare = alias_key[len("email:"):]
            for k in (bare, bare.lower(), alias_key, alias_key.lower()):
                e2p[k] = pid
        else:  # slack:U… / jira:U… / future source keys
            for k in (alias_key, alias_key.lower()):
                e2p[k] = pid

    return {
        "_source": "centralized_identity",
        "persons": persons_out,
        "email_to_person_id": e2p,
    }


def write_resolved_registry(org: str, data_dir: Path) -> Path | None:
    """Read identity.json, write resolved_registry.json (legacy shape). Returns the
    path written, or None when there's no identity.json (nothing to bridge)."""
    ipaths = IdentityPaths(org, data_dir)
    if not ipaths.identity.exists():
        log.info("no identity.json for %s — resolved_registry not written", org)
        return None
    artifact = json.loads(ipaths.identity.read_text(encoding="utf-8"))
    registry = identity_to_registry(artifact)
    ipaths.makedirs()
    ipaths.resolved_registry.write_text(
        json.dumps(registry, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("wrote resolved registry: %s (%d persons, %d alias keys)",
             ipaths.resolved_registry, len(registry["persons"]),
             len(registry["email_to_person_id"]))
    return ipaths.resolved_registry


def registry_source(org: str, data_dir: Path) -> Path:
    """The registry file the validator + extractor should read.

    Prefer the centralized resolved registry; fall back to the Gmail-built
    person_registry when no identity run has happened (keeps the Gmail-only path
    behaving exactly as before)."""
    resolved = IdentityPaths(org, data_dir).resolved_registry
    if resolved.exists():
        return resolved
    return KnowledgePaths(org, data_dir).person_registry
