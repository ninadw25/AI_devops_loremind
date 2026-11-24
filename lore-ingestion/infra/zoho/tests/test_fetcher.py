"""Unit tests for infra/zoho/fetcher.py — token caching + Mail/Cliq pagination.

All network calls are mocked via monkeypatching httpx.Client. No real Zoho
credentials or network access are required.
"""
from __future__ import annotations

import time

import httpx
from types import SimpleNamespace

import pytest

from infra.zoho import fetcher


class _FakeSettings:
    zoho_client_id = "client-1"
    zoho_client_secret = "secret-1"
    zoho_refresh_token = "refresh-1"
    zoho_accounts_url = "https://accounts.zoho.com"
    zoho_mail_base_url = "https://mail.zoho.com/api"
    zoho_cliq_base_url = "https://cliq.zoho.com/api/v2"
    zoho_mail_scanback_days = 90
    zoho_cliq_scanback_days = 30


@pytest.fixture(autouse=True)
def _clear_token_cache():
    fetcher._TOKEN_CACHE.clear()
    yield
    fetcher._TOKEN_CACHE.clear()


class _FakeResponse:
    def __init__(self, json_data, status_code=200, headers=None):
        self._json = json_data
        self.status_code = status_code
        self.headers = headers or {}

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def test_get_access_token_caches_in_memory(monkeypatch):
    calls = {"n": 0}

    def fake_post(self, url, data=None):
        calls["n"] += 1
        assert url == "https://accounts.zoho.com/oauth/v2/token"
        assert data["grant_type"] == "refresh_token"
        return _FakeResponse({"access_token": "tok-1", "expires_in": 3600})

    monkeypatch.setattr("httpx.Client.post", fake_post)

    token1 = fetcher.get_access_token(_FakeSettings())
    token2 = fetcher.get_access_token(_FakeSettings())

    assert token1 == "tok-1"
    assert token2 == "tok-1"
    assert calls["n"] == 1  # second call served from cache


def test_get_access_token_raises_on_missing_credentials():
    class _Empty:
        zoho_client_id = ""
        zoho_client_secret = ""
        zoho_refresh_token = ""

    with pytest.raises(RuntimeError):
        fetcher.get_access_token(_Empty())


def test_zoho_get_retries_on_429_then_succeeds(monkeypatch):
    responses = [
        _FakeResponse({}, status_code=429, headers={"Retry-After": "0"}),
        _FakeResponse({"data": [{"ok": True}]}, status_code=200),
    ]

    def fake_get(self, url, headers=None, params=None):
        return responses.pop(0)

    monkeypatch.setattr("httpx.Client.get", fake_get)
    monkeypatch.setattr("time.sleep", lambda *_a, **_k: None)

    result = fetcher.zoho_get("https://mail.zoho.com/api/accounts", "tok-1")
    assert result == {"data": [{"ok": True}]}


ONE_FOLDER = [{"folderId": "f1", "folderName": "Inbox"}]


def test_fetch_mail_for_account_pages_until_watermark(monkeypatch, tmp_path):
    monkeypatch.setattr(fetcher, "get_access_token", lambda settings=None: "tok-1")
    monkeypatch.setattr(fetcher, "fetch_mail_folders", lambda account_id, settings=None: ONE_FOLDER)

    # Paging ends on an EMPTY page, not a short one — Zoho caps `limit`
    # server-side, so a short page means "that's all I serve at once", never
    # "that's all there is". The fake honours `start` for that reason.
    now_ms = int(time.time() * 1000)
    rows = [
        {"messageId": "m3", "receivedTime": now_ms},
        {"messageId": "m2", "receivedTime": now_ms - 1000},
        {"messageId": "m1", "receivedTime": now_ms - 2000},
    ]

    def fake_zoho_get(url, token, params=None, max_retries=5):
        if "/content" in url:
            return {"data": {"content": "body"}}
        start = params["start"]
        return {"data": rows[start - 1:start - 1 + params["limit"]]}

    monkeypatch.setattr(fetcher, "zoho_get", fake_zoho_get)

    store_path = tmp_path / "cursors.json"
    messages = list(fetcher.fetch_mail_for_account(
        org="acme",
        account_id="acc-1",
        account_email="alice@acme.com",
        scanback_days=90,
        settings=_FakeSettings(),
        store_path=store_path,
    ))

    assert [m["messageId"] for m in messages] == ["m3", "m2", "m1"]
    assert store_path.exists()


