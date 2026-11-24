"""
builders/l0_ledger.py
─────────────────────
L0 EVENT LEDGER — build step 1 of the revised ingestion pipeline
(`docs/revised_ingestion_pipeline/00_DESIGN.md` §3, §11).

The append-only Postgres system of record. Promotes the per-source filtered
KnowledgeEvent JSONL into `lore.events` — a star-schema fact table so dashboards
are plain SQL, and the permanent substrate every later layer (claims,
projections) is a diff against. Idempotent on the deterministic event id, so
re-runs and watermark-overlap re-fetches are free no-ops.

Reuses chronograph's Postgres patterns verbatim (self-init-on-connect,
pg_insert on_conflict idiom, DeclarativeBase+SCHEMA models) — same client-VPC
Postgres instance, new `lore` schema alongside `chronograph`.

Erasure (A10): crypto-shred columns + `lore.erasure_keys` ship NOW (they are the
irreversible commitment — you cannot retrofit encryption onto events already in
backups). The pgcrypto encrypt/decrypt of body/raw lights up when client-VPC key
material exists; until then the loader writes plaintext with dek_id NULL.

Usage (from lore-ingestion/):
    ../.venv/bin/python -m builders.l0_ledger --org ezobooks [--source gmail] [--input path.jsonl] [--dry-run]
"""

from __future__ import annotations

import argparse
import json
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Generator, Iterator

import sqlalchemy as sa
from psycopg2 import errors
from sqlalchemy import create_engine, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, insert as pg_insert
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from common.config import get_settings
from common.logger import get_logger

log = get_logger("builders.l0_ledger")

SCHEMA = "lore"


class Base(DeclarativeBase):
    pass


class Event(Base):
    __tablename__ = "events"
    __table_args__ = (
        sa.Index("events_org_source_time_idx", "org", "source",
                 sa.text("occurred_at DESC NULLS LAST")),
        sa.Index("events_org_actor_idx", "org", "primary_actor"),
        sa.Index("events_org_type_idx", "org", "event_type"),
        {"schema": SCHEMA},
    )

    id: Mapped[str] = mapped_column(sa.Text, primary_key=True)   # make_id(source, source_id)
    org: Mapped[str] = mapped_column(sa.Text, nullable=False)
    source: Mapped[str] = mapped_column(sa.Text, nullable=False)
    event_type: Mapped[str] = mapped_column(sa.Text, nullable=False)
    source_id: Mapped[str] = mapped_column(sa.Text, nullable=False, default="")

    occurred_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    ingested_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now())

    title: Mapped[str] = mapped_column(sa.Text, nullable=False, default="")
    body: Mapped[str] = mapped_column(sa.Text, nullable=False, default="")
    summary: Mapped[str] = mapped_column(sa.Text, nullable=False, default="")
    raw: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    actors: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    primary_actor: Mapped[str | None] = mapped_column(sa.Text)

    # Which version of the pure payload→event projection produced this row.
    # A normalizer improvement re-runs as an UPDATE, not a re-fetch (§3.2).
    normalizer_version: Mapped[str | None] = mapped_column(sa.Text)

    # Noise verdict as a column, not presence/absence of the row (kills F8):
    # 'signal' = keep, any other value = the reason it's noise. Downstream reads
    # WHERE noise_class = 'signal'. Reclassifying is an UPDATE — an opinion never
    # deletes an event.
    noise_class: Mapped[str | None] = mapped_column(sa.Text)
    filter_version: Mapped[str | None] = mapped_column(sa.Text)

    # Conversation this message belongs to — derived by connected components over
    # reply-chain headers + cross-mailbox sightings (§3.3). Correct across
    # mailboxes and across time; replaces the per-mailbox threadId grouping that
    # scattered 2.6% of multi-message conversations.
    conversation_id: Mapped[str | None] = mapped_column(sa.Text)

    url: Mapped[str | None] = mapped_column(sa.Text)
    project: Mapped[str] = mapped_column(sa.Text, nullable=False, default="")
    tags: Mapped[list] = mapped_column(ARRAY(sa.Text), nullable=False, default=list)
    parent_id: Mapped[str | None] = mapped_column(sa.Text)
    related_ids: Mapped[list] = mapped_column(ARRAY(sa.Text), nullable=False, default=list)

    # ACL — fail-closed (D-INGEST-001): empty array = invisible, NEVER NULL/public.
    scope_id: Mapped[str | None] = mapped_column(sa.Text)
    scope_ids: Mapped[list] = mapped_column(ARRAY(sa.Text), nullable=False, default=list)
    scope_all_ids: Mapped[list] = mapped_column(ARRAY(sa.Text), nullable=False, default=list)

    # Erasure (A10 crypto-shred) — hooks ship now, encryption lights up later.
    dek_id: Mapped[str | None] = mapped_column(sa.Text)
    erased_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    erasure_reason: Mapped[str | None] = mapped_column(sa.Text)


