"""
control/observability.py
────────────────────────
Read-only introspection of the ingestion substrate, for the Loremind-side
operations dashboard.

Every layer of the substrate already records its own progress durably —
`source_payloads` knows what was fetched, `fetch_cursors` knows how far each
mailbox got, `events` knows what was normalized and filtered, the L1/L2 tables
know what was resolved, `claim_digest` knows which conversations are extracted.
Nothing here computes anything new; it reads the layers' own truth and shapes
it for display.

Deliberately read-only and side-effect free: this module never writes, never
triggers a run, and never holds a long transaction. It is safe to poll while
every stage of the pipeline is running.

Stage model — one row per layer of `docs/revised_ingestion_pipeline/00_DESIGN.md`:

    fetch      L0 source_payloads (+ per-mailbox cursors)
    normalize  L0 events
    filter     L0 events.noise_class (signal vs noise verdicts)
    group      L0 events.conversation_id (conversation assembly)
    registry   L1 entity_* tables
    edges      L2 edge_* tables
    claims     L3 claim / claim_digest  ← the LLM stage, the slow one
    dedup      L3 claim_relation
    project    Neo4j claim-substrate graph (counted at the source, in PG)
"""

from __future__ import annotations

import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import sqlalchemy as sa

from builders.l0_ledger import SCHEMA, LedgerStore, _resolve_dsn, safe_scalar
from common.logger import get_logger

log = get_logger("control.observability")

LOG_DIR = Path("./output/logs")

