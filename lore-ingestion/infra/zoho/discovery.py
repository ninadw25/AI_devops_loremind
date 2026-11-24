"""Zoho user + Cliq channel discovery — emits ACL artifacts.

Mirrors infra/outlook/discovery.py's shape:
  - discover_users()                 -> list of org users (via Cliq Users API)
  - discover_cliq_channel_members()  -> channel_id -> member ZUIDs
  - emit_zoho_identities()           -> {org}_zoho_identities.json
  - emit_cliq_scope_map()            -> {org}_zoho_cliq_scopes.json
  - emit_zoho_scope_principals()     -> {org}_scope_principals.json (standard contract)
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Set

from common.logger import get_logger
from common.scopes import zoho_cliq_channel
from infra.zoho.fetcher import fetch_cliq_channel_members, fetch_cliq_channels, zoho_get

log = get_logger("zoho.discovery")


def discover_users(token: str, org: str, settings=None) -> List[Dict[str, Any]]:
    """Enumerate org users via the Cliq Users API (GET /users).

    Returns empty list when the token has no Cliq org (e.g. personal accounts
    that granted Cliq scopes but are not part of a Zoho organization).

    Args:
        token: valid Zoho bearer token (caller is responsible for obtaining it)
        org:   organization slug (used for log messages only)

    Returns:
        List of dicts::

            {
                "zuid":        str,   # Zoho user ID
                "displayName": str,
                "email":       str,
            }
    """
    from common.config import get_settings

    settings = settings or get_settings()
    try:
        raw: Any = zoho_get(f"{settings.zoho_cliq_base_url}/users", token)
    except Exception as exc:
        log.warning("discover_users [%s]: Cliq /users unavailable (%s) — skipping user discovery", org, exc)
        return []
    rows = raw.get("data", []) if isinstance(raw, dict) else (raw if isinstance(raw, list) else [])

    users: List[Dict[str, Any]] = []
    for u in rows:
        if not isinstance(u, dict):
            continue
        users.append(
            {
                "zuid": str(u.get("id") or u.get("zuid") or ""),
                "displayName": u.get("name") or u.get("display_name") or "",
                "email": u.get("email") or "",
            }
        )

    log.info("discover_users [%s]: %d users", org, len(users))
    return users


def discover_cliq_channel_members(token: str) -> Dict[str, Dict[str, Any]]:
    """Map every Cliq channel to its member ZUIDs.

    Returns empty dict when the account is not part of a Zoho org (Cliq
    requires an org/team workspace; personal accounts get a 400 response).

    Flow::

        GET /channels
        → for each channel: GET /channels/{channel_id}/members

    Returns:
        Dict keyed by channel_id::

            {
                channel_id: {
                    "channel_name":  str,
                    "member_zuids":  [str, ...],
                }
            }
    """
    try:
        channels = fetch_cliq_channels(token)
    except Exception as exc:
        log.warning("discover_cliq_channel_members: Cliq channels unavailable (%s) — returning empty map", exc)
        return {}

    result: Dict[str, Dict[str, Any]] = {}
    for channel in channels:
        if not isinstance(channel, dict):
            continue
        channel_id = str(channel.get("chat_id") or channel.get("id") or "")
        channel_name = channel.get("name") or channel.get("title") or ""
        if not channel_id:
            continue

        member_zuids: List[str] = []
        try:
            members = fetch_cliq_channel_members(channel_id, token)
            for member in members:
                if not isinstance(member, dict):
                    continue
                zuid = str(member.get("id") or member.get("zuid") or "")
                if zuid:
                    member_zuids.append(zuid)
        except Exception as exc:
            log.warning("Failed to fetch members for channel %s: %s", channel_id, exc)

        result[channel_id] = {
            "channel_name": channel_name,
            "member_zuids": member_zuids,
        }
        log.debug("channel %s (%s): %d members", channel_id, channel_name, len(member_zuids))

    log.info("discover_cliq_channel_members: %d channels", len(result))
    return result


# ── Emit functions ────────────────────────────────────────────────────────

def emit_zoho_identities(org: str, data_dir: Path, token: str) -> Path:
    """Fetch Zoho users and write the identities artifact.

    Output: {data_dir}/{org}_zoho_identities.json
    """
    users = discover_users(token, org)

    identities = sorted(
        [
            {
                "zuid": u["zuid"],
                "displayName": u["displayName"],
                "email": u["email"],
                "account_type": "member",
            }
            for u in users
        ],
        key=lambda x: x["zuid"],
    )

    payload: Dict[str, Any] = {
        "org": org,
        "source": "zoho",
        "count": len(identities),
        "identities": identities,
    }

    out_dir = Path(data_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{org}_zoho_identities.json"
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    log.info("emit_zoho_identities [%s]: %d identities -> %s", org, len(identities), out_path)
    return out_path


def emit_cliq_scope_map(org: str, data_dir: Path, token: str) -> Path:
    """Fetch Cliq channel membership and write the per-user scope map.

    Output: {data_dir}/{org}_zoho_cliq_scopes.json
    """
    channel_map = discover_cliq_channel_members(token)

    user_scopes: Dict[str, Set[str]] = {}
    for channel_id, info in channel_map.items():
        scope_id = zoho_cliq_channel(channel_id)
        for zuid in info["member_zuids"]:
            if zuid:
                user_scopes.setdefault(zuid, set()).add(scope_id)

    scopes_out: Dict[str, List[str]] = {
        zuid: sorted(scope_set) for zuid, scope_set in sorted(user_scopes.items())
    }

    payload: Dict[str, Any] = {
        "org": org,
        "source": "zoho_cliq",
        "person_count": len(scopes_out),
        "scopes": scopes_out,
    }

    out_dir = Path(data_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{org}_zoho_cliq_scopes.json"
    out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    log.info(
        "emit_cliq_scope_map [%s]: %d users / %d channels -> %s",
        org, len(scopes_out), len(channel_map), out_path,
    )
    return out_path


def emit_zoho_scope_principals(org: str, data_dir: Path) -> Path:
    """Fold the two Zoho raw discovery artifacts into the standard
    scope_principals contract used by Slack / Jira / Outlook.

    Reads (must already exist — produced by --stage discover):
        {data_dir}/{org}_zoho_identities.json
        {data_dir}/{org}_zoho_cliq_scopes.json

    Writes:
        {data_dir}/{org}_scope_principals.json

    principal_id format:  zoho:{zuid}
    scope_id format:      zoho:cliq:channel:{channel_id}   (Cliq channels only —
                          Zoho Mail has no channel scope; participant-based
                          model like Gmail)
    """
    root = Path(data_dir)
    identities_path = root / f"{org}_zoho_identities.json"
    cliq_scopes_path = root / f"{org}_zoho_cliq_scopes.json"
    out_path = root / f"{org}_scope_principals.json"

    id_raw: Dict[str, Any] = {}
    if identities_path.exists():
        id_raw = json.loads(identities_path.read_text(encoding="utf-8"))
    else:
        log.warning("emit_zoho_scope_principals [%s]: identities artifact missing (%s)", org, identities_path)

    scopes_raw: Dict[str, Any] = {}
    if cliq_scopes_path.exists():
        scopes_raw = json.loads(cliq_scopes_path.read_text(encoding="utf-8"))
    else:
        log.warning("emit_zoho_scope_principals [%s]: cliq_scopes artifact missing (%s)", org, cliq_scopes_path)

    zuid_to_identity: Dict[str, Dict[str, Any]] = {}
    for ident in id_raw.get("identities", []):
        zuid = ident.get("zuid") or ""
        if not zuid:
            continue
        zuid_to_identity[zuid] = ident

    principals: List[Dict[str, Any]] = [
        {
            "principal_id": f"zoho:{zuid}",
            "display_name": ident.get("displayName") or "",
            "email": ident.get("email") or "",
            "account_type": ident.get("account_type") or "member",
        }
        for zuid, ident in sorted(zuid_to_identity.items())
    ]

    all_channel_scope_ids: Set[str] = set()
    for scope_list in scopes_raw.get("scopes", {}).values():
        for sid in scope_list:
            if sid:
                all_channel_scope_ids.add(sid)

    scopes: List[Dict[str, Any]] = [
        {"scope_id": sid, "kind": "zoho_cliq_channel", "display_name": sid}
        for sid in sorted(all_channel_scope_ids)
    ]

    scope_principals: List[Dict[str, Any]] = []
    for zuid, scope_list in sorted(scopes_raw.get("scopes", {}).items()):
        principal_id = f"zoho:{zuid}"
        for sid in sorted(scope_list):
            scope_principals.append({"scope_id": sid, "principal_id": principal_id})

    payload: Dict[str, Any] = {
        "org": org,
        "source": "zoho",
        "scopes": scopes,
        "principals": principals,
        "scope_principals": scope_principals,
    }

    root.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(out_path)

    log.info(
        "emit_zoho_scope_principals [%s]: %d scopes, %d principals, %d grants -> %s",
        org, len(scopes), len(principals), len(scope_principals), out_path,
    )
    return out_path
