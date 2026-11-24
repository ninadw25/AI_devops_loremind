from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

from infra.slack.discovery import discover_scopes
from infra.slack.run import process_channel


class FakeSlackFetcher:
    def __init__(self) -> None:
        self.team_id = "T123"
        self.team_name = "Smoke Team"
        self.bot_allowlist: set[str] = set()
        self._channels = [
            {"id": "C100", "name": "public-alpha", "is_private": False, "is_im": False, "is_mpim": False},
            {"id": "C200", "name": "finance-private", "is_private": True, "is_im": False, "is_mpim": False},
            {"id": "D300", "name": "dm-user", "is_private": False, "is_im": True, "is_mpim": False},
        ]
        self._members = {
            "C200": ["U001", "U002", "U003"],
        }
        self._user_cache = {
            "U001": {
                "id": "U001",
                "team_id": "T123",
                "is_bot": False,
                "deleted": False,
                "profile": {"email": "alice@ezobooks.in", "display_name": "Alice"},
            },
            "U002": {
                "id": "U002",
                "team_id": "T123",
                "is_bot": False,
                "deleted": False,
                "profile": {"email": "bob@ezobooks.in", "display_name": "Bob"},
            },
            # External Slack Connect guest that should be filtered from discovery principals.
            "U003": {
                "id": "U003",
                "team_id": "T999",
                "is_bot": False,
                "deleted": False,
                "profile": {"email": "guest#EXT#@ext.example.com", "display_name": "Guest #EXT#"},
            },
        }

    def validate_auth(self, required_scopes=None):
        return {"ok": True}

    def load_user_cache(self):
        return self._user_cache

    @property
    def user_cache(self):
        return self._user_cache

    def get_user(self, user_id: str):
        return self._user_cache.get(user_id)

    def _get(self, endpoint: str, params: dict | None = None):
        params = params or {}
        if endpoint == "conversations.list":
            return {
                "ok": True,
                "channels": self._channels,
                "response_metadata": {"next_cursor": ""},
            }
        if endpoint == "conversations.members":
            return {
                "ok": True,
                "members": self._members.get(params.get("channel", ""), []),
                "response_metadata": {"next_cursor": ""},
            }
        raise AssertionError(f"Unexpected endpoint: {endpoint}")

    def iter_channel_events(self, channel: dict, since: datetime | None = None):
        cid = channel["id"]
        if cid == "C100":
            # 2 distinct threads in public channel.
            yield (
                {
                    "ts": "1717000000.000100",
                    "thread_ts": "1717000000.000100",
                    "text": "Decision made to ship Sprint 14 this week.",
                    "user": "U001",
                },
                [],
            )
            yield (
                {
                    "ts": "1717000001.000200",
                    "thread_ts": "1717000001.000200",
                    "text": "Task assigned: prepare rollout checklist by EOD.",
                    "user": "U002",
                },
                [],
            )
        elif cid == "C200":
            # 2 distinct threads in private channel.
            yield (
                {
                    "ts": "1717000002.000300",
                    "thread_ts": "1717000002.000300",
                    "text": "Finance update: VAT filing completed and submitted.",
                    "user": "U001",
                },
                [],
            )
            yield (
                {
                    "ts": "1717000003.000400",
                    "thread_ts": "1717000003.000400",
                    "text": "Budget note: approve cloud spend cap for next month.",
                    "user": "U002",
                },
                [],
            )


def test_discovery_shape_and_public_has_no_member_rows(tmp_path: Path):
    fetcher = FakeSlackFetcher()
    artifact = discover_scopes(fetcher=fetcher, org="ezobooks", output_dir=tmp_path)

    payload = artifact.to_dict()
    assert set(payload.keys()) == {"scopes", "principals", "scope_principals"}

    scope_map = {row["scope_id"]: row for row in payload["scopes"]}
    assert "slack:workspace:T123" in scope_map
    assert "slack:channel:C100" in scope_map
    assert "slack:channel:C200" in scope_map

    # DM scope should be absent from discovery outputs.
    assert "slack:channel:D300" not in scope_map

    # Public channels do not emit membership rows.
    public_rows = [r for r in payload["scope_principals"] if r["scope_id"] == "slack:channel:C100"]
    private_rows = [r for r in payload["scope_principals"] if r["scope_id"] == "slack:channel:C200"]
    assert public_rows == []
    assert len(private_rows) == 2