class ErasureKey(Base):
    __tablename__ = "erasure_keys"
    __table_args__ = (
        sa.UniqueConstraint("org", "principal_id", name="erasure_keys_org_principal"),
        {"schema": SCHEMA},
    )

    dek_id: Mapped[str] = mapped_column(sa.Text, primary_key=True)
    org: Mapped[str] = mapped_column(sa.Text, nullable=False)
    principal_id: Mapped[str] = mapped_column(sa.Text, nullable=False)  # e.g. person:{email}
    key_wrapped: Mapped[bytes | None] = mapped_column(sa.LargeBinary)   # NULL on erasure
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now())
    destroyed_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))


# ── L0.payload — the source replica (05_SYNC_AND_LEDGER §3.1) ────────────────

class SourcePayload(Base):
    """One row per source object fetched — the canonical raw, permanent.

    payload_id is deterministic from (source, org, canonical_id), so the PK
    conflict IS the cross-mailbox dedup — no seen-ids checkpoint file (kills F9).
    `sightings` is keyed by mailbox: an internal mail to 20 colleagues is stored
    once + 20 sighting entries, and re-fetching a mailbox overwrites its own key
    instead of appending a duplicate. Each entry is the per-mailbox threadId,
    which is exactly what stitches conversations across mailboxes (fixes F7.3).
    """
    __tablename__ = "source_payloads"
    __table_args__ = (
        sa.UniqueConstraint("org", "source", "canonical_id",
                            name="source_payloads_natural_key"),
        sa.Index("source_payloads_org_source_idx", "org", "source"),
        {"schema": SCHEMA},
    )

    payload_id: Mapped[str] = mapped_column(sa.Text, primary_key=True)
    org: Mapped[str] = mapped_column(sa.Text, nullable=False)
    source: Mapped[str] = mapped_column(sa.Text, nullable=False)
    canonical_id: Mapped[str] = mapped_column(sa.Text, nullable=False)  # RFC Message-ID etc.

    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    sightings: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    fetched_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now())
    fetch_run_id: Mapped[str | None] = mapped_column(sa.Text)

    # Erasure hooks mirror Event — canonical raw is the most sensitive artifact.
    dek_id: Mapped[str | None] = mapped_column(sa.Text)
    erased_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))


class FetchCursor(Base):
    """Per-(org, source, principal) replication cursor (05_SYNC_AND_LEDGER §3.4).

    Advances transactionally with the payload insert, so fetch progress is
    independent of any downstream derivation (kills F11: a bad extraction day no
    longer stalls fetching).
    """
    __tablename__ = "fetch_cursors"
    __table_args__ = ({"schema": SCHEMA},)

    org: Mapped[str] = mapped_column(sa.Text, primary_key=True)
    source: Mapped[str] = mapped_column(sa.Text, primary_key=True)
    principal: Mapped[str] = mapped_column(sa.Text, primary_key=True)  # mailbox email
    last_internal_date_ms: Mapped[int | None] = mapped_column(sa.BigInteger)
    delta_token: Mapped[str | None] = mapped_column(sa.Text)
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now())


