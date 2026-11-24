"""
builders/l2_edges.py
─────────────────────
L2 interaction graph — build step 3 of `docs/revised_ingestion_pipeline/00_DESIGN.md` §11.

Deterministic edges computed from L0 events × L1 registries. Zero LLM. Feeds
expert search / bus-factor (L4 views) and the Neo4j projection directly:

    edge_authored          person AUTHORED artifact (sent >=1 signal message in it)
    edge_participated_in   person PARTICIPATED_IN artifact (any signal actor, incl. author)
    edge_communicates_with person COMMUNICATES_WITH person (directed sender -> recipient)
    edge_member_of         person MEMBER_OF container (owns the mailbox)
    edge_assigned          person ASSIGNED artifact — empty by design; no source in this
                            substrate carries assignment yet (lands with Jira ingestion)

Like L1: a pure, re-runnable projection. Rebuild is DELETE + INSERT per org.

Usage (from lore-ingestion/):
    ../.venv/bin/python -m builders.l2_edges --org ezobooks
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from builders.l0_ledger import SCHEMA, Base, Event, LedgerStore, _resolve_dsn
from common.logger import get_logger
from pipeline.person_registry import clean_email

log = get_logger("builders.l2_edges")

EDGE_VERSION = "l2-1"


# ── Tables ───────────────────────────────────────────────────────────────────

class EdgeAuthored(Base):
    __tablename__ = "edge_authored"
    __table_args__ = ({"schema": SCHEMA},)
    org: Mapped[str] = mapped_column(sa.Text, primary_key=True)
    person_id: Mapped[str] = mapped_column(sa.Text, primary_key=True)
    artifact_id: Mapped[str] = mapped_column(sa.Text, primary_key=True)
    weight: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    first_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    last_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    edge_version: Mapped[str] = mapped_column(sa.Text, nullable=False)


class EdgeParticipatedIn(Base):
    __tablename__ = "edge_participated_in"
    __table_args__ = ({"schema": SCHEMA},)
    org: Mapped[str] = mapped_column(sa.Text, primary_key=True)
    person_id: Mapped[str] = mapped_column(sa.Text, primary_key=True)
    artifact_id: Mapped[str] = mapped_column(sa.Text, primary_key=True)
    weight: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    first_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    last_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    edge_version: Mapped[str] = mapped_column(sa.Text, nullable=False)


class EdgeCommunicatesWith(Base):
    __tablename__ = "edge_communicates_with"
    __table_args__ = ({"schema": SCHEMA},)
    org: Mapped[str] = mapped_column(sa.Text, primary_key=True)
    person_id: Mapped[str] = mapped_column(sa.Text, primary_key=True)       # sender
    other_person_id: Mapped[str] = mapped_column(sa.Text, primary_key=True)  # recipient
    weight: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    first_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    last_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    edge_version: Mapped[str] = mapped_column(sa.Text, nullable=False)


class EdgeMemberOf(Base):
    __tablename__ = "edge_member_of"
    __table_args__ = ({"schema": SCHEMA},)
    org: Mapped[str] = mapped_column(sa.Text, primary_key=True)
    person_id: Mapped[str] = mapped_column(sa.Text, primary_key=True)
    container_id: Mapped[str] = mapped_column(sa.Text, primary_key=True)
    edge_version: Mapped[str] = mapped_column(sa.Text, nullable=False)


class EdgeAssigned(Base):
    """Empty by design for this substrate — Gmail carries no assignment
    signal. Populated once Jira lands (issue.assignee is deterministic,
    zero LLM, same as every other L2 edge)."""
    __tablename__ = "edge_assigned"
    __table_args__ = ({"schema": SCHEMA},)
    org: Mapped[str] = mapped_column(sa.Text, primary_key=True)
    person_id: Mapped[str] = mapped_column(sa.Text, primary_key=True)
    artifact_id: Mapped[str] = mapped_column(sa.Text, primary_key=True)
    edge_version: Mapped[str] = mapped_column(sa.Text, nullable=False)


# ── Pure core (no DB — unit-tested directly) ────────────────────────────────

class Scan:
    """Accumulators for one streaming pass over signal events."""

    def __init__(self):
        self.authored: dict[tuple, dict] = {}
        self.participated: dict[tuple, dict] = {}
        self.comm: dict[tuple, dict] = {}

    @staticmethod
    def _bump(bucket: dict, key: tuple, at: datetime | None) -> None:
        row = bucket.setdefault(key, {"weight": 0, "first_at": None, "last_at": None})
        row["weight"] += 1
        if at is not None:
            if row["first_at"] is None or at < row["first_at"]:
                row["first_at"] = at
            if row["last_at"] is None or at > row["last_at"]:
                row["last_at"] = at

    def feed(self, ev: dict, email_to_person: dict[str, str]) -> None:
        """One signal event: {actors, primary_actor, occurred_at, conversation_id}."""
        if ev.get("noise_class") != "signal":
            return
        conv = ev.get("conversation_id")
        if not conv:
            return
        at = ev.get("occurred_at")
        sender_email = clean_email(ev.get("primary_actor") or "")
        sender_pid = email_to_person.get(sender_email)

        actor_pids = set()
        for a in ev.get("actors") or []:
            email = clean_email(a.get("email") or a.get("id") or "")
            pid = email_to_person.get(email)
            if pid:
                actor_pids.add(pid)

        for pid in actor_pids:
            self._bump(self.participated, (pid, conv), at)
        if sender_pid:
            self._bump(self.authored, (sender_pid, conv), at)
            for pid in actor_pids:
                if pid != sender_pid:
                    self._bump(self.comm, (sender_pid, pid), at)


def resolve(scan: Scan, org: str) -> dict:
    """Accumulators -> edge rows. Deterministic, no judgment calls."""
    def _rows(bucket, keys):
        return [{"org": org, **dict(zip(keys, k)), **v, "edge_version": EDGE_VERSION}
                for k, v in bucket.items()]

    return {
        "authored": _rows(scan.authored, ("person_id", "artifact_id")),
        "participated_in": _rows(scan.participated, ("person_id", "artifact_id")),
        "communicates_with": _rows(scan.comm, ("person_id", "other_person_id")),
    }


# ── DB pass ─────────────────────────────────────────────────────────────────

def build(org: str, source: str | None = None) -> dict:
    """Rebuild the org's L2 interaction edges. source=None (default) scans ALL
    of the org's sources — interaction is person↔person / person↔artifact and
    inherently cross-source, so edges must span Gmail + Zoho + … together. A
    single source is for isolated debugging only."""
    store = LedgerStore(_resolve_dsn())
    if not store.connect():
        raise RuntimeError("l2_edges: could not connect to Postgres.")
    Base.metadata.create_all(store._engine)   # add L2 tables if missing

    with store._session() as s:
        email_to_person = dict(s.execute(sa.text(
            f"SELECT email, person_id FROM {SCHEMA}.entity_person_alias WHERE org=:o"),
            {"o": org}).all())
        containers = s.execute(sa.text(
            f"SELECT container_id, source_key FROM {SCHEMA}.entity_container WHERE org=:o"),
            {"o": org}).all()

    scan = Scan()
    n = 0
    with store._session() as s:
        q = (sa.select(Event.actors, Event.primary_actor, Event.occurred_at,
                       Event.conversation_id, Event.noise_class)
             .where(Event.org == org)
             .execution_options(yield_per=2000))
        if source is not None:
            q = q.where(Event.source == source)
        for row in s.execute(q):
            scan.feed({
                "actors": row.actors, "primary_actor": row.primary_actor,
                "occurred_at": row.occurred_at, "conversation_id": row.conversation_id,
                "noise_class": row.noise_class,
            }, email_to_person)
            n += 1
            if n % 50000 == 0:
                log.info("[l2] scanned %d events", n)
    log.info("[l2] scanned %d events — resolving", n)

    rows = resolve(scan, org)

    # MEMBER_OF: direct from the registry, no event scan needed — the mailbox
    # owner is whichever person's primary/alias email equals the container's
    # source_key.
    member_of = [{"org": org, "person_id": email_to_person[c.source_key],
                  "container_id": c.container_id, "edge_version": EDGE_VERSION}
                 for c in containers if c.source_key in email_to_person]

    with store._session() as s:
        for model in (EdgeAuthored, EdgeParticipatedIn, EdgeCommunicatesWith, EdgeMemberOf):
            s.execute(sa.delete(model).where(model.org == org))
        for model, data in ((EdgeAuthored, rows["authored"]),
                            (EdgeParticipatedIn, rows["participated_in"]),
                            (EdgeCommunicatesWith, rows["communicates_with"]),
                            (EdgeMemberOf, member_of)):
            for i in range(0, len(data), 1000):
                s.execute(sa.insert(model), data[i:i + 1000])

    stats = {
        "events_scanned": n, "authored": len(rows["authored"]),
        "participated_in": len(rows["participated_in"]),
        "communicates_with": len(rows["communicates_with"]),
        "member_of": len(member_of), "edge_version": EDGE_VERSION,
    }
    log.info("[l2] STAGE_SUMMARY stage=l2_edges org=%s authored=%d participated_in=%d "
             "communicates_with=%d member_of=%d",
             org, stats["authored"], stats["participated_in"],
             stats["communicates_with"], stats["member_of"])
    log.info("[l2] DONE %s", stats)
    store.disconnect()
    return stats


def main() -> None:
    ap = argparse.ArgumentParser(description="L0 events + L1 registries -> L2 interaction graph (step 3)")
    ap.add_argument("--org", required=True)
    ap.add_argument("--source", default=None,
                    help="Debug only: restrict to one source. Omit to build edges "
                         "over ALL the org's sources (correct default).")
    args = ap.parse_args()
    build(args.org, args.source)


if __name__ == "__main__":
    main()
