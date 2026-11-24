"""
infra/slack/discovery.py
Stage 0 for Slack ACL discovery.

Builds channel scopes and principal mappings, then writes:
- output/slack/{org}_scope_principals.json
- output/cypher/{org}_slack_acl.cypher
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from common import scopes as scope_ids   # aliased: `scopes` is a local var name here
from common.config import get_settings
from common.logger import get_logger, setup_logging
from common.output_paths import SlackPaths
from .fetcher import SlackFetcher, SLACK_BASE_URL
from .admin import SlackAdminClient, build_grid_directory
from .capabilities import AclPathway
from .discovery_api import SlackDiscoveryClient

log = get_logger("slack.discovery")


@dataclass(frozen=True)
class ScopePrincipalArtifact:
    scopes: list[dict]
    principals: list[dict]
    scope_principals: list[dict]

    def to_dict(self) -> dict:
        return {
            "scopes": self.scopes,
            "principals": self.principals,
            "scope_principals": self.scope_principals,
        }


def _scope_id_for_channel(channel_id: str) -> str:
    return scope_ids.slack_channel(channel_id)


def _workspace_scope_id(team_id: str) -> str:
    return scope_ids.slack_workspace(team_id)


def _channel_name(channel: dict) -> str:
    name = channel.get("name") or channel.get("id") or ""
    return f"#{name}" if name and not name.startswith("#") else name


def _channel_match(channel: dict, requested: set[str] | None) -> bool:
    if not requested:
        return True
    channel_id = channel.get("id", "")
    channel_name = channel.get("name", "")
    return channel_id in requested or channel_name in requested or f"#{channel_name}" in requested


def _is_external_principal(user: dict, team_id: str) -> bool:
    if user.get("is_bot"):
        return True
    if user.get("deleted"):
        return True

    email = (user.get("profile", {}) or {}).get("email") or ""
    display_name = (user.get("profile", {}) or {}).get("display_name") or user.get("real_name") or user.get("name") or ""
    user_team = user.get("team_id") or ""

    if team_id and user_team and user_team != team_id:
        return True
    if "#EXT#" in email or "#EXT#" in display_name:
        return True
    return False


def _principal_id(user: dict) -> str:
    return scope_ids.slack_user(user["id"])


def _principal_record(user: dict) -> dict:
    profile = user.get("profile", {}) or {}
    return {
        "principal_id": _principal_id(user),
        "email": profile.get("email") or None,
        "display_name": profile.get("display_name") or profile.get("real_name") or user.get("real_name") or user.get("name") or user.get("id"),
    }


def _scope_row(channel: dict, org: str) -> dict:
    return {
        "scope_id": _scope_id_for_channel(channel["id"]),
        "source": "slack",
        "kind": "channel",
        "is_public": not bool(channel.get("is_private")),
        "name": _channel_name(channel),
        "tenant_id": org,
        "channel_id": channel["id"],
    }


def _workspace_scope_row(team_id: str, org: str, team_name: str | None = None) -> dict:
    return {
        "scope_id": _workspace_scope_id(team_id),
        "source": "slack",
        "kind": "workspace",
        "is_public": True,
        "tenant_id": org,
    }


def _cypher_for_scope(scope: dict) -> str:
    if scope["kind"] == "workspace":
        return (
            f"MERGE (s:AclScope {{scope_id:'{scope['scope_id']}'}})\n"
            f"SET s.source='slack', s.kind='workspace', s.is_public=true, s.tenant_id='{scope['tenant_id']}';"
        )
    return (
        f"MERGE (s:AclScope {{scope_id:'{scope['scope_id']}'}})\n"
        f"SET s.source='slack', s.kind='channel', s.is_public={str(scope['is_public']).lower()},\n"
        f"    s.name={json.dumps(scope['name'])}, s.tenant_id='{scope['tenant_id']}';"
    )


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _iter_channels(fetcher: SlackFetcher, include_archived: bool = False) -> list[dict]:
    channels: list[dict] = []
    cursor = ""
    while True:
        params = {
            "limit": 200,
            "types": "public_channel,private_channel",
            "exclude_archived": not include_archived,
        }
        if cursor:
            params["cursor"] = cursor
        data = fetcher._get("conversations.list", params)
        channels.extend(data.get("channels", []))
        cursor = (data.get("response_metadata", {}) or {}).get("next_cursor", "")
        if not cursor:
            break
    return channels


def _iter_members(fetcher: SlackFetcher, channel_id: str) -> Iterable[str]:
    cursor = ""
    while True:
        params = {"channel": channel_id, "limit": 200}
        if cursor:
            params["cursor"] = cursor
        data = fetcher._get("conversations.members", params)
        for member_id in data.get("members", []) or []:
            yield member_id
        cursor = (data.get("response_metadata", {}) or {}).get("next_cursor", "")
        if not cursor:
            break


def discover_scopes(
    fetcher: SlackFetcher,
    org: str,
    output_dir: Path = Path("output"),
    include_archived: bool = False,
    only_channels: Optional[set[str]] = None,
) -> ScopePrincipalArtifact:
    """Run Stage 0 discovery and write Slack ACL artifacts."""
    paths = SlackPaths(org, output_dir)
    setup_out = paths.root
    cypher_dir = paths.cypher_dir

    fetcher.validate_auth(required_scopes=["channels:read", "groups:read", "users:read", "users:read.email"])
    user_cache = fetcher.load_user_cache()
    team_id = fetcher.team_id
    team_name = fetcher.team_name or org

    channels = [c for c in _iter_channels(fetcher, include_archived=include_archived) if not c.get("is_im") and not c.get("is_mpim")]
    channels = [c for c in channels if _channel_match(c, only_channels)]
    channels = sorted(channels, key=lambda c: (c.get("id") or "", c.get("name") or ""))

    scopes: list[dict] = [_workspace_scope_row(team_id, org, team_name)]
    principals_by_id: dict[str, dict] = {}
    scope_principals: list[dict] = []
    scope_principal_keys: set[tuple[str, str]] = set()
    cypher_lines: list[str] = ["// Stage 0: Slack ACL discovery", ""]

    cypher_lines.append(_cypher_for_scope(scopes[0]))
    cypher_lines.append("")

    for channel in channels:
        scope = _scope_row(channel, org)
        scopes.append(scope)
        cypher_lines.append(_cypher_for_scope(scope))
        cypher_lines.append("")

        if channel.get("is_private"):
            for member_id in _iter_members(fetcher, channel["id"]):
                user = user_cache.get(member_id) or fetcher.get_user(member_id)
                if not user or _is_external_principal(user, team_id):
                    continue
                principal = _principal_record(user)
                principals_by_id[principal["principal_id"]] = principal
                sp_key = (scope["scope_id"], principal["principal_id"])
                if sp_key in scope_principal_keys:
                    continue
                scope_principal_keys.add(sp_key)
                scope_principals.append({
                    "scope_id": scope["scope_id"],
                    "principal_id": principal["principal_id"],
                    "grant_source": "slack_channel_membership",
                })

    # Rebuild principals deterministically.
    principals = sorted(principals_by_id.values(), key=lambda item: item["principal_id"])
    scope_principals = sorted(scope_principals, key=lambda item: (item["scope_id"], item["principal_id"]))
    scopes = sorted({item["scope_id"]: item for item in scopes}.values(), key=lambda item: item["scope_id"])

    artifact = ScopePrincipalArtifact(scopes=scopes, principals=principals, scope_principals=scope_principals)

    _write_json(paths.scope_principals, artifact.to_dict())
    _write_text(paths.acl_cypher, "\n".join(line for line in cypher_lines if line is not None).strip() + "\n")

    log.info("Discovery complete: %d scopes, %d principals, %d scope_principals",
             len(scopes), len(principals), len(scope_principals))
    return artifact


def grid_discover_scopes(
    admin_client: SlackAdminClient,
    discovery_client: SlackDiscoveryClient,
    org: str,
    team_id: str,
    output_dir: Path = Path("output"),
    only_channels: Optional[set[str]] = None,
) -> ScopePrincipalArtifact:
    """Enterprise Grid ACL discovery — deterministic, org-wide, no bot membership.

    Channels + private-channel MEMBERS come from the Discovery API (the only
    complete source — admin Web API has no org-wide member-list method).
    Identity + roles come from the admin directory. Public channels follow the
    bot-path convention (scope row only; visibility is workspace-wide). Private
    channels get explicit member principals; a private channel whose members
    cannot be resolved emits NO principals (fail-closed — deny over over-share).
    """
    paths = SlackPaths(org, output_dir)

    directory = build_grid_directory(admin_client)
    principals_by_id: dict[str, dict] = {
        p["principal_id"]: {
            "principal_id": p["principal_id"],
            "email": p.get("email"),
            "display_name": p.get("display_name"),
        }
        for p in directory.principals
    }

    channels = discovery_client.conversations_list()
    channels = [c for c in channels if _channel_match(c, only_channels)]
    channels = sorted(channels, key=lambda c: (c.get("id") or "", c.get("name") or ""))

    scopes: list[dict] = [_workspace_scope_row(team_id, org)]
    scope_principals: list[dict] = []
    scope_principal_keys: set[tuple[str, str]] = set()
    cypher_lines: list[str] = ["// Stage 0: Slack ACL discovery (Grid/Discovery)", ""]
    cypher_lines.append(_cypher_for_scope(scopes[0]))
    cypher_lines.append("")

    unresolved_private = 0
    for channel in channels:
        cid = channel.get("id", "")
        scope = _scope_row(channel, org)
        scopes.append(scope)
        cypher_lines.append(_cypher_for_scope(scope))
        cypher_lines.append("")

        if not channel.get("is_private"):
            continue  # public → workspace-wide visibility, no explicit principals

        team = channel.get("team") or channel.get("team_id") or team_id
        try:
            members = discovery_client.channel_members(cid, team)
        except Exception as exc:
            members = []
            log.warning("grid discovery: members unresolved for %s: %s", cid, exc)
        if not members:
            unresolved_private += 1  # fail-closed: emit no principals for this scope
            continue
        for uid in members:
            pid = scope_ids.slack_user(uid)
            if pid not in principals_by_id:
                continue  # external / unknown identity — skip (fail-closed)
            sp_key = (scope["scope_id"], pid)
            if sp_key in scope_principal_keys:
                continue
            scope_principal_keys.add(sp_key)
            scope_principals.append({
                "scope_id": scope["scope_id"],
                "principal_id": pid,
                "grant_source": "grid_discovery_membership",
            })

    principals = sorted(principals_by_id.values(), key=lambda item: item["principal_id"])
    scope_principals = sorted(scope_principals, key=lambda item: (item["scope_id"], item["principal_id"]))
    scopes = sorted({item["scope_id"]: item for item in scopes}.values(), key=lambda item: item["scope_id"])

    artifact = ScopePrincipalArtifact(scopes=scopes, principals=principals, scope_principals=scope_principals)
    _write_json(paths.scope_principals, artifact.to_dict())
    _write_text(paths.acl_cypher, "\n".join(line for line in cypher_lines if line is not None).strip() + "\n")

    log.info("Grid discovery complete: %d scopes, %d principals, %d scope_principals "
             "(%d private channels unresolved → fail-closed)",
             len(scopes), len(principals), len(scope_principals), unresolved_private)
    return artifact


def run_discovery(
    fetcher: SlackFetcher,
    org: str,
    output_dir: Path = Path("output"),
    include_archived: bool = False,
    only_channels: Optional[set[str]] = None,
) -> ScopePrincipalArtifact:
    """Auto-route ACL discovery to the Grid path or the bot path by capability.

    Reads the token's capabilities (Grid + admin scopes ⇒ Grid path) and
    dispatches. The Grid path needs Discovery enabled; if the admin/Discovery
    calls fail (e.g. Discovery not yet approved for the org) it falls back to
    the bot membership path with a loud warning.
    """
    fetcher.validate_auth(required_scopes=["users:read"])
    caps = fetcher.capabilities
    if caps is not None and caps.acl_pathway == AclPathway.ADMIN_ENUM:
        log.info("ACL discovery: Grid pathway (admin + Discovery)")
        try:
            admin_client = SlackAdminClient(token=fetcher.token, base_url=fetcher.base_url)
            discovery_client = SlackDiscoveryClient(token=fetcher.token, base_url=fetcher.base_url)
            return grid_discover_scopes(
                admin_client, discovery_client, org,
                team_id=fetcher.team_id, output_dir=output_dir, only_channels=only_channels,
            )
        except Exception as exc:
            log.warning("Grid discovery failed (%s) — falling back to bot membership path", exc)
    else:
        log.info("ACL discovery: bot membership pathway (standard workspace)")
    return discover_scopes(
        fetcher=fetcher, org=org, output_dir=output_dir,
        include_archived=include_archived, only_channels=only_channels,
    )


def build_parser() -> argparse.ArgumentParser:
    s = get_settings()
    parser = argparse.ArgumentParser(description="Stage 0 Slack ACL discovery")
    parser.add_argument("--org", default=s.slack_org, help="Organization slug")
    parser.add_argument("--token", default=s.slack_token, help="Slack token (xoxp- admin user token or xoxb- bot token)")
    parser.add_argument("--output-dir", default="output", help="Root output directory")
    parser.add_argument("--base-url", default=SLACK_BASE_URL, help=argparse.SUPPRESS)
    parser.add_argument("--include-archived", action="store_true")
    parser.add_argument("--channels", nargs="*", default=None, help="Optional list of channel IDs or names to discover")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    return parser


def main(argv: Optional[list[str]] = None) -> ScopePrincipalArtifact:
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.org:
        parser.error("--org is required (or set SLACK_ORG env var)")
    if not args.token:
        parser.error("--token is required (or set SLACK_TOKEN env var)")

    output_dir = Path(args.output_dir)
    (output_dir / "slack").mkdir(parents=True, exist_ok=True)
    (output_dir / "cypher").mkdir(parents=True, exist_ok=True)
    setup_logging(org=args.org, log_dir=str(output_dir / "logs"), source="slack.discovery")

    fetcher = SlackFetcher(token=args.token, org=args.org, base_url=args.base_url)
    return run_discovery(
        fetcher=fetcher,
        org=args.org,
        output_dir=output_dir,
        include_archived=args.include_archived,
        only_channels=set(args.channels) if args.channels else None,
    )


if __name__ == "__main__":
    main()