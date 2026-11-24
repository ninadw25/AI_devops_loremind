"""Runnable check for the L0 event ledger (builders/l0_ledger.py).

Pure-mapping asserts always run. Idempotency asserts are DB-gated on
DATABASE_URL/POSTGRES_URI and skip when no live Postgres is reachable.
"""

import os

import pytest

from builders.l0_ledger import _event_to_row, _parse_ts


def test_scope_stamping_fail_closed():
    row = _event_to_row(
        {"id": "x", "source": "gmail", "event_type": "gmail.thread",
         "timestamp": "not-a-date", "scope_ids": [], "extra_scope_ids": ["a"]},
        org="acme")
    assert row["scope_ids"] == []          # empty stays empty — invisible, not public
    assert row["scope_all_ids"] == ["a"]   # extra_scope_ids → scope_all_ids (locked name)
    assert row["occurred_at"] is None      # bad timestamp degrades, no crash
    assert row["org"] == "acme"
    assert row["dek_id"] is None           # plaintext dev mode


def test_scopes_are_recorded_not_derived():
    # L0 records what the ACL-aware fetch stamped. Actors are NOT a scope source:
    # an event with participants but no scopes stays invisible.
    row = _event_to_row(
        {"id": "z", "source": "gmail", "event_type": "gmail.thread",
         "actors": [{"email": "alice@x.com"}, {"email": "bob@x.com"}]},
        org="acme")
    assert row["scope_ids"] == []
    assert row["scope_id"] is None

    row = _event_to_row(
        {"id": "z2", "source": "slack", "event_type": "slack.message.posted",
         "raw": {"scope_id": "slack:channel:C123"}}, org="acme")
    assert row["scope_ids"] == ["slack:channel:C123"]


def test_org_and_timestamp():
    row = _event_to_row(
        {"id": "y", "source": "slack", "event_type": "slack.message.posted",
         "org": "beta", "timestamp": "2025-09-16T15:00:00Z", "scope_ids": ["s1"]},
        org="acme")
    assert row["org"] == "beta"            # event's own org wins over the arg
    assert row["occurred_at"] is not None
    assert _parse_ts(None) is None and _parse_ts("") is None


def _live_store():
    dsn = (os.environ.get("DATABASE_URL", "")
           or os.environ.get("POSTGRES_URI", ""))
    if not dsn:
        from common.config import get_settings
        dsn = getattr(get_settings(), "postgres_uri", "")
    if not dsn:
        pytest.skip("no Postgres DSN configured")
    from builders.l0_ledger import LedgerStore
    store = LedgerStore(dsn)
    if not store.connect():
        pytest.skip("Postgres not reachable")
    return store


def test_idempotent_upsert():
    store = _live_store()
    try:
        org = "__l0test__"
        rows = [_event_to_row({"id": "t1", "source": "slack", "event_type": "slack.message.posted",
                               "timestamp": "2025-01-01T00:00:00Z",
                               "scope_ids": ["slack:channel:C1"]}, org),
                _event_to_row({"id": "t2", "source": "slack", "event_type": "slack.message.posted",
                               "timestamp": "2025-01-02T00:00:00Z",
                               "scope_ids": ["slack:channel:C2"]}, org)]
        # clean slate
        with store._session() as s:
            from builders.l0_ledger import Event
            import sqlalchemy as sa
            s.execute(sa.delete(Event).where(Event.org == org))

        first = store.upsert_events(rows)
        second = store.upsert_events(rows)      # same rows again
        assert first == 2, f"expected 2 inserted, got {first}"
        assert second == 0, f"re-insert must be a no-op, got {second}"
        assert store.count(org) == 2            # append-only, no duplication

        with store._session() as s:
            from builders.l0_ledger import Event
            got = s.get(Event, "t1")
            assert got.scope_ids == ["slack:channel:C1"]   # round-trips as stamped
            import sqlalchemy as sa
            s.execute(sa.delete(Event).where(Event.org == org))
    finally:
        store.disconnect()
