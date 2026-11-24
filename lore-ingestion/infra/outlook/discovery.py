"""MS365 / Teams discovery functions for LOR-65.

Phase 2 — raw discovery:
  - discover_users()                  → list of enabled member users
  - discover_teams_channel_members()  → channel_id → member object IDs

Phase 3A — emit functions (file I/O):
  - emit_ms365_identities()           → {org}_ms365_identities.json
  - emit_teams_scope_map()            → {org}_ms365_teams_scopes.json
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Set

from common.logger import get_logger
from common.output_paths import OutlookPaths
from common.scopes import ms365_channel_scope
from infra.outlook.fetcher import graph_get

log = get_logger("outlook.discovery")


def discover_users(token: str, org: str) -> List[Dict[str, Any]]:
    """Enumerate enabled member users from MS365 via /users.

    Args:
        token: valid Graph API bearer token (caller is responsible for obtaining it)
        org:   organization slug (used for log messages only in Phase 2)

    Returns:
        List of dicts::

            {
                "objectId":        str,   # Azure AD object ID
                "displayName":     str,
                "upn":             str,   # userPrincipalName
                "email":           str,   # mail field; falls back to upn
                "accountEnabled":  True,
            }

        Excludes accounts where accountEnabled is False or userType is "Guest".
    """
    params: Dict[str, str] = {
        "$select": "id,displayName,userPrincipalName,mail,accountEnabled,userType",
        "$filter": "accountEnabled eq true",
    }

    raw: Any = graph_get("/users", token, params=params)
    if isinstance(raw, dict):
        raw = raw.get("value", []) if isinstance(raw.get("value"), list) else []
    if not isinstance(raw, list):
        raw = []

    users: List[Dict[str, Any]] = []
    for u in raw:
        if not isinstance(u, dict):
            continue
        # Belt-and-suspenders — Graph OData filter already excludes disabled
        # accounts but not guests (ne operator has null-value edge cases).
        if u.get("userType") == "Guest":
            continue
        if u.get("accountEnabled") is False:
            continue
        users.append(
            {
                "objectId": u.get("id") or "",
                "displayName": u.get("displayName") or "",
                "upn": u.get("userPrincipalName") or "",
                # mail is absent for sync'd accounts; fall back to UPN.
                "email": u.get("mail") or u.get("userPrincipalName") or "",
                "accountEnabled": True,
            }
        )

    log.info("discover_users [%s]: %d enabled member users", org, len(users))
    return users


def discover_teams_channel_members(token: str) -> Dict[str, Dict[str, Any]]:
    """Map every Teams channel to its member Azure AD object IDs.

    Flow::

        GET /groups?$filter=resourceProvisioningOptions/Any(x:x eq 'Team')
        → for each team:    GET /teams/{team_id}/channels
        → for each channel: GET /teams/{team_id}/channels/{channel_id}/members

    Args:
        token: valid Graph API bearer token

    Returns:
        Dict keyed by channel_id::

            {
                channel_id: {
                    "team_id":           str,
                    "team_name":         str,
                    "channel_name":      str,
                    "member_object_ids": [str, ...],
                }
            }

        member_object_ids contains Azure AD object IDs (userId field on
        aadUserConversationMember). An empty list means the channel was
        discovered but member enumeration failed or returned nothing.
    """
    teams_params: Dict[str, str] = {
        "$filter": "resourceProvisioningOptions/Any(x:x eq 'Team')",
        "$select": "id,displayName",
    }

    raw_teams: Any = graph_get("/groups", token, params=teams_params)
    if isinstance(raw_teams, dict):
        raw_teams = raw_teams.get("value", []) if isinstance(raw_teams.get("value"), list) else []
    if not isinstance(raw_teams, list):
        raw_teams = []

    result: Dict[str, Dict[str, Any]] = {}

    for team in raw_teams:
        if not isinstance(team, dict):
            continue
        team_id: str = team.get("id") or ""
        team_name: str = team.get("displayName") or ""
        if not team_id:
            continue

        raw_channels: Any = graph_get(
            f"/teams/{team_id}/channels",
            token,
            params={"$select": "id,displayName"},
        )
        if isinstance(raw_channels, dict):
            raw_channels = (
                raw_channels.get("value", [])
                if isinstance(raw_channels.get("value"), list)
                else []
            )
        if not isinstance(raw_channels, list):
            raw_channels = []

        for channel in raw_channels:
            if not isinstance(channel, dict):
                continue
            channel_id: str = channel.get("id") or ""
            channel_name: str = channel.get("displayName") or ""
            if not channel_id:
                continue

            member_object_ids: List[str] = []
            try:
                raw_members: Any = graph_get(
                    f"/teams/{team_id}/channels/{channel_id}/members",
                    token,
                    params={"$select": "id,userId,displayName"},
                )
                if isinstance(raw_members, dict):
                    raw_members = (
                        raw_members.get("value", [])
                        if isinstance(raw_members.get("value"), list)
                        else []
                    )
                if not isinstance(raw_members, list):
                    raw_members = []

                for member in raw_members:
                    if not isinstance(member, dict):
                        continue
                    # aadUserConversationMember exposes userId (Azure AD object ID).
                    # Fall back to id (the conversation-member GUID) only if absent.
                    uid: str = member.get("userId") or member.get("id") or ""
                    if uid:
                        member_object_ids.append(uid)

            except Exception as exc:
                log.warning(
                    "Failed to fetch members for channel %s (team %s): %s",
                    channel_id,
                    team_id,
                    exc,
                )

            result[channel_id] = {
                "team_id": team_id,
                "team_name": team_name,
                "channel_name": channel_name,
                "member_object_ids": member_object_ids,
            }
            log.debug(
                "channel %s (%s / %s): %d members",
                channel_id,
                team_name,
                channel_name,
                len(member_object_ids),
            )

    log.info(
        "discover_teams_channel_members: %d channels across %d teams",
        len(result),
        len(raw_teams),
    )
    return result


# ── Emit functions (Phase 3A) ─────────────────────────────────────────

def emit_ms365_identities(org: str, data_dir: Path, token: str) -> Path:
    """Fetch MS365 users and write the identities artifact.

    Calls discover_users() and writes a JSON file with all enabled member
    users, sorted deterministically by objectId.

    Args:
        org:      organization slug
        data_dir: directory to write into (created if absent)
        token:    valid Graph API bearer token

    Returns:
        Path of the written file.

    Output: {data_dir}/{org}_ms365_identities.json
    """
    users = discover_users(token, org)

    identities = sorted(
        [
            {
                "objectId": u["objectId"],
                "displayName": u["displayName"],
                "upn": u["upn"],
                "email": u["email"],
                "account_type": "member",
            }
            for u in users
        ],
        key=lambda x: x["objectId"],
    )

    payload: Dict[str, Any] = {
        "org": org,
        "source": "ms365",
        "count": len(identities),
        "identities": identities,
    }

    out_dir = Path(data_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{org}_ms365_identities.json"
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    log.info(
        "emit_ms365_identities [%s]: %d identities -> %s",
        org, len(identities), out_path,
    )
    return out_path


def emit_teams_scope_map(org: str, data_dir: Path, token: str) -> Path:
    """Fetch Teams channel membership and write the per-user scope map.

    Inverts the channel→members mapping produced by
    discover_teams_channel_members() to generate a dict of
    objectId → [ms365:channel:... scope IDs].

    Args:
        org:      organization slug
        data_dir: directory to write into (created if absent)
        token:    valid Graph API bearer token

    Returns:
        Path of the written file.

    Output: {data_dir}/{org}_ms365_teams_scopes.json
    """
    channel_map = discover_teams_channel_members(token)

    # Invert: objectId → set of scope IDs
    user_scopes: Dict[str, Set[str]] = {}
    for channel_id, info in channel_map.items():
        scope_id = ms365_channel_scope(channel_id)
        for obj_id in info["member_object_ids"]:
            if obj_id:
                user_scopes.setdefault(obj_id, set()).add(scope_id)

    # Deterministic output: sorted by objectId key, sorted scope lists
    scopes_out: Dict[str, List[str]] = {
        obj_id: sorted(scope_set)
        for obj_id, scope_set in sorted(user_scopes.items())
    }

    payload: Dict[str, Any] = {
        "org": org,
        "source": "ms_teams",
        "person_count": len(scopes_out),
        "scopes": scopes_out,
    }

    out_dir = Path(data_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{org}_ms365_teams_scopes.json"
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    log.info(
        "emit_teams_scope_map [%s]: %d users / %d channels -> %s",
        org, len(scopes_out), len(channel_map), out_path,
    )
    return out_path


def emit_ms365_scope_principals(org: str, data_dir: Path) -> Path:
    """Fold the two MS365 raw discovery artifacts into the standard
    scope_principals contract that rbac-service and the identity resolver
    already consume for Slack and Jira.

    Reads (must already exist — produced by --stage discover):
        output/outlook/{org}_ms365_identities.json
        output/outlook/{org}_ms365_teams_scopes.json

    Writes:
        output/outlook/{org}_scope_principals.json

    Output schema (identical to Slack / Jira):
        scopes:           [{ scope_id, kind, display_name }]
        principals:       [{ principal_id, display_name, email, account_type }]
        scope_principals: [{ scope_id, principal_id }]

    principal_id format:  ms365:{objectId}
    scope_id format:      ms365:channel:{channelId}   (Teams channels only —
                          Outlook Mail has no channel scope; it uses the
                          participant-based model like Gmail)
    """
    paths = OutlookPaths(org=org, data_dir=Path(data_dir))

    # ── Load raw artifacts ────────────────────────────────────────────
    id_raw: Dict[str, Any] = {}
    if paths.identities_json.exists():
        id_raw = json.loads(paths.identities_json.read_text(encoding="utf-8"))
    else:
        log.warning("emit_ms365_scope_principals [%s]: identities artifact missing (%s)",
                    org, paths.identities_json)

    scopes_raw: Dict[str, Any] = {}
    if paths.teams_scopes_json.exists():
        scopes_raw = json.loads(paths.teams_scopes_json.read_text(encoding="utf-8"))
    else:
        log.warning("emit_ms365_scope_principals [%s]: teams_scopes artifact missing (%s)",
                    org, paths.teams_scopes_json)

    # ── Build principals list (one entry per MS365 user) ─────────────
    # Index by objectId for quick lookup when building scope_principals.
    obj_to_identity: Dict[str, Dict[str, Any]] = {}
    for ident in id_raw.get("identities", []):
        obj_id = ident.get("objectId") or ""
        if not obj_id:
            continue
        obj_to_identity[obj_id] = ident

    principals: List[Dict[str, Any]] = [
        {
            "principal_id": f"ms365:{obj_id}",
            "display_name": ident.get("displayName") or "",
            "email": ident.get("email") or ident.get("upn") or "",
            "account_type": ident.get("account_type") or "member",
        }
        for obj_id, ident in sorted(obj_to_identity.items())
    ]

    # ── Build scopes list (one entry per Teams channel) ───────────────
    # Derive channel ids from the teams_scopes map (objectId → [scope_ids]).
    all_channel_scope_ids: Set[str] = set()
    for scope_list in scopes_raw.get("scopes", {}).values():
        for sid in scope_list:
            if sid:
                all_channel_scope_ids.add(sid)

    scopes: List[Dict[str, Any]] = [
        {
            "scope_id": sid,
            "kind": "ms365_channel",
            "display_name": sid,   # channel display name not in this artifact; id is human-readable
        }
        for sid in sorted(all_channel_scope_ids)
    ]

    # ── Build scope_principals (grant) list ───────────────────────────
    scope_principals: List[Dict[str, Any]] = []
    for obj_id, scope_list in sorted(scopes_raw.get("scopes", {}).items()):
        principal_id = f"ms365:{obj_id}"
        for sid in sorted(scope_list):
            scope_principals.append({"scope_id": sid, "principal_id": principal_id})

    payload: Dict[str, Any] = {
        "org": org,
        "source": "ms365",
        "scopes": scopes,
        "principals": principals,
        "scope_principals": scope_principals,
    }

    out_path = paths.scope_principals
    tmp = out_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(out_path)

    log.info(
        "emit_ms365_scope_principals [%s]: %d scopes, %d principals, %d grants -> %s",
        org, len(scopes), len(principals), len(scope_principals), out_path,
    )
    return out_path
