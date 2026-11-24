"""
infra/slack/discovery_api.py
────────────────────────────
Enterprise Grid **Discovery API** client — the system-of-record on Grid for
both (1) private-channel ACL membership and (2) the edit/delete change-stream.

Why this is the Grid path (user decision, 2026-06): Slack's normal admin Web API
has NO org-wide "list members" method for private channels, and Audit Logs do
not carry message edits/deletes. The Discovery API closes both gaps:

    discovery.conversations.info     → full member list of ANY channel (ACL)
    discovery.conversations.recent   → which channels changed in a time window
    discovery.conversations.history  → messages (with edit/delete markers)
    discovery.conversations.edits    → authoritative edit/delete records
    discovery.users.list             → org-wide user directory
    discovery.conversations.list     → all channels org-wide

Edit/delete detection (deterministic, no snapshot-diff):
    • DELETE: root message `type == "deleted"`, OR an `edits[]` entry with
      `subtype == "message_deleted"`.
    • EDIT:   root message has an `edited:{user,ts}` object, OR an `edits[]`
      entry with `subtype == "message_changed"`; the new text is the root
      `text`, prior versions live in `edits[].previous`.

This emits the SAME change records the bot-fallback reconciler emits
(`source_id` = "<channel>:<ts>", action edited/deleted), so everything
downstream is pathway-agnostic.

ENABLEMENT: Discovery is Enterprise-Grid-only, needs an Org-Owner-installed,
Slack-APPROVED app and a `discovery:read` org token. It is invisible (403) until
Slack enables it for the org — so this client is built + unit-tested with fake
responses and only runs live once a Grid customer provisions Discovery.

Response-shape caveats (flagged by research, verify against a live token):
  • `conversations.info` member list key may be `info.members` or
    `channel_info.members` — parsed defensively below.
  • `discovery.users.list` may key users as `users[]` or `members[]`.
  • exact per-method Discovery rate limits aren't published — honor 429 +
    Retry-After and keep pages ≤1000 / poll ≥60s.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Iterable, Iterator, Optional

import requests

from common.logger import get_logger

log = get_logger("slack.discovery")

SLACK_BASE_URL = "https://slack.com"
RECENT_MAX_WINDOW_S = 7 * 24 * 3600   # discovery.conversations.recent caps at 7 days

Requester = Callable[[str, dict], dict]


class SlackDiscoveryError(RuntimeError):
    """A Discovery API call failed non-recoverably (incl. not-enabled/403)."""


class SlackDiscoveryClient:
    """Client over Slack's `discovery.*` methods.

    Inject `requester(method, params) -> dict` in tests; production uses the
    real Bearer-auth HTTP getter with 429/Retry-After backoff.
    """

    def __init__(
        self,
        token: str,
        base_url: str = SLACK_BASE_URL,
        requester: Optional[Requester] = None,
        request_delay: float = 0.5,
        max_retries: int = 5,
        default_retry_sleep: float = 3.0,
    ) -> None:
        self.token = token
        self.base_url = base_url.rstrip("/")
        self._requester = requester or self._http_get
        self._request_delay = request_delay
        self._max_retries = max_retries
        self._default_retry_sleep = default_retry_sleep
        self._session = requests.Session()
        self._session.headers.update({"Authorization": f"Bearer {token}"})

    # ── transport ────────────────────────────────────────────────────
    def _http_get(self, method: str, params: dict) -> dict:
        url = f"{self.base_url}/api/{method}"
        time.sleep(self._request_delay)
        for attempt in range(1, self._max_retries + 2):
            resp = self._session.get(url, params=params, timeout=60)
            if resp.status_code == 429:
                retry_after = resp.headers.get("Retry-After")
                sleep_s = float(retry_after) if retry_after else self._default_retry_sleep
                if attempt > self._max_retries:
                    raise SlackDiscoveryError(f"{method}: rate limited after {self._max_retries} retries")
                log.warning("[%s] 429 — sleeping %ss (attempt %d)", method, sleep_s, attempt)
                time.sleep(sleep_s)
                continue
            if resp.status_code >= 500:
                if attempt > self._max_retries:
                    resp.raise_for_status()
                time.sleep(min(self._default_retry_sleep * 2 ** (attempt - 1), 60.0))
                continue
            resp.raise_for_status()
            return resp.json()
        raise SlackDiscoveryError(f"{method}: exhausted retries")

    def _get(self, method: str, params: Optional[dict] = None) -> dict:
        data = self._requester(method, params or {})
        if not data.get("ok"):
            raise SlackDiscoveryError(f"{method}: {data.get('error', 'unknown_error')}")
        return data

    def _paginate_cursor(self, method: str, result_key: str, params: dict) -> Iterator[dict]:
        """Cursor pagination (response_metadata.next_cursor) — message methods."""
        cursor = ""
        base = dict(params)
        base.setdefault("limit", 200)
        while True:
            call = dict(base)
            if cursor:
                call["cursor"] = cursor
            data = self._get(method, call)
            for item in data.get(result_key, []) or []:
                yield item
            cursor = (data.get("response_metadata", {}) or {}).get("next_cursor", "")
            if not cursor:
                break

    def _paginate_offset(self, method: str, result_key: str, params: dict) -> Iterator[dict]:
        """Offset pagination (top-level `offset`) — list/users methods."""
        offset = ""
        base = dict(params)
        base.setdefault("limit", 1000)
        while True:
            call = dict(base)
            if offset:
                call["offset"] = offset
            data = self._get(method, call)
            for item in data.get(result_key, []) or []:
                yield item
            offset = data.get("offset", "")
            if not offset:
                break

    # ── methods ──────────────────────────────────────────────────────
    def enterprise_info(self) -> dict:
        return self._get("discovery.enterprise.info").get("enterprise", {})

    def users_list(self) -> list[dict]:
        # users may be keyed `users` or `members`; try both.
        out: list[dict] = list(self._paginate_offset("discovery.users.list", "users", {"include_deleted": "true"}))
        if not out:
            out = list(self._paginate_offset("discovery.users.list", "members", {"include_deleted": "true"}))
        return out

    def conversations_list(self, team: Optional[str] = None, only_private: bool = False) -> list[dict]:
        params: dict = {}
        if team:
            params["team"] = team
        if only_private:
            params["only_private"] = "true"
        return list(self._paginate_offset("discovery.conversations.list", "channels", params))

    def conversations_info(self, channel: str, team: Optional[str] = None) -> dict:
        params: dict = {"channel": channel}
        if team:
            params["team"] = team
        data = self._get("discovery.conversations.info", params)
        # member list key flagged uncertain — accept info / channel_info.
        return data.get("info") or data.get("channel_info") or {}

    def channel_members(self, channel: str, team: Optional[str] = None) -> list[str]:
        """Full member list of a channel (incl. private) — the ACL gap-closer."""
        info = self.conversations_info(channel, team)
        return list(info.get("members") or [])

    def conversations_history(
        self, channel: str, team: str, oldest: Optional[float] = None, latest: Optional[float] = None
    ) -> Iterator[dict]:
        params: dict = {"channel": channel, "team": team}
        if oldest is not None:
            params["oldest"] = oldest
        if latest is not None:
            params["latest"] = latest
        return self._paginate_cursor("discovery.conversations.history", "messages", params)

    def conversations_recent(
        self, oldest: float, latest: float, team: Optional[str] = None
    ) -> list[dict]:
        """Channels with activity (new/edit/delete) in [oldest, latest] (≤7d)."""
        if latest - oldest > RECENT_MAX_WINDOW_S:
            raise SlackDiscoveryError(
                f"recent window {latest - oldest:.0f}s exceeds 7-day max; poll more often"
            )
        params: dict = {"oldest": oldest, "latest": latest, "limit": 500}
        if team:
            params["team"] = team
        return self._get("discovery.conversations.recent", params).get("channels", []) or []

    def conversations_edits(self, channel: str, team: str) -> list[dict]:
        params = {"channel": channel, "team": team, "limit": 200}
        return self._get("discovery.conversations.edits", params).get("edits", []) or []


# ── change classification (deterministic) ────────────────────────────

def classify_change(msg: dict) -> str:
    """Classify a Discovery message as 'deleted', 'edited', or 'new'."""
    if msg.get("type") == "deleted":
        return "deleted"
    edits = msg.get("edits") or []
    for e in edits:
        if e.get("subtype") == "message_deleted":
            return "deleted"
    if msg.get("edited"):
        return "edited"
    for e in edits:
        if e.get("subtype") == "message_changed":
            return "edited"
    return "new"


def message_source_id(channel_id: str, msg: dict) -> str:
    """Stable key matching the rest of the pipeline: '<channel>:<ts>'."""
    return f"{channel_id}:{msg.get('ts', '')}"


@dataclass(frozen=True)
class ChangeRecord:
    """One detected change, in the same shape the bot-path reconciler emits."""
    source_id: str
    channel_id: str
    action: str          # 'edited' | 'deleted' | 'new'
    ts: str
    body: str = ""

    def to_delta(self, org: str) -> dict:
        rec = {
            "source_id": self.source_id,
            "source": "slack",
            "org": org,
            "reconcile_action": self.action,
        }
        if self.action in ("edited", "new") and self.body:
            rec["body"] = self.body
        return rec


def iter_changes(
    client: SlackDiscoveryClient,
    oldest: float,
    latest: float,
    team: Optional[str] = None,
) -> Iterator[ChangeRecord]:
    """Stream edit/delete/new changes in a window via recent → history.

    This is the deterministic Grid replacement for the bot-path snapshot-diff:
    no prior snapshot needed — Discovery tells us exactly what changed.
    """
    for ch in client.conversations_recent(oldest=oldest, latest=latest, team=team):
        channel_id = ch.get("id") or ch.get("channel") or ""
        ch_team = ch.get("team") or team or ""
        if not channel_id:
            continue
        for msg in client.conversations_history(channel_id, ch_team, oldest=oldest, latest=latest):
            action = classify_change(msg)
            yield ChangeRecord(
                source_id=message_source_id(channel_id, msg),
                channel_id=channel_id,
                action=action,
                ts=str(msg.get("ts", "")),
                body=str(msg.get("text", "")) if action != "deleted" else "",
            )


__all__ = [
    "SlackDiscoveryClient",
    "SlackDiscoveryError",
    "ChangeRecord",
    "classify_change",
    "message_source_id",
    "iter_changes",
    "SLACK_BASE_URL",
    "RECENT_MAX_WINDOW_S",
]