def test_fetch_mail_for_account_stops_at_existing_watermark(monkeypatch, tmp_path):
    monkeypatch.setattr(fetcher, "get_access_token", lambda settings=None: "tok-1")
    monkeypatch.setattr(fetcher, "fetch_mail_folders", lambda account_id, settings=None: ONE_FOLDER)

    now_ms = int(time.time() * 1000)
    store_path = tmp_path / "cursors.json"
    store = fetcher.ZohoCursorStore("acme", path=store_path)
    store.set("mail:alice@acme.com", str(now_ms - 1000), 1)
    store.save()

    rows = [
        {"messageId": "m3", "receivedTime": now_ms},
        {"messageId": "m2", "receivedTime": now_ms - 1000},  # not newer than watermark -> stop
    ]

    def fake_zoho_get(url, token, params=None, max_retries=5):
        if "/content" in url:
            return {"data": {"content": "body"}}
        start = params["start"]
        return {"data": rows[start - 1:start - 1 + params["limit"]]}

    monkeypatch.setattr(fetcher, "zoho_get", fake_zoho_get)

    messages = list(fetcher.fetch_mail_for_account(
        org="acme",
        account_id="acc-1",
        account_email="alice@acme.com",
        settings=_FakeSettings(),
        store_path=store_path,
    ))

    assert [m["messageId"] for m in messages] == ["m3"]


def test_fetch_cliq_messages_pages_via_next_token(monkeypatch, tmp_path):
    monkeypatch.setattr(fetcher, "get_access_token", lambda settings=None: "tok-1")

    now_ms = int(time.time() * 1000)
    pages = [
        {"messages": [{"id": "c2", "time": now_ms}], "next_token": "tok-page2"},
        {"messages": [{"id": "c1", "time": now_ms - 1000}], "next_token": None},
    ]

    def fake_zoho_get(url, token, params=None, max_retries=5):
        return pages.pop(0)

    monkeypatch.setattr(fetcher, "zoho_get", fake_zoho_get)

    store_path = tmp_path / "cursors.json"
    messages = list(fetcher.fetch_cliq_messages(
        org="acme",
        channel_id="chan-1",
        channel_name="general",
        scanback_days=30,
        settings=_FakeSettings(),
        store_path=store_path,
    ))

    assert [m["id"] for m in messages] == ["c2", "c1"]
    assert all(m["channel_id"] == "chan-1" for m in messages)


def test_cursor_store_roundtrip(tmp_path):
    path = tmp_path / "cursors.json"
    store = fetcher.ZohoCursorStore("acme", path=path)
    assert store.get("mail:alice@acme.com") is None

    store.set("mail:alice@acme.com", "12345", 7)
    store.save()

    reloaded = fetcher.ZohoCursorStore("acme", path=path)
    rec = reloaded.get("mail:alice@acme.com")
    assert rec == {"cursor": "12345", "message_count": 7}


