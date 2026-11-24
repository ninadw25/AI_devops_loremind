"""
pipeline/identity/resolve.py
────────────────────────────
The centralized identity stage (CENTRALIZED_IDENTITY.md §7). Wires the per-source adapters
into the resolver and writes the two deliverables:

    output/identity/{org}_identity.json   persons[] + alias_table{}  → Postgres person/person_alias
    output/identity/{org}_identity_review.json  conflicts + Tier-2b suggestions for an operator

Run:
    ../.venv/bin/python -m pipeline.identity.resolve --org ezobooks
    ../.venv/bin/python -m pipeline.identity.resolve --org techtest --sources slack,jira

Ordering (matters): directory-first, emails-first — so the internal roster is fully seeded
before an email-less actor (Jira) tries to Tier-2a match against it.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from common.logger import get_logger, setup_logging
from common.org_config import load_org_config
from common.output_paths import IdentityPaths
from pipeline.identity.source_identity import ADAPTERS, SourceIdentity
from pipeline.identity.resolver import IdentityStore

log = get_logger("identity.resolve")

# directory-first: a source's directory seeds the roster before later sources resolve.
SOURCE_ORDER = {"gmail": 0, "slack": 1, "jira": 2, "ms365": 3}


def _ordering_key(si: SourceIdentity) -> tuple:
    # within the run: directory source first, then emails-bearing before email-less,
    # so the roster is populated before any Tier-2a name match fires.
    return (SOURCE_ORDER.get(si.source, 9), 0 if si.emails else 1)


def build(org: str, data_dir: Path, config_dir: Path, sources: list[str],
          seed: dict | None = None) -> IdentityStore:
    org_domains = {d.lower() for d in load_org_config(org, config_dir).get("org_domains", [])}
    if not org_domains:
        log.warning("no org_domains for %s — internal classification leans on directory presence", org)

    identities: list[SourceIdentity] = []
    for src in sources:
        adapter = ADAPTERS.get(src)
        if not adapter:
            log.warning("unknown source %r — skipping", src)
            continue
        identities.extend(adapter(org, data_dir, org_domains))

    identities.sort(key=_ordering_key)

    store = IdentityStore.from_artifact(seed) if seed else IdentityStore(org=org)
    if seed:
        store.org = org
    resolved = 0
    for si in identities:
        if store.resolve(si) is not None:
            resolved += 1
    log.info("resolved %d/%d source identities", resolved, len(identities))
    return store


def _summary(store: IdentityStore) -> None:
    art = store.to_artifact()
    c = art["counts"]
    log.info("── Centralized identity ───────────────────────────────────")
    log.info("persons:        %d   %s", c["persons"], c["by_kind"])
    log.info("alias keys:     %d", c["alias_keys"])
    log.info("merges:         %d", c["merges"])
    # cross-source coverage
    persons = art["persons"]
    multi = [p for p in persons if len([s for s in p["sources"].values() if s]) > 1]
    log.info("multi-source persons (linked across ≥2 sources): %d", len(multi))
    for p in multi[:15]:
        srcs = ",".join(s for s, v in p["sources"].items() if v)
        log.info("    %-16s %-22s [%s]", p["person_id"], (p["canonical_name"] or "?")[:22], srcs)
    if store.review:
        from collections import Counter
        log.info("review queue: %d  %s", len(store.review),
                 dict(Counter(r["type"] for r in store.review)))


def main() -> None:
    ap = argparse.ArgumentParser(description="Centralized cross-source identity resolution")
    ap.add_argument("--org", required=True)
    ap.add_argument("--data-dir", default="output", type=Path)
    ap.add_argument("--config-dir", default="output/config", type=Path)
    ap.add_argument("--sources", default="gmail,slack,jira,ms365",
                    help="comma list, in resolution order (default gmail,slack,jira,ms365)")
    ap.add_argument("--seed", type=Path, default=None,
                    help="prior identity.json to seed from (keeps person_ids stable across runs)")
    args = ap.parse_args()
    setup_logging()

    seed = None
    if args.seed and args.seed.exists():
        seed = json.loads(args.seed.read_text(encoding="utf-8"))
        log.info("seeding from %s", args.seed)

    sources = [s.strip() for s in args.sources.split(",") if s.strip()]
    store = build(args.org, args.data_dir, args.config_dir, sources, seed)

    paths = IdentityPaths(args.org, args.data_dir)
    paths.makedirs()
    paths.identity.write_text(json.dumps(store.to_artifact(), indent=2, ensure_ascii=False),
                              encoding="utf-8")
    paths.review.write_text(json.dumps({"review": store.review}, indent=2, ensure_ascii=False),
                            encoding="utf-8")
    # Bridge into the legacy registry shape the validator + extractor consume.
    from pipeline.identity.registry_view import write_resolved_registry
    write_resolved_registry(args.org, args.data_dir)
    _summary(store)
    log.info("wrote %s and %s", paths.identity, paths.review)


if __name__ == "__main__":
    main()
