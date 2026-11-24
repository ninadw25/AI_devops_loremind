"""Checks for the normalize projection (05_SYNC_AND_LEDGER build step 1b).

Pure asserts prove the payload→event projection reproduces the legacy event's
identity/actors/scope while keeping the FULL body (the F7 fix). DB asserts run
the worker end-to-end and skip with no live Postgres.
"""
import base64
import os

import pytest

from infra.gmail.normalizer import (
    NORMALIZER_VERSION, canonical_payload, event_from_payload,
    normalize_email_message,
)


def _b64(s: str) -> str:
    return base64.urlsafe_b64encode(s.encode()).decode()


def _raw(body: str, mid="<m1@x>", tid="thr1"):
    return {
        "id": "loc1", "threadId": tid, "internalDate": "1700000000000",
        "labelIds": ["INBOX"],
        "payload": {"mimeType": "text/plain",
                    "headers": [
                        {"name": "Message-ID", "value": mid},
                        {"name": "From", "value": "Alice <alice@ezobooks.in>"},
                        {"name": "To", "value": "bob@ezobooks.in"},
                        {"name": "Subject", "value": "Budget"},
                    ],
                    "body": {"data": _b64(body)}},
    }


def test_projection_reproduces_legacy_identity_and_scope():
    # The event projected from the payload must match what the legacy path
    # produced for the same message — same id, actors, scope, subject, thread.
    raw = _raw("hello team")
    legacy = normalize_email_message(raw, "ezobooks", "alice@ezobooks.in")
    projected = event_from_payload(canonical_payload(raw), "alice@ezobooks.in", "ezobooks")

    assert projected.id == legacy.id
    assert projected.raw["scope_ids"] == legacy.raw["scope_ids"]
    assert {a["email"] for a in projected.actors} == {a["email"] for a in legacy.actors}
    assert projected.title == legacy.title
    assert projected.timestamp == legacy.timestamp
    assert projected.raw["thread_id"] == legacy.raw["thread_id"]


def test_projection_keeps_full_body_legacy_chops_it():
    # An inline reply: fresh text, then a quoted reply marker, then more.
    body = "Here is my answer.\nOn Mon, Bob wrote:\n> the original question\nAnd a PS."
    raw = _raw(body)

    legacy = normalize_email_message(raw, "ezobooks", "alice@ezobooks.in")
    projected = event_from_payload(canonical_payload(raw), "alice@ezobooks.in", "ezobooks")

    # legacy chops at the "On ... wrote:" marker (loses the PS after the quote)
    assert "And a PS." not in legacy.body
    # the projection keeps the whole thing — the F7 inline-reply recovery
    assert projected.body == body
    assert "And a PS." in projected.body


def test_html_only_mail_recovered_by_projection():
    # text/plain-only decode returned "" for HTML-only mail (F7.4). The
    # projection falls back to the HTML part's text.
    raw = {"id": "h1", "threadId": "t", "internalDate": "1700000000000", "labelIds": [],
           "payload": {"mimeType": "multipart/alternative",
                       "headers": [{"name": "Message-ID", "value": "<h@x>"},
                                   {"name": "From", "value": "x@ezobooks.in"},
                                   {"name": "Subject", "value": "S"}],
                       "parts": [{"mimeType": "text/html", "filename": "",
                                  "body": {"data": _b64("<p>real content</p>")}}]}}
    legacy = normalize_email_message(raw, "ezobooks", "x@ezobooks.in")
    projected = event_from_payload(canonical_payload(raw), "x@ezobooks.in", "ezobooks")
    assert legacy.body == ""                       # old behaviour
    assert "real content" in projected.body        # recovered


# ── DB-gated: the normalize worker projects payloads → events, re-runnably ────

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


def test_normalize_worker_roundtrip():
    from builders.l0_ledger import Event, SourcePayload
    from builders.normalize_events import normalize_events
    from common.hashing import make_id
    import sqlalchemy as sa

    st = _store()
    org = "__nptest__"
    try:
        cid = "<worker@x>"
        pid = make_id("gmail.payload", f"{org}:{cid}")
        payload = canonical_payload(_raw("worker body here", mid=cid, tid="tW"))
        with st._session() as s:
            s.execute(sa.delete(Event).where(Event.org == org))
            s.execute(sa.delete(SourcePayload).where(SourcePayload.org == org))
        st.upsert_payloads([{
            "payload_id": pid, "org": org, "source": "gmail", "canonical_id": cid,
            "payload": payload, "sightings": {"alice@ezobooks.in": {"source_thread_id": "tW"}}}])

        r1 = normalize_events(org, source="gmail")
        assert r1["payloads"] == 1 and r1["events"] == 1

        with st._session() as s:
            ev = s.execute(sa.select(Event).where(Event.org == org)).scalar_one()
            assert ev.normalizer_version == NORMALIZER_VERSION
            assert ev.body == "worker body here"          # full body stored
            assert ev.scope_ids == ["person:alice@ezobooks.in"]

        # re-projection is idempotent (upsert, one row) and refreshes the version
        r2 = normalize_events(org, source="gmail")
        assert r2["payloads"] == 1
        with st._session() as s:
            assert s.execute(sa.select(sa.func.count()).select_from(Event)
                             .where(Event.org == org)).scalar() == 1

        with st._session() as s:
            s.execute(sa.delete(Event).where(Event.org == org))
            s.execute(sa.delete(SourcePayload).where(SourcePayload.org == org))
    finally:
        st.disconnect()