def test_content_url_is_folder_scoped(monkeypatch, tmp_path):
    """Zoho's message-content endpoint lives UNDER the folder:
    /accounts/{acc}/folders/{fid}/messages/{mid}/content.

    The account-scoped form 404s for every message, which silently produced
    empty bodies for a whole mailbox — the fetch "succeeded" with no content.
    Verified against the live API. folderId rides on each list row.
    """
    monkeypatch.setattr(fetcher, "get_access_token", lambda settings=None: "tok-1")
    monkeypatch.setattr(fetcher, "fetch_mail_folders",
                        lambda account_id, settings=None: [{"folderId": "f9", "folderName": "Inbox"}])

    now_ms = int(time.time() * 1000)
    rows = [{"messageId": "m1", "folderId": "f9", "receivedTime": now_ms}]
    seen: list[str] = []

    def fake_zoho_get(url, token, params=None, max_retries=5):
        if "/content" in url:
            seen.append(url)
            return {"data": {"content": "body"}}
        start = params["start"]
        return {"data": rows[start - 1:start - 1 + params["limit"]]}

    monkeypatch.setattr(fetcher, "zoho_get", fake_zoho_get)

    msgs = list(fetcher.fetch_mail_for_account(
        org="acme", account_id="acc-1", account_email="a@acme.com",
        scanback_days=90, settings=_FakeSettings(),
        store_path=tmp_path / "cursors.json",
    ))

    assert len(seen) == 1, "content fetched once per message"
    assert seen[0].endswith("/accounts/acc-1/folders/f9/messages/m1/content"), seen[0]
    assert msgs[0]["content"] == "body"


def test_skip_ids_and_skip_content_avoid_body_requests(monkeypatch, tmp_path):
    """Bodies are ~99.5% of the request budget, so both hooks must actually
    prevent the content GET — not merely discard its result.

    m1 is already replicated (skip_ids) → not yielded at all.
    m2 is condemned by the noise predicate → yielded with an empty body and
        content_skipped, so it stays in the replica and is re-buyable.
    m3 is neither → the one and only content request.
    """
    monkeypatch.setattr(fetcher, "get_access_token", lambda settings=None: "tok-1")
    monkeypatch.setattr(fetcher, "fetch_mail_folders", lambda account_id, settings=None: ONE_FOLDER)

    now_ms = int(time.time() * 1000)
    page = {"data": [
        {"messageId": "m1", "folderId": "f1", "receivedTime": now_ms, "fromAddress": "a@keep.com"},
        {"messageId": "m2", "folderId": "f1", "receivedTime": now_ms - 1, "fromAddress": "n@spam.com"},
        {"messageId": "m3", "folderId": "f1", "receivedTime": now_ms - 2, "fromAddress": "b@keep.com"},
    ]}
    content_calls: list[str] = []

    def fake_zoho_get(url, token, params=None, max_retries=5):
        if "/content" in url:
            content_calls.append(url)
            return {"data": {"content": "body"}}
        return page if params and params.get("start") == 1 else {"data": []}

    monkeypatch.setattr(fetcher, "zoho_get", fake_zoho_get)

    msgs = list(fetcher.fetch_mail_for_account(
        org="acme", account_id="acc-1", account_email="a@acme.com",
        scanback_days=90, settings=_FakeSettings(),
        store_path=tmp_path / "cursors.json",
        skip_ids={"m1"},
        skip_content=lambda row: "spam.com" in (row.get("fromAddress") or ""),
    ))

    assert [m["messageId"] for m in msgs] == ["m2", "m3"], "skip_ids drops m1 entirely"
    assert len(content_calls) == 1, f"only m3 buys a body, got {content_calls}"
    assert content_calls[0].endswith("/messages/m3/content")
    assert msgs[0]["content"] == "" and msgs[0]["content_skipped"] is True
    assert msgs[1]["content"] == "body" and "content_skipped" not in msgs[1]