# Processes we expect to be alive during a full-flow run, by the pattern that
# identifies them in `ps`. Presence/absence is itself dashboard state — a
# stopped extractor with work pending is the single most useful alert here.
DAEMONS = {
    "extraction": "runners.extract_claims",
    "flow": "runners.substrate_flow",
    "fetch": "runners.ledger_sync",
    "control_api": "control.app",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


# A tenant partway through onboarding has only the layers it has actually
# run — every dashboard endpoint 500'd on that until this dashboard-specific
# copy was written; it's now the shared builders.l0_ledger.safe_scalar,
# reused by runners/substrate_flow.py's own count poll for the same reason.
_scalar = safe_scalar


def _tenant_dsn(org: str) -> str:
    """The ledger DSN for `org` — from Vault when the control plane is
    tenant-scoped, else the box's own DATABASE_URL (local dev).

    Without this every dashboard read hit `_resolve_dsn()`, which reads
    DATABASE_URL from the environment — deliberately unset in production since
    credentials moved to Vault per tenant. So every endpoint answered "could not
    connect to the ledger database" while the pipeline itself was running fine.
    """
    from common.config import get_settings
    if not get_settings().vault_proxy_url:
        return _resolve_dsn()
    from common.vault_client import resolve_postgres_dsn
    return resolve_postgres_dsn(org)


def _store(org: str) -> LedgerStore:
    store = LedgerStore(_tenant_dsn(org))
    if not store.connect():
        raise RuntimeError("observability: could not connect to the ledger database")
    return store


# ── Daemons ─────────────────────────────────────────────────────────────────

def daemon_status() -> list[dict]:
    """Which pipeline processes are alive right now. `pgrep -f` against the
    module path each daemon is launched with."""
    out = []
    for name, pattern in DAEMONS.items():
        try:
            res = subprocess.run(["pgrep", "-f", pattern],
                                 capture_output=True, text=True, timeout=5)
            pids = [p for p in res.stdout.split() if p.strip()]
        except (subprocess.SubprocessError, OSError):
            pids = []
        out.append({"name": name, "pattern": pattern,
                    "running": bool(pids), "pids": pids})
    return out


# ── Stages ──────────────────────────────────────────────────────────────────

def stage_snapshot(org: str, source: str = "gmail") -> list[dict]:
    """One row per pipeline stage: how much is done, how much is pending, and
    whether it currently has anything to do.

    `pending` is computed from the layer below — that's what makes it a
    pipeline view rather than a pile of counters. A stage with done>0 and
    pending==0 is caught up; pending>0 with no daemon running is a stall.
    """
    store = _store(org)
    try:
        with store._session() as ses:
            payloads = _scalar(ses, f"SELECT count(*) FROM {SCHEMA}.source_payloads "
                                    "WHERE org=:o AND source=:s", o=org, s=source)
            events = _scalar(ses, f"SELECT count(*) FROM {SCHEMA}.events "
                                  "WHERE org=:o AND source=:s", o=org, s=source)
            classified = _scalar(ses, f"SELECT count(*) FROM {SCHEMA}.events "
                                      "WHERE org=:o AND source=:s AND noise_class IS NOT NULL",
                                 o=org, s=source)
            signal = _scalar(ses, f"SELECT count(*) FROM {SCHEMA}.events "
                                  "WHERE org=:o AND source=:s AND noise_class='signal'",
                             o=org, s=source)
            noise = classified - signal
            grouped = _scalar(ses, f"SELECT count(*) FROM {SCHEMA}.events "
                                   "WHERE org=:o AND source=:s AND conversation_id IS NOT NULL",
                              o=org, s=source)
            conversations = _scalar(ses, f"SELECT count(DISTINCT conversation_id) FROM {SCHEMA}.events "
                                         "WHERE org=:o AND source=:s AND conversation_id IS NOT NULL",
                                    o=org, s=source)

            persons = _scalar(ses, f"SELECT count(*) FROM {SCHEMA}.entity_person WHERE org=:o", o=org)
            orgs_n = _scalar(ses, f"SELECT count(*) FROM {SCHEMA}.entity_organization WHERE org=:o", o=org)
            artifacts = _scalar(ses, f"SELECT count(*) FROM {SCHEMA}.entity_artifact WHERE org=:o", o=org)

            authored = _scalar(ses, f"SELECT count(*) FROM {SCHEMA}.edge_authored WHERE org=:o", o=org)
            participated = _scalar(ses, f"SELECT count(*) FROM {SCHEMA}.edge_participated_in WHERE org=:o", o=org)
            comm = _scalar(ses, f"SELECT count(*) FROM {SCHEMA}.edge_communicates_with WHERE org=:o", o=org)

            digests = _scalar(ses, f"SELECT count(*) FROM {SCHEMA}.claim_digest WHERE org=:o", o=org)
            claims = _scalar(ses, f"SELECT count(*) FROM {SCHEMA}.claim WHERE org=:o", o=org)
            grounded = _scalar(ses, f"SELECT count(*) FROM {SCHEMA}.claim "
                                    "WHERE org=:o AND evidence_grounded", o=org)
            relations = _scalar(ses, f"SELECT count(*) FROM {SCHEMA}.claim_relation WHERE org=:o", o=org)

            # Extraction backlog uses the same predicate the runner does, so
            # the dashboard number and the runner's number can't disagree.
            claims_pending = _scalar(ses, f"""
                SELECT count(*) FROM (
                  SELECT e.conversation_id FROM {SCHEMA}.events e
                  LEFT JOIN {SCHEMA}.claim_digest d ON d.conversation_id = e.conversation_id
                  WHERE e.org=:o AND e.noise_class='signal'
                    AND e.conversation_id IS NOT NULL AND d.conversation_id IS NULL
                  GROUP BY e.conversation_id HAVING count(*) >= 2) x""", o=org)
            claims_single_pending = _scalar(ses, f"""
                SELECT count(*) FROM (
                  SELECT e.conversation_id FROM {SCHEMA}.events e
                  LEFT JOIN {SCHEMA}.claim_digest d ON d.conversation_id = e.conversation_id
                  WHERE e.org=:o AND e.noise_class='signal'
                    AND e.conversation_id IS NOT NULL AND d.conversation_id IS NULL
                  GROUP BY e.conversation_id HAVING count(*) = 1) x""", o=org)
    finally:
        store.disconnect()

    return [
        {"key": "fetch", "label": "Fetch", "layer": "L0",
         "done": payloads, "pending": None, "unit": "messages",
         "detail": {"unique_messages": payloads}},
        {"key": "normalize", "label": "Normalize", "layer": "L0",
         "done": events, "pending": max(0, payloads - events), "unit": "events",
         "detail": {"events": events}},
        {"key": "filter", "label": "Filter", "layer": "L0",
         "done": classified, "pending": max(0, events - classified), "unit": "events",
         "detail": {"signal": signal, "noise": noise,
                    "signal_pct": round(100 * signal / max(classified, 1), 1)}},
        {"key": "group", "label": "Conversations", "layer": "L0",
         "done": grouped, "pending": max(0, events - grouped), "unit": "events",
         "detail": {"conversations": conversations}},
        {"key": "registry", "label": "Registries", "layer": "L1",
         "done": persons + orgs_n + artifacts, "pending": None, "unit": "entities",
         "detail": {"persons": persons, "organizations": orgs_n, "artifacts": artifacts}},
        {"key": "edges", "label": "Interaction graph", "layer": "L2",
         "done": authored + participated + comm, "pending": None, "unit": "edges",
         "detail": {"authored": authored, "participated_in": participated,
                    "communicates_with": comm}},
        {"key": "claims", "label": "Claim extraction", "layer": "L3",
         "done": digests, "pending": claims_pending, "unit": "conversations",
         "detail": {"claims": claims, "grounded": grounded,
                    "grounded_pct": round(100 * grounded / max(claims, 1), 1),
                    "single_message_deferred": claims_single_pending}},
        {"key": "dedup", "label": "Dedup", "layer": "L3",
         "done": relations, "pending": None, "unit": "relations",
         "detail": {"corroborates": relations}},
    ]


def claim_kinds(org: str) -> dict[str, int]:
    store = _store(org)
    try:
        with store._session() as ses:
            return dict(ses.execute(sa.text(
                f"SELECT kind, count(*) FROM {SCHEMA}.claim WHERE org=:o "
                "GROUP BY 1 ORDER BY 2 DESC"), {"o": org}).all())
    finally:
        store.disconnect()


def throughput(org: str, minutes: int = 5) -> dict:
    """Extraction rate measured from the digests' own timestamps — immune to
    log staleness, which is why the dashboard trusts it over any log line."""
    store = _store(org)
    try:
        with store._session() as ses:
            recent = _scalar(ses, f"SELECT count(*) FROM {SCHEMA}.claim_digest "
                                  f"WHERE org=:o AND created_at > now() - interval '{minutes} minutes'",
                             o=org)
            claims_recent = _scalar(ses, f"SELECT count(*) FROM {SCHEMA}.claim "
                                         f"WHERE org=:o AND created_at > now() - interval '{minutes} minutes'",
                                    o=org)
            pending = _scalar(ses, f"""
                SELECT count(*) FROM (
                  SELECT e.conversation_id FROM {SCHEMA}.events e
                  LEFT JOIN {SCHEMA}.claim_digest d ON d.conversation_id = e.conversation_id
                  WHERE e.org=:o AND e.noise_class='signal'
                    AND e.conversation_id IS NOT NULL AND d.conversation_id IS NULL
                  GROUP BY e.conversation_id HAVING count(*) >= 2) x""", o=org)
    finally:
        store.disconnect()

    per_min = recent / minutes
    return {
        "window_minutes": minutes,
        "conversations_per_min": round(per_min, 1),
        "claims_per_min": round(claims_recent / minutes, 1),
        "pending": pending,
        "eta_seconds": int(pending / per_min * 60) if per_min > 0 else None,
    }


def mailboxes(org: str, source: str = "gmail") -> list[dict]:
    """Per-mailbox fetch state: how far each cursor got and how many messages
    that mailbox has actually contributed. `sightings` is a JSONB map of
    mailbox → seen, so one message cc'd to five people counts once as a row
    and five times as a sighting."""
    store = _store(org)
    try:
        with store._session() as ses:
            cursors = ses.execute(sa.text(
                f"SELECT principal, last_internal_date_ms, updated_at "
                f"FROM {SCHEMA}.fetch_cursors WHERE org=:o AND source=:s "
                "ORDER BY principal"), {"o": org, "s": source}).all()
            counts = dict(ses.execute(sa.text(
                f"SELECT key, count(*) FROM {SCHEMA}.source_payloads, "
                "jsonb_object_keys(sightings) AS key "
                "WHERE org=:o AND source=:s GROUP BY key"), {"o": org, "s": source}).all())
    finally:
        store.disconnect()

    return [{
        "principal": c.principal,
        "messages": counts.get(c.principal, 0),
        "last_message_at": (datetime.fromtimestamp(c.last_internal_date_ms / 1000, timezone.utc).isoformat()
                            if c.last_internal_date_ms else None),
        "cursor_updated_at": c.updated_at.isoformat() if c.updated_at else None,
    } for c in cursors]


def recent_activity(org: str, limit: int = 25) -> list[dict]:
    """The live feed: what the extractor just produced, newest first. This is
    the 'watch mail turn into knowledge' view."""
    store = _store(org)
    try:
        with store._session() as ses:
            rows = ses.execute(sa.text(f"""
                SELECT d.conversation_id, d.digest, d.claim_count, d.created_at,
                       d.sensitivity, d.department_tags,
                       a.title, a.message_count
                FROM {SCHEMA}.claim_digest d
                LEFT JOIN {SCHEMA}.entity_artifact a ON a.artifact_id = d.conversation_id
                WHERE d.org=:o ORDER BY d.created_at DESC LIMIT :k
            """), {"o": org, "k": limit}).all()
    finally:
        store.disconnect()
    return [{
        "conversation_id": r.conversation_id,
        "title": r.title or "",
        "digest": r.digest or "",
        "claim_count": r.claim_count,
        "message_count": r.message_count or 0,
        "sensitivity": r.sensitivity,
        "department_tags": r.department_tags or [],
        "created_at": r.created_at.isoformat() if r.created_at else None,
    } for r in rows]


def recent_claims(org: str, limit: int = 25, kind: str | None = None) -> list[dict]:
    store = _store(org)
    try:
        with store._session() as ses:
            sql = (f"SELECT claim_id, kind, statement, evidence_grounded, confidence, "
                   f"occurred_at, conversation_id, created_at FROM {SCHEMA}.claim WHERE org=:o")
            params = {"o": org, "k": limit}
            if kind:
                sql += " AND kind=:kind"
                params["kind"] = kind
            sql += " ORDER BY created_at DESC LIMIT :k"
            rows = ses.execute(sa.text(sql), params).all()
    finally:
        store.disconnect()
    return [{
        "claim_id": r.claim_id, "kind": r.kind, "statement": r.statement,
        "grounded": r.evidence_grounded, "confidence": r.confidence,
        "occurred_at": r.occurred_at, "conversation_id": r.conversation_id,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    } for r in rows]


# ── Logs & errors ───────────────────────────────────────────────────────────

ANSI = re.compile(r"\x1b\[[0-9;]*m")
# "[2026-07-23 14:50:56] [INFO ] [runners.extract_claims] message"
LOG_LINE = re.compile(r"^\[([\d\-: ]+)\]\s*\[(\w+)\s*\]\s*\[([\w.]+)\]\s*(.*)$")


def _read_tail(path: Path, lines: int, timeout: float = 10.0) -> list[str]:
    """Last N lines without loading the whole file — logs reach hundreds of MB
    during a long run."""
    if not path.exists():
        return []
    try:
        res = subprocess.run(["tail", "-n", str(lines), str(path)],
                             capture_output=True, text=True, timeout=timeout)
        return [ANSI.sub("", ln) for ln in res.stdout.splitlines()]
    except (subprocess.SubprocessError, OSError) as exc:
        log.warning("log tail failed for %s: %s", path, exc)
        return []


def list_logs() -> list[dict]:
    if not LOG_DIR.exists():
        return []
    out = []
    for p in sorted(LOG_DIR.glob("*.log"), key=lambda x: x.stat().st_mtime, reverse=True):
        st = p.stat()
        out.append({"name": p.name, "size_bytes": st.st_size,
                    "modified_at": datetime.fromtimestamp(st.st_mtime, timezone.utc).isoformat()})
    return out


def read_log(name: str, lines: int = 200) -> dict:
    """Tail one log by NAME ONLY — never a caller-supplied path. `Path(name).name`
    strips any traversal, and the file must resolve inside LOG_DIR."""
    safe = Path(name).name
    path = (LOG_DIR / safe).resolve()
    if not str(path).startswith(str(LOG_DIR.resolve())):
        raise ValueError("log path escapes the log directory")
    raw = _read_tail(path, lines)
    parsed = []
    for ln in raw:
        m = LOG_LINE.match(ln)
        if m:
            ts, level, logger_name, msg = m.groups()
            parsed.append({"ts": ts, "level": level.strip(), "logger": logger_name, "message": msg})
        else:
            parsed.append({"ts": None, "level": "RAW", "logger": "", "message": ln})
    return {"name": safe, "lines": parsed}


# Levels that ARE an operational signal. The parsed level is the primary
# filter — grepping message text for "429" matched hex claim ids and Neo4j
# batch payloads, which buried the real failures under DEBUG noise.
ALERT_LEVELS = {"ERROR", "CRITICAL", "WARNING"}

# Unparseable lines (tracebacks, driver-script echoes) still matter, so they
# get a narrow text check — anchored phrases only, no bare numbers.
RAW_ALERT_PAT = re.compile(
    r"Traceback \(most recent call last\)|"
    r"\bThrottlingException\b|\bTooManyRequests\b|"
    r"HTTP/1\.1 (?:429|5\d\d)\b|"
    r"could not connect|connection refused|refusing all requests|"
    r"^\s*\w*(?:Error|Exception):",
    re.IGNORECASE,
)

# Logs untouched for this long are history, not operations. Skipping them keeps
# the scan bounded no matter how many old runs are on disk.
ERROR_SCAN_MAX_AGE_S = 24 * 3600
ERROR_SCAN_MAX_LOGS = 12


def recent_errors(limit: int = 60, scan_lines: int = 2000) -> list[dict]:
    """Errors and warnings across recent pipeline logs, newest first.

    Scans the tail of each recent log rather than whole files: an error buried
    thousands of lines back is history, and reading full logs would make this
    endpoint unusable during a heavy run (a stale unified_builder log is large
    enough to time `tail` out on its own).
    """
    out = []
    cutoff = _now().timestamp() - ERROR_SCAN_MAX_AGE_S
    for meta in list_logs()[:ERROR_SCAN_MAX_LOGS]:
        path = LOG_DIR / meta["name"]
        try:
            if path.stat().st_mtime < cutoff:
                continue
        except OSError:
            continue
        for ln in _read_tail(path, scan_lines, timeout=5.0):
            m = LOG_LINE.match(ln)
            if m:
                ts, level, logger_name, msg = m.groups()
                level = level.strip().upper()
                if level not in ALERT_LEVELS:
                    continue
            else:
                if not RAW_ALERT_PAT.search(ln):
                    continue
                ts, level, logger_name, msg = None, "ERROR", "", ln
            out.append({"log": meta["name"], "ts": ts, "level": level,
                        "logger": logger_name, "message": msg.strip()[:500]})
    out.sort(key=lambda r: (r["ts"] or ""), reverse=True)
    return out[:limit]


# ── Whole-dashboard snapshot ────────────────────────────────────────────────

def overview(org: str, source: str = "gmail") -> dict:
    """Everything the dashboard's landing view needs, in one round trip."""
    stages = stage_snapshot(org, source)
    by_key = {s["key"]: s for s in stages}
    claims_stage = by_key["claims"]
    total_convs = claims_stage["done"] + (claims_stage["pending"] or 0)
    return {
        "org": org,
        "source": source,
        "generated_at": _now().isoformat(),
        "stages": stages,
        "throughput": throughput(org),
        "kinds": claim_kinds(org),
        "daemons": daemon_status(),
        "progress_pct": round(100 * claims_stage["done"] / max(total_convs, 1), 1),
    }
