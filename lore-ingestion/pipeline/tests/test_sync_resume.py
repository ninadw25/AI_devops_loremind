"""Checks that a fetch survives interruption (05_SYNC_AND_LEDGER build step 1a).

A real overnight run died at message 34,405 of a mailbox and lost every one of
them, then a retry hung silently for 30 minutes mid-mailbox. These asserts pin
the four properties that make those unrepeatable:

  1. batches are handed over AS THEY LAND, so an interruption keeps prior work;
  2. a resumed fetch skips ids already replicated, so the retry is cheap;
  3. every Google socket has a deadline, so a stall raises instead of blocking;
  4. a blip in the periodic service refresh doesn't abandon the mailbox.

All run against a fake Gmail service — no network, no DB.
"""
import socket

import pytest

from infra.gmail.fetcher import GmailFetcher


class _FakeGmail:
    """Minimal stand-in for the Gmail client: returns `ids` from one list page."""

    def __init__(self, ids):
        self._ids = ids

    def users(self):
        return self

    def messages(self):
        return self

    def list(self, **kw):
        return self

    def execute(self):
        return {"messages": [{"id": i, "threadId": "t"} for i in self._ids]}


def _fetcher(monkeypatch, ids, batch_size=2):
    f = GmailFetcher.__new__(GmailFetcher)          # no auth, no network
    f.domain, f.batch_size, f.batch_pause = "x.com", batch_size, 0
    # depth 1 keeps batch order deterministic for the ordering asserts below;
    # a huge rate means the throttle never sleeps in tests.
    f.units_per_sec, f.pipeline_depth = 1e9, 1
    monkeypatch.setattr(f, "_build_credentials", lambda email: None)
    monkeypatch.setattr("infra.gmail.fetcher.build", lambda *a, **k: _FakeGmail(ids))
    monkeypatch.setattr(f, "_fetch_batch",
                        lambda svc, chunk, prefix="", throttle=None:
                            [{"id": c["id"]} for c in chunk])
    return f


@pytest.fixture
def fetcher(monkeypatch):
    return _fetcher(monkeypatch, ["m1", "m2", "m3", "m4", "m5"])


def test_batches_stream_out_as_they_land(fetcher):
    seen = []
    out = fetcher.fetch_user_messages(email="a@x.com", on_batch=seen.append)

    # handed over in batches, not one lump at the end
    assert [len(b) for b in seen] == [2, 2, 1], "must stream per batch"
    assert [m["id"] for b in seen for m in b] == ["m1", "m2", "m3", "m4", "m5"]
    # nothing accumulated in RAM when someone else is taking the batches
    assert out == []


def test_resume_fetches_only_what_is_missing(fetcher):
    seen = []
    fetcher.fetch_user_messages(email="a@x.com", on_batch=seen.append,
                                skip_local_ids={"m1", "m2", "m4"})

    got = [m["id"] for b in seen for m in b]
    assert got == ["m3", "m5"], "already-replicated ids must not be re-fetched"


def test_no_on_batch_still_returns_everything(fetcher):
    # the old contract still holds for callers that want the whole list
    out = fetcher.fetch_user_messages(email="a@x.com")
    assert [m["id"] for m in out] == ["m1", "m2", "m3", "m4", "m5"]


def test_google_sockets_get_a_deadline():
    """A socket with no timeout blocks forever, so the retry ladder never gets
    an error to catch — that is how a fetch hung for 30min with the process
    still alive. httplib2 leaves its own timeout None, so the process-wide
    default is what has to reach Google."""
    from httplib2 import has_timeout

    from common.config import get_settings

    original = socket.getdefaulttimeout()
    try:
        socket.setdefaulttimeout(None)
        GmailFetcher(service_key_path="key.json", domain="x.com", admin_email="a@x.com")
        assert socket.getdefaulttimeout() == get_settings().gmail_socket_timeout
        # httplib2 only overrides a timeout it was handed, so the default wins
        assert has_timeout(None) is False
    finally:
        socket.setdefaulttimeout(original)


def test_service_refresh_blip_does_not_abandon_the_mailbox(monkeypatch):
    """Service (re)builds are plumbing. A mailbox thousands of messages in must
    not be thrown away because ONE credential build stalled — a single blip is
    retried/kept-going, only a persistent failure may fail the mailbox."""
    ids = [f"m{i}" for i in range(201)]         # enough to trip the 200th batch
    f = _fetcher(monkeypatch, ids, batch_size=1)

    calls = []

    def _blip_once(email):
        calls.append(email)
        if len(calls) == 2:         # exactly one stalled build, then recovery
            raise TimeoutError("credential build stalled")
        return None

    monkeypatch.setattr(f, "_build_credentials", _blip_once)

    seen = []
    f.fetch_user_messages(email="a@x.com", on_batch=seen.append)

    # the blip really happened (listing build + the failed one + its retry)...
    assert len(calls) >= 3, "the stalled build should have been retried"
    # ...and every message still arrived anyway
    assert len([m for b in seen for m in b]) == 201