def test_paging_survives_a_server_side_page_cap(monkeypatch, tmp_path):
    """Zoho caps `limit` at 100 regardless of what we ask for.

    The old loop broke out when `len(rows) < page_size`, so asking for 200 and
    receiving 100 read as "last page" — every mailbox silently stopped after
    its newest 100 messages and then advanced its cursor to the top, making the
    truncation permanent and invisible. Paging must advance by what the server
    RETURNED and stop only on an empty page.
    """
    monkeypatch.setattr(fetcher, "get_access_token", lambda settings=None: "tok-1")
    monkeypatch.setattr(fetcher, "fetch_mail_folders", lambda account_id, settings=None: ONE_FOLDER)

    now_ms = int(time.time() * 1000)
    # 250 messages behind a server that never returns more than 100 at a time.
    all_rows = [{"messageId": f"m{i}", "folderId": "f1", "receivedTime": now_ms - i}
                for i in range(250)]
    starts: list[int] = []

    def fake_zoho_get(url, token, params=None, max_retries=5):
        if "/content" in url:
            return {"data": {"content": "body"}}
        start = params["start"]
        starts.append(start)
        assert params["limit"] <= 100, "must not ask for more than Zoho serves"
        return {"data": all_rows[start - 1:start - 1 + 100]}   # hard cap at 100

    monkeypatch.setattr(fetcher, "zoho_get", fake_zoho_get)

    msgs = list(fetcher.fetch_mail_for_account(
        org="acme", account_id="acc-1", account_email="a@acme.com",
        scanback_days=3650, settings=_FakeSettings(),
        store_path=tmp_path / "cursors.json",
    ))

    assert len(msgs) == 250, f"whole mailbox, not just the first page — got {len(msgs)}"
    assert starts == [1, 101, 201, 251], f"advance by rows returned, got {starts}"


def test_token_is_reread_per_request_not_captured_once(monkeypatch, tmp_path):
    """Zoho tokens expire after an hour; a full-history mailbox pages for longer.

    The fetcher used to grab one token and reuse it for the whole mailbox, so a
    76k-message backfill 401'd on every call exactly 60 minutes in and died
    mid-mailbox. Every request must re-read the token (the cache makes that
    free), so a mid-fetch expiry is a silent refresh rather than a failed run.
    """
    tokens = iter(["tok-hour-1"] + ["tok-hour-2"] * 50)
    monkeypatch.setattr(fetcher, "get_access_token", lambda settings=None: next(tokens))
    monkeypatch.setattr(fetcher, "fetch_mail_folders", lambda account_id, settings=None: ONE_FOLDER)

    now_ms = int(time.time() * 1000)
    rows = [{"messageId": f"m{i}", "folderId": "f1", "receivedTime": now_ms - i}
            for i in range(3)]
    used: list[str] = []

    def fake_zoho_get(url, token, params=None, max_retries=5):
        used.append(token)
        if "/content" in url:
            return {"data": {"content": "body"}}
        start = params["start"]
        return {"data": rows[start - 1:start - 1 + params["limit"]]}

    monkeypatch.setattr(fetcher, "zoho_get", fake_zoho_get)

    msgs = list(fetcher.fetch_mail_for_account(
        org="acme", account_id="acc-1", account_email="a@acme.com",
        scanback_days=90, settings=_FakeSettings(),
        store_path=tmp_path / "cursors.json"))

    assert len(msgs) == 3
    assert "tok-hour-2" in used, "later requests must pick up a refreshed token"


def test_auth_failure_on_content_fails_the_mailbox(monkeypatch, tmp_path):
    """A 401 mid-mailbox must abort, not bank an empty body.

    Swallowing it stored content="" for every remaining message; because those
    rows count as replicated, `skip_ids` would skip them on the next run and the
    mail would stay permanently blank. Transient errors still degrade to "".
    """
    monkeypatch.setattr(fetcher, "get_access_token", lambda settings=None: "tok-1")
    monkeypatch.setattr(fetcher, "fetch_mail_folders", lambda account_id, settings=None: ONE_FOLDER)

    now_ms = int(time.time() * 1000)
    rows = [{"messageId": "m1", "folderId": "f1", "receivedTime": now_ms}]

    def fake_zoho_get(url, token, params=None, max_retries=5):
        if "/content" in url:
            resp = httpx.Response(401, request=httpx.Request("GET", url))
            raise httpx.HTTPStatusError("401", request=resp.request, response=resp)
        start = params["start"]
        return {"data": rows[start - 1:start - 1 + params["limit"]]}

    monkeypatch.setattr(fetcher, "zoho_get", fake_zoho_get)

    with pytest.raises(httpx.HTTPStatusError):
        list(fetcher.fetch_mail_for_account(
            org="acme", account_id="acc-1", account_email="a@acme.com",
            scanback_days=90, settings=_FakeSettings(),
            store_path=tmp_path / "cursors.json"))


