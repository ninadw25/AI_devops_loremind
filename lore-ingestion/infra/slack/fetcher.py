"""
infra/slack/fetcher.py
Adapted from slack-connector; uses repo's `common` logger.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone, timedelta
from typing import Iterator, Optional

import requests

from common.config import get_settings
from common.logger import get_logger
from .capabilities import SlackCapabilities, detect as detect_capabilities

log = get_logger("slack.fetcher")

_settings = get_settings()


def _parse_allowlist(value: str | None) -> set[str]:
    if not value:
        return set()
    return {item.strip() for item in value.split(",") if item.strip()}


SLACK_BASE_URL = "https://slack.com"

REQUIRED_SCOPES = [
    "channels:read",
    "channels:history",
    "groups:read",
    "groups:history",
    "im:read",
    "im:history",
    "mpim:read",
    "mpim:history",
    "users:read",
    "users:read.email",
    "usergroups:read",
    "reactions:read",
    "files:read",
]

RECOMMENDED_SCOPE = "users:read.email"
OPTIONAL_SCOPES = ["mpim:read", "mpim:history", "usergroups:read", "reactions:read", "files:read"]

SCANBACK_DAYS = _settings.slack_scanback_days
MAX_RETRIES = 5
DEFAULT_RETRY_SLEEP = _settings.slack_default_retry_sleep
TIER3_HISTORY_PER_MINUTE = 50
TIER3_HISTORY_MIN_DELAY = 60.0 / TIER3_HISTORY_PER_MINUTE

_raw_delay = _settings.slack_request_delay
_app_tier = _settings.slack_app_tier.lower()

# Non-Marketplace apps created after 2025-05 are throttled hard on
# conversations.history/replies: ~1 request/minute and at most 15 messages
# per call. Selected via SLACK_APP_TIER=restricted (aliases below).
_RESTRICTED_TIERS = {"restricted", "non_marketplace", "non-marketplace", "marketplace_restricted"}

if _raw_delay is not None and _raw_delay.strip() != "":
    try:
        REQUEST_DELAY = float(_raw_delay)
    except Exception:
        REQUEST_DELAY = 0.5
else:
    if _app_tier == "dev":
        REQUEST_DELAY = 15.0
    elif _app_tier == "enterprise":
        REQUEST_DELAY = 0.3
    elif _app_tier in {"tier3", "tier-3", "tier_3"}:
        REQUEST_DELAY = TIER3_HISTORY_MIN_DELAY
    elif _app_tier in _RESTRICTED_TIERS:
        REQUEST_DELAY = 60.0    # ~1 conversations.history call per minute
    else:
        REQUEST_DELAY = 1.0

PAGE_LIMIT = 200
# conversations.history / conversations.replies page size. Restricted
# (non-Marketplace) apps are capped at 15 messages/call; others get 200.
HISTORY_PAGE_LIMIT = 15 if _app_tier in _RESTRICTED_TIERS else PAGE_LIMIT
MIN_DELAY = 0.1


class SlackFetcher:
    def __init__(self, token: str, org: str, base_url: str = SLACK_BASE_URL) -> None:
        self.token = token
        self.org = org
        self.base_url = base_url.rstrip("/")

        self._session = requests.Session()
        self._session.headers.update({"Authorization": f"Bearer {token}"})

        self._user_cache: dict[str, dict] = {}
        self.bot_allowlist: set[str] = _parse_allowlist(_settings.slack_bot_allowlist)

        self._fetched_threads: set[str] = set()

        self.team_id = ""
        self.team_name = ""
        self.bot_user_id = ""
        self.has_email_scope = False
        self.scope_list: set[str] = set()
        self.has_im_scope = False
        self.has_mpim_scope = False
        # Populated by validate_auth(): the resolved capability/pathway object
        # that every stage consults to pick the Grid-admin vs bot-fallback path.
        self.auth_payload: dict = {}
        self.capabilities: Optional[SlackCapabilities] = None

        # Fixed per-request delay from the app-tier preset. No runtime self-tuning:
        # honoring Retry-After on 429 (below) is the only backpressure mechanism.
        self.request_delay: float = REQUEST_DELAY
        self._rate_floor_delay: float = TIER3_HISTORY_MIN_DELAY if _app_tier in {"tier3", "tier-3", "tier_3"} else MIN_DELAY

    def _get(self, endpoint: str, params: dict | None = None) -> dict:
        url = f"{self.base_url}/api/{endpoint}"
        if endpoint in {"conversations.history", "conversations.replies"}:
            self.request_delay = max(self.request_delay, self._rate_floor_delay)
        time.sleep(self.request_delay)

        for attempt in range(1, MAX_RETRIES + 2):
            try:
                resp = self._session.get(url, params=params or {}, timeout=30)
            except requests.RequestException as exc:
                log.warning(f"[{endpoint}] network error (attempt {attempt}): {exc}")
                if attempt > MAX_RETRIES:
                    raise
                backoff = min(DEFAULT_RETRY_SLEEP * (2 ** (attempt - 1)), 60.0)
                time.sleep(backoff)
                continue

            if resp.status_code == 429:
                retry_after_header = resp.headers.get("Retry-After")
                try:
                    retry_after = float(retry_after_header) if retry_after_header is not None else DEFAULT_RETRY_SLEEP
                except ValueError:
                    retry_after = DEFAULT_RETRY_SLEEP

                log.warning(f"[{endpoint}] rate limited (429) — sleeping {retry_after}s (attempt {attempt}/{MAX_RETRIES})")
                if attempt > MAX_RETRIES:
                    raise RuntimeError(f"Rate limit exceeded after {MAX_RETRIES} retries: {endpoint}")
                time.sleep(retry_after)
                continue

            resp.raise_for_status()
            data = resp.json()

            rl_limit = resp.headers.get("X-RateLimit-Limit")
            rl_remaining = resp.headers.get("X-RateLimit-Remaining")
            rl_reset = resp.headers.get("X-RateLimit-Reset")
            if rl_limit or rl_remaining or rl_reset:
                log.debug(f"[{endpoint}] rate-limit headers: limit={rl_limit} remaining={rl_remaining} reset={rl_reset}")

            if not data.get("ok"):
                error = data.get("error", "unknown_error")
                if error in ("not_in_channel", "channel_not_found", "user_not_found"):
                    return data
                raise RuntimeError(f"Slack API error [{endpoint}]: {error}")

            return data

        raise RuntimeError(f"Exhausted retries for [{endpoint}]")

    def validate_auth(self, required_scopes: Optional[list[str]] = None) -> dict:
        url = f"{self.base_url}/api/auth.test"
        resp = self._session.get(url, timeout=30)
        resp.raise_for_status()
        data = resp.json()

        if not data.get("ok"):
            raise RuntimeError(f"Auth failed: {data.get('error', 'invalid_auth')}. Provide a valid Slack admin token.")

        self.team_id = data.get("team_id", "")
        self.team_name = data.get("team", "")
        self.bot_user_id = data.get("user_id", "")

        scopes_header = resp.headers.get("X-OAuth-Scopes", "")
        scope_list = [s.strip() for s in scopes_header.split(",") if s.strip()]
        self.scope_list = set(scope_list)
        self.has_email_scope = RECOMMENDED_SCOPE in self.scope_list
        self.has_im_scope = "im:read" in self.scope_list and "im:history" in self.scope_list
        self.has_mpim_scope = "mpim:read" in self.scope_list and "mpim:history" in self.scope_list

        # Resolve the capability/pathway object once, from the live token. Every
        # downstream stage (discovery ACL, normalizer enrichment, edit/delete)
        # branches on this rather than re-checking scopes ad hoc.
        self.auth_payload = data
        self.capabilities = detect_capabilities(data, scopes_header, token=self.token)
        log.info("Slack capabilities: %s", self.capabilities.summary())

        # Scope check is advisory, not fatal: a public-channels-only workspace
        # legitimately lacks groups:*; a Grid user token legitimately lacks the
        # bot history scopes (it reads via admin/Discovery). Fail ONLY when the
        # token can't read messages by ANY pathway — that's an actual misconfig.
        expected_scopes = required_scopes if required_scopes is not None else REQUIRED_SCOPES
        missing = [s for s in expected_scopes if s not in self.scope_list]
        if missing:
            log.warning("Slack scopes not granted (degraded coverage): %s", missing)

        can_read = self.capabilities.can_read_history or self.capabilities.can_admin_conversations
        if not can_read:
            raise RuntimeError(
                "Token cannot read messages by any pathway: needs at least one "
                "of channels:history / groups:history (bot) or "
                "admin.conversations:read on Enterprise Grid (admin). "
                f"Granted scopes: {sorted(self.scope_list)}"
            )

        if not self.has_email_scope:
            log.warning("users:read.email absent — Slack actors will use slack:{user_id} without Gmail cross-source merge.")

        missing_optional = [s for s in OPTIONAL_SCOPES if s not in self.scope_list]
        if missing_optional:
            log.info("Optional Slack scopes not granted — continuing with partial coverage: %s", ", ".join(missing_optional))

        log.info(f"Auth OK — team={self.team_name} bot={self.bot_user_id}")
        return data

    def load_user_cache(self) -> dict[str, dict]:
        log.info("Loading user directory…")
        cursor = ""
        total = 0
        while True:
            params: dict = {"limit": PAGE_LIMIT}
            if cursor:
                params["cursor"] = cursor
            data = self._get("users.list", params)
            members = data.get("members", [])
            for user in members:
                self._user_cache[user["id"]] = user
            total += len(members)
            cursor = data.get("response_metadata", {}).get("next_cursor", "")
            if not cursor:
                break
        log.info(f"User cache loaded: {total} members")
        return self._user_cache

    @property
    def user_cache(self) -> dict[str, dict]:
        if not self._user_cache:
            self.load_user_cache()
        return self._user_cache

    def get_user(self, user_id: str) -> Optional[dict]:
        if user_id in self.user_cache:
            return self.user_cache[user_id]
        log.debug(f"Cache miss for user {user_id} — fetching users.info")
        data = self._get("users.info", {"user": user_id})
        if data.get("ok"):
            user = data["user"]
            self._user_cache[user_id] = user
            return user
        return None

    def _enrich_files(self, msg: dict) -> dict:
        files = msg.get("files") or []
        if not files:
            return msg
        enriched_files: list[dict] = []
        changed = False
        for file_obj in files:
            file_data = dict(file_obj)
            file_id = file_obj.get("id")
            if file_id:
                try:
                    data = self._get("files.info", {"file": file_id})
                    if data.get("ok") and data.get("file"):
                        file_data.update(data["file"])
                        changed = True
                except Exception as exc:
                    log.debug("files.info failed for %s: %s", file_id, exc)
            enriched_files.append(file_data)
        if not changed:
            return msg
        return {**msg, "files": enriched_files}

    def list_channels(self, types: str = "public_channel,private_channel,im,mpim", exclude_archived: bool = True, only_channels: Optional[list[str]] = None) -> list[dict]:
        log.info(f"Discovering channels (types={types}, exclude_archived={exclude_archived})…")
        channels: list[dict] = []
        cursor = ""
        while True:
            params: dict = {"types": types, "exclude_archived": str(exclude_archived).lower(), "limit": PAGE_LIMIT}
            if cursor:
                params["cursor"] = cursor
            data = self._get("conversations.list", params)
            batch = data.get("channels", [])
            channels.extend(batch)
            cursor = data.get("response_metadata", {}).get("next_cursor", "")
            if not cursor:
                break
        if only_channels:
            only_set = set(only_channels)
            channels = [c for c in channels if c.get("id") in only_set or c.get("name") in only_set]
        log.info(f"Discovered {len(channels)} channels")
        return channels

    def fetch_channel_messages(self, channel_id: str, oldest: Optional[float] = None, 
                                latest: Optional[float] = None) -> list[dict]:
        log.debug(f"Fetching messages for channel {channel_id}…")
        messages: list[dict] = []
        cursor = ""
        while True:
            params: dict = {"channel": channel_id, "limit": HISTORY_PAGE_LIMIT}
            if oldest is not None:
                params["oldest"] = str(oldest)
            if latest is not None:
                params["latest"] = str(latest)
            if cursor:
                params["cursor"] = cursor
            data = self._get("conversations.history", params)
            if not data.get("ok"):
                error = data.get("error", "")
                if error in ("not_in_channel", "channel_not_found"):
                    log.warning(f"Channel {channel_id} inaccessible: {error}")
                    return []
                raise RuntimeError(f"conversations.history error: {error}")
            messages.extend(data.get("messages", []))
            if not data.get("has_more"):
                break
            cursor = data.get("response_metadata", {}).get("next_cursor", "")
            if not cursor:
                break
        log.debug(f"Channel {channel_id}: {len(messages)} messages fetched")
        # Ensure every raw message records its channel id in slack_meta
        for msg in messages:
            sm = msg.get("slack_meta") or {}
            sm.setdefault("channel_id", channel_id)
            msg["slack_meta"] = sm
        return messages

    def fetch_channel_messages_reverse(
        self, 
        channel_id: str, 
        anchor_ts: Optional[float] = None,
        since: Optional[float] = None,
        cursor: Optional[str] = None,
    ) -> tuple[list[dict], Optional[str], Optional[float]]:
        """
        Fetch messages from NOW backwards (reverse-chronological).
        
        Slack's conversations.history returns messages newest-first by default.
        This method paginates backwards through history with resume support.
        
        Args:
            channel_id: Slack channel ID
            anchor_ts: Upper bound timestamp (default: now)
                       This is the "NOW" we started from
            since: Lower bound timestamp (how far back to go)
            cursor: Pagination cursor for resume
            
        Returns:
            (messages, next_cursor, oldest_ts_seen)
            - messages: List of message dicts (newest first within batch)
            - next_cursor: Cursor for next batch (None if complete)
            - oldest_ts_seen: Oldest timestamp in this batch (for progress tracking)
        """
        log.debug(f"Reverse-fetching messages for channel {channel_id}...")
        
        params: dict = {"channel": channel_id, "limit": HISTORY_PAGE_LIMIT}
        
        if anchor_ts is not None:
            params["latest"] = str(anchor_ts)
        if since is not None:
            params["oldest"] = str(since)
        if cursor:
            params["cursor"] = cursor
            
        data = self._get("conversations.history", params)
        
        if not data.get("ok"):
            error = data.get("error", "")
            if error in ("not_in_channel", "channel_not_found"):
                log.warning(f"Channel {channel_id} inaccessible: {error}")
                return [], None, None
            raise RuntimeError(f"conversations.history error: {error}")
        
        messages = data.get("messages", [])
        
        # Track oldest timestamp in this batch for progress
        oldest_ts = None
        if messages:
            # Messages are newest-first; last one is oldest in batch
            oldest_ts = float(messages[-1]["ts"])
        
        # Enrich with channel_id
        for msg in messages:
            sm = msg.get("slack_meta") or {}
            sm.setdefault("channel_id", channel_id)
            msg["slack_meta"] = sm
        
        # Get next cursor for pagination
        next_cursor = None
        if data.get("has_more"):
            next_cursor = data.get("response_metadata", {}).get("next_cursor", "")
            if not next_cursor:
                next_cursor = None
        
        log.debug(f"Channel {channel_id}: fetched {len(messages)} messages, "
                  f"oldest_ts={oldest_ts}, has_more={data.get('has_more', False)}")
        
        return messages, next_cursor, oldest_ts

    def iter_channel_events_reverse(
        self, 
        channel: dict, 
        anchor_ts: Optional[float] = None,
        since: Optional[float] = None,
        checkpoint: Optional[dict] = None,
    ):
        """
        Iterate through channel messages from NOW backwards with resume support.
        
        Args:
            channel: Channel dict with at least "id" key
            anchor_ts: Starting timestamp (default: now)
            since: Don't fetch messages older than this
            checkpoint: Resume checkpoint dict with "cursor" and "oldest_ts"
            
        Yields:
            (msg, replies) tuples, just like iter_channel_events
        """
        channel_id = channel["id"]
        
        # Set anchor to NOW if not provided
        if anchor_ts is None:
            anchor_ts = datetime.now(timezone.utc).timestamp()
        
        # Use checkpoint if resuming
        cursor = checkpoint.get("cursor") if checkpoint else None
        oldest_so_far = checkpoint.get("oldest_fetched") if checkpoint else anchor_ts
        
        while True:
            messages, cursor, batch_oldest = self.fetch_channel_messages_reverse(
                channel_id=channel_id,
                anchor_ts=anchor_ts,
                since=since,
                cursor=cursor,
            )
            
            if not messages:
                break
            
            # Track progress
            if batch_oldest and batch_oldest < oldest_so_far:
                oldest_so_far = batch_oldest
            
            for msg in messages:
                msg = self._enrich_files(msg)
                reply_count = msg.get("reply_count", 0)
                
                if reply_count > 0:
                    thread_ts = msg.get("thread_ts", msg["ts"])
                    replies = self.fetch_thread(channel_id, thread_ts)
                    yield msg, replies, {"oldest_fetched": oldest_so_far, "cursor": cursor}
                else:
                    yield msg, [], {"oldest_fetched": oldest_so_far, "cursor": cursor}
            
            if cursor is None:
                # All messages fetched for this channel
                break
            
            # Check if we've reached the since threshold
            if since and oldest_so_far <= since:
                break

    def fetch_thread(self, channel_id: str, thread_ts: str) -> list[dict]:
        key = f"{channel_id}:{thread_ts}"
        if key in self._fetched_threads:
            log.debug(f"Skipping already-fetched thread {key}")
            return []
        self._fetched_threads.add(key)
        log.debug(f"Fetching thread {key}…")
        messages: list[dict] = []
        cursor = ""
        while True:
            params: dict = {"channel": channel_id, "ts": thread_ts, "limit": HISTORY_PAGE_LIMIT}
            if cursor:
                params["cursor"] = cursor
            data = self._get("conversations.replies", params)
            if not data.get("ok"):
                log.warning(f"Thread {key} inaccessible: {data.get('error')}")
                return []
            messages.extend(data.get("messages", []))
            if not data.get("has_more"):
                break
            cursor = data.get("response_metadata", {}).get("next_cursor", "")
            if not cursor:
                break
        # Ensure replies include channel id in slack_meta
        for msg in messages:
            sm = msg.get("slack_meta") or {}
            sm.setdefault("channel_id", channel_id)
            msg["slack_meta"] = sm
        return messages

    def join_channel(self, channel_id: str) -> bool:
        try:
            data = self._get("conversations.join", {"channel": channel_id})
        except Exception as exc:
            log.warning(f"conversations.join failed for {channel_id}: {exc}")
            return False
        if not data.get("ok"):
            err = data.get("error", "")
            log.warning(f"conversations.join returned error for {channel_id}: {err}")
            return False
        log.info(f"Joined channel {channel_id}")
        return True

    def iter_channel_events(self, channel: dict, since: Optional[datetime] = None) -> Iterator[tuple[dict, list[dict]]]:
        channel_id = channel["id"]
        oldest: Optional[float] = None
        if since is not None:
            oldest = since.timestamp()
        scanback_cutoff = (datetime.now(timezone.utc) - timedelta(days=SCANBACK_DAYS)).timestamp()
        messages = self.fetch_channel_messages(channel_id, oldest=oldest)
        for msg in messages:
            msg = self._enrich_files(msg)
            reply_count = msg.get("reply_count", 0)
            latest_reply_ts = msg.get("latest_reply", "0")
            if reply_count > 0:
                should_fetch = True
                if since is not None:
                    latest_reply_float = float(latest_reply_ts)
                    if latest_reply_float < scanback_cutoff:
                        should_fetch = False
                if should_fetch:
                    thread_ts = msg.get("thread_ts", msg["ts"])
                    replies = self.fetch_thread(channel_id, thread_ts)
                    yield msg, replies
                    continue
            yield msg, []
