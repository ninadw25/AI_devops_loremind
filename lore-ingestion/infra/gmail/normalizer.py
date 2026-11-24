"""
gmail/normalizer.py
────────────────────
Converts raw Gmail API responses → KnowledgeEvents.
Same output schema as github normalizer so the knowledge brain
sees one unified feed regardless of source.

Assumes you're using the Gmail API with DWD (domain-wide delegation)
and messages are in "full" format from messages.get().
"""

import re
import base64
from email.utils import formataddr, getaddresses, parseaddr

from common.schemas import KnowledgeEvent, Actor, EventType
from common.hashing import make_id
from common.logger import get_logger
from common import scopes
from common.scopes import person_email

log = get_logger("gmail.normalizer")


# ════════════════════════════════════════════
# HELPERS
# ════════════════════════════════════════════

def _extract_header(headers: list[dict], name: str) -> str:
    for h in headers:
        if h["name"].lower() == name.lower():
            return h["value"]
    return ""


def canonical_id(message: dict) -> str:
    """Source-global identity of a Gmail message (05_SYNC_AND_LEDGER §3.1).

    The RFC-2822 Message-ID header is identical in every mailbox that holds the
    message, so it is the dedup key. Falls back to the per-mailbox Gmail id only
    when the header is absent (0/212k in the real corpus) — never empty, so the
    replica's unique key always exists.
    """
    mid = _extract_header((message.get("payload") or {}).get("headers", []), "Message-ID")
    return mid.strip() if mid else f"gmail:{message.get('id', '')}"


def _canonical_part(part: dict) -> dict:
    """One MIME part → canonical form: text decoded in place, attachment BYTES
    dropped but metadata kept. Recurses into multipart children.

    'Canonical raw' = lossless in meaning, lossy only in transport: base64 text
    is decoded and the encoded copy dropped; attachment bytes are dropped while
    filename/mimeType/size survive (05_SYNC_AND_LEDGER §3.1)."""
    mime = part.get("mimeType", "")
    body = part.get("body", {}) or {}
    filename = part.get("filename", "")
    out: dict = {"mimeType": mime, "headers": part.get("headers", [])}
    if filename:
        out["filename"] = filename

    data = body.get("data")
    if data and not filename and mime.startswith("text/"):
        try:
            out["text"] = base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")
        except Exception as e:  # noqa: BLE001 — never let one bad part sink the payload
            log.debug("canonical: failed to decode %s part: %s", mime, e)
            out["text"] = ""
    elif data or body.get("attachmentId"):
        # Attachment / non-text blob — keep the metadata, drop the bytes.
        out["attachment"] = {"size": body.get("size"),
                             "attachment_id": body.get("attachmentId")}

    children = part.get("parts")
    if children:
        out["parts"] = [_canonical_part(p) for p in children]
    return out


def canonical_payload(message: dict) -> dict:
    """Full Gmail message → canonical raw for the L0 payload replica.

    Keeps everything that carries meaning — all headers, every text part decoded,
    labels, threadId, internalDate, snippet — and drops only transport encodings
    and attachment bytes. This is the permanent floor: a normalizer bug becomes a
    re-projection (an UPDATE over this), not a re-fetch (F7)."""
    return {
        "id": message.get("id", ""),
        "threadId": message.get("threadId", ""),
        "internalDate": message.get("internalDate", ""),
        "labelIds": message.get("labelIds", []),
        "snippet": message.get("snippet", ""),
        "sizeEstimate": message.get("sizeEstimate"),
        "payload": _canonical_part(message.get("payload", {}) or {}),
    }


def reply_refs(payload: dict) -> list[str]:
    """Message-IDs this message replies to — from References + In-Reply-To.

    These are global (same in every mailbox), so they link a message to its
    conversation ancestors regardless of which inbox it was fetched from. The
    edges they form are half of the conversation grouping (05_SYNC_AND_LEDGER
    §3.3); the cross-mailbox `sightings` are the other half."""
    headers = (payload.get("payload") or {}).get("headers", [])
    raw = (_extract_header(headers, "References") + " "
           + _extract_header(headers, "In-Reply-To"))
    # Message-IDs are angle-bracketed tokens; fall back to bare tokens.
    ids = re.findall(r"<[^>]+>", raw) or raw.split()
    return [i.strip() for i in ids if i.strip()]


# The L0 sync core (runners/l0_sync.py) reads these two symbols as part of the
# fixed per-source contract; keeping them beside canonical_id/payload/sighting.
L0_PAYLOAD_KIND = "gmail.payload"


def internal_date_ms(message: dict) -> int:
    """Gmail `internalDate` (epoch-ms string) → int; the fetch-cursor watermark."""
    try:
        return int(message.get("internalDate") or 0)
    except (TypeError, ValueError):
        return 0