def test_fetch_mail_folders_lists_the_account(monkeypatch):
    monkeypatch.setattr(fetcher, "get_access_token", lambda settings=None: "tok-1")
    seen: list[str] = []

    def fake_zoho_get(url, token, params=None, max_retries=5):
        seen.append(url)
        return {"data": [{"folderId": "f1", "folderName": "Inbox"},
                         {"folderId": "f2", "folderName": "Sent"}]}

    monkeypatch.setattr(fetcher, "zoho_get", fake_zoho_get)
    folders = fetcher.fetch_mail_folders("acc-1", _FakeSettings())
    assert [f["folderName"] for f in folders] == ["Inbox", "Sent"]
    assert seen == ["https://mail.zoho.com/api/accounts/acc-1/folders"]


def test_fetch_mail_for_account_covers_every_folder_including_sent(monkeypatch, tmp_path):
    """The bug this whole rewrite exists to fix: an unscoped /messages/view
    silently skipped Sent (and every other folder it didn't feel like
    including) while still reporting a clean, complete-looking run.

    Two folders, one message each, same watermark — both must come back, and
    each list call must carry an explicit folderId so there is no ambiguity
    left for Zoho to resolve on our behalf.
    """
    monkeypatch.setattr(fetcher, "get_access_token", lambda settings=None: "tok-1")
    monkeypatch.setattr(fetcher, "fetch_mail_folders", lambda account_id, settings=None: [
        {"folderId": "f-inbox", "folderName": "Inbox"},
        {"folderId": "f-sent", "folderName": "Sent"},
    ])

    now_ms = int(time.time() * 1000)
    by_folder = {
        "f-inbox": [{"messageId": "in-1", "folderId": "f-inbox", "receivedTime": now_ms}],
        "f-sent": [{"messageId": "sent-1", "folderId": "f-sent", "receivedTime": now_ms}],
    }
    list_calls: list[dict] = []

    def fake_zoho_get(url, token, params=None, max_retries=5):
        if "/content" in url:
            return {"data": {"content": "body"}}
        list_calls.append(params)
        fid = params["folderId"]
        return {"data": by_folder[fid]} if params["start"] == 1 else {"data": []}

    monkeypatch.setattr(fetcher, "zoho_get", fake_zoho_get)

    msgs = list(fetcher.fetch_mail_for_account(
        org="acme", account_id="acc-1", account_email="a@acme.com",
        scanback_days=90, settings=_FakeSettings(), store_path=tmp_path / "cursors.json",
    ))

    assert {m["messageId"] for m in msgs} == {"in-1", "sent-1"}, "Sent must not be silently skipped"
    assert {c["folderId"] for c in list_calls if c["start"] == 1} == {"f-inbox", "f-sent"}, \
        "every folder must be listed with folderId set explicitly"


def test_fetch_mail_for_account_dedups_a_message_seen_in_two_folders(monkeypatch, tmp_path):
    """Zoho's folder list includes aggregate folders (e.g. 'All Mail') that
    mirror messages also present in their real folder. The same messageId
    must be fetched and yielded exactly once per run, off whichever folder
    serves it first — otherwise every aggregate folder doubles the request
    budget and the replicated row count.
    """
    monkeypatch.setattr(fetcher, "get_access_token", lambda settings=None: "tok-1")
    monkeypatch.setattr(fetcher, "fetch_mail_folders", lambda account_id, settings=None: [
        {"folderId": "f-inbox", "folderName": "Inbox"},
        {"folderId": "f-all", "folderName": "All Mail"},
    ])

    now_ms = int(time.time() * 1000)
    by_folder = {
        "f-inbox": [{"messageId": "dup-1", "folderId": "f-inbox", "receivedTime": now_ms}],
        "f-all": [{"messageId": "dup-1", "folderId": "f-all", "receivedTime": now_ms}],
    }
    content_calls: list[str] = []

    def fake_zoho_get(url, token, params=None, max_retries=5):
        if "/content" in url:
            content_calls.append(url)
            return {"data": {"content": "body"}}
        fid = params["folderId"]
        return {"data": by_folder[fid]} if params["start"] == 1 else {"data": []}

    monkeypatch.setattr(fetcher, "zoho_get", fake_zoho_get)

    msgs = list(fetcher.fetch_mail_for_account(
        org="acme", account_id="acc-1", account_email="a@acme.com",
        scanback_days=90, settings=_FakeSettings(), store_path=tmp_path / "cursors.json",
    ))

    assert [m["messageId"] for m in msgs] == ["dup-1"], "yielded once, not once per folder"
    assert len(content_calls) == 1, "body bought once, not once per folder"


