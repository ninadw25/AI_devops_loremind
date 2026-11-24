"""Zoho L0 projection: the payload-replica + event contract.

Pins the properties that let Zoho ride the SAME substrate as Gmail:

  1. canonical_id is stable and namespaced per source (never collides with a
     gmail id, never empty);
  2. canonical_payload keeps the body and all addressing losslessly (it is the
     permanent floor — a normalizer fix must be a re-projection, not a re-fetch);
  3. event_from_payload resolves participants and, crucially, stamps
     participant-derived scope_ids into raw — the fail-closed ACL the whole
     graph depends on, which the legacy Zoho normalizer omitted.
"""
from infra.zoho.l0 import (canonical_id, canonical_payload, event_from_payload,
                          internal_date_ms, sighting)

SAMPLE = {
    "messageId": "18400000001234567",
    "threadId": "18400000009999999",
    "subject": "Q3 vendor renewal — Servetel",
    "fromAddress": "Mukesh Sethia <mukesh@ezobooks.in>",
    "toAddress": "pooja@ezobooks.in, accounts@ezobooks.in",
    "ccAddress": "gk@ezobooks.in",
    "content": "<div>Approved the renewal at <b>INR 44,250</b> incl GST.</div>",
    "receivedTime": "1730451600000",
    "folderId": "18400000000000123",
    "hasAttachment": False,
}


def test_canonical_id_namespaced_and_stable():
    assert canonical_id(SAMPLE) == "zoho_mail:18400000001234567"
    assert canonical_id(SAMPLE).startswith("zoho_mail:")   # never collides with gmail
    assert canonical_id({}).startswith("zoho_mail:")        # never empty


def test_canonical_payload_is_lossless_on_meaning():
    p = canonical_payload(SAMPLE)
    assert p["content"] == SAMPLE["content"]                # body kept verbatim
    assert p["fromAddress"] == SAMPLE["fromAddress"]
    assert p["toAddress"] == SAMPLE["toAddress"] and p["ccAddress"] == SAMPLE["ccAddress"]
    assert p["messageId"] == SAMPLE["messageId"] and p["threadId"] == SAMPLE["threadId"]


def test_sighting_records_per_account_coordinates():
    s = sighting(SAMPLE, "mukesh@ezobooks.in")
    assert s["source_local_id"] == "18400000001234567"
    assert s["source_thread_id"] == "18400000009999999"


def test_internal_date_parses_epoch_ms():
    assert internal_date_ms(SAMPLE) == 1730451600000
    assert internal_date_ms({"receivedTime": "garbage"}) == 0
    assert internal_date_ms({}) == 0


def test_event_resolves_participants_and_stamps_fail_closed_scopes():
    ev = event_from_payload(canonical_payload(SAMPLE), "mukesh@ezobooks.in", "ezobooks")
    assert ev.source == "zoho_mail"
    assert ev.title == "Q3 vendor renewal — Servetel"
    assert "INR 44,250" in ev.body and "<b>" not in ev.body   # html stripped, content kept
    # sender + 2 To + 1 Cc = 4 participants
    emails = {a["email"] for a in ev.actors}
    assert emails == {"mukesh@ezobooks.in", "pooja@ezobooks.in",
                      "accounts@ezobooks.in", "gk@ezobooks.in"}
    # the load-bearing bit: every participant becomes a scope id, fail-closed
    scopes = ev.raw["scope_ids"]
    assert len(scopes) == 4 and ev.raw["scope_id"] == scopes[0]
    assert ev.primary_actor == "email:mukesh@ezobooks.in"


def test_event_with_no_participants_has_empty_scopes_not_public():
    ev = event_from_payload(canonical_payload({"messageId": "1", "content": "x"}),
                            "acct@ezobooks.in", "ezobooks")
    assert ev.raw["scope_ids"] == [] and ev.raw["scope_id"] == ""   # invisible, never public