# DDL that create_all can't express or that must ALTER an existing table.
# create_all only creates missing tables — it never adds a column to a table
# that already exists, so new columns ship as idempotent ADD COLUMN IF NOT EXISTS.
_EXTRA_DDL = f"""
CREATE INDEX IF NOT EXISTS events_scope_gin
    ON {SCHEMA}.events USING gin(scope_ids);
ALTER TABLE {SCHEMA}.events
    ADD COLUMN IF NOT EXISTS normalizer_version text;
ALTER TABLE {SCHEMA}.events
    ADD COLUMN IF NOT EXISTS noise_class text;
ALTER TABLE {SCHEMA}.events
    ADD COLUMN IF NOT EXISTS filter_version text;
CREATE INDEX IF NOT EXISTS events_org_noise_idx
    ON {SCHEMA}.events (org, source, noise_class);
ALTER TABLE {SCHEMA}.events
    ADD COLUMN IF NOT EXISTS conversation_id text;
CREATE INDEX IF NOT EXISTS events_conversation_idx
    ON {SCHEMA}.events (org, source, conversation_id);
CREATE INDEX IF NOT EXISTS payloads_sightings_gin
    ON {SCHEMA}.source_payloads USING gin(sightings);
"""


def _resolve_dsn() -> str:
    import os
    s = get_settings()
    dsn = (os.environ.get("DATABASE_URL", "")
           or getattr(s, "database_url", "")
           or getattr(s, "postgres_uri", ""))
    if not dsn:
        raise ValueError("L0 ledger requires PostgreSQL: set DATABASE_URL or POSTGRES_URI.")
    return dsn


def safe_scalar(session: Session, sql: str, **params) -> int:
    """A COUNT(*)-style query that treats a table that doesn't exist yet as zero.

    Later layers (L1 entities, L2 edges, L3 claims) each create their own
    tables lazily on their own first run — `lore.claim`, for instance, is
    created by `runners/extract_claims.py`, not by this module. A tenant
    that has only ever run the L0 fetch has `events`/`source_payloads` but
    no `claim` table at all, and every caller that polls "how much has this
    tenant produced so far" (the substrate_flow daemon, the observability
    dashboard) needs zero for that layer, not a crash — that poll matters
    most exactly while a tenant is mid-onboarding and most layers are still
    missing.

    Postgres aborts the whole transaction on a failed statement, so the
    rollback here is what lets the caller keep querying OTHER tables in the
    same session after one turns out to be missing.
    """
    try:
        return session.execute(sa.text(sql), params).scalar() or 0
    except sa.exc.ProgrammingError as exc:
        if not isinstance(exc.orig, errors.UndefinedTable):
            raise
        session.rollback()
        return 0


