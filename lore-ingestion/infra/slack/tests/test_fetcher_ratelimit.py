"""
Regression tests for the simplified Slack fetcher rate limiting (LOR-39 cleanup).

The self-tuning, disk-persisted request_delay was removed. The fetcher must:
  - honor Retry-After on a 429 and retry,
  - NEVER mutate its steady-state request_delay (no escalate / decay),
  - NEVER persist tuning state to disk.
"""

import infra.slack.fetcher as fetchmod


class _FakeResp:
    def __init__(self, status, headers=None, payload=None):
        self.status_code = status
        self.headers = headers or {}
        self._payload = payload if payload is not None else {"ok": True}

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400 and self.status_code != 429:
            raise RuntimeError(f"HTTP {self.status_code}")


def _make_fetcher(monkeypatch):
    sleeps = []
    monkeypatch.setattr(fetchmod.time, "sleep", lambda s: sleeps.append(s))
    f = fetchmod.SlackFetcher(token="x-token", org="testorg")
    return f, sleeps


def test_persistence_and_tuning_removed(monkeypatch):
    f, _ = _make_fetcher(monkeypatch)
    # The self-tuning / persistence surface is gone entirely.
    for attr in ("_persist_org_config", "_load_org_config", "_org_config_path",
                 "_success_streak", "_api_429_count"):
        assert not hasattr(f, attr), f"{attr} should have been removed"


def test_429_honors_retry_after_without_mutating_delay(monkeypatch):
    f, sleeps = _make_fetcher(monkeypatch)
    start_delay = f.request_delay

    responses = [
        _FakeResp(429, headers={"Retry-After": "7"}),
        _FakeResp(200, payload={"ok": True, "foo": "bar"}),
    ]
    monkeypatch.setattr(
        f._session, "get",
        lambda url, params=None, timeout=None: responses.pop(0),
    )

    data = f._get("conversations.list")

    assert data["foo"] == "bar"
    # Retry-After was honored.
    assert 7 in sleeps
    # Steady-state delay is untouched — no escalation carried forward.
    assert f.request_delay == start_delay


def test_repeated_429s_never_escalate_delay(monkeypatch):
    f, _ = _make_fetcher(monkeypatch)
    start_delay = f.request_delay

    # Four 429s (each well under MAX_RETRIES) then success.
    responses = [_FakeResp(429, headers={"Retry-After": "1"}) for _ in range(4)]
    responses.append(_FakeResp(200, payload={"ok": True}))
    monkeypatch.setattr(
        f._session, "get",
        lambda url, params=None, timeout=None: responses.pop(0),
    )

    f._get("conversations.list")

    # A storm of 429s does not ratchet request_delay up (the old failure mode).
    assert f.request_delay == start_delay
