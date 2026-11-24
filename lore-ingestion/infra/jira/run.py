"""Top-level Jira connector runner for lore-ingestion."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path

try:
    from dotenv import dotenv_values, load_dotenv

    _ENV_PATH = Path(__file__).parents[2] / ".env"
    # override=False: env vars injected by the caller (client-admin gateway sets
    # JIRA_BASE_URL/JIRA_EMAIL/JIRA_API_TOKEN/NVIDIA_API_KEY per-org) MUST win
    # over the local .env file, which only holds dev placeholders.
    load_dotenv(_ENV_PATH, override=False)
    try:
        _env_values = dotenv_values(_ENV_PATH)
        for _key in ("NVIDIA_API_KEY", "JIRA_BASE_URL", "JIRA_EMAIL", "JIRA_API_TOKEN"):
            _value = (_env_values.get(_key) or "").strip()
            if _value:
                os.environ.setdefault(_key, _value)  # fill gaps only; never clobber injected
    except Exception:
        pass
except Exception:
    pass

from common import scopes
from common.config import get_settings
from common.logger import get_logger, setup_logging
from common.org_config import load_org_config
from common.output_paths import JiraPaths
from common.stage_gate import check_nonempty
from common.stage_summary import log_summary
from infra.jira.discovery import append_dynamic_issue_scope_rows, run_discovery
from infra.jira.fetcher import JiraFetcher
from infra.jira.metrics import (
    JIRA_ISSUES_FETCHED,
    JIRA_EVENTS_EMITTED,
    JIRA_PROJECTS_CRAWLED,
)
from infra.jira.normalizer import normalize_comment, normalize_issue, normalize_changelog

log = get_logger("jira.run")


async def _normalize_issue_bundle(fetcher: JiraFetcher, issue: dict, org: str, base_url: str, field_map: dict | None = None) -> list[dict]:
    enriched_issue = dict(issue)
    if not enriched_issue.get("changelog") and issue.get("key"):
        try:
            changelog = await fetcher.fetch_issue_changelog(issue.get("key"))
            if isinstance(changelog, dict):
                enriched_issue["changelog"] = changelog
        except Exception:
            pass

    return normalize_issue(enriched_issue, org=org, base_url=base_url, field_map=field_map)


async def async_main() -> None:
    _s = get_settings()
    default_base_url = _s.jira_base_url
    default_admin_email = _s.jira_email
    default_api_token = _s.jira_api_token

    parser = argparse.ArgumentParser(description="Jira connector runner")
    parser.add_argument("--org", required=True)
    parser.add_argument("--base-url", default=default_base_url)
    parser.add_argument("--admin-email", default=default_admin_email)
    parser.add_argument("--api-token", default=default_api_token)
    parser.add_argument("--output-dir", default="output")
    parser.add_argument("--stage", choices=["discovery", "full", "smoke"], default="full")
    parser.add_argument("--limit", type=int, default=0, help="Optional cap on issues to fetch")
    parser.add_argument("--extract-limit", type=int, default=None,
                        help="Optional cap on threads to extract during smoke runs")
    parser.add_argument("--dry-run", action="store_true", help="Normalize but do not write JSONL")
    parser.add_argument("--allow-empty", action="store_true",
                        help="Allow empty downstream stages during smoke runs")
    parser.add_argument("--force-restart", action="store_true",
                        help="Force triple extraction to restart from scratch")
    parser.add_argument("--mock-mode", action="store_true",
                        help="Use mock data from seed_data.json instead of real API")
    parser.add_argument("--mock-seed", default=None,
                        help="Path to seed_data.json (default: mock_testing/seed_data.json)")
    args = parser.parse_args()

    if not args.mock_mode and not args.base_url:
        raise SystemExit("--base-url or JIRA_BASE_URL is required")

    setup_logging(org=args.org, log_dir=str(Path(args.output_dir) / "jira" / "logs"), source="jira")

    paths = JiraPaths(args.org, data_dir=Path(args.output_dir))
    paths.makedirs()

    # Use mock fetcher if --mock-mode is set
    if args.mock_mode:
        from .mock_fetcher import MockJiraFetcher
        seed_path = Path(args.mock_seed) if args.mock_seed else Path(__file__).parents[2] / "mock_testing" / "seed_data.json"
        log.info("Mock mode enabled — loading seed data from %s", seed_path)
        fetcher = MockJiraFetcher(seed_path=seed_path, base_url=args.base_url or "http://localhost:8091")
    else:
        fetcher = JiraFetcher(base_url=args.base_url, admin_email=args.admin_email, api_token=args.api_token)
    
    try:
        if not args.mock_mode:
            try:
                me = await fetcher.get("/rest/api/3/myself")
                if not isinstance(me, dict) or not me.get("accountId"):
                    raise ValueError("unexpected response")
                log.info("Jira credentials valid — authenticated as %s (%s)",
                         me.get("displayName", "unknown"), me.get("emailAddress", ""))
            except Exception as exc:
                raise SystemExit(
                    f"Jira credentials invalid or token expired: {exc}\n"
                    "Regenerate your API token at "
                    "https://id.atlassian.com/manage-profile/security/api-tokens "
                    "and update JIRA_API_TOKEN in .env"
                )
        else:
            log.info("Jira mock mode — skipping auth check")

        if args.stage == "discovery":
            disc_artifact = await run_discovery(args.org, fetcher, output_dir=Path(args.output_dir))
            scope_count = len(disc_artifact.get("scopes", [])) if isinstance(disc_artifact, dict) else 0
            principal_count = len(disc_artifact.get("scope_principals", [])) if isinstance(disc_artifact, dict) else 0
            log_summary(
                "jira_discovery",
                org=args.org,
                scopes=scope_count,
                scope_principals=principal_count,
                output=str(paths.scope_principals),
            )
            # 0 scopes means discovery found no projects — an empty ACL artifact
            # would silently deny everyone downstream. Fail unless intended.
            check_nonempty(
                "jira_discovery",
                count=scope_count,
                what="ACL scopes discovered",
                allow_empty=args.allow_empty,
            )
            return

        artifact = await run_discovery(args.org, fetcher, output_dir=Path(args.output_dir))

        acl_incomplete = artifact.get("acl_incomplete_projects", [])
        if acl_incomplete:
            log.warning(
                "%d project(s) have INCOMPLETE ACLs (permission scheme unreadable): %s. "
                "Their issues — if any are ingested — will be denied to ALL users by the "
                "query-time gate (fail-closed). Fix browse access and re-run discovery.",
                len(acl_incomplete),
                ", ".join(f"{p['project_key']} ({p['reason']})" for p in acl_incomplete),
            )

        config_dir = Path(args.output_dir) / "config"
        config_dir.mkdir(parents=True, exist_ok=True)
        org_config_data = load_org_config(args.org, config_dir)
        whitelist: set[str] = set(org_config_data.get("jira_projects", []))

        project_keys = [
            s["scope_id"].split("jira:project:")[-1]
            for s in artifact.get("scopes", [])
            if s.get("kind") == "project" and s.get("scope_id", "").startswith("jira:project:")
        ]
        total_discovered = len(project_keys)
        if whitelist:
            project_keys = [k for k in project_keys if k in whitelist]
            log.info(
                "Project whitelist active — ingesting %d of %d discovered projects: %s",
                len(project_keys), total_discovered, ", ".join(sorted(project_keys)),
            )

        JIRA_PROJECTS_CRAWLED.labels(org=args.org).set(len(project_keys))

        if project_keys:
            keys_list = ", ".join(project_keys)
            jql = f"project in ({keys_list}) ORDER BY created ASC"
        else:
            jql = "project is not EMPTY ORDER BY created ASC"

        issues: list[dict] = []
        # seen_issue_ids deduplicates within a single run (Jira pagination can
        # return the same issue on adjacent pages during concurrent edits).
        # At 500k+ issues swap this set for a Bloom filter to cut RAM usage.
        seen_issue_ids: set[str] = set()
        start_at = 0
        next_page_token: str | None = None
        max_results = 100

        while True:
            resp = await fetcher.fetch_issues(
                jql=jql,
                start_at=start_at,
                max_results=max_results,
                fields="summary,description,project,reporter,assignee,security,comment,issuetype,status,updated,created",
                expand="changelog",
                next_page_token=next_page_token,
            )
            if not isinstance(resp, dict):
                break
            batch = resp.get("issues", [])
            if not batch:
                break
            for issue in batch:
                key = issue.get("key")
                if key and key not in seen_issue_ids:
                    seen_issue_ids.add(key)
                    issues.append(issue)
            if args.limit and len(issues) >= args.limit:
                issues = issues[: args.limit]
                break
            next_page_token = resp.get("nextPageToken")
            if resp.get("isLast") or not next_page_token:
                break
            start_at += len(batch)

        run_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        raw_path = paths.raw_events(run_date)

        field_map = fetcher.discover_fields(org=args.org)

        # Known security-level scopes from discovery. An issue referencing a
        # seclevel NOT in this set (e.g. a level created after discovery ran)
        # would get an extra_scope_id pointing at a non-existent scope — the
        # query-time gate then denies EVERYONE with no error (silent data loss).
        # Warn loudly so the operator re-runs `--stage discovery`.
        known_seclevel_scopes = {
            s.get("scope_id")
            for s in artifact.get("scopes", [])
            if s.get("kind") == "security_level"
        }
        unknown_seclevels: set[str] = set()

        all_events: list[dict] = []
        for issue in issues:
            all_events.extend(await _normalize_issue_bundle(fetcher, issue, args.org, args.base_url, field_map=field_map))
            append_dynamic_issue_scope_rows(artifact, issue)

            security = ((issue.get("fields") or {}).get("security")) or {}
            sec_id = security.get("id")
            if sec_id:
                sec_scope = scopes.jira_seclevel(sec_id)
                if sec_scope not in known_seclevel_scopes:
                    unknown_seclevels.add(sec_scope)

        if unknown_seclevels:
            log.warning(
                "%d issue security level(s) referenced by issues are MISSING from the "
                "discovery artifact: %s. Items with these levels will be denied to ALL "
                "users by the query-time gate. Re-run `--stage discovery` to capture "
                "security schemes created since the last crawl.",
                len(unknown_seclevels), ", ".join(sorted(unknown_seclevels)),
            )

        JIRA_ISSUES_FETCHED.labels(org=args.org).inc(len(issues))
        JIRA_EVENTS_EMITTED.labels(org=args.org).inc(len(all_events))

        if not args.dry_run:
            raw_path.write_text("\n".join(json.dumps(ev, ensure_ascii=False) for ev in all_events) + ("\n" if all_events else ""), encoding="utf-8")
            log.info("Wrote %d Jira events to %s", len(all_events), raw_path)
            # Atomic rewrite (tmp + replace): this is the authoritative artifact,
            # superseding discovery's initial write with the dynamic reporter/
            # assignee grants. A non-atomic write here could corrupt the ACL
            # artifact mid-write and silently break the query-time gate.
            _scope_tmp = paths.scope_principals.with_suffix(".json.tmp")
            _scope_tmp.write_text(json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8")
            _scope_tmp.replace(paths.scope_principals)
            log.info("Updated Jira ACL artifact with dynamic issue grants -> %s", paths.scope_principals)

            meta = {
                "org": args.org,
                "run_ts": datetime.now(timezone.utc).isoformat(),
                "run_date": run_date,
                "issues_fetched": len(issues),
                "events_emitted": len(all_events),
                "projects": project_keys,
                "whitelist_active": bool(whitelist),
                "overflows": [],
                "acl_incomplete_projects": acl_incomplete,
            }
            tmp = paths.jira_meta.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
            tmp.replace(paths.jira_meta)
            log.info("Wrote run health summary -> %s", paths.jira_meta)
        else:
            log.info("Dry run complete; normalized %d Jira events (not written)", len(all_events))

        log.info("Discovery artifact: %s", paths.scope_principals)
        log.info("Artifact keys: %s", ", ".join(sorted(artifact.keys())))

        log_summary(
            "jira_fetch",
            org=args.org,
            issues_fetched=len(issues),
            events_emitted=len(all_events),
            projects=len(project_keys),
            unknown_seclevels=len(unknown_seclevels),
            output=str(raw_path) if not args.dry_run else None,
        )
        # 0 events emitted from a non-empty issue set is almost always an
        # upstream fetch/normalize bug; fail loudly before it cascades.
        check_nonempty(
            "jira_fetch",
            count=len(all_events),
            what="events emitted",
            allow_empty=args.allow_empty or args.dry_run,
        )

        if args.stage == "smoke":
            if args.dry_run:
                log.warning("--dry-run is ignored for smoke runs because downstream stages need persisted JSONL")
            # Deferred import: the end-to-end smoke pipeline lives in runners/
            # (Layer 3) because it orchestrates pipeline stages. Importing it
            # lazily here keeps this connector free of any module-level
            # dependency on pipeline/ (the one-way layering rule).
            from runners.jira_smoke import run_jira_smoke_pipeline
            smoke_stats = await run_jira_smoke_pipeline(
                org=args.org,
                output_dir=Path(args.output_dir),
                raw_path=raw_path,
                limit=args.limit or None,
                allow_empty=args.allow_empty,
                force_restart=args.force_restart,
                extract_limit=args.extract_limit,
            )
            log.info("Smoke pipeline complete: %s", smoke_stats)
            return
    finally:
        fetcher.close()


def main() -> None:
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