class LedgerStore:
    """Append-only L0 event ledger over Postgres."""

    def __init__(self, dsn: str):
        self.dsn = dsn
        self._engine = None
        self._Session = None

    def connect(self) -> bool:
        try:
            import psycopg2 as _pg2
            _boot = _pg2.connect(self.dsn)
            _boot.autocommit = True
            with _boot.cursor() as cur:
                cur.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")
            _boot.close()

            self._engine = create_engine(self.dsn, pool_pre_ping=True, echo=False)
            self._Session = sessionmaker(bind=self._engine, expire_on_commit=False)
            Base.metadata.create_all(self._engine)
            with self._engine.begin() as conn:
                for stmt in _EXTRA_DDL.strip().split(";"):
                    if stmt.strip():
                        conn.execute(text(stmt))
            log.info("[l0] Connected to %s", self.dsn.split("@")[-1])
            return True
        except Exception as exc:
            log.error("[l0] Connection failed: %s", exc)
            self._engine = self._Session = None
            return False

    def disconnect(self) -> None:
        if self._engine:
            self._engine.dispose()
            self._engine = self._Session = None

    @contextmanager
    def _session(self) -> Generator[Session, None, None]:
        session: Session = self._Session()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def upsert_events(self, rows: list[dict]) -> int:
        """Append-only insert; DO NOTHING on id conflict. Returns inserted count."""
        if not rows:
            return 0
        # Dedup within batch (a repeated id in one INSERT is a cardinality error).
        seen: set[str] = set()
        deduped = []
        for r in rows:
            if r["id"] in seen:
                continue
            seen.add(r["id"])
            deduped.append(r)
        stmt = pg_insert(Event).values(deduped).on_conflict_do_nothing(index_elements=["id"])
        with self._session() as s:
            return s.execute(stmt).rowcount

    # ── L0.payload replica (05_SYNC_AND_LEDGER) ─────────────────────────────

    def upsert_payloads(self, rows: list[dict], session: Session | None = None) -> dict:
        """Insert canonical payloads; on conflict merge sightings by mailbox.

        `sightings` is a mailbox-keyed object, so the merge `existing || incoming`
        is idempotent: a new mailbox adds a key, a re-fetched mailbox overwrites
        its own (labels may refresh) — never a duplicate. The payload body itself
        is first-writer-wins (same Message-ID = same content across mailboxes).
        Returns {inserted, merged}. Pass `session` to run inside a caller's
        transaction (e.g. atomically with the cursor advance).
        """
        if not rows:
            return {"inserted": 0, "merged": 0}
        # Collapse within-batch duplicates (same payload_id from two mailboxes in
        # one flush), merging their sightings so none is lost before the INSERT.
        by_id: dict[str, dict] = {}
        for r in rows:
            cur = by_id.get(r["payload_id"])
            if cur:
                cur["sightings"] = {**cur.get("sightings", {}), **r.get("sightings", {})}
            else:
                by_id[r["payload_id"]] = dict(r)
        # Sorted so concurrent workers lock overlapping rows in the same order
        # — two mailboxes upserting a shared thread in opposite orders is a
        # textbook deadlock, and parallel fetch makes that overlap routine.
        ins = pg_insert(SourcePayload).values(
            sorted(by_id.values(), key=lambda r: r["payload_id"]))
        stmt = ins.on_conflict_do_update(
            index_elements=["payload_id"],
            set_={"sightings": SourcePayload.sightings.op("||")(ins.excluded.sightings)},
        ).returning(sa.literal_column("(xmax = 0)").label("inserted"))

        def _run(s: Session) -> dict:
            res = s.execute(stmt).all()
            inserted = sum(1 for row in res if row.inserted)
            return {"inserted": inserted, "merged": len(res) - inserted}

        if session is not None:
            return _run(session)
        with self._session() as s:
            return _run(s)

    def set_fetch_cursor(self, org: str, source: str, principal: str,
                         last_internal_date_ms: int | None = None,
                         delta_token: str | None = None,
                         session: Session | None = None) -> None:
        """Upsert a replication cursor. Only advances forward (never rewinds a
        watermark on an overlapping re-fetch)."""
        vals = {"org": org, "source": source, "principal": principal,
                "last_internal_date_ms": last_internal_date_ms,
                "delta_token": delta_token}
        ins = pg_insert(FetchCursor).values(vals)
        set_ = {"updated_at": sa.func.now()}
        if last_internal_date_ms is not None:
            set_["last_internal_date_ms"] = sa.func.greatest(
                sa.func.coalesce(FetchCursor.last_internal_date_ms, 0),
                ins.excluded.last_internal_date_ms)
        if delta_token is not None:
            set_["delta_token"] = ins.excluded.delta_token
        stmt = ins.on_conflict_do_update(
            index_elements=["org", "source", "principal"], set_=set_)
        if session is not None:
            session.execute(stmt)
        else:
            with self._session() as s:
                s.execute(stmt)

    def get_fetch_cursor(self, org: str, source: str, principal: str) -> int | None:
        with self._session() as s:
            return s.execute(
                sa.select(FetchCursor.last_internal_date_ms).where(
                    FetchCursor.org == org, FetchCursor.source == source,
                    FetchCursor.principal == principal)
            ).scalar()

    def known_local_ids(self, org: str, source: str, mailbox: str) -> set[str]:
        """The source's own message ids already replicated for one mailbox.

        Lets a resumed fetch skip what it already has: listing ids is a cheap
        API call, fetching bodies is hours. Reads the per-mailbox sighting the
        fetch wrote, so it needs no separate checkpoint file to drift out of
        sync with the replica.
        """
        col = SourcePayload.sightings[mailbox]["source_local_id"].astext
        with self._session() as s:
            rows = s.execute(
                sa.select(col).where(
                    SourcePayload.org == org, SourcePayload.source == source,
                    SourcePayload.sightings.has_key(mailbox))  # noqa: W601 — JSONB ?
            ).scalars().all()
        return {r for r in rows if r}

    def sync_user_payloads(self, rows: list[dict], org: str, source: str,
                           principal: str, cursor_ms: int | None) -> dict:
        """Upsert one mailbox's payloads AND advance its cursor in one
        transaction — the replica's atomic unit (§3.4)."""
        with self._session() as s:
            stats = self.upsert_payloads(rows, session=s)
            self.set_fetch_cursor(org, source, principal,
                                  last_internal_date_ms=cursor_ms, session=s)
        return stats

    def count_payloads(self, org: str, source: str | None = None) -> int:
        q = sa.select(sa.func.count()).select_from(SourcePayload).where(
            SourcePayload.org == org)
        if source:
            q = q.where(SourcePayload.source == source)
        with self._session() as s:
            return s.execute(q).scalar() or 0

    def iter_payloads(self, org: str, source: str,
                      batch: int = 500) -> Iterator[list["SourcePayload"]]:
        """Yield source_payloads in id-ordered batches for re-projection."""
        offset = 0
        while True:
            with self._session() as s:
                rows = s.execute(
                    sa.select(SourcePayload)
                    .where(SourcePayload.org == org, SourcePayload.source == source)
                    .order_by(SourcePayload.payload_id)
                    .offset(offset).limit(batch)
                ).scalars().all()
                # detach so callers can read attributes after the session closes
                for r in rows:
                    s.expunge(r)
            if not rows:
                return
            yield rows
            offset += len(rows)

    def iter_events(self, org: str, source: str,
                    batch: int = 1000) -> Iterator[list["Event"]]:
        """Yield events in id-ordered batches (for column workers like noise-class)."""
        offset = 0
        while True:
            with self._session() as s:
                rows = s.execute(
                    sa.select(Event)
                    .where(Event.org == org, Event.source == source)
                    .order_by(Event.id).offset(offset).limit(batch)
                ).scalars().all()
                for r in rows:
                    s.expunge(r)
            if not rows:
                return
            yield rows
            offset += len(rows)

    def update_noise_class(self, rows: list[dict]) -> int:
        """Set noise_class + filter_version by id. rows: [{id, noise_class,
        filter_version}]. An opinion updates a column, never deletes a row."""
        if not rows:
            return 0
        # Core table update (not the ORM entity): a plain UPDATE ... WHERE with
        # executemany params, not an ORM bulk-update-by-PK.
        t = Event.__table__
        stmt = (sa.update(t).where(t.c.id == sa.bindparam("b_id"))
                .values(noise_class=sa.bindparam("v_noise"),
                        filter_version=sa.bindparam("v_filter")))
        params = [{"b_id": r["id"], "v_noise": r["noise_class"],
                   "v_filter": r["filter_version"]} for r in rows]
        with self._session() as s:
            s.execute(stmt, params)
        return len(params)

    def update_conversation_id(self, rows: list[dict]) -> int:
        """Set conversation_id by event id. rows: [{id, conversation_id}].
        Events not yet projected are simply not matched (grouped next run)."""
        if not rows:
            return 0
        t = Event.__table__
        stmt = (sa.update(t).where(t.c.id == sa.bindparam("b_id"))
                .values(conversation_id=sa.bindparam("v_conv")))
        params = [{"b_id": r["id"], "v_conv": r["conversation_id"]} for r in rows]
        with self._session() as s:
            s.execute(stmt, params)
        return len(params)

    def count_events_with_conversation_id(self, org: str, source: str) -> int:
        """Ground-truth count — unlike update_conversation_id's return value
        (len(params), an attempted count), this is what actually landed."""
        with self._session() as s:
            return s.execute(
                sa.select(sa.func.count()).select_from(Event.__table__)
                .where(Event.org == org, Event.source == source,
                       Event.conversation_id.isnot(None))
            ).scalar() or 0

    def count_events_by_noise(self, org: str, source: str) -> dict:
        with self._session() as s:
            res = s.execute(
                sa.select(Event.noise_class, sa.func.count())
                .where(Event.org == org, Event.source == source)
                .group_by(Event.noise_class)).all()
        return {(k or "<unclassified>"): n for k, n in res}

    def upsert_events_projection(self, rows: list[dict]) -> int:
        """Upsert projected events — on id conflict, OVERWRITE the derived columns.

        Unlike upsert_events (append-only, DO NOTHING), a projection re-run with a
        new normalizer_version must refresh existing rows. ACL + identity columns
        are re-derived too, so a scope fix propagates on re-projection."""
        if not rows:
            return 0
        seen: set[str] = set()
        deduped = []
        for r in rows:
            if r["id"] in seen:
                continue
            seen.add(r["id"])
            deduped.append(r)
        ins = pg_insert(Event).values(deduped)
        mutable = ["event_type", "source_id", "occurred_at", "title", "body",
                   "summary", "raw", "actors", "primary_actor", "normalizer_version",
                   "url", "project", "tags", "parent_id", "related_ids",
                   "scope_id", "scope_ids", "scope_all_ids"]
        stmt = ins.on_conflict_do_update(
            index_elements=["id"],
            set_={c: getattr(ins.excluded, c) for c in mutable})
        with self._session() as s:
            return s.execute(stmt).rowcount

    def load_watermark(self, org: str, source: str) -> datetime | None:
        """L0 watermark = MAX(occurred_at) for (org, source) — served by the time index."""
        with self._session() as s:
            return s.execute(
                sa.select(sa.func.max(Event.occurred_at))
                .where(Event.org == org, Event.source == source)
            ).scalar()

    def count(self, org: str, source: str | None = None) -> int:
        q = sa.select(sa.func.count()).select_from(Event).where(Event.org == org)
        if source:
            q = q.where(Event.source == source)
        with self._session() as s:
            return s.execute(q).scalar() or 0


