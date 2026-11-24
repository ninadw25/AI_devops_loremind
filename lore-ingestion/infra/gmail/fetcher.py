"""
gmail/fetcher.py
─────────────────
Handles all Gmail API interaction:
  - List domain users via Admin SDK
  - Fetch emails per user with pagination
  - Rate limiting (0.1s between requests, well under 250 units/sec)
  - Exponential backoff on 429 (rate limit exceeded)
  - Pre-fetch filtering via Gmail query operators (Phase 1)

Does NOT normalize data — that's gmail/normalizer.py's job.
"""

import time
import socket
import threading
import logging as _logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Optional

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from common.config import get_settings
from common.logger import get_logger
from infra.gmail.throttle import UNITS_PER_GET, QuotaThrottle


def _retry_after_from_http_error(err: HttpError) -> Optional[float]:
    """Extract a Retry-After value (seconds) from a Gmail HttpError, if any.

    Gmail's `HttpError.resp` is an httplib2 Response (dict-like, lowercased
    header keys). Returns None when absent or unparseable so the caller
    falls back to its computed exponential backoff."""
    resp = getattr(err, "resp", None)
    if resp is None:
        return None
    try:
        ra = resp.get("retry-after")
    except AttributeError:
        return None
    if ra is None:
        return None
    try:
        return float(ra)
    except (TypeError, ValueError):
        return None

log = get_logger("gmail.fetcher")

# Suppress noisy "file_cache is only supported with oauth2client<4.0.0" warning
_logging.getLogger("googleapiclient.discovery_cache").setLevel(_logging.ERROR)

# Network-level errors that warrant a retry + service rebuild.
# httplib2 is a transitive dep of google-api-python-client; guard the import
# in case Pylance can't resolve it statically.
try:
    import httplib2 as _httplib2
    _NETWORK_ERRORS = (
        socket.timeout, socket.error, ConnectionError,
        OSError, TimeoutError, _httplib2.ServerNotFoundError,
    )
except ImportError:
    _NETWORK_ERRORS = (socket.timeout, socket.error, ConnectionError, OSError, TimeoutError)

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/admin.directory.user.readonly",
]

# ══════════════════════════════════════════════
# PRE-FETCH FILTER CONFIGURATION
# ══════════════════════════════════════════════

# Email prefixes that indicate shared/functional mailboxes (not personal inboxes).
# These get a restricted query: only SENT + internal conversations are fetched.
FUNCTIONAL_MAILBOX_PREFIXES = {
    "accounts", "ai", "dev", "dm", "hr", "influencer", "manager",
    "partner", "sales", "support", "info", "admin", "billing",
    "noreply", "no-reply", "marketing", "contact", "hello",
    "team", "ops", "operations", "finance", "legal",
}

# Gmail category tabs to exclude at the API query level.
# These map to Gmail's automatic categorization and are extremely
# reliable for removing bulk/automated mail before we ever download it.
EXCLUDE_CATEGORIES = [
    "category:promotions",
    "category:social",
    "category:updates",
    "category:forums",
]

# Sender patterns to exclude at query level (Gmail search operators).
# These are the highest-volume noreply senders that we can filter
# before downloading any message body.
EXCLUDE_SENDER_PATTERNS = [
    "from:noreply",
    "from:no-reply",
    "from:donotreply",
    "from:do-not-reply",
    "from:mailer-daemon",
    "from:notifications@",
    "from:notification@",
    "from:calendar-notification",
    "from:postmaster",
]


def is_functional_mailbox(email: str, extra_functional: Optional[set[str]] = None) -> bool:
    """
    Check if an email address is a shared/functional mailbox.

    Heuristics:
    - Exact match of local part against known functional prefixes
    - Any dot/dash/underscore-separated segment matches a functional prefix
    - Extra overrides can be passed per-org
    """
    local_part = email.split("@")[0].lower()

    # Check extra overrides first
    if extra_functional and local_part in extra_functional:
        return True

    # Exact match
    if local_part in FUNCTIONAL_MAILBOX_PREFIXES:
        return True

    # Check individual segments: "dhanda.ai" → ["dhanda", "ai"], "ai" matches
    segments = set(local_part.replace(".", " ").replace("-", " ").replace("_", " ").split())
    if segments & FUNCTIONAL_MAILBOX_PREFIXES:
        return True

    return False


