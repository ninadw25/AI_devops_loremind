"""Zoho fetcher: OAuth refresh-token retrieval and Mail/Cliq GET helpers.

Mirrors infra/outlook/fetcher.py's shape (token cache, paginated GET with
retry/backoff, a small cursor store for incremental sync) but for Zoho's
self-client OAuth flow and REST APIs instead of Microsoft Graph.

This module imports only `common.*` and third-party `httpx`.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

from common.config import get_settings
from common.logger import get_logger
from common import checkpoint as _checkpoint

log = get_logger("zoho.fetcher")

# Simple in-memory token cache: keyed by client_id (one org's self-client).
_TOKEN_CACHE: Dict[str, Dict[str, Any]] = {}


class ZohoCursorStore:
    """Persist per-account/channel incremental-sync cursors.

    Stored JSON shape:

    {
      "mail:user@org.zoho.com": {
        "cursor": "1718000000000",
        "last_sync": "2026-06-08T12:00:00+00:00",
        "message_count": 42
      },
      "cliq:channel_id": { ... }
    }
    """

    def __init__(self, org: str, path: Optional[Path] = None) -> None:
        self.org = org
        if path is None:
            state_dir = _checkpoint._state_dir()
            state_dir.mkdir(parents=True, exist_ok=True)
            self.path = state_dir / f"{org}_zoho_cursors.json"
        else:
            self.path = Path(path)
            self.path.parent.mkdir(parents=True, exist_ok=True)

        self._data: Dict[str, Dict[str, Any]] = {}
        self.load()

    def load(self) -> Dict[str, Dict[str, Any]]:
        if not self.path.exists():
            self._data = {}
            return self._data
        try:
            import json

            text = self.path.read_text(encoding="utf-8")
            self._data = json.loads(text) if text.strip() else {}
        except Exception as exc:
            log.warning("ZohoCursorStore: failed to load %s — returning empty store: %s", self.path, exc)
            self._data = {}
        return self._data

    def get(self, key: str) -> Optional[Dict[str, Any]]:
        rec = self._data.get(key)
        if not rec:
            return None
        return {"cursor": rec.get("cursor"), "message_count": rec.get("message_count")}

    def set(self, key: str, cursor: str, message_count: int) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self._data[key] = {
            "cursor": cursor,
            "last_sync": now,
            "message_count": int(message_count),
        }

    def save(self) -> None:
        payload = dict(self._data)
        try:
            _checkpoint._atomic_write(self.path, payload)
            log.info("ZohoCursorStore: saved %d keys -> %s", len(payload), self.path)
        except Exception as exc:
            log.error("ZohoCursorStore: failed to save %s: %s", self.path, exc)
            raise


def _parse_retry_after(header_value: Optional[str]) -> Optional[float]:
    """Parse Retry-After header supporting delta-seconds or HTTP-date."""
    if not header_value:
        return None
    header_value = header_value.strip()
    try:
        return float(header_value)
    except ValueError:
        pass
    try:
        from email.utils import parsedate_to_datetime

        when = parsedate_to_datetime(header_value)
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        return max(0.0, (when - datetime.now(timezone.utc)).total_seconds())
    except Exception:
        return None


def get_access_token(settings=None) -> str:
    """Acquire a Zoho access token via the OAuth refresh-token grant.

    Self-client flow (no redirect URI): a refresh_token, generated once via
    the api-console grant-code exchange, is exchanged for short-lived
    (1h) access tokens indefinitely.

    Behavior:
        - Caches token in-process keyed by client_id
        - Refreshes 60s before expiry
        - Raises RuntimeError on missing config or HTTP/parsing failures
    """
    settings = settings or get_settings()

    client_id = settings.zoho_client_id
    client_secret = settings.zoho_client_secret
    refresh_token = settings.zoho_refresh_token

    if not client_id or not client_secret or not refresh_token:
        raise RuntimeError("Zoho OAuth credentials not configured in settings")

    cache = _TOKEN_CACHE.get(client_id)
    now_ts = time.time()
    if cache:
        expires_at = cache.get("expires_at", 0)
        if now_ts < (expires_at - 60):
            log.debug("Using cached Zoho token for client %s (expires in %.0fs)", client_id, expires_at - now_ts)
            return cache["access_token"]

    token_url = f"{settings.zoho_accounts_url}/oauth/v2/token"
    data = {
        "grant_type": "refresh_token",
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
    }

    log.info("Requesting Zoho access token for client %s", client_id)
    try:
        with httpx.Client(timeout=30) as client:
            resp = client.post(token_url, data=data)
            resp.raise_for_status()
            j = resp.json()
    except Exception as exc:
        log.error("Failed to obtain Zoho token: %s", exc)
        raise RuntimeError(f"Zoho token request failed: {exc}") from exc

    access_token = j.get("access_token")
    expires_in = j.get("expires_in")
    if not access_token or not expires_in:
        # Keys only — the body carries access_token, and logs are readable
        # through the control API's /logs endpoints.
        log.error("Unexpected Zoho token response, keys=%s", sorted(j))
        raise RuntimeError("Zoho token response missing fields")

    expires_at = now_ts + float(expires_in)
    _TOKEN_CACHE[client_id] = {
        "access_token": access_token,
        "expires_at": expires_at,
        "fetched_at": now_ts,
    }
    log.info("Obtained Zoho access token for client %s (expires in %ds)", client_id, int(expires_in))
    return access_token


def zoho_get(
    url: str,
    token: str,
    params: Optional[Dict[str, Any]] = None,
    max_retries: int = 5,
) -> Any:
    """Single-page GET against a Zoho REST endpoint with 429/5xx retry.

    Unlike Microsoft Graph, Zoho Mail/Cliq do not use a uniform `@odata`-style
    pagination envelope, so this helper performs ONE request and returns the
    parsed JSON; callers handle Zoho's own paging convention per-endpoint
    (start/limit for Mail, next_token for Cliq) by calling this repeatedly.

    Args:
        url: full URL to GET.
        token: bearer token (sent as `Zoho-oauthtoken`, not `Bearer`).
        params: query params.
        max_retries: 429 / 5xx / transport retry budget.
    """
    headers = {"Authorization": f"Zoho-oauthtoken {token}", "Accept": "application/json"}
    backoffs = [2, 4, 8, 16, 32]

    attempt = 0
    while True:
        try:
            with httpx.Client(timeout=30) as client:
                resp = client.get(url, headers=headers, params=params)
            status = resp.status_code
            if status == 429:
                attempt += 1
                if attempt > max_retries:
                    resp.raise_for_status()
                retry_after = _parse_retry_after(resp.headers.get("Retry-After"))
                wait = retry_after if retry_after is not None else backoffs[min(attempt - 1, len(backoffs) - 1)]
                log.warning("Zoho rate-limited (429) on %s — sleeping %.1fs (attempt %d/%d)", url, wait, attempt, max_retries)
                time.sleep(wait)
                continue
            if status in (502, 503, 504):
                attempt += 1
                if attempt > max_retries:
                    resp.raise_for_status()
                wait = backoffs[min(attempt - 1, len(backoffs) - 1)]
                log.warning("Zoho transient error %d on %s — retrying in %.1fs (attempt %d/%d)", status, url, wait, attempt, max_retries)
                time.sleep(wait)
                continue

            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError:
            raise
        except Exception as exc:
            attempt += 1
            if attempt > max_retries:
                log.error("Zoho GET failed on %s after %d retries: %s", url, attempt - 1, exc)
                raise RuntimeError(f"Zoho GET failed: {exc}") from exc
            wait = backoffs[min(attempt - 1, len(backoffs) - 1)]
            log.warning("Zoho GET exception on %s — retrying in %.1fs (attempt %d/%d): %s", url, wait, attempt, max_retries, exc)
            time.sleep(wait)
            continue


# ─── Zoho Mail ──────────────────────────────────────────────────────────────

def fetch_mail_accounts(token: str, settings=None) -> List[Dict[str, Any]]:
    """Every Zoho Mail account this tenant should ingest.

    `GET /accounts` returns only the accounts belonging to the user who granted
    the token — for a self-client grant that is exactly ONE mailbox, the
    grantor's. A run against a 60-person org silently replicated one person's
    mail and reported success, because "1 of 1 principals" is indistinguishable
    from a complete run in the logs.

    So when the tenant has an org id, ask the organization endpoint instead and
    get the whole estate.

    A failure on the org path RAISES — it must never degrade to the personal
    view. Quietly falling back would reproduce the very bug this function exists
    to fix: one mailbox replicated, "[1/1] principals" logged, exit 0.

    Opting out is explicit: `ZOHO_MAIL_SINGLE_MAILBOX=true` for a tenant that
    genuinely has one mailbox. It is a separate setting rather than "no org_id"
    because org_id belongs to Cliq's admin discovery — a single-mailbox tenant
    still needs it, so making mail infer scope from it would force a choice
    between working mail and working Cliq.

    The org path needs `ZohoMail.organization.accounts.READ` on the grant; a
    401/403 means the client must re-consent, and that is worth failing a run
    over. (Verified live: without the scope Zoho answers 401
    `INVALID_OAUTHSCOPE` — the endpoint itself is correct.)
    """
    settings = settings or get_settings()
    org_id = getattr(settings, "zoho_org_id", "") or ""
    single = bool(getattr(settings, "zoho_mail_single_mailbox", False))

    if single:
        log.info("Tenant declared single-mailbox — using /accounts, org_id %s left to Cliq.",
                 org_id or "(unset)")
    elif org_id:
        try:
            j = zoho_get(f"{settings.zoho_mail_base_url}/organization/{org_id}/accounts", token)
        except Exception as exc:
            raise RuntimeError(
                f"Zoho organization API failed for org_id={org_id!r} ({exc}). The grant likely "
                "lacks ZohoMail.organization.accounts.READ — the client must re-consent. "
                "Refusing to fall back to the token owner's own mailbox: that would replicate "
                "one inbox and report a complete run. If this tenant really has one mailbox, "
                "set mail_single_mailbox=true in its Vault zoho entry (leave org_id — Cliq "
                "needs it)."
            ) from exc
        data = j.get("data", j) if isinstance(j, dict) else j
        if not isinstance(data, list) or not data:
            raise RuntimeError(
                f"Zoho organization API returned no mailboxes for org_id={org_id!r}. "
                "Refusing to fall back to the token owner's own mailbox."
            )
        log.info("Zoho org %s: %d mailboxes via organization API", org_id, len(data))
        return data

    j = zoho_get(f"{settings.zoho_mail_base_url}/accounts", token)
    data = j.get("data", j) if isinstance(j, dict) else j
    accounts = data if isinstance(data, list) else []
    log.warning("No ZOHO_ORG_ID for this tenant — fetching only the %d mailbox(es) the token's "
                "own user owns, not the organization's. Set it in Vault for a multi-mailbox org.",
                len(accounts))
    return accounts


def fetch_mail_folders(account_id: str, settings=None) -> List[Dict[str, Any]]:
    """Every folder in `account_id`'s mailbox (GET /accounts/{id}/folders).

    `/messages/view` takes `folderId` as an optional FILTER among many
    (status, labelid, threadId, ...) — Zoho's docs never state what folders it
    covers when `folderId` is omitted. In production that omission silently
    covered 17 of a 30-folder mailbox and skipped Sent entirely: a client's
    entire outgoing correspondence, replicated as zero messages, reported as a
    clean successful run. There is no "give me every folder" call to fall back
    on, so every folder from here is now passed to /messages/view explicitly —
    the fetch is only ever as complete as this list.
    """
    settings = settings or get_settings()
    j = zoho_get(f"{settings.zoho_mail_base_url}/accounts/{account_id}/folders",
                 get_access_token(settings))
    data = j.get("data", j) if isinstance(j, dict) else j
    return data if isinstance(data, list) else []


def fetch_mail_for_account(
    org: str,
    account_id: str,
    account_email: str,
    scanback_days: Optional[int] = None,
    settings: Optional[Any] = None,
    store_path: Optional[Path] = None,
    since_epoch_ms: Optional[int] = None,
    manage_cursor: bool = True,
    skip_ids: Optional[set] = None,
    skip_content: Optional[Any] = None,
    folders: Optional[List[Dict[str, Any]]] = None,
):
    """Fetch every Zoho Mail message for one account, folder by folder.

    Zoho Mail's list endpoint pages via `start`/`limit` (1-indexed, max 100
    per page in practice — Zoho's docs claim 200 but this account never
    returned more than 100 regardless of what was asked) and returns messages
    newest-first within a folder. Each folder is paged to an empty page, and
    the run-wide watermark (latest `receivedTime` seen across ALL folders) is
    what gets stored as the cursor — Zoho Mail has no native delta-token, so
    this is a watermark, not a true delta.

    Yields raw Zoho Mail message dicts (already containing full `content`,
    fetched per-message since the list view omits body).

    The L0 sync front-end owns its cursor in Postgres, so it passes
    `since_epoch_ms` (the watermark to resume from) and `manage_cursor=False`
    (don't touch the legacy JSONL cursor store). Defaults preserve the old
    JSONL-cursor behaviour for `infra/zoho/run.py`.

    Request budget is the whole game here: one list call covers up to 100
    messages, but the body is a SEPARATE GET per message, so content is
    ~99.5% of every request this function spends. Three things exist to not
    spend it twice:

      `skip_ids`      source message-ids already in the replica. A resumed
                      backfill re-lists every folder (cheap) but never
                      re-fetches a body it already holds, so an interruption
                      costs list calls only.
      `skip_content`  predicate over the LIST ROW — return True to yield the
                      row with an empty body instead of buying it. The list row
                      already carries fromAddress/subject/to/cc, which is every
                      signal `pipeline.filter.is_noise` uses except the
                      empty-body rule, so noise can be judged before paying.
      in-run dedup    a message can surface under more than one folder view
                      (Zoho's folder list includes aggregate folders like "All
                      Mail" that mirror everything else) — the SAME messageId
                      is only ever fetched/yielded once per run, off the first
                      folder that serves it.

    A body-skipped message is still replicated (never silently dropped); its
    payload carries `content_skipped: true` so a later pass can buy the bodies
    back if the noise rules change.
    """
    settings = settings or get_settings()
    scanback_days = scanback_days if scanback_days is not None else settings.zoho_mail_scanback_days
    get_access_token(settings)   # fail fast on bad credentials, before any paging

    store = None
    if manage_cursor:
        store = ZohoCursorStore(org, path=store_path) if store_path is not None else ZohoCursorStore(org)
    key = f"mail:{account_email}"
    if since_epoch_ms is not None:
        watermark_ms = int(since_epoch_ms)
    else:
        rec = store.get(key) if store else None
        watermark_ms = int(rec["cursor"]) if rec and rec.get("cursor") else None

    if watermark_ms is None:
        scanback_dt = datetime.now(timezone.utc) - timedelta(days=int(scanback_days))
        watermark_ms = int(scanback_dt.timestamp() * 1000)
        log.info("No cursor for %s — full sync since %s", account_email, scanback_dt.isoformat())
    else:
        log.info("Existing cursor for %s — incremental sync since epoch_ms=%d", account_email, watermark_ms)

    account_base = f"{settings.zoho_mail_base_url}/accounts/{account_id}"
    list_url = f"{account_base}/messages/view"
    page_size = 100

    folder_list = folders if folders is not None else fetch_mail_folders(account_id, settings)
    if not folder_list:
        raise RuntimeError(f"Zoho returned no folders for account {account_id} ({account_email}) "
                           "— refusing to fall back to an unscoped list call.")
    log.info("Zoho account %s: %d folders to fetch", account_email, len(folder_list))

    newest_seen_ms = watermark_ms
    fetched = 0
    seen_ids: set = set()

    for folder in folder_list:
        folder_id = folder.get("folderId")
        folder_name = folder.get("folderName", "?")
        if not folder_id:
            continue
        start = 1

        while True:
            # Re-read the token per request, never once per mailbox. Zoho access
            # tokens expire after an hour, and a full-history mailbox pages for
            # far longer than that — a captured token turned every call into a
            # 401 exactly 60 minutes in, killing the backfill mid-mailbox.
            # get_access_token serves from its in-process cache until 60s before
            # expiry, so this is a dict lookup on all but one call an hour.
            page = zoho_get(list_url, get_access_token(settings),
                            params={"start": start, "limit": page_size, "folderId": folder_id})
            rows = page.get("data", []) if isinstance(page, dict) else []
            if not rows:
                break

            stop = False
            for row in rows:
                received_ms = int(row.get("receivedTime", 0) or 0)
                if received_ms <= watermark_ms:
                    stop = True
                    break
                newest_seen_ms = max(newest_seen_ms, received_ms)

                message_id = str(row.get("messageId", ""))
                if message_id in seen_ids:
                    continue      # already fetched/yielded off an earlier folder this run
                seen_ids.add(message_id)

                if skip_ids and message_id in skip_ids:
                    continue      # already replicated — re-listing is cheap, re-fetching is not

                if skip_content is not None and skip_content(row):
                    fetched += 1
                    yield {**row, "content": "", "content_skipped": True}
                    continue

                # List view omits body; fetch full content per message. The
                # content endpoint is folder-scoped by path — folder_id is the
                # loop's own, not the row's, since we asked this folder for it.
                content_url = f"{account_base}/folders/{folder_id}/messages/{message_id}/content"
                try:
                    content_resp = zoho_get(content_url, get_access_token(settings))
                    content = (content_resp.get("data", {}) or {}).get("content", "") if isinstance(content_resp, dict) else ""
                except httpx.HTTPStatusError as exc:
                    # An expired or revoked token is not a per-message problem: every
                    # remaining message would also fail, and swallowing it banks
                    # thousands of empty-bodied payloads that `skip_ids` would then
                    # skip forever on the next run — permanently blank mail that looks
                    # replicated. Fail the mailbox instead; its cursor stays put and
                    # the next run refetches cleanly.
                    if exc.response.status_code in (401, 403):
                        raise
                    log.warning("Failed to fetch content for message %s: %s", message_id, exc)
                    content = ""
                except Exception as exc:
                    log.warning("Failed to fetch content for message %s: %s", message_id, exc)
                    content = ""

                fetched += 1
                yield {**row, "folderId": folder_id, "content": content}

            if stop:
                break
            start += len(rows)

        log.info("Zoho account %s folder %r: done", account_email, folder_name)

    if store is not None and newest_seen_ms > watermark_ms:
        store.set(key, str(newest_seen_ms), fetched)
        store.save()
        log.info("Updated Zoho Mail cursor for %s (messages=%d)", account_email, fetched)


# ─── Zoho Cliq ──────────────────────────────────────────────────────────────

def fetch_cliq_channels(token: str, settings=None) -> List[Dict[str, Any]]:
    """List Cliq channels visible to this token (GET /channels)."""
    settings = settings or get_settings()
    j = zoho_get(f"{settings.zoho_cliq_base_url}/channels", token)
    data = j.get("channels", j) if isinstance(j, dict) else j
    return data if isinstance(data, list) else []


def fetch_cliq_channel_members(channel_id: str, token: str, settings=None) -> List[Dict[str, Any]]:
    """List members of a single Cliq channel."""
    settings = settings or get_settings()
    j = zoho_get(f"{settings.zoho_cliq_base_url}/channels/{channel_id}/members", token)
    data = j.get("data", j) if isinstance(j, dict) else j
    return data if isinstance(data, list) else []


def fetch_cliq_messages(
    org: str,
    channel_id: str,
    channel_name: str,
    scanback_days: Optional[int] = None,
    settings: Optional[Any] = None,
    store_path: Optional[Path] = None,
):
    """Fetch Cliq channel messages, page by page, newest-first.

    Cliq's message history endpoint pages via `next_token`. We store the
    latest seen `time` (epoch ms) as a watermark cursor, same approach as
    Zoho Mail — Cliq has no durable delta-link either.

    Yields raw Cliq message dicts annotated with channel_id/channel_name.
    """
    settings = settings or get_settings()
    scanback_days = scanback_days if scanback_days is not None else settings.zoho_cliq_scanback_days
    token = get_access_token(settings)

    store = ZohoCursorStore(org, path=store_path) if store_path is not None else ZohoCursorStore(org)
    key = f"cliq:{channel_id}"
    rec = store.get(key)
    watermark_ms = int(rec["cursor"]) if rec and rec.get("cursor") else None

    if watermark_ms is None:
        scanback_dt = datetime.now(timezone.utc) - timedelta(days=int(scanback_days))
        watermark_ms = int(scanback_dt.timestamp() * 1000)
        log.info("No cursor for channel %s — full sync since %s", channel_id, scanback_dt.isoformat())
    else:
        log.info("Existing cursor for channel %s — incremental sync since epoch_ms=%d", channel_id, watermark_ms)

    messages_url = f"{settings.zoho_cliq_base_url}/channels/{channel_id}/messages"
    next_token: Optional[str] = None
    newest_seen_ms = watermark_ms
    fetched = 0

    while True:
        params: Dict[str, Any] = {"limit": 100}
        if next_token:
            params["next_token"] = next_token
        page = zoho_get(messages_url, token, params=params)
        rows = page.get("messages", []) if isinstance(page, dict) else []
        if not rows:
            break

        stop = False
        for row in rows:
            msg_ms = int(row.get("time", 0) or 0)
            if msg_ms <= watermark_ms:
                stop = True
                break
            newest_seen_ms = max(newest_seen_ms, msg_ms)
            fetched += 1
            yield {**row, "channel_id": channel_id, "channel_name": channel_name}

        if stop:
            break
        next_token = page.get("next_token") if isinstance(page, dict) else None
        if not next_token:
            break

    if newest_seen_ms > watermark_ms:
        store.set(key, str(newest_seen_ms), fetched)
        store.save()
        log.info("Updated Cliq cursor for channel %s (messages=%d)", channel_id, fetched)
