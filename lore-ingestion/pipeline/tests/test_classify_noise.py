"""Checks for the noise-class worker (05_SYNC_AND_LEDGER build step 1c).

The point of 1c: a noise verdict becomes a column, never the presence/absence of
the row. Pure asserts confirm the reused is_noise verdicts; the DB asserts prove
the worker labels events (signal vs reason) without deleting anything, and
re-runs are idempotent. DB asserts skip with no live Postgres.
"""
import os

import pytest

from pipeline.filter import FILTER_VERSION, is_noise


def test_is_noise_verdicts_reused():
    # signal: internal mail with a body
    junk, _ = is_noise({"tags": ["email", "internal"], "primary_actor": "email:a@ezobooks.in",
                        "body": "real content"})
    assert junk is False
    # noise: empty body from an external sender
    junk, reason = is_noise({"tags": ["email", "external"],
                             "primary_actor": "email:x@vendor.com", "body": ""})
    assert junk is True and reason == "empty_body_external"


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


def _ev(store, org, eid, tags, actor, body):
    from builders.l0_ledger import Event
    with store._session() as s:
        s.add(Event(id=eid, org=org, source="gmail", event_type="gmail.message",
                    source_id=eid, title="t", body=body, primary_actor=actor,
                    tags=tags, scope_ids=["person:a@ezobooks.in"]))


def test_noise_class_labels_not_deletes():
    from builders.classify_noise import classify_noise, SIGNAL
    from builders.l0_ledger import Event
    import sqlalchemy as sa

    st = _store()
    org = "__cntest__"
    try:
        with st._session() as s:
            s.execute(sa.delete(Event).where(Event.org == org))
        _ev(st, org, "keep1", ["email", "internal"], "email:a@ezobooks.in", "real content here")
        _ev(st, org, "junk1", ["email", "external"], "email:x@vendor.com", "")  # empty body ext

        res = classify_noise(org, source="gmail")
        assert res["classified"] == 2
        assert res["signal"] == 1 and res["noise"] == 1

        with st._session() as s:
            keep = s.get(Event, "keep1")
            junk = s.get(Event, "junk1")
            assert keep.noise_class == SIGNAL
            assert keep.filter_version == FILTER_VERSION
            assert junk.noise_class == "empty_body_external"   # reason retained (audit)
            assert junk is not None                            # NOT deleted — just labeled
            # total rows unchanged: a verdict never removes an event
            assert s.execute(sa.select(sa.func.count()).select_from(Event)
                             .where(Event.org == org)).scalar() == 2

        # re-run is idempotent (still 2 rows, same verdicts)
        res2 = classify_noise(org, source="gmail")
        assert res2["signal"] == 1 and res2["noise"] == 1

        with st._session() as s:
            s.execute(sa.delete(Event).where(Event.org == org))
    finally:
        st.disconnect()