def build_query(
    since: Optional[str] = None,
    since_epoch: Optional[int] = None,
    is_functional: bool = False,
    domain: Optional[str] = None,
    org_domains: Optional[list[str]] = None,
) -> str:
    """
    Build a Gmail search query with pre-fetch filtering.

    For personal mailboxes: exclude noise categories + noreply senders.
    For functional mailboxes: only fetch SENT mail + internal conversations.

    Args:
        since: ISO date — only fetch after this date (human-friendly, day-resolution)
        since_epoch: exact epoch seconds — for lossless incremental sync from a
            watermark. Takes precedence over `since` when both are provided.
            Gmail's `after:<seconds>` is strict greater-than against
            `internalDate`, so passing the last-seen internalDate gives the
            next message and nothing before it.
        is_functional: True if this is a shared/functional mailbox
        domain: The org's primary domain (e.g. "ezobooks.in")
        org_domains: All domains belonging to this org (for internal detection)
    """
    parts = ["-in:spam", "-in:trash"]

    # Precise watermark takes priority over the human-friendly --since.
    if since_epoch is not None:
        parts.append(f"after:{int(since_epoch)}")
    elif since:
        from datetime import datetime as dt
        try:
            since_dt = dt.fromisoformat(since.replace("Z", "+00:00"))
            after_epoch = int(since_dt.timestamp())
            parts.append(f"after:{after_epoch}")
        except ValueError:
            pass

    if is_functional:
        # Functional mailboxes: only their sent mail + internal conversations
        # This avoids downloading thousands of vendor pitches, job apps, etc.
        domain_filter_parts = []
        all_domains = list(set((org_domains or []) + ([domain] if domain else [])))
        for d in all_domains:
            domain_filter_parts.append(f"from:@{d}")
            domain_filter_parts.append(f"to:@{d}")
        if domain_filter_parts:
            # At least one side of the conversation must be internal
            parts.append(f"{{{' '.join(domain_filter_parts)}}}")
    else:
        # Personal mailboxes: exclude noise categories and noreply senders
        for cat in EXCLUDE_CATEGORIES:
            parts.append(f"-{cat}")
        for sender in EXCLUDE_SENDER_PATTERNS:
            parts.append(f"-{sender}")

    return " ".join(parts)