def test_scope_plumbing_ingested_events_match_discovery_scopes(tmp_path: Path):
    fetcher = FakeSlackFetcher()
    artifact = discover_scopes(fetcher=fetcher, org="ezobooks", output_dir=tmp_path)
    scope_ids = {row["scope_id"] for row in artifact.scopes if row["kind"] == "channel"}

    channels = [
        {"id": "C100", "name": "public-alpha", "is_private": False, "is_im": False, "is_mpim": False},
        {"id": "C200", "name": "finance-private", "is_private": True, "is_im": False, "is_mpim": False},
    ]

    seen_ids: set[str] = set()
    all_events = []
    for channel in channels:
        events = process_channel(
            fetcher=fetcher,
            channel=channel,
            org="ezobooks",
            since=None,
            dry_run=False,
            seen_ids=seen_ids,
            log=None,
        )
        all_events.extend(events)

    # Smoke run should ingest 3-5 threads; fixture emits 4.
    assert 3 <= len(all_events) <= 5

    for event in all_events:
        scope_id = (event.raw or {}).get("scope_id")
        assert scope_id in scope_ids


def test_citation_fields_present_and_slack_url_format(monkeypatch):
    # Config now flows through the common.config Settings singleton, so the
    # env change must force a singleton rebuild (and a clean rebuild after).
    from common.config import _reset_settings
    monkeypatch.setenv("SLACK_WORKSPACE_DOMAIN", "ezobooks")
    _reset_settings()
    try:
        fetcher = FakeSlackFetcher()
        channel = {"id": "C200", "name": "finance-private", "is_private": True, "is_im": False, "is_mpim": False}

        events = process_channel(
            fetcher=fetcher,
            channel=channel,
            org="ezobooks",
            since=None,
            dry_run=False,
            seen_ids=set(),
            log=None,
        )
    finally:
        # Restore a clean singleton so SLACK_WORKSPACE_DOMAIN doesn't leak
        # into other tests once monkeypatch unsets the env var.
        _reset_settings()
    assert events, "Expected at least one normalized event"

    sample = events[0]
    raw = sample.raw or {}
    assert raw.get("source_ref")
    assert raw.get("source_url")

    # Expected format: https://{workspace}.slack.com/archives/{channel}/p{packed_ts}
    assert re.match(r"^C[0-9A-Z]+:[0-9]+\.[0-9]+$", raw["source_ref"])
    assert re.match(r"^https://[a-z0-9-]+\.slack\.com/archives/C[0-9A-Z]+/p[0-9]{16,}$", raw["source_url"])


def _allowed_scopes(scopes: list[dict], scope_principals: list[dict], principal_id: str, tenant_id: str) -> set[str]:
    member_scopes = {
        row["scope_id"]
        for row in scope_principals
        if row.get("principal_id") == principal_id
    }
    public_scopes = {
        row["scope_id"]
        for row in scopes
        if row.get("tenant_id") == tenant_id and bool(row.get("is_public"))
    }
    return member_scopes | public_scopes


def _query_time_visible(node: dict, allowed: set[str]) -> bool:
    scope_id = node.get("scope_id")
    extra_scopes = node.get("extra_scope_ids") or []
    return scope_id in allowed and all(s in allowed for s in extra_scopes)


def test_acl_gate_simulation_private_scope_filtered_out():
    scopes = [
        {"scope_id": "slack:workspace:T123", "tenant_id": "ezobooks", "is_public": True},
        {"scope_id": "slack:channel:C100", "tenant_id": "ezobooks", "is_public": True},
        {"scope_id": "slack:channel:C200", "tenant_id": "ezobooks", "is_public": False},
    ]
    scope_principals = [
        {"scope_id": "slack:channel:C100", "principal_id": "slack:U001"},
        {"scope_id": "slack:channel:C200", "principal_id": "slack:U002"},
    ]

    allowed = _allowed_scopes(scopes, scope_principals, principal_id="slack:U001", tenant_id="ezobooks")

    nodes = [
        {"id": "k_public", "scope_id": "slack:channel:C100", "extra_scope_ids": []},
        {"id": "k_private", "scope_id": "slack:channel:C200", "extra_scope_ids": []},
    ]
    visible = [node["id"] for node in nodes if _query_time_visible(node, allowed)]

    assert "k_public" in visible
    assert "k_private" not in visible