def _parse_ts(ts: str | None) -> datetime | None:
    """Best-effort ISO-8601 → datetime. Bad/absent timestamp degrades to None."""
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _read_scopes(ev: dict) -> tuple[str | None, list[str], list[str]]:
    """Read the scopes the fetcher stamped on the event. No derivation.

    Scope is decided at capture by the ACL-aware fetch; L0 only records it.
    An event with no scopes stays scopeless and is therefore invisible
    (fail-closed, D-INGEST-001) — that is a signal the source needs re-fetching
    with ACL extraction, not something to reconstruct here.
    """
    raw = ev.get("raw") or {}
    sid = ev.get("scope_id") or raw.get("scope_id")
    scope_ids = ev.get("scope_ids") or ([sid] if sid else [])
    scope_all = ev.get("extra_scope_ids") or raw.get("extra_scope_ids") or []
    return sid, list(scope_ids), list(scope_all)


def _event_to_row(ev: dict, org: str, normalizer_version: str | None = None) -> dict:
    """Map a KnowledgeEvent dict → the events column dict.

    Renames extra_scope_ids → scope_all_ids (the LOCKED backend gate name).
    Preserves empty scope arrays as empty (fail-closed) — never a default scope.
    """
    scope_id, scope_ids, scope_all_ids = _read_scopes(ev)
    return {
        "id": ev["id"],
        "org": ev.get("org") or org,
        "source": ev.get("source", ""),
        "event_type": ev.get("event_type", ""),
        "source_id": ev.get("source_id", "") or "",
        "occurred_at": _parse_ts(ev.get("timestamp")),
        "title": ev.get("title", "") or "",
        "body": ev.get("body", "") or "",
        "summary": ev.get("summary", "") or "",
        "raw": ev.get("raw") or {},
        "actors": ev.get("actors") or [],
        "primary_actor": ev.get("primary_actor"),
        "normalizer_version": normalizer_version,
        "url": ev.get("url"),
        "project": ev.get("project", "") or "",
        "tags": ev.get("tags") or [],
        "parent_id": ev.get("parent_id"),
        "related_ids": ev.get("related_ids") or [],
        "scope_id": scope_id,
        "scope_ids": scope_ids,
        "scope_all_ids": scope_all_ids,
        "dek_id": None,   # ponytail: NULL ⇒ plaintext dev mode; crypto-shred wires in with key material
    }


