"""
infra/zoho/l0.py
────────────────
Zoho Mail → L0 replica projection, the Zoho twin of `infra/gmail/normalizer.py`'s
L0 contract (canonical_id / canonical_payload / sighting / event_from_payload).

This is what puts Zoho onto the NEW 4-layer substrate (05_SYNC_AND_LEDGER):
`runners/zoho_ledger_sync.py` writes `canonical_payload` rows into
`source_payloads`, and `builders/normalize_events` projects them into `events`
via `event_from_payload` — after which L1/L2/claim-extraction/dedup/projection
are entirely source-agnostic and Zoho flows to the graph exactly like Gmail.

Kept separate from the legacy `infra/zoho/normalizer.py` (which still feeds the
old JSONL→triples path) so neither disturbs the other.
"""

from __future__ import annotations

from datetime import datetime, timezone

from common.hashing import make_id
from common.schemas import Actor, EventType, KnowledgeEvent
from common.scopes import person_email
from infra.zoho.normalizer import _actor_from_zoho_address, strip_html

ZOHO_MAIL_NORMALIZER_VERSION = "zoho_mail-1"

# Read by the L0 sync core (runners/l0_sync.py) as part of the per-source contract.
L0_PAYLOAD_KIND = "zoho_mail.payload"


def canonical_id(msg: dict) -> str:
    """Source-global identity of a Zoho message (05_SYNC_AND_LEDGER §3.1).

    Zoho's `messageId` is per-account, so the SAME internal message appears
    under two ids (sender's Sent, recipient's Inbox). Without the RFC-2822
    Message-ID (not returned by Zoho's message view) there is no cheap
    cross-account key, so v1 keys on the per-account id.

    ponytail: per-account identity — an internal Zoho message becomes two L0
    payloads → two events. The downstream claim dedup collapses the resulting
    duplicate claims (same subject_person + date), so it self-heals at L3. If
    that proves insufficient, the upgrade path is to fetch the RFC Message-ID
    header per message and key on it here (exactly as Gmail does).
    """
    mid = str(msg.get("messageId", "") or "")
    return f"zoho_mail:{mid}" if mid else f"zoho_mail:{make_id('zoho.fallback', str(sorted(msg.items())))}"


def canonical_payload(msg: dict) -> dict:
    """Full Zoho message → canonical raw for the L0 payload replica.

    Keeps everything that carries meaning; Zoho already hands back decoded
    text/HTML content and address strings, so there are no transport encodings
    or attachment bytes to strip (unlike Gmail's MIME tree). A normalizer bug is
    a re-projection over this floor (an UPDATE), never a re-fetch (F7)."""
    return {
        "messageId": str(msg.get("messageId", "") or ""),
        "threadId": str(msg.get("threadId", "") or ""),
        "subject": msg.get("subject", "") or "",
        "fromAddress": msg.get("fromAddress", "") or "",
        "toAddress": msg.get("toAddress", "") or "",
        "ccAddress": msg.get("ccAddress", "") or "",
        "content": msg.get("content", "") or "",
        "receivedTime": str(msg.get("receivedTime", "") or ""),
        "folderId": msg.get("folderId"),
        "hasAttachment": msg.get("hasAttachment"),
        # True when the fetcher judged this noise from the list row and never
        # bought the body (infra/zoho/fetcher.py `skip_content`). The row is a
        # complete replica of everything the list view gave us — only the body
        # is absent, and it is re-buyable by message id if the rules change.
        "content_skipped": bool(msg.get("content_skipped", False)),
    }


def sighting(msg: dict, account_email: str) -> dict:
    """The (account → per-account coordinates) entry for source_payloads.sightings.

    Keyed by account_email at the call site; the value records this account's
    own Zoho message-id + threadId + folder — the per-mailbox coordinates that
    let cross-account grouping stitch a conversation later (F7.3/F9)."""
    return {
        "source_local_id": str(msg.get("messageId", "") or ""),
        "source_thread_id": str(msg.get("threadId", "") or ""),
        "folder_id": msg.get("folderId"),
    }


def internal_date_ms(msg: dict) -> int:
    """Zoho `receivedTime` (epoch-ms string) → int; the fetch-cursor watermark."""
    try:
        return int(msg.get("receivedTime") or 0)
    except (TypeError, ValueError):
        return 0


def event_from_payload(payload: dict, account_email: str, org: str) -> KnowledgeEvent:
    """Pure projection: canonical Zoho payload (L0.payload) → KnowledgeEvent.

    Mirrors gmail.normalizer.event_from_payload so the two sources produce the
    same event shape — same participant-derived `scope_ids` in `raw` (the
    fail-closed ACL the whole graph depends on; the legacy Zoho normalizer
    omitted it), same tag scheme, same thread parent id. Re-runnable over the
    whole replica as an UPDATE when the mapping improves."""
    subject = payload.get("subject") or "(no subject)"
    message_id = str(payload.get("messageId", "") or "")
    thread_id = str(payload.get("threadId", "") or "")

    sender_raw = payload.get("fromAddress", "") or ""
    sender = _actor_from_zoho_address(sender_raw) if sender_raw else None

    to_raw = payload.get("toAddress", "") or ""
    cc_raw = payload.get("ccAddress", "") or ""
    all_actors: dict[str, Actor] = {}
    if sender:
        all_actors[sender.id] = sender
    for addr in (to_raw.split(",") + cc_raw.split(",")):
        if addr.strip():
            a = _actor_from_zoho_address(addr)
            all_actors.setdefault(a.id, a)

    domain = account_email.split("@")[-1] if "@" in account_email else ""
    is_external = bool(domain and sender and sender.email and domain not in (sender.email or ""))
    tags = ["email", "external" if is_external else "internal"]

    ms = internal_date_ms(payload)
    ts = (datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat() if ms else "")

    # Participant-derived, fail-closed ACL — identical to Gmail's.
    scope_ids = [person_email(a.email) for a in all_actors.values() if a.email]

    return KnowledgeEvent(
        id=make_id("zoho_mail.message", message_id or canonical_id(payload)),
        source="zoho_mail",
        event_type=EventType.EMAIL_MESSAGE,
        timestamp=ts,
        title=subject,
        body=strip_html(payload.get("content", "") or ""),   # FULL body — no cap
        primary_actor=sender.id if sender else None,
        actors=[a.to_dict() for a in all_actors.values()],
        url=f"https://mail.zoho.com/zm/#mail/folder/inbox/message/{message_id}",
        source_id=message_id,
        org=org,
        project=subject,
        tags=tags,
        parent_id=make_id("zoho_mail.thread", thread_id) if thread_id else None,
        raw={
            "message_id": message_id,
            "thread_id": thread_id,
            "folder_id": payload.get("folderId"),
            "has_attachment": payload.get("hasAttachment"),
            "scope_ids": scope_ids,
            "scope_id": scope_ids[0] if scope_ids else "",
            "source_ref": thread_id,
            "source_url": (f"https://mail.zoho.com/zm/#mail/folder/inbox/message/{message_id}"
                           if message_id else ""),
        },
    )