def sighting(message: dict, mailbox: str) -> dict:
    """The (mailbox → per-mailbox coordinates) entry for source_payloads.sightings.

    Keyed by mailbox at the call site; the value carries this mailbox's own
    Gmail message-id + threadId + labels, which is what stitches one conversation
    across the mailboxes that hold it (F7.3/F9)."""
    return {
        "source_local_id": message.get("id", ""),
        "source_thread_id": message.get("threadId", ""),
        "labels": message.get("labelIds", []),
    }


def _decode_body(payload: dict) -> str:
    """Recursively extract text/plain body from Gmail message payload."""
    mime = payload.get("mimeType", "")

    if mime == "text/plain":
        data = payload.get("body", {}).get("data", "")
        if data:
            try:
                return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")
            except Exception as e:
                log.debug("Failed to decode base64 body: %s", e)
                return ""

    if mime.startswith("multipart/"):
        for part in payload.get("parts", []):
            text = _decode_body(part)
            if text:
                return text

    return ""


def _clean_email_body(body: str) -> str:
    """Strip quoted reply chains and signatures to keep the fresh content."""
    lines = body.split("\n")
    clean = []
    for line in lines:
        stripped = line.strip()
        # Stop at common reply markers
        if stripped.startswith(("On ", "> ", "From:", "Sent:", "-----Original")):
            break
        clean.append(line)
    return "\n".join(clean).strip()


def _actor_from_email_address(raw_address: str) -> Actor:
    """
    Parses 'Rahul Sharma <rahul@company.com>' → Actor
    Or just 'rahul@company.com' → Actor

    Uses stdlib RFC-2822 parsing: the hand-rolled regex kept the angle
    brackets on a bare '<rahul@company.com>' (no display name), which then
    became the ACL id 'person:<rahul@company.com>' — a scope that matches
    nobody, silently hiding that person's own mail from them.
    """
    name, email = parseaddr(raw_address)
    if not email:                        # unparseable → keep raw, fail loudly downstream
        name = email = raw_address.strip()

    return Actor(
        id=f"email:{email.lower()}",
        display_name=name or email,
        email=email.lower(),
        source="gmail",
    )


def _all_recipients(headers: list[dict]) -> list[Actor]:
    actors = []
    for field_name in ("To", "Cc", "Bcc"):
        raw = _extract_header(headers, field_name)
        if raw:
            # getaddresses, not split(","): a comma inside a quoted display
            # name ('"Kumar, Ravi" <ravi@x>') split the address in half.
            for name, email in getaddresses([raw]):
                if email.strip():
                    actors.append(_actor_from_email_address(formataddr((name, email))))
    return actors


def get_message_id_header(message: dict) -> str:
    """
    Extract the RFC 2822 Message-ID header from a Gmail message.
    Used for cross-inbox deduplication.
    """
    payload = message.get("payload", {})
    headers = payload.get("headers", [])
    return _extract_header(headers, "Message-ID")


# ════════════════════════════════════════════
# NORMALIZE: canonical payload → KnowledgeEvent (the pure projection)
# ════════════════════════════════════════════

# Bump when event_from_payload's mapping changes — the analogue of
# extractor_version on claims. A change becomes a re-projection (UPDATE over the
# replica), never a re-fetch (05_SYNC_AND_LEDGER §3.2).
NORMALIZER_VERSION = "gmail-1"


def _collect_text(part: dict, want_mime: str) -> str:
    """First decoded text of the given mime type in a canonical MIME tree (DFS)."""
    if part.get("mimeType", "") == want_mime and part.get("text"):
        return part["text"]
    for child in part.get("parts", []) or []:
        t = _collect_text(child, want_mime)
        if t:
            return t
    return ""


def _canonical_text(root: dict) -> str:
    """Full faithful body from a canonical payload: prefer text/plain, fall back
    to text/html (recovers HTML-only mail — F7.4). No quote-stripping, no
    truncation — those are render-time, per-consumer concerns."""
    return _collect_text(root, "text/plain") or _collect_text(root, "text/html") or ""