class GmailFetcher:
    """
    Fetches raw Gmail data for all users in a Google Workspace domain.

    Uses Domain-Wide Delegation (DWD) via a service account to
    impersonate each user and read their emails.
    """

    def __init__(
        self,
        service_key_path: str,
        domain: str,
        admin_email: str,
        rate_limit_delay: Optional[float] = None,
        user_cooldown: Optional[float] = None,
        batch_size: Optional[int] = None,
        batch_pause: Optional[float] = None,
        max_retries: Optional[int] = None,
    ):
        # Resolve rate-limit/batching knobs from config when not overridden,
        # so they're tunable via env without code changes.
        s = get_settings()
        self.service_key_path = service_key_path
        self.domain = domain
        self.admin_email = admin_email
        self.rate_limit_delay = (
            s.gmail_rate_limit_delay if rate_limit_delay is None else rate_limit_delay)
        self.user_cooldown = (
            s.gmail_user_cooldown if user_cooldown is None else user_cooldown)
        self.batch_size = s.gmail_batch_size if batch_size is None else batch_size
        self.batch_pause = s.gmail_batch_pause if batch_pause is None else batch_pause
        self.max_retries = s.gmail_max_retries if max_retries is None else max_retries
        self.units_per_sec = s.gmail_units_per_sec
        self.pipeline_depth = s.gmail_pipeline_depth

        # Give every Google socket a deadline. The retry ladder here catches
        # socket.timeout, but a socket with no timeout never raises one — it
        # just blocks, and the fetch dies silently mid-mailbox with the process
        # still alive. Set globally because httplib2 (under googleapiclient)
        # exposes no timeout on the service built from credentials.
        socket.setdefaulttimeout(s.gmail_socket_timeout)

        log.info("GmailFetcher initialized — domain=%s, admin=%s", domain, admin_email)
        log.debug("Batch size %d, pause %.2fs/batch, %.2fs/req, %.1fs/user cooldown, %d retries",
                  self.batch_size, self.batch_pause, self.rate_limit_delay,
                  self.user_cooldown, self.max_retries)

    def _build_credentials(self, subject: str) -> service_account.Credentials:
        """Create impersonated credentials for a given user."""
        return service_account.Credentials.from_service_account_file(
            self.service_key_path, scopes=SCOPES
        ).with_subject(subject)

    # ══════════════════════════════════════════════
    # LIST USERS
    # ══════════════════════════════════════════════

    def list_users(self, extra_functional: Optional[set[str]] = None) -> list[dict]:
        """
        List all users in the domain via Admin SDK.
        Filters out suspended accounts. Classifies as personal vs functional.

        Returns list of dicts with keys: email, name, suspended, is_functional, orgUnitPath
        """
        log.info("Listing users in domain: %s", self.domain)

        creds = self._build_credentials(self.admin_email)
        admin_service = build("admin", "directory_v1", credentials=creds)

        all_users = []
        page_token = None

        while True:
            params = {"domain": self.domain, "maxResults": 500}
            if page_token:
                params["pageToken"] = page_token

            result = admin_service.users().list(**params).execute()
            users = result.get("users", [])
            all_users.extend(users)

            page_token = result.get("nextPageToken")
            if not page_token:
                break

        # Parse into clean format, classify as personal vs functional
        parsed = []
        suspended_count = 0
        functional_count = 0
        for u in all_users:
            is_suspended = u.get("suspended", False)
            if is_suspended:
                suspended_count += 1
                continue

            email = u["primaryEmail"]
            is_func = is_functional_mailbox(email, extra_functional=extra_functional)
            if is_func:
                functional_count += 1

            parsed.append({
                "email": email,
                "name": u.get("name", {}).get("fullName", email),
                "suspended": False,
                "is_functional": is_func,
                "orgUnitPath": u.get("orgUnitPath", "/"),
                "creationTime": u.get("creationTime", ""),
                "lastLoginTime": u.get("lastLoginTime", ""),
            })

        log.info("Found %d users (%d active, %d suspended — skipped)",
                 len(all_users), len(parsed), suspended_count)
        log.info("  Personal mailboxes: %d | Functional mailboxes: %d",
                 len(parsed) - functional_count, functional_count)

        for u in parsed:
            tag = "[FUNC]" if u["is_functional"] else "[PERSONAL]"
            log.debug("  %s %s (%s)", tag, u["email"], u["name"])

        return parsed

    # ══════════════════════════════════════════════
    # FETCH MESSAGES FOR ONE USER
    # ══════════════════════════════════════════════

    def fetch_user_messages(
        self,
        email: str,
        since: Optional[str] = None,
        since_epoch: Optional[int] = None,
        user_index: int = 0,
        total_users: int = 1,
        is_functional: bool = False,
        org_domains: Optional[list[str]] = None,
        on_batch: Optional[Callable[[list[dict]], None]] = None,
        skip_local_ids: Optional[set[str]] = None,
    ) -> list[dict]:
        """
        Fetch all email messages for a single user.

        Args:
            email: User's email address to impersonate
            since: ISO date string — only fetch messages after this date
            user_index: Current user number (for logging)
            total_users: Total users being processed (for logging)
            is_functional: True if this is a shared/functional mailbox
            org_domains: All domains belonging to this org (for internal detection)
            on_batch: called with each batch as it lands. A 30k-message mailbox
                takes hours; accumulating it all in RAM and returning at the end
                means an interruption loses every message fetched so far. With
                this set, batches are handed over as they arrive and nothing is
                accumulated (the return is empty).
            skip_local_ids: Gmail message ids already held for this mailbox —
                dropped after listing so a resumed fetch costs only what is
                actually missing, not the whole inbox again.

        Returns:
            List of raw Gmail API message dicts (full format), or [] when
            `on_batch` is handling them.
        """
        prefix = f"[{user_index}/{total_users}]"
        mailbox_tag = "FUNC" if is_functional else "PERSONAL"
        log.info("%s %s [%s] — starting message fetch", prefix, email, mailbox_tag)

        creds = self._build_credentials(email)
        gmail = build("gmail", "v1", credentials=creds)

        # Build smart pre-filtered query
        query = build_query(
            since=since,
            since_epoch=since_epoch,
            is_functional=is_functional,
            domain=self.domain,
            org_domains=org_domains,
        )
        log.info("%s Query: %s", prefix, query)

        # ── Phase 1: List all message IDs ─────────────────────────────────
        log.info("%s Phase 1/2: Listing message IDs (may take a while for large inboxes)...", prefix)
        message_ids = []
        page_token = None
        list_start = time.time()

        while True:
            params = {"userId": "me", "q": query, "maxResults": 500}
            if page_token:
                params["pageToken"] = page_token

            result = gmail.users().messages().list(**params).execute()
            batch = result.get("messages", [])
            message_ids.extend(batch)

            # Progress every 10,000 IDs so large inboxes don't look frozen
            if len(message_ids) % 10000 < 500 and len(message_ids) > 0:
                log.info("%s  ...listed %d message IDs so far (%.0fs)",
                         prefix, len(message_ids), time.time() - list_start)

            page_token = result.get("nextPageToken")
            if not page_token:
                break

        log.info("%s %s — %d messages found", prefix, email, len(message_ids))

        # Drop what the replica already holds — listing is cheap, fetching is not.
        if skip_local_ids:
            before = len(message_ids)
            message_ids = [m for m in message_ids if m.get("id") not in skip_local_ids]
            log.info("%s %s — %d already replicated, %d to fetch",
                     prefix, email, before - len(message_ids), len(message_ids))

        total_msgs = len(message_ids)
        if total_msgs == 0:
            return []

        # ── Phase 2: Fetch full content — pipelined batches under a quota
        # throttle (docs/PARALLEL_FETCH.md). A single in-flight batch spends
        # its life waiting on the round trip (~1.4s for 20 messages ≈ 7 msg/s);
        # `pipeline_depth` batches in flight overlap those waits, and the
        # token bucket paces total spend to ~80% of the mailbox's quota. Each
        # worker thread gets its OWN service: httplib2 connections are not
        # thread-safe, and two batches on one socket corrupt responses.
        messages = []
        fetched_count = 0
        done_batches = 0
        fetch_start = time.time()
        BATCH_SIZE = self.batch_size

        chunks = [message_ids[i:i + BATCH_SIZE] for i in range(0, len(message_ids), BATCH_SIZE)]
        depth = max(1, self.pipeline_depth)
        throttle = QuotaThrottle(rate=self.units_per_sec)
        log.info("%s Fetching %d messages in %d batches of %d (depth %d, %.0f units/s)",
                 prefix, total_msgs, len(chunks), BATCH_SIZE, depth, throttle.target)

        tl = threading.local()

        def _service():
            """Per-thread Gmail service, rebuilt every 200 of its batches.

            The rebuild is an optimization against stale connections; a blip
            must not abandon a mailbox thousands of messages in. So: keep the
            old service if we have one, retry the build once if we don't.
            """
            if getattr(tl, "svc", None) is not None and tl.uses < 200:
                tl.uses += 1
                return tl.svc
            for attempt in (1, 2):
                try:
                    tl.svc = build("gmail", "v1",
                                   credentials=self._build_credentials(email))
                    tl.uses = 1
                    return tl.svc
                except Exception as exc:  # noqa: BLE001 — refresh is optional
                    if getattr(tl, "svc", None) is not None:
                        log.warning("%s Service refresh failed (%s) — "
                                    "continuing with the existing service", prefix, exc)
                        tl.uses = 1
                        return tl.svc
                    if attempt == 2:
                        raise
                    log.warning("%s Service build failed (%s) — retrying once", prefix, exc)

        def _one(chunk: list[dict]) -> list[dict]:
            throttle.acquire(len(chunk) * UNITS_PER_GET)
            return self._fetch_batch(_service(), chunk, prefix, throttle)

        pool = ThreadPoolExecutor(max_workers=depth, thread_name_prefix="gmail-batch")
        try:
            futures = [pool.submit(_one, c) for c in chunks]
            for fut in as_completed(futures):
                batch_results = fut.result()  # a failed batch fails the mailbox
                fetched_count += len(batch_results)
                done_batches += 1
                if on_batch is not None:
                    on_batch(batch_results)
                else:
                    messages.extend(batch_results)
                elapsed = time.time() - fetch_start
                log.info("%s [%d/%d] Batches done... (%d msgs fetched, %.1fs elapsed)",
                         prefix, done_batches, len(chunks), fetched_count, elapsed)
        finally:
            # On failure: drop queued batches so the mailbox fails fast; the
            # runner leaves its cursor unchanged and the next run resumes.
            pool.shutdown(wait=True, cancel_futures=True)

        elapsed = time.time() - fetch_start
        log.info("%s %s — fetched %d/%d messages in %.1fs (%.1f msg/s)",
                 prefix, email, fetched_count, total_msgs, elapsed,
                 fetched_count / elapsed if elapsed > 0 else 0)

        return messages

    def _fetch_batch(
        self,
        gmail_service,
        msg_ids: list[dict],
        log_prefix: str = "",
        throttle: Optional[QuotaThrottle] = None,
    ) -> list[dict]:
        """
        Fetch a batch of up to 50 messages in a single HTTP request.
        Uses gmail's batch endpoint — ~10x faster than individual calls.
        Falls back to individual fetches if batch fails.

        When a throttle is provided it gets the quota feedback: any 429 in
        the batch halves the pace, a fully clean batch nudges it back up.
        """
        results: dict[str, dict] = {}
        errors: dict[str, Exception] = {}

        def _callback(request_id: str, response: dict, exception: Exception):
            if exception:
                errors[request_id] = exception
            elif response:
                results[request_id] = response

        try:
            batch = gmail_service.new_batch_http_request(callback=_callback)
            for msg_ref in msg_ids:
                msg_id = msg_ref["id"]
                batch.add(
                    gmail_service.users().messages().get(
                        userId="me", id=msg_id, format="full"
                    ),
                    request_id=msg_id,
                )
            batch.execute()
        except _NETWORK_ERRORS as e:
            log.warning("%s Batch request failed (%s: %s) — falling back to individual fetches",
                        log_prefix, type(e).__name__, e)
            # Fallback: fetch individually
            for msg_ref in msg_ids:
                msg = self._get_message_with_retry(gmail_service, msg_ref["id"], log_prefix)
                if msg:
                    results[msg_ref["id"]] = msg
            return list(results.values())
        except Exception as e:
            # A whole-batch 429 is the strongest "slow down" signal there is.
            if (throttle is not None and isinstance(e, HttpError)
                    and getattr(e.resp, "status", None) == 429):
                throttle.penalize()
            log.warning("%s Unexpected batch error (%s: %s) — falling back to individual fetches",
                        log_prefix, type(e).__name__, e)
            for msg_ref in msg_ids:
                msg = self._get_message_with_retry(gmail_service, msg_ref["id"], log_prefix)
                if msg:
                    results[msg_ref["id"]] = msg
            return list(results.values())

        # Log any per-message errors from the batch
        saw_429 = False
        if errors:
            for msg_id, err in errors.items():
                if isinstance(err, HttpError) and err.resp.status == 429:
                    if not saw_429 and throttle is not None:
                        throttle.penalize()  # once per batch: halve the pace
                    saw_429 = True
                    # Retry this one individually
                    log.warning("%s Batch 429 for message %s — retrying individually", log_prefix, msg_id)
                    msg = self._get_message_with_retry(gmail_service, msg_id, log_prefix)
                    if msg:
                        results[msg_id] = msg
                elif isinstance(err, HttpError) and err.resp.status == 404:
                    log.debug("%s Message %s not found (404), skipping", log_prefix, msg_id)
                else:
                    log.warning("%s Error in batch for message %s (%s) — skipping",
                                log_prefix, msg_id, err)

        if not saw_429 and throttle is not None:
            throttle.reward()  # clean batch: creep back toward target pace
        return list(results.values())

    # ══════════════════════════════════════════════
    # FETCH SINGLE MESSAGE WITH RETRY
    # ══════════════════════════════════════════════

    def _get_message_with_retry(
        self,
        gmail_service,
        msg_id: str,
        log_prefix: str = "",
        max_retries: Optional[int] = None,
    ) -> Optional[dict]:
        """
        Fetch a single message with retry logic for two failure modes:

        - HTTP 429 (rate limit): honors Retry-After when present, else
          exponential backoff 30s → 60s → 120s
        - Network errors (timeout, DNS failure, connection reset):
          exponential backoff 10s → 20s → 40s, then give up and let
          the caller rebuild the service
        """
        if max_retries is None:
            max_retries = self.max_retries
        rate_backoff = 30     # grows on each 429: 30, 60, 120
        net_backoff = 10      # grows on each network error: 10, 20, 40

        for attempt in range(max_retries + 1):
            try:
                if self.rate_limit_delay > 0:
                    time.sleep(self.rate_limit_delay)
                return gmail_service.users().messages().get(
                    userId="me",
                    id=msg_id,
                    format="full",
                ).execute()

            except HttpError as e:
                if e.resp.status == 429:
                    if attempt < max_retries:
                        retry_after = _retry_after_from_http_error(e)
                        wait = retry_after if retry_after is not None else rate_backoff
                        log.warning(
                            "%s RATE_LIMIT — 429 on message %s. "
                            "Backing off %.0fs (attempt %d/%d)%s",
                            log_prefix, msg_id, wait, attempt + 1, max_retries,
                            " (Retry-After)" if retry_after is not None else "",
                        )
                        time.sleep(wait)
                        rate_backoff *= 2
                    else:
                        log.error(
                            "%s RATE_LIMIT — 429 on message %s. "
                            "Max retries (%d) exhausted, skipping.",
                            log_prefix, msg_id, max_retries,
                        )
                        return None
                elif e.resp.status == 404:
                    log.debug("%s Message %s not found (404), skipping", log_prefix, msg_id)
                    return None
                else:
                    log.error(
                        "%s HTTP %d fetching message %s: %s",
                        log_prefix, e.resp.status, msg_id, e.reason,
                    )
                    return None

            except _NETWORK_ERRORS as e:
                if attempt < max_retries:
                    log.warning(
                        "%s NETWORK_ERROR on message %s (%s: %s). "
                        "Waiting %ds before retry (attempt %d/%d)...",
                        log_prefix, msg_id, type(e).__name__, e,
                        net_backoff, attempt + 1, max_retries,
                    )
                    time.sleep(net_backoff)
                    net_backoff *= 2
                else:
                    log.error(
                        "%s NETWORK_ERROR on message %s after %d retries — skipping. "
                        "Caller will rebuild service.",
                        log_prefix, msg_id, max_retries,
                    )
                    return None

            except Exception as e:
                # Unexpected error — log and skip immediately (don't retry)
                log.error("%s Unexpected error fetching message %s (%s): %s",
                          log_prefix, msg_id, type(e).__name__, e)
                return None

        return None

    # ══════════════════════════════════════════════
    # DRY RUN — count messages per user without fetching
    # ══════════════════════════════════════════════

    def dry_run_counts(
        self,
        users: list[dict],
        since: Optional[str] = None,
        org_domains: Optional[list[str]] = None,
    ) -> list[dict]:
        """
        For each user, get actual message count by paginating messages.list.
        Uses the same pre-fetch filters as the real fetch so counts are accurate.
        Only uses message IDs (no full content fetch) — 5 quota units per page.

        Note: resultSizeEstimate is unreliable (caps around 200), so we
        paginate through all message IDs to get the true count.
        """
        log.info("DRY RUN — counting messages per user (with pre-fetch filters)")

        results = []
        for i, user in enumerate(users, 1):
            email = user["email"]
            user_is_functional = user.get("is_functional", is_functional_mailbox(email))

            query = build_query(
                since=since,
                is_functional=user_is_functional,
                domain=self.domain,
                org_domains=org_domains,
            )

            try:
                creds = self._build_credentials(email)
                gmail = build("gmail", "v1", credentials=creds)

                # Paginate to get true count (not resultSizeEstimate which caps ~200)
                count = 0
                page_token = None
                while True:
                    params = {"userId": "me", "q": query, "maxResults": 500}
                    if page_token:
                        params["pageToken"] = page_token
                    result = gmail.users().messages().list(**params).execute()
                    count += len(result.get("messages", []))
                    page_token = result.get("nextPageToken")
                    if not page_token:
                        break
                    time.sleep(0.1)  # gentle rate limit between pages

                tag = "FUNC" if user_is_functional else "PERSONAL"
                log.info("[%d/%d] %s [%s] — %d messages", i, len(users), email, tag, count)
                results.append({
                    "email": email,
                    "message_count": count,
                    "is_functional": user_is_functional,
                    "query": query,
                })

            except Exception as e:
                log.error("[%d/%d] %s — failed: %s", i, len(users), email, e)
                results.append({"email": email, "message_count": -1, "error": str(e)})

            time.sleep(0.2)  # gentle rate limit between users

        total = sum(r["message_count"] for r in results if r["message_count"] > 0)
        log.info("DRY RUN TOTAL — %d messages across %d users", total, len(users))
        log.info("Estimated full fetch time: ~%.0f minutes",
                 (total * (self.rate_limit_delay + 0.05) + len(users) * self.user_cooldown) / 60)

        return results