def test_fetch_mail_for_account_raises_when_no_folders(monkeypatch, tmp_path):
    monkeypatch.setattr(fetcher, "get_access_token", lambda settings=None: "tok-1")
    monkeypatch.setattr(fetcher, "fetch_mail_folders", lambda account_id, settings=None: [])

    with pytest.raises(RuntimeError, match="no folders"):
        list(fetcher.fetch_mail_for_account(
            org="acme", account_id="acc-1", account_email="a@acme.com",
            scanback_days=90, settings=_FakeSettings(), store_path=tmp_path / "cursors.json",
        ))


def test_org_accounts_preferred_with_personal_fallback(monkeypatch):
    """With an org id, enumerate the whole estate; without the scope, degrade.

    `/accounts` returns only the grantor's own mailbox, so a self-client token
    against a 60-person org replicated one inbox and logged "1/1 principals" —
    a complete-looking run. The organization endpoint is the difference between
    one mailbox and the company.
    """
    class _S(_FakeSettings):
        zoho_org_id = "zoid-1"

    seen: list[str] = []

    def org_ok(url, token, params=None, max_retries=5):
        seen.append(url)
        return {"data": [{"accountId": "a1"}, {"accountId": "a2"}, {"accountId": "a3"}]}

    monkeypatch.setattr(fetcher, "zoho_get", org_ok)
    accounts = fetcher.fetch_mail_accounts("tok-1", _S())
    assert len(accounts) == 3
    assert seen == ["https://mail.zoho.com/api/organization/zoid-1/accounts"]

    # Scope not granted, but org_id asserts a multi-mailbox tenant -> RAISE.
    # Degrading to the grantor's own inbox here would replicate one mailbox and
    # log "[1/1] principals": a complete-looking run over 1/60th of the data.
    def org_denied(url, token, params=None, max_retries=5):
        if "/organization/" in url:
            resp = httpx.Response(403, request=httpx.Request("GET", url))
            raise httpx.HTTPStatusError("403", request=resp.request, response=resp)
        return {"data": [{"accountId": "solo"}]}

    monkeypatch.setattr(fetcher, "zoho_get", org_denied)
    with pytest.raises(RuntimeError, match="organization"):
        fetcher.fetch_mail_accounts("tok-1", _S())

    # Declared single-mailbox -> personal path, and org_id survives for Cliq.
    # The opt-out must be its own flag: clearing org_id would fix mail by
    # breaking Cliq's admin discovery, which needs that same id.
    class _Solo(_S):
        zoho_mail_single_mailbox = True

    hit: list[str] = []

    def personal_only(url, token, params=None, max_retries=5):
        hit.append(url)
        return {"data": [{"accountId": "solo"}]}

    monkeypatch.setattr(fetcher, "zoho_get", personal_only)
    assert [a["accountId"] for a in fetcher.fetch_mail_accounts("tok-1", _Solo())] == ["solo"]
    assert not any("/organization/" in u for u in hit), "must not call the org endpoint at all"
    assert _Solo.zoho_org_id == "zoid-1", "org_id stays put for Cliq"
