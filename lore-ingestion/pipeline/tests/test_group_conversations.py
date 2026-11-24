"""Checks for conversation grouping (05_SYNC_AND_LEDGER build step 1d).

Pure asserts prove the reply-refs parser and the connected-components logic
(reply-chain edges + cross-mailbox sighting edges). DB asserts run the worker
end-to-end and prove a conversation split across two mailboxes collapses to ONE
conversation_id — the fix the oldest-message key could not do. DB skips with no
live Postgres.
"""
import base64
import os

import pytest

from infra.gmail.normalizer import canonical_payload, reply_refs


def _b64(s):
    return base64.urlsafe_b64encode(s.encode()).decode()


def _raw(mid, refs="", tid="t", local="loc"):
    headers = [{"name": "Message-ID", "value": mid},
               {"name": "From", "value": "a@ezobooks.in"},
               {"name": "Subject", "value": "S"}]
    if refs:
        headers.append({"name": "References", "value": refs})
    return {"id": local, "threadId": tid, "internalDate": "1700000000000", "labelIds": [],
            "payload": {"mimeType": "text/plain", "headers": headers,
                        "body": {"data": _b64("body")}}}


def test_reply_refs_parses_message_ids():
    p = canonical_payload(_raw("<c@x>", refs="<a@x> <b@x>"))
    assert reply_refs(p) == ["<a@x>", "<b@x>"]
    assert reply_refs(canonical_payload(_raw("<c@x>"))) == []


def test_connected_components_over_both_edge_types():
    from builders.group_conversations import _UnionFind
    uf = _UnionFind()
    # A (root), B replies to A, C shares mukesh's thread with A (sighting edge)
    uf.union("<b>", "<a>")          # header edge B→A
    uf.union("<a>", "<c>")          # sighting edge (A and C in mukesh thread T9)
    assert uf.find("<a>") == uf.find("<b>") == uf.find("<c>")
    uf.find("<d>")                  # unrelated
    assert uf.find("<d>") != uf.find("<a>")


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


def test_cross_mailbox_conversation_unified():
    from builders.group_conversations import group_conversations
    from builders.l0_ledger import Event, SourcePayload
    from common.hashing import make_id
    from infra.gmail.normalizer import event_from_payload
    import sqlalchemy as sa

    st = _store()
    org = "__gctest__"
    try:
        with st._session() as s:
            s.execute(sa.delete(Event).where(Event.org == org))
            s.execute(sa.delete(SourcePayload).where(SourcePayload.org == org))

        # One conversation, but Gmail gave each mailbox its own threadId:
        #   <a> = root, held by shri (thr_S) AND mukesh (thr_M)  [cross-mailbox]
        #   <b> = reply to <a>, only in shri (thr_S)
        #   <c> = only in mukesh (thr_M), NO reply header (joins via sighting)
        # The old per-mailbox threadId scattered this into thr_S and thr_M.
        specs = [
            ("<a@x>", "", {"shri@x": "thr_S", "mukesh@x": "thr_M"}),
            ("<b@x>", "<a@x>", {"shri@x": "thr_S"}),
            ("<c@x>", "", {"mukesh@x": "thr_M"}),
        ]
        for mid, refs, sightings in specs:
            payload = canonical_payload(_raw(mid, refs=refs, local=mid))
            pid = make_id("gmail.payload", f"{org}:{mid}")
            st.upsert_payloads([{"payload_id": pid, "org": org, "source": "gmail",
                                 "canonical_id": mid, "payload": payload,
                                 "sightings": {mb: {"source_thread_id": t}
                                               for mb, t in sightings.items()}}])
            # project the event so the worker has a row to label
            ev = event_from_payload(payload, "shri@x", org)
            with st._session() as s:
                from builders.l0_ledger import _event_to_row
                st.upsert_events_projection([_event_to_row(ev.to_dict(), org, "gmail-1")])

        res = group_conversations(org, source="gmail")
        assert res["payloads"] == 3
        assert res["conversations"] == 1, "the three messages must be ONE conversation"

        with st._session() as s:
            convs = s.execute(sa.select(Event.conversation_id).where(Event.org == org)).scalars().all()
            assert len(set(convs)) == 1 and all(convs), convs   # all labeled, all the same

        with st._session() as s:
            s.execute(sa.delete(Event).where(Event.org == org))
            s.execute(sa.delete(SourcePayload).where(SourcePayload.org == org))
    finally:
        st.disconnect()
