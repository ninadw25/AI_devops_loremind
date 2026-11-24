"""Gmail ACL scope tests.

Asserts:
  1. normalize_email_message() stamps scope_ids in person:{email} format.
  2. grouping.group_by_thread() unions scope_ids across all messages in a thread.
  3. A thread with empty scope_ids fails the _has_scope() check (fail-closed).
  4. person_email() format is locked — matches what elysia-rbac expects.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from common.scopes import person_email
from infra.gmail.normalizer import normalize_email_message
from pipeline.extraction.grouping import group_by_thread


# ── helpers ───────────────────────────────────────────────────────────────────

def _raw_message(
    gmail_id: str,
    thread_id: str,
    from_addr: str,
    to_addrs: list[str],
    subject: str = "Test subject",
    body: str = "Hello world",
) -> dict:
    """Minimal Gmail API message dict."""
    headers = [
        {"name": "Subject", "value": subject},
        {"name": "From", "value": from_addr},
        {"name": "To", "value": ", ".join(to_addrs)},
        {"name": "Message-ID", "value": f"<{gmail_id}@mail.example.com>"},
    ]
    import base64
    encoded = base64.urlsafe_b64encode(body.encode()).decode()
    return {
        "id": gmail_id,
        "threadId": thread_id,
        "internalDate": "1700000000000",
        "labelIds": ["INBOX"],
        "payload": {
            "mimeType": "text/plain",
            "headers": headers,
            "body": {"data": encoded},
        },
    }


def _has_scope(thread: dict) -> bool:
    """Mirror of unified_builder._has_scope() — the fail-closed check."""
    if thread.get("scope_id"):
        return True
    sids = thread.get("scope_ids")
    return isinstance(sids, list) and len(sids) > 0


def _write_jsonl(events: list[dict], path: Path) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")


# ── 1. person_email format is locked ─────────────────────────────────────────

def test_person_email_format():
    assert person_email("alice@company.com") == "person:alice@company.com"
    assert person_email("BOB@Company.COM") == "person:bob@company.com"
    assert person_email("  carol@org.in  ") == "person:carol@org.in"


# ── 2. normalizer stamps scope_ids in raw ────────────────────────────────────

def test_normalize_stamps_scope_ids():
    msg = _raw_message(
        gmail_id="msg1",
        thread_id="thread_abc",
        from_addr="alice@company.com",
        to_addrs=["bob@company.com", "carol@company.com"],
    )
    event = normalize_email_message(msg, org="testorg", employee_email="alice@company.com")
    raw = event.raw

    assert "scope_ids" in raw, "scope_ids must be in raw"
    assert len(raw["scope_ids"]) > 0, "scope_ids must not be empty"
    for sid in raw["scope_ids"]:
        assert sid.startswith("person:"), f"scope_id must start with 'person:': {sid}"

    assert person_email("alice@company.com") in raw["scope_ids"]
    assert person_email("bob@company.com") in raw["scope_ids"]
    assert person_email("carol@company.com") in raw["scope_ids"]


def test_normalize_stamps_source_ref():
    msg = _raw_message("msg2", "thread_xyz", "alice@company.com", ["bob@company.com"])
    event = normalize_email_message(msg, org="testorg", employee_email="alice@company.com")
    assert event.raw["source_ref"] == "thread_xyz"
    assert "thread_xyz" in event.raw["source_url"]


# ── 3. grouping unions scope_ids across messages ─────────────────────────────

def test_grouping_unions_scope_ids_across_messages():
    msg1 = _raw_message("m1", "t1", "alice@company.com", ["bob@company.com"])
    msg2 = _raw_message("m2", "t1", "bob@company.com", ["alice@company.com", "dave@company.com"])

    ev1 = normalize_email_message(msg1, "testorg", "alice@company.com").to_dict()
    ev2 = normalize_email_message(msg2, "testorg", "bob@company.com").to_dict()

    with tempfile.NamedTemporaryFile(suffix=".jsonl", mode="w",
                                     delete=False, encoding="utf-8") as f:
        tmp = Path(f.name)
        for ev in (ev1, ev2):
            ev["embedding_text"] = ""
            f.write(json.dumps(ev) + "\n")

    threads = group_by_thread(tmp)
    tmp.unlink()

    assert len(threads) == 1
    thread = next(iter(threads.values()))

    assert _has_scope(thread), "thread must have scope after grouping"
    scope_ids = thread.get("scope_ids", [])
    assert person_email("alice@company.com") in scope_ids
    assert person_email("bob@company.com") in scope_ids
    assert person_email("dave@company.com") in scope_ids


# ── 4. fail-closed: empty scope_ids → invisible ───────────────────────────────

def test_empty_scope_fails_closed():
    thread_with_scope = {"scope_ids": ["person:alice@company.com"], "scope_id": "person:alice@company.com"}
    thread_no_scope   = {"scope_ids": [], "scope_id": ""}
    thread_null_scope = {}

    assert _has_scope(thread_with_scope) is True
    assert _has_scope(thread_no_scope)   is False
    assert _has_scope(thread_null_scope) is False


# ── 5. validator preserves scope_ids (plural) ────────────────────────────────

def test_validator_preserves_scope_ids():
    """validate_thread must not drop scope_ids — the builder reads the list."""
    from pipeline.validator import validate_thread, build_name_lookup
    thread_result = {
        "thread_id": "t_val",
        "triples": [],
        "thread_summary": "Test",
        "department_tags": ["general"],
        "sensitivity": "internal",
        "scope_id": "person:alice@company.com",
        "scope_ids": ["person:alice@company.com", "person:bob@company.com"],
        "source_ref": "t_val",
        "source_url": "https://mail.google.com/mail/u/0/#inbox/t_val",
        "email_count": 2,
    }
    name_lookup = build_name_lookup({"persons": []})
    validated, _ = validate_thread(thread_result, name_lookup, {})
    assert "scope_ids" in validated, "validate_thread must preserve scope_ids"
    assert validated["scope_ids"] == ["person:alice@company.com", "person:bob@company.com"]


# ── 6. single-message thread still gets scoped ───────────────────────────────

def test_single_message_thread_is_scoped():
    msg = _raw_message("solo", "thread_solo", "alice@company.com", ["bob@company.com"])
    ev = normalize_email_message(msg, "testorg", "alice@company.com").to_dict()

    with tempfile.NamedTemporaryFile(suffix=".jsonl", mode="w",
                                     delete=False, encoding="utf-8") as f:
        tmp = Path(f.name)
        ev["embedding_text"] = ""
        f.write(json.dumps(ev) + "\n")

    threads = group_by_thread(tmp)
    tmp.unlink()

    thread = next(iter(threads.values()))
    assert _has_scope(thread), "single-message Gmail thread must be scoped"


# ── 7. address parsing must not corrupt the scope id ─────────────────────────

def test_bare_angle_address_yields_clean_scope():
    # '<ravi@x>' with no display name used to keep the brackets, producing the
    # scope 'person:<ravi@x>' — matches nobody, hides the person's own mail.
    from infra.gmail.normalizer import _actor_from_email_address
    a = _actor_from_email_address("<ravi@ezobanks.com>")
    assert a.email == "ravi@ezobanks.com"
    assert person_email(a.email) == "person:ravi@ezobanks.com"


def test_quoted_comma_in_display_name_keeps_one_recipient():
    # '"Kumar, Ravi" <ravi@x>' was split on the comma into two junk addresses.
    msg = _raw_message("msg3", "thread_c", "alice@company.com",
                       ['"Kumar, Ravi" <ravi@ezobooks.in>', "bob@company.com"])
    event = normalize_email_message(msg, org="testorg", employee_email="alice@company.com")
    emails = {a["email"] for a in event.actors}
    assert "ravi@ezobooks.in" in emails
    assert "bob@company.com" in emails
    assert all("<" not in e and '"' not in e for e in emails), emails


# ── 8. one email = one identity, whichever mailbox it came from ──────────────

def test_same_email_from_two_mailboxes_has_one_id():
    # Gmail gives each mailbox its own message id + threadId for the SAME email;
    # only the Message-ID header is global. Keying identity on the per-mailbox id
    # gave one email two ledger rows and the append-only ledger could not tell.
    shri = _raw_message("18f2a1shri", "thr_shri", "alice@company.com", ["bob@company.com"])
    mukesh = _raw_message("18f2a1mukesh", "thr_mukesh", "alice@company.com", ["bob@company.com"])
    for m in (shri, mukesh):                      # same email → same Message-ID header
        for h in m["payload"]["headers"]:
            if h["name"] == "Message-ID":
                h["value"] = "<CAF=shared@mail.gmail.com>"

    a = normalize_email_message(shri, org="testorg", employee_email="shri@ezobooks.in")
    b = normalize_email_message(mukesh, org="testorg", employee_email="mukesh@ezobooks.in")
    assert a.id == b.id, "same email must collapse to one ledger id"

    # ...and two genuinely different emails must NOT collide.
    other = _raw_message("18f2a2", "thr_x", "alice@company.com", ["bob@company.com"])
    assert normalize_email_message(other, org="testorg",
                                   employee_email="shri@ezobooks.in").id != a.id

