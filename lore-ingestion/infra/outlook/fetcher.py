"""Outlook fetcher: implements Azure AD token retrieval and Graph GET helper.

Phases implemented here:
- Phase 1: package stubs (file exists)
- Phase 2: `get_access_token(settings)` — client credentials flow using httpx
- Phase 3: `graph_get(endpoint, token, params=None)` — pagination, 429 and
  transient 5xx retries with backoff

This module imports only `common.*` and third-party `httpx` as required.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import httpx

from common.config import get_settings
from common.logger import get_logger
from common import checkpoint as _checkpoint
from pathlib import Path
import json

log = get_logger("outlook.fetcher")

# Simple in-memory token cache: keyed by tenant_id
_TOKEN_CACHE: Dict[str, Dict[str, Any]] = {}


class DeltaStore:
    """Persist Microsoft Graph delta tokens for mail and teams scopes.

    Stored JSON shape:

    {
      "mail:john@company.com": {
        "delta_token": "...",
        "last_sync": "2026-06-08T12:00:00+00:00",
        "message_count": 123
      },
      "teams:abc:def": { ... }
    }
    """

    def __init__(self, org: str, path: Optional[Path] = None) -> None:
        self.org = org
        # Default path: <state_dir>/<org>_outlook_delta_tokens.json
        if path is None:
            state_dir = _checkpoint._state_dir()
            state_dir.mkdir(parents=True, exist_ok=True)
            self.path = state_dir / f"{org}_outlook_delta_tokens.json"
        else:
            self.path = Path(path)
            self.path.parent.mkdir(parents=True, exist_ok=True)

        self._data: Dict[str, Dict[str, Any]] = {}
        self.load()

    def load(self) -> Dict[str, Dict[str, Any]]:
        """Load existing token file. Return empty dict on missing/corrupt file."""
        if not self.path.exists():
            self._data = {}
            return self._data
        try:
            text = self.path.read_text(encoding="utf-8")
            self._data = json.loads(text) if text.strip() else {}
        except Exception as exc:
            log.warning("DeltaStore: failed to load %s — returning empty store: %s", self.path, exc)
            self._data = {}
        return self._data

    def get(self, key: str) -> Optional[Dict[str, Any]]:
        """Return stored record for `key` or None. Returns only delta_token and message_count."""
        rec = self._data.get(key)
        if not rec:
            return None
        return {"delta_token": rec.get("delta_token"), "message_count": rec.get("message_count")}

    def set(self, key: str, delta_token: str, message_count: int) -> None:
        """Set token, last_sync timestamp (ISO), and message_count for `key`."""
        now = datetime.now(timezone.utc).isoformat()
        self._data[key] = {
            "delta_token": delta_token,
            "last_sync": now,
            "message_count": int(message_count),
        }

    def save(self) -> None:
        """Persist the store atomically using repository checkpoint pattern."""
        # Use same atomic-write semantics as common.checkpoint
        payload = dict(self._data)
        try:
            _checkpoint._atomic_write(self.path, payload)
            log.info("DeltaStore: saved %d keys -> %s", len(payload), self.path)
        except Exception as exc:
            log.error("DeltaStore: failed to save %s: %s", self.path, exc)
            raise


def _parse_retry_after(header_value: Optional[str]) -> Optional[float]:
    """Parse Retry-After header supporting delta-seconds or HTTP-date."""
    if not header_value:
        return None
    header_value = header_value.strip()
    # Try delta-seconds
    try:
        return float(header_value)
    except ValueError:
        pass
    # Try HTTP-date
    try:
        from email.utils import parsedate_to_datetime

        when = parsedate_to_datetime(header_value)
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        return max(0.0, (when - datetime.now(timezone.utc)).total_seconds())
    except Exception:
        return None


def get_access_token(settings) -> str:
    """Acquire an Azure AD access token using client credentials.

    Args:
        settings: Pydantic settings object; expected attributes:
            - azure_tenant_id
            - azure_client_id
            - azure_client_secret

    Behavior:
        - Caches token in-process keyed by tenant_id
        - Refreshes token 60s before expiry
        - Raises RuntimeError on HTTP or parsing failures
    """
    # Prefer ms365_* (LOR-66 naming); fall back to azure_* (LOR-64 naming).
    # Both are supported — whichever pair is present in .env will work.
    tenant = (
        getattr(settings, "resolved_ms365_tenant_id", None)
        or getattr(settings, "ms365_tenant_id", None)
        or getattr(settings, "azure_tenant_id", None)
    )
    client_id = (
        getattr(settings, "resolved_ms365_client_id", None)
        or getattr(settings, "ms365_client_id", None)
        or getattr(settings, "azure_client_id", None)
    )
    client_secret = (
        getattr(settings, "resolved_ms365_client_secret", None)
        or getattr(settings, "ms365_client_secret", None)
        or getattr(settings, "azure_client_secret", None)
    )

    if not tenant or not client_id or not client_secret:
        raise RuntimeError("Azure AD client credentials not configured in settings")

    cache = _TOKEN_CACHE.get(tenant)
    now_ts = time.time()
    if cache:
        expires_at = cache.get("expires_at", 0)
        # Refresh 60s before expiry
        if now_ts < (expires_at - 60):
            log.debug("Using cached token for tenant %s (expires in %.0fs)", tenant, expires_at - now_ts)
            return cache["access_token"]

    token_url = f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
    data = {
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret,
        "scope": "https://graph.microsoft.com/.default",
    }

    log.info("Requesting Azure AD token for tenant %s", tenant)
    try:
        with httpx.Client(timeout=30) as client:
            resp = client.post(token_url, data=data)
            resp.raise_for_status()
            j = resp.json()
    except Exception as exc:
        log.error("Failed to obtain Azure AD token: %s", exc)
        raise RuntimeError(f"Azure AD token request failed: {exc}") from exc

    access_token = j.get("access_token")
    expires_in = j.get("expires_in")
    if not access_token or not expires_in:
        # Keys only — the body carries access_token, and logs are readable
        # through the control API's /logs endpoints.
        log.error("Unexpected Azure AD token response, keys=%s", sorted(j))
        raise RuntimeError("Azure AD token response missing fields")

    expires_at = now_ts + float(expires_in)
    _TOKEN_CACHE[tenant] = {
        "access_token": access_token,
        "expires_at": expires_at,
        "fetched_at": now_ts,
    }
    log.info("Obtained Azure AD token for tenant %s (expires in %ds)", tenant, int(expires_in))
    return access_token


def graph_get(endpoint: str, token: str, params: Optional[Dict[str, Any]] = None, return_last_response: bool = False) -> Any:
    """Perform GET(s) against Microsoft Graph with pagination and retries.

    Args:
        endpoint: Graph path (e.g. '/users') or full URL
        token: Bearer token
        params: query params (dict)

    Returns:
        If response contains 'value' arrays with pagination, returns combined list.
        Otherwise returns parsed JSON response from the terminal call.
    """
    # Normalize URL
    if endpoint.startswith("http://") or endpoint.startswith("https://"):
        url = endpoint
    else:
        url = f"https://graph.microsoft.com/v1.0{endpoint}"

    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}

    max_retries = 5
    attempt = 0
    backoffs = [2, 4, 8, 16, 32]

    results: List[Any] = []
    next_url: Optional[str] = url
    next_params = params
    last_json: Optional[Dict[str, Any]] = None

    while next_url:
        attempt = 0
        while True:
            try:
                with httpx.Client(timeout=30) as client:
                    resp = client.get(next_url, headers=headers, params=next_params)
                status = resp.status_code
                if status == 429:
                    attempt += 1
                    if attempt > max_retries:
                        resp.raise_for_status()
                    retry_after = _parse_retry_after(resp.headers.get("Retry-After"))
                    wait = retry_after if retry_after is not None else backoffs[min(attempt - 1, len(backoffs) - 1)]
                    log.warning("Graph rate-limited (429) on %s — sleeping %.1fs (attempt %d/%d)", next_url, wait, attempt, max_retries)
                    time.sleep(wait)
                    continue
                if status in (502, 503, 504):
                    attempt += 1
                    if attempt > max_retries:
                        resp.raise_for_status()
                    wait = backoffs[min(attempt - 1, len(backoffs) - 1)]
                    log.warning("Graph transient error %d on %s — retrying in %.1fs (attempt %d/%d)", status, next_url, wait, attempt, max_retries)
                    time.sleep(wait)
                    continue

                resp.raise_for_status()
                j = resp.json()
                break
            except httpx.HTTPStatusError:
                raise
            except Exception as exc:
                attempt += 1
                if attempt > max_retries:
                    log.error("Graph GET failed on %s after %d retries: %s", next_url, attempt - 1, exc)
                    raise RuntimeError(f"Graph GET failed: {exc}") from exc
                wait = backoffs[min(attempt - 1, len(backoffs) - 1)]
                log.warning("Graph GET exception on %s — retrying in %.1fs (attempt %d/%d): %s", next_url, wait, attempt, max_retries, exc)
                time.sleep(wait)
                continue

        # If response contains paged 'value', accumulate and follow @odata.nextLink
        if isinstance(j, dict) and "value" in j and isinstance(j["value"], list):
            results.extend(j["value"])
            last_json = j
            next_link = j.get("@odata.nextLink")
            if next_link:
                next_url = next_link
                next_params = None
                log.debug("Following @odata.nextLink -> %s", next_url)
                continue
            # No more pages
            if return_last_response:
                return results, last_json
            return results

        # Not a paged 'value' response — return the JSON object
        if return_last_response:
            return j, j
        return j


def _extract_delta_token_from_link(link: str) -> Optional[str]:
    """Given an @odata.deltaLink URL, extract the $deltatoken value if present.

    If parsing fails, return the original link so it can be stored as-is.
    """
    try:
        from urllib.parse import urlparse, parse_qs

        parsed = urlparse(link)
        qs = parse_qs(parsed.query)
        # Graph uses '$deltatoken' as the query key
        token_list = qs.get("$deltatoken") or qs.get("deltatoken")
        if token_list:
            return token_list[0]
    except Exception:
        pass
    return link


def fetch_mail_for_user(org: str, user_email: str, scanback_days: int = 30, settings: Optional[Any] = None, store_path: Optional[Path] = None):
    """Fetch Outlook mail for a single user using delta sync semantics.

    Yields raw Graph message dicts. Uses `DeltaStore` to persist delta tokens.

    Modes:
      - First sync (no token): calls `/users/{user_email}/messages/delta` with
        a `$filter=receivedDateTime ge {scanback_date}` and performs a full
        initial sync. Stores the returned delta token when complete.
      - Incremental sync (token exists): calls `/users/{user_email}/messages/delta`
        with `$deltatoken=...` to fetch only changes. Updates stored token.

    Args:
      org: organization slug (used for DeltaStore path)
      user_email: user's email address (also used as identifier in Graph path)
      scanback_days: lookback window for initial sync
      settings: optional settings object (Pydantic); falls back to `get_settings()`
    """
    settings = settings or get_settings()
    token = get_access_token(settings)

    # Allow tests to inject a specific DeltaStore path via `store_path`.
    store = DeltaStore(org, path=store_path) if store_path is not None else DeltaStore(org)
    key = f"mail:{user_email}"
    rec = store.get(key)

    is_incremental = bool(rec and rec.get("delta_token"))

    if is_incremental:
        delta_token = rec["delta_token"]
        log.info("Existing delta token found for %s — performing incremental sync", user_email)
        # If the stored value is a full URL (deltaLink), replay it directly.
        if isinstance(delta_token, str) and delta_token.startswith(("http://", "https://")):
            endpoint = delta_token
            params = None
        else:
            endpoint = f"/users/{user_email}/messages/delta"
            params = {"$deltatoken": delta_token}
    else:
        scanback_date = (datetime.now(timezone.utc) - timedelta(days=int(scanback_days))).replace(microsecond=0).isoformat()
        log.info("No delta token for %s — performing full sync since %s", user_email, scanback_date)
        endpoint = f"/users/{user_email}/messages/delta"
        params = {"$filter": f"receivedDateTime ge {scanback_date}"}

    # Reuse graph_get but request the last page metadata so we can capture @odata.deltaLink
    res = graph_get(endpoint, token, params=params, return_last_response=True)

    results = []
    last_json = None
    if isinstance(res, tuple):
        results, last_json = res
    else:
        # graph_get may return a dict (non-paged) or list — normalize
        if isinstance(res, list):
            results = res
        elif isinstance(res, dict):
            # Single-page non-paged JSON — if it contains 'value' use it
            if "value" in res and isinstance(res["value"], list):
                results = res["value"]
                last_json = res
            else:
                # Unexpected shape — yield the object and return
                yield res
                return

    # Yield each raw message dict
    for msg in results:
        yield msg

    # Attempt to extract the delta token from the last page's @odata.deltaLink
    new_token = None
    if last_json:
        delta_link = last_json.get("@odata.deltaLink") or last_json.get("odata.deltaLink")
        if delta_link:
            new_token = _extract_delta_token_from_link(delta_link)

    if new_token:
        try:
            store.set(key, new_token, len(results))
            store.save()
            log.info("Updated delta token for %s (messages=%d)", user_email, len(results))
        except Exception as exc:
            log.error("Failed to persist delta token for %s: %s", user_email, exc)
    else:
        log.warning("No @odata.deltaLink found for %s — delta token not updated", user_email)


def fetch_teams_channels(org: str, settings: Optional[Any] = None) -> List[Dict[str, Any]]:
    """Discover Teams-enabled groups and their channels.

    Returns a flat list of dictionaries, one per team/channel pair, including
    core identifiers and any available metadata that may help ACL discovery later.
    This function only discovers teams/channels; it does not normalize or create
    KnowledgeEvents.
    """
    settings = settings or get_settings()
    token = get_access_token(settings)

    teams_endpoint = "/groups"
    teams_params = {
        "$filter": "resourceProvisioningOptions/Any(x:x eq 'Team')",
    }

    teams = graph_get(teams_endpoint, token, params=teams_params)
    if isinstance(teams, dict):
        teams = teams.get("value", []) if isinstance(teams.get("value"), list) else []

    discovered: List[Dict[str, Any]] = []

    for team in teams:
        if not isinstance(team, dict):
            continue

        team_id = team.get("id")
        team_name = team.get("displayName") or team.get("name")
        if not team_id:
            continue

        channels = graph_get(f"/teams/{team_id}/channels", token)
        if isinstance(channels, dict):
            channels = channels.get("value", []) if isinstance(channels.get("value"), list) else []

        for channel in channels:
            if not isinstance(channel, dict):
                continue

            row = {
                "team_id": team_id,
                "team_name": team_name,
                "channel_id": channel.get("id"),
                "channel_name": channel.get("displayName") or channel.get("name"),
                "membership_type": channel.get("membershipType"),
            }

            # Preserve additional useful metadata for later ACL discovery and diagnostics.
            for extra_key in (
                "description",
                "email",
                "webUrl",
                "createdDateTime",
                "isArchived",
                "isFavoriteByDefault",
                "tenantId",
            ):
                if extra_key in channel and channel.get(extra_key) is not None:
                    row[extra_key] = channel.get(extra_key)

            for extra_key in (
                "description",
                "mail",
                "visibility",
                "webUrl",
                "resourceProvisioningOptions",
                "tenantId",
            ):
                if extra_key in team and team.get(extra_key) is not None:
                    row[f"team_{extra_key}"] = team.get(extra_key)

            discovered.append(row)

    return discovered


def fetch_channel_messages(
    org: str,
    team_id: str,
    team_name: str,
    channel_id: str,
    channel_name: str,
    settings: Optional[Any] = None,
    store_path: Optional[Path] = None,
):
    """Fetch root messages and replies from a Teams channel using delta sync.

    Returns a generator of raw Graph message dicts (root messages and replies),
    with attached metadata and reply relationships. Uses DeltaStore to persist
    delta tokens per channel.

    Modes:
      - First sync (no token): calls `/teams/{team_id}/channels/{channel_id}/messages/delta`
        and fetches all pages, plus replies for each message.
      - Incremental sync (token exists): same as mail — replays full deltaLink URL
        if stored value is a URL, otherwise uses `$deltatoken` param.

    Args:
      org: organization slug (used for DeltaStore path)
      team_id: Teams group ID
      team_name: Teams group display name
      channel_id: channel ID
      channel_name: channel display name
      settings: optional settings object; falls back to `get_settings()`
      store_path: optional test path for DeltaStore
    """
    settings = settings or get_settings()
    token = get_access_token(settings)

    store = DeltaStore(org, path=store_path) if store_path is not None else DeltaStore(org)
    key = f"teams:{team_id}:{channel_id}"
    rec = store.get(key)

    is_incremental = bool(rec and rec.get("delta_token"))

    if is_incremental:
        delta_token = rec["delta_token"]
        log.info(
            "Existing delta token for teams:%s:%s — performing incremental sync",
            team_id,
            channel_id,
        )
        # If stored value is a full URL, replay directly.
        if isinstance(delta_token, str) and delta_token.startswith(("http://", "https://")):
            endpoint = delta_token
            params = None
        else:
            endpoint = f"/teams/{team_id}/channels/{channel_id}/messages/delta"
            params = {"$deltatoken": delta_token}
    else:
        log.info(
            "No delta token for teams:%s:%s — performing full sync",
            team_id,
            channel_id,
        )
        endpoint = f"/teams/{team_id}/channels/{channel_id}/messages/delta"
        params = None

    # Fetch root messages using delta (handles pagination via graph_get)
    res = graph_get(endpoint, token, params=params, return_last_response=True)

    root_messages = []
    last_json = None
    if isinstance(res, tuple):
        root_messages, last_json = res
    else:
        if isinstance(res, list):
            root_messages = res
        elif isinstance(res, dict):
            if "value" in res and isinstance(res["value"], list):
                root_messages = res["value"]
                last_json = res
            else:
                yield res
                return

    # Fetch replies for each root message
    all_messages = []
    for root_msg in root_messages:
        if not isinstance(root_msg, dict):
            continue

        root_id = root_msg.get("id")
        if not root_id:
            continue

        # Attach metadata to root message
        root_msg_with_meta = dict(root_msg)
        root_msg_with_meta["team_id"] = team_id
        root_msg_with_meta["team_name"] = team_name
        root_msg_with_meta["channel_id"] = channel_id
        root_msg_with_meta["channel_name"] = channel_name

        # Preserve or synthesize channelIdentity
        if "channelIdentity" not in root_msg_with_meta:
            root_msg_with_meta["channelIdentity"] = {
                "teamId": team_id,
                "channelId": channel_id,
            }

        all_messages.append(root_msg_with_meta)
        yield root_msg_with_meta

        # Fetch replies for this root message
        try:
            replies_endpoint = f"/teams/{team_id}/channels/{channel_id}/messages/{root_id}/replies"
            replies_res = graph_get(replies_endpoint, token)

            replies = []
            if isinstance(replies_res, dict) and "value" in replies_res and isinstance(replies_res["value"], list):
                replies = replies_res["value"]
            elif isinstance(replies_res, list):
                replies = replies_res

            for reply in replies:
                if not isinstance(reply, dict):
                    continue

                # Inject replyToId and metadata
                reply_with_meta = dict(reply)
                if "replyToId" not in reply_with_meta:
                    reply_with_meta["replyToId"] = root_id

                reply_with_meta["team_id"] = team_id
                reply_with_meta["team_name"] = team_name
                reply_with_meta["channel_id"] = channel_id
                reply_with_meta["channel_name"] = channel_name

                # Preserve or synthesize channelIdentity
                if "channelIdentity" not in reply_with_meta:
                    reply_with_meta["channelIdentity"] = {
                        "teamId": team_id,
                        "channelId": channel_id,
                    }

                all_messages.append(reply_with_meta)
                yield reply_with_meta

        except Exception as exc:
            log.warning(
                "Failed to fetch replies for message %s in teams:%s:%s: %s",
                root_id,
                team_id,
                channel_id,
                exc,
            )

    # Extract and store delta token
    new_token = None
    if last_json:
        delta_link = last_json.get("@odata.deltaLink") or last_json.get("odata.deltaLink")
        if delta_link:
            new_token = _extract_delta_token_from_link(delta_link)

    if new_token:
        try:
            store.set(key, new_token, len(all_messages))
            store.save()
            log.info("Updated delta token for teams:%s:%s (messages=%d)", team_id, channel_id, len(all_messages))
        except Exception as exc:
            log.error("Failed to persist delta token for teams:%s:%s: %s", team_id, channel_id, exc)
    else:
        log.warning("No @odata.deltaLink found for teams:%s:%s — delta token not updated", team_id, channel_id)
