"""Checks for the L0 payload replica (05_SYNC_AND_LEDGER build step 1a).

Pure canonicalization asserts always run. Store asserts (dedup by unique key,
sightings merge, cursor advance) are DB-gated and skip with no live Postgres.
"""
import base64
import os

import pytest

from infra.gmail.normalizer import canonical_id, canonical_payload, sighting


def _b64(s: str) -> str:
    return base64.urlsafe_b64encode(s.encode()).decode()


def _msg():
    # A multipart message: a text/plain part, an HTML part, and a PDF attachment.
    return {
        "id": "mailbox_local_1",
        "threadId": "thr_1",
        "internalDate": "1700000000000",
        "labelIds": ["INBOX", "IMPORTANT"],
        "snippet": "hi there",
        "payload": {
            "mimeType": "multipart/mixed",
            "headers": [
                {"name": "Message-ID", "value": "<abc@mail.gmail.com>"},
                {"name": "References", "value": "<root@x> <mid@x>"},
                {"name": "Subject", "value": "Q3 numbers"},
            ],
            "parts": [
                {"mimeType": "text/plain", "filename": "",
                 "body": {"data": _b64("the real body\nwith two lines")}},
                {"mimeType": "text/html", "filename": "",
                 "body": {"data": _b64("<p>the real body</p>")}},
                {"mimeType": "application/pdf", "filename": "report.pdf",
                 "body": {"size": 90210, "attachmentId": "att_9"}},
            ],
        },
    }


def test_canonical_id_is_message_id_header():
    assert canonical_id(_msg()) == "<abc@mail.gmail.com>"
    assert canonical_id({"id": "x", "payload": {"headers": []}}) == "gmail:x"


def test_canonical_payload_keeps_meaning_drops_transport():
    p = canonical_payload(_msg())
    parts = p["payload"]["parts"]

    # every header survives — including References, the threading fix F7 needs
    hdrs = {h["name"]: h["value"] for h in p["payload"]["headers"]}
    assert hdrs["References"] == "<root@x> <mid@x>"

    text_part = next(x for x in parts if x["mimeType"] == "text/plain")
    assert text_part["text"] == "the real body\nwith two lines"   # decoded
    assert "data" not in text_part                                # encoded copy dropped

    html_part = next(x for x in parts if x["mimeType"] == "text/html")
    assert html_part["text"] == "<p>the real body</p>"            # HTML kept as text (F7.4)

    pdf = next(x for x in parts if x["mimeType"] == "application/pdf")
    assert pdf["filename"] == "report.pdf"                        # metadata kept
    assert pdf["attachment"]["size"] == 90210
    assert "text" not in pdf and "data" not in pdf                # bytes dropped

    assert p["labelIds"] == ["INBOX", "IMPORTANT"]                # labels kept


def test_sighting_carries_per_mailbox_coordinates():
    s = sighting(_msg(), "alice@ezobooks.in")
    assert s == {"source_local_id": "mailbox_local_1",
                 "source_thread_id": "thr_1", "labels": ["INBOX", "IMPORTANT"]}


# ── DB-gated: the replica IS the dedup, sightings merge across mailboxes ──────

def _store():
    dsn = os.environ.get("DATABASE_URL", "") or os.environ.get("POSTGRES_URI", "")
    if not dsn:
        from common.config import get_settings
        dsn = getattr(get_settings(), "postgres_uri", "")
    if not dsn:
        pytest.skip("no Postgres DSN configured")
    from builders.l0_ledger import LedgerStore
    st = LedgerStore(dsn)
    if not st.connect():
        pytest.skip("Postgres not reachable")
    return st


def _row(org, cid, mailbox, tid):
    from common.hashing import make_id
    return {"payload_id": make_id("gmail.payload", f"{org}:{cid}"),
            "org": org, "source": "gmail", "canonical_id": cid,
            "payload": {"threadId": tid},
            "sightings": {mailbox: {"source_thread_id": tid}}}


def test_same_message_two_mailboxes_one_row_two_sightings():
    st = _store()
    org = "__pltest__"
    try:
        import sqlalchemy as sa
        from builders.l0_ledger import SourcePayload, FetchCursor
        with st._session() as s:
            s.execute(sa.delete(SourcePayload).where(SourcePayload.org == org))
            s.execute(sa.delete(FetchCursor).where(FetchCursor.org == org))

        cid = "<shared@mail.gmail.com>"
        # shri holds it under thr_S, mukesh under thr_M — the classic split.
        a = st.sync_user_payloads([_row(org, cid, "shri@x", "thr_S")],
                                  org, "gmail", "shri@x", cursor_ms=200)
        b = st.sync_user_payloads([_row(org, cid, "mukesh@x", "thr_M")],
                                  org, "gmail", "mukesh@x", cursor_ms=100)
        assert a["inserted"] == 1
        assert b["inserted"] == 0 and b["merged"] == 1     # dedup by canonical_id
        assert st.count_payloads(org, "gmail") == 1        # one row, not two

        with st._session() as s:
            got = s.execute(sa.select(SourcePayload).where(
                SourcePayload.org == org)).scalar_one()
            assert set(got.sightings) == {"shri@x", "mukesh@x"}   # both threadIds retained
            assert got.sightings["shri@x"]["source_thread_id"] == "thr_S"
            assert got.sightings["mukesh@x"]["source_thread_id"] == "thr_M"

        # re-fetching shri must NOT add a duplicate sighting (idempotent)
        st.sync_user_payloads([_row(org, cid, "shri@x", "thr_S")],
                              org, "gmail", "shri@x", cursor_ms=200)
        with st._session() as s:
            got = s.execute(sa.select(SourcePayload).where(
                SourcePayload.org == org)).scalar_one()
            assert set(got.sightings) == {"shri@x", "mukesh@x"}

        # cursor advanced per mailbox, forward-only
        assert st.get_fetch_cursor(org, "gmail", "shri@x") == 200
        assert st.get_fetch_cursor(org, "gmail", "mukesh@x") == 100
        st.set_fetch_cursor(org, "gmail", "mukesh@x", last_internal_date_ms=50)
        assert st.get_fetch_cursor(org, "gmail", "mukesh@x") == 100   # never rewinds

        with st._session() as s:
            s.execute(sa.delete(SourcePayload).where(SourcePayload.org == org))
            s.execute(sa.delete(FetchCursor).where(FetchCursor.org == org))
    finally:
        st.disconnect()