def iter_jsonl(path: Path) -> Iterator[dict]:
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                yield json.loads(line)


def source_inputs(org: str, source: str | None) -> list[Path]:
    """Resolve per-source filtered KnowledgeEvent JSONL. Gmail is the built-in
    default; other sources pass --input until their L0 wiring is added.
    ponytail: add sources here as they graduate to L0."""
    root = get_settings().output_root / "filtered"
    known = {"gmail": root / f"{org}_gmail_filtered.jsonl"}
    src = source or "gmail"
    p = known.get(src)
    if not p:
        raise ValueError(f"No built-in filtered path for source {src!r}; pass --input.")
    return [p]


def ingest(org: str, source: str | None = None,
           inputs: list[Path] | None = None, batch: int = 1000,
           dry_run: bool = False) -> dict:
    paths = inputs or source_inputs(org, source)
    store = None
    if not dry_run:
        store = LedgerStore(_resolve_dsn())
        if not store.connect():
            raise RuntimeError("L0 ledger: could not connect to Postgres.")

    read = inserted = 0
    empty_scope = 0
    try:
        buf: list[dict] = []
        for path in paths:
            if not path.exists():
                log.warning("[l0] input not found, skipping: %s", path)
                continue
            log.info("[l0] ingesting %s", path)
            for ev in iter_jsonl(path):
                row = _event_to_row(ev, org)
                if not row["scope_ids"] and not row["scope_all_ids"]:
                    empty_scope += 1   # fail-closed: stays invisible, still recorded
                read += 1
                if dry_run:
                    continue
                buf.append(row)
                if len(buf) >= batch:
                    inserted += store.upsert_events(buf)
                    buf.clear()
        if buf and not dry_run:
            inserted += store.upsert_events(buf)

        watermark = None
        if not dry_run:
            watermark = store.load_watermark(org, source or "gmail")
        result = {"read": read, "inserted": inserted,
                  "empty_scope": empty_scope,
                  "watermark": watermark.isoformat() if watermark else None}
        log.info("[l0] %s", result)
        return result
    finally:
        if store:
            store.disconnect()


def main() -> None:
    ap = argparse.ArgumentParser(description="L0 event ledger — promote filtered JSONL to Postgres")
    ap.add_argument("--org", required=True)
    ap.add_argument("--source", default="gmail")
    ap.add_argument("--input", default=None, help="Override filtered JSONL path")
    ap.add_argument("--dry-run", action="store_true", help="Map + count, no writes")
    args = ap.parse_args()

    inputs = [Path(args.input)] if args.input else None
    ingest(args.org, source=args.source, inputs=inputs, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