def event_from_payload(payload: dict, mailbox: str, org: str) -> KnowledgeEvent:
    """Pure projection: canonical payload (L0.payload) → KnowledgeEvent (L0.event).

    Re-runnable over the whole replica as an UPDATE when the mapping improves —
    a normalizer bug is a re-projection, not a re-fetch. Stores the FULL body;
    quote-stripping and truncation move to whichever consumer renders it.
    `mailbox` is any inbox that held the message (for the internal/external tag);
    scope/identity are participant-derived and mailbox-independent."""
    root = payload.get("payload", {}) or {}
    headers = root.get("headers", [])

    subject = _extract_header(headers, "Subject") or "(no subject)"
    from_raw = _extract_header(headers, "From") or mailbox
    message_id = (_extract_header(headers, "Message-ID").strip()
                  or f"gmail:{payload.get('id', '')}")
    # NOTE: threadId here is per-mailbox and only a fallback grouping key. The
    # cross-mailbox-correct conversation grouping is derived later from the
    # References/In-Reply-To headers (kept in the payload) + the sightings that
    # record every mailbox's threadId (05_SYNC_AND_LEDGER §3.3).
    thread_id = payload.get("threadId", "")

    sender = _actor_from_email_address(from_raw)
    all_actors = {sender.id: sender}
    for r in _all_recipients(headers):
        all_actors.setdefault(r.id, r)

    domain = mailbox.split("@")[-1] if "@" in mailbox else ""
    tags = ["email"]
    tags.append("external" if (domain and sender.email and domain not in (sender.email or ""))
                else "internal")

    epoch_ms = payload.get("internalDate")
    if epoch_ms:
        from datetime import datetime, timezone
        ts = datetime.fromtimestamp(int(epoch_ms) / 1000, tz=timezone.utc).isoformat()
    else:
        ts = _extract_header(headers, "Date") or ""

    list_unsubscribe = _extract_header(headers, "List-Unsubscribe")
    scope_ids = [person_email(a.email) for a in all_actors.values() if a.email]

    return KnowledgeEvent(
        id=make_id("gmail.message", message_id),   # global Message-ID identity
        source="gmail",
        event_type=EventType.EMAIL_MESSAGE,
        timestamp=ts,
        title=subject,
        body=_canonical_text(root),                 # FULL body — no clean, no cap
        primary_actor=sender.id,
        actors=[a.to_dict() for a in all_actors.values()],
        url=f"https://mail.google.com/mail/u/0/#inbox/{payload.get('id', '')}",
        source_id=payload.get("id", ""),
        org=org,
        project=subject,
        tags=tags + (payload.get("labelIds") or []),
        parent_id=make_id("gmail.thread", thread_id) if thread_id else None,
        raw={
            "message_id": message_id,
            "thread_id": thread_id,
            "label_ids": payload.get("labelIds", []),
            "list_unsubscribe": list_unsubscribe or "",
            "scope_ids": scope_ids,
            "scope_id": scope_ids[0] if scope_ids else "",
            "source_ref": thread_id,
            "source_url": f"https://mail.google.com/mail/u/0/#inbox/{thread_id}" if thread_id else "",
        },
    )


# ════════════════════════════════════════════
# NORMALIZE: single raw Gmail message → KnowledgeEvent (legacy JSONL path)
# ════════════════════════════════════════════
def normalize_email_message(
    message: dict,          # raw Gmail API message object
    org: str,               # customer org name (for context)
    employee_email: str,    # whose inbox this came from (for actor context)
) -> KnowledgeEvent:
    """Legacy fetch→JSONL path. Now a thin wrapper over the canonical projection
    so both routes share one mapping. Re-applies the OLD body rendering
    (text/plain only, quote-stripped, 4000-char cap) so pre-1b output is
    reproduced byte-for-byte; the ledger projection keeps the full body instead."""
    payload = canonical_payload(message)
    ev = event_from_payload(payload, employee_email, org)
    legacy_body = _clean_email_body(_collect_text(payload.get("payload", {}), "text/plain"))
    ev.body = legacy_body[:4000]
    return ev


# ════════════════════════════════════════════
# NORMALIZE: EMAIL THREAD → KnowledgeEvent (aggregated)
# ════════════════════════════════════════════
def normalize_email_thread(
    thread_messages: list[KnowledgeEvent],   # already normalized messages
    thread_id: str,
    org: str,
) -> KnowledgeEvent | None:
    """
    Aggregates a thread of messages into one thread-level KnowledgeEvent.
    This is what gets embedded for 'what happened in this conversation' queries.
    """
    if not thread_messages:
        log.debug("Empty thread %s, skipping", thread_id)
        return None

    # Sort by timestamp
    sorted_msgs = sorted(thread_messages, key=lambda e: e.timestamp)
    first = sorted_msgs[0]
    last = sorted_msgs[-1]

    # Collect all actors across thread
    all_actors = {}
    for msg in sorted_msgs:
        for a in msg.actors:
            all_actors[a["id"]] = a

    # Build readable thread body
    thread_body = "\n\n---\n\n".join(
        f"From: {msg.primary_actor} ({msg.timestamp[:10]})\n"
        f"Subject: {msg.title}\n\n{msg.body[:800]}"
        for msg in sorted_msgs
    )

    log.debug("Thread %s: %d messages, %d participants",
              thread_id, len(sorted_msgs), len(all_actors))

    return KnowledgeEvent(
        id=make_id("gmail.thread", thread_id),
        source="gmail",
        event_type=EventType.EMAIL_THREAD,
        timestamp=last.timestamp,           # thread timestamp = last message
        title=first.title,                  # subject line
        body=thread_body[:5000],
        primary_actor=first.primary_actor,  # thread starter
        actors=list(all_actors.values()),
        url=f"https://mail.google.com/mail/u/0/#inbox/{thread_id}",
        source_id=thread_id,
        org=org,
        project=first.title,
        tags=["email_thread"] + list({tag for msg in sorted_msgs for tag in msg.tags}),
        related_ids=[msg.id for msg in sorted_msgs],
        raw={"thread_id": thread_id, "message_count": len(sorted_msgs)},
    )
