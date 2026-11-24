"""
builders/l1_registry.py
───────────────────────
L1 entity registries — build step 2 of `docs/revised_ingestion_pipeline/00_DESIGN.md` §11.

Projects the L0 event ledger into resolved entity tables:

    entity_person        one row per resolved person (org-domain aliases merged),
                         sender-typed by the rule classifier (pipeline/sender_type)
    entity_person_alias  every email address → its person
    entity_organization  one row per counterparty domain that HUMANS actually
                         mailed with (system/newsletter senders never mint orgs)
    entity_container     one row per replicated mailbox (later: channels, projects)
    entity_artifact      one row per conversation (from build step 1d grouping)
    entity_topic         table only — seeded by closed-world curation (step 6),
                         never by this pass

Like every derived layer: a pure, re-runnable projection of L0. Rebuild is
DELETE + INSERT per org — nothing here is a source of record. Deterministic,
zero LLM. Identity merging is the proven org-domain local-part rule only;
personal-account linkage (Fellegi–Sunter) is step 9 and lands as
SAME_PERSON_CANDIDATE suggestions, never silent merges here.

Usage (from lore-ingestion/):
    ../.venv/bin/python -m builders.l1_registry --org ezobooks
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from builders.l0_ledger import SCHEMA, Base, Event, LedgerStore, _resolve_dsn
from common.logger import get_logger
from common.org_config import load_org_config
from pipeline.person_registry import PERSONAL_DOMAINS, clean_email, select_canonical_name
from pipeline.sender_type import PROMO_LABELS, build_features, classify_sender

log = get_logger("builders.l1_registry")

REGISTRY_VERSION = "l1-1"


# ── Tables ───────────────────────────────────────────────────────────────────

class EntityPerson(Base):
    __tablename__ = "entity_person"
    __table_args__ = (
        sa.Index("entity_person_org_idx", "org"),
        sa.Index("entity_person_org_type_idx", "org", "sender_type"),
        {"schema": SCHEMA},
    )
    person_id: Mapped[str] = mapped_column(sa.Text, primary_key=True)  # person:{primary_email}
    org: Mapped[str] = mapped_column(sa.Text, nullable=False)
    primary_email: Mapped[str] = mapped_column(sa.Text, nullable=False)
    display_name: Mapped[str] = mapped_column(sa.Text, nullable=False, default="")
    sender_type: Mapped[str] = mapped_column(sa.Text, nullable=False)
    sender_type_reason: Mapped[str] = mapped_column(sa.Text, nullable=False, default="")
    is_internal: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=False)
    alias_count: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=1)
    emails_sent: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    emails_received: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    unique_contacts: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    first_seen: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    last_seen: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    registry_version: Mapped[str] = mapped_column(sa.Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now())


class EntityPersonAlias(Base):
    __tablename__ = "entity_person_alias"
    __table_args__ = (
        sa.Index("entity_person_alias_person_idx", "person_id"),
        {"schema": SCHEMA},
    )
    org: Mapped[str] = mapped_column(sa.Text, primary_key=True)
    email: Mapped[str] = mapped_column(sa.Text, primary_key=True)
    person_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    display_name: Mapped[str] = mapped_column(sa.Text, nullable=False, default="")
    emails_sent: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    emails_received: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)


class EntityOrganization(Base):
    __tablename__ = "entity_organization"
    __table_args__ = (
        sa.Index("entity_organization_org_idx", "org"),
        {"schema": SCHEMA},
    )
    org_id: Mapped[str] = mapped_column(sa.Text, primary_key=True)  # org:{domain}
    org: Mapped[str] = mapped_column(sa.Text, nullable=False)
    domain: Mapped[str] = mapped_column(sa.Text, nullable=False)
    kind: Mapped[str] = mapped_column(sa.Text, nullable=False)  # internal|external|personal_provider
    person_count: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    event_count: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    registry_version: Mapped[str] = mapped_column(sa.Text, nullable=False)


class EntityContainer(Base):
    __tablename__ = "entity_container"
    __table_args__ = (
        sa.Index("entity_container_org_idx", "org"),
        {"schema": SCHEMA},
    )
    container_id: Mapped[str] = mapped_column(sa.Text, primary_key=True)
    org: Mapped[str] = mapped_column(sa.Text, nullable=False)
    source: Mapped[str] = mapped_column(sa.Text, nullable=False)
    kind: Mapped[str] = mapped_column(sa.Text, nullable=False)      # gmail.mailbox
    source_key: Mapped[str] = mapped_column(sa.Text, nullable=False)  # the mailbox email
    display_name: Mapped[str] = mapped_column(sa.Text, nullable=False, default="")
    registry_version: Mapped[str] = mapped_column(sa.Text, nullable=False)


class EntityArtifact(Base):
    __tablename__ = "entity_artifact"
    __table_args__ = (
        sa.Index("entity_artifact_org_idx", "org", "kind"),
        {"schema": SCHEMA},
    )
    artifact_id: Mapped[str] = mapped_column(sa.Text, primary_key=True)  # = conversation_id
    org: Mapped[str] = mapped_column(sa.Text, nullable=False)
    source: Mapped[str] = mapped_column(sa.Text, nullable=False)
    kind: Mapped[str] = mapped_column(sa.Text, nullable=False)      # gmail.conversation
    title: Mapped[str] = mapped_column(sa.Text, nullable=False, default="")
    message_count: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    signal_count: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    participant_count: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    first_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    last_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    registry_version: Mapped[str] = mapped_column(sa.Text, nullable=False)


class EntityTopic(Base):
    """Created empty on purpose: topics enter ONLY via closed-world curation
    (design §6) — deterministic anchors + promoted proposals. If this pass
    minted topics from data, we'd rebuild exactly the F2 explosion."""
    __tablename__ = "entity_topic"
    __table_args__ = (
        sa.Index("entity_topic_org_idx", "org"),
        {"schema": SCHEMA},
    )
    topic_id: Mapped[str] = mapped_column(sa.Text, primary_key=True)
    org: Mapped[str] = mapped_column(sa.Text, nullable=False)
    name: Mapped[str] = mapped_column(sa.Text, nullable=False)
    aliases: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    status: Mapped[str] = mapped_column(sa.Text, nullable=False, default="seed")
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now())


# ── Pure core (no DB — unit-tested directly) ────────────────────────────────

class Scan:
    """Accumulators for one streaming pass over events."""

    def __init__(self):
        self.names: dict[str, Counter] = defaultdict(Counter)
        self.sent: Counter = Counter()
        self.received: Counter = Counter()
        self.contacts: dict[str, set] = defaultdict(set)
        self.first: dict[str, datetime] = {}
        self.last: dict[str, datetime] = {}
        self.is_bot: set[str] = set()
        self.bulk: set[str] = set()          # sender showed List-Unsubscribe / promo label
        self.conversations: dict[str, dict] = {}

    def _seen(self, email: str, at: datetime | None) -> None:
        if at is None:
            return
        if email not in self.first or at < self.first[email]:
            self.first[email] = at
        if email not in self.last or at > self.last[email]:
            self.last[email] = at

    def feed(self, ev: dict) -> None:
        """One event: {actors, primary_actor, occurred_at, conversation_id,
        noise_class, title, raw}. Person/org signals come from SIGNAL events
        only; conversations count every message (noise ratio is itself data)."""
        is_signal = ev.get("noise_class") == "signal"
        at = ev.get("occurred_at")
        sender = clean_email(ev.get("primary_actor") or "")
        raw = ev.get("raw") or {}

        emails_here = set()
        if is_signal:
            for a in ev.get("actors") or []:
                email = clean_email(a.get("email") or a.get("id") or "")
                if not email:
                    continue
                emails_here.add(email)
                name = a.get("display_name") or ""
                if name and name != email:
                    self.names[email][name] += 1
                else:
                    self.names[email] += Counter()   # register the email itself
                if a.get("is_bot"):
                    self.is_bot.add(email)
                self._seen(email, at)
                if email == sender:
                    self.sent[email] += 1
                else:
                    self.received[email] += 1
            for e in emails_here:
                self.contacts[e] |= emails_here - {e}
            if sender and (raw.get("list_unsubscribe")
                           or set(raw.get("label_ids") or ()) & PROMO_LABELS):
                self.bulk.add(sender)

        conv = ev.get("conversation_id")
        if conv:
            c = self.conversations.setdefault(conv, {
                "message_count": 0, "signal_count": 0, "participants": set(),
                "first_at": None, "last_at": None, "title": "",
            })
            c["message_count"] += 1
            if is_signal:
                c["signal_count"] += 1
                c["participants"] |= emails_here
            if at is not None:
                if c["first_at"] is None or at < c["first_at"]:
                    c["first_at"], c["title"] = at, (ev.get("title") or c["title"])
                if c["last_at"] is None or at > c["last_at"]:
                    c["last_at"] = at


def _cluster(emails, org_domains: set[str]) -> list[set[str]]:
    """Org-domain aliases with the same local part are one person
    (gk@ezobooks.in = gk@ezobanks.com). Everyone else stands alone."""
    by_local: dict[str, set] = defaultdict(set)
    singles: list[set] = []
    for e in emails:
        local, _, domain = e.partition("@")
        if domain in org_domains:
            by_local[local].add(e)
        else:
            singles.append({e})
    return list(by_local.values()) + singles


def resolve(scan: Scan, org: str, org_domains: list[str],
            extra_functional: set[str] | None = None,
            noise_domains: set[str] | None = None,
            allowlisted_domains: set[str] | None = None) -> dict:
    """Accumulators → registry rows. Deterministic; the only judgment calls are
    the rule classifier's, and each row records which rule fired."""
    domset = {d.lower() for d in org_domains}
    persons, aliases = [], []

    for cluster in _cluster(scan.names.keys(), domset):
        sent = sum(scan.sent[e] for e in cluster)
        received = sum(scan.received[e] for e in cluster)
        contacts = set().union(*(scan.contacts[e] for e in cluster)) - cluster
        # Primary: the org-domain alias with the most traffic, else the busiest.
        def _traffic(e):
            return (scan.sent[e] + scan.received[e], e)
        org_aliases = [e for e in cluster if e.partition("@")[2] in domset]
        primary = max(org_aliases or cluster, key=_traffic)
        merged_names = Counter()
        for e in cluster:
            merged_names += scan.names[e]
        display = select_canonical_name(merged_names) if merged_names else primary

        feats = build_features(
            primary,
            is_org_domain=bool(org_aliases),
            is_bot=any(e in scan.is_bot for e in cluster),
            had_list_unsubscribe=any(e in scan.bulk for e in cluster),
            promo_label=any(e in scan.bulk for e in cluster),
            extra_functional=extra_functional,
            noise_domains=noise_domains,
            allowlisted_domains=allowlisted_domains,
            personal_domains=PERSONAL_DOMAINS,
            emails_sent=sent, emails_received=received,
            unique_contacts=len(contacts),
        )
        stype, reason = classify_sender(feats)

        firsts = [scan.first[e] for e in cluster if e in scan.first]
        lasts = [scan.last[e] for e in cluster if e in scan.last]
        pid = f"person:{primary}"
        persons.append({
            "person_id": pid, "org": org, "primary_email": primary,
            "display_name": display, "sender_type": stype.value,
            "sender_type_reason": reason, "is_internal": bool(org_aliases),
            "alias_count": len(cluster), "emails_sent": sent,
            "emails_received": received, "unique_contacts": len(contacts),
            "first_seen": min(firsts) if firsts else None,
            "last_seen": max(lasts) if lasts else None,
            "registry_version": REGISTRY_VERSION,
        })
        for e in sorted(cluster):
            top = scan.names[e].most_common(1)
            aliases.append({
                "org": org, "email": e, "person_id": pid,
                "display_name": top[0][0] if top else "",
                "emails_sent": scan.sent[e], "emails_received": scan.received[e],
            })

    # Organizations: minted from HUMAN and FUNCTIONAL persons only — a noreply
    # sender or newsletter must never create a counterparty org (F2's cousin).
    dom_stats: dict[str, dict] = defaultdict(lambda: {"persons": 0, "events": 0})
    for p in persons:
        if p["sender_type"] not in ("human", "functional_mailbox"):
            continue
        domain = p["primary_email"].partition("@")[2]
        if not domain:
            continue
        d = dom_stats[domain]
        d["persons"] += 1
        d["events"] += p["emails_sent"] + p["emails_received"]
    orgs = []
    for domain, d in sorted(dom_stats.items()):
        kind = ("internal" if domain in domset
                else "personal_provider" if domain in PERSONAL_DOMAINS
                else "external")
        orgs.append({
            "org_id": f"org:{domain}", "org": org, "domain": domain, "kind": kind,
            "person_count": d["persons"], "event_count": d["events"],
            "registry_version": REGISTRY_VERSION,
        })

    artifacts = [{
        "artifact_id": conv, "org": org, "source": "gmail",
        "kind": "gmail.conversation", "title": (c["title"] or "")[:500],
        "message_count": c["message_count"], "signal_count": c["signal_count"],
        "participant_count": len(c["participants"]),
        "first_at": c["first_at"], "last_at": c["last_at"],
        "registry_version": REGISTRY_VERSION,
    } for conv, c in scan.conversations.items()]

    return {"persons": persons, "aliases": aliases, "orgs": orgs, "artifacts": artifacts}


# ── DB pass ─────────────────────────────────────────────────────────────────

def build(org: str, source: str | None = None) -> dict:
    """Rebuild the org's L1 registries. source=None (default) scans ALL of the
    org's sources so the person/org spine MERGES across Gmail, Zoho, etc. —
    the cross-source identity resolution the registry exists for. Passing a
    single source is for isolated debugging only; a per-source build would
    DELETE the whole org's registry (below) then repopulate from one source,
    wiping the other sources' spine — so multi-source callers must omit it."""
    store = LedgerStore(_resolve_dsn())
    if not store.connect():
        raise RuntimeError("l1_registry: could not connect to Postgres.")
    Base.metadata.create_all(store._engine)   # add L1 tables if missing

    cfg = load_org_config(org, Path("./output/config")) or {}
    org_domains = cfg.get("org_domains") or []
    extra_functional = set(cfg.get("extra_functional_mailboxes") or [])
    noise_domains = set(cfg.get("discovered_noise_domains") or [])
    allowlisted = set(cfg.get("allowlisted_domains") or [])

    scan = Scan()
    n = 0
    with store._session() as s:
        q = (sa.select(Event.actors, Event.primary_actor, Event.occurred_at,
                       Event.conversation_id, Event.noise_class, Event.title, Event.raw)
             .where(Event.org == org)
             .execution_options(yield_per=2000))
        if source is not None:
            q = q.where(Event.source == source)
        for row in s.execute(q):
            scan.feed({
                "actors": row.actors, "primary_actor": row.primary_actor,
                "occurred_at": row.occurred_at, "conversation_id": row.conversation_id,
                "noise_class": row.noise_class, "title": row.title, "raw": row.raw,
            })
            n += 1
            if n % 20000 == 0:
                log.info("[l1] scanned %d events", n)
    log.info("[l1] scanned %d events — resolving", n)

    rows = resolve(scan, org, org_domains, extra_functional, noise_domains, allowlisted)

    # Derived layer: rebuild in place. DELETE + INSERT per org, one transaction.
    with store._session() as s:
        for model in (EntityPersonAlias, EntityPerson, EntityOrganization, EntityArtifact):
            s.execute(sa.delete(model).where(model.org == org))
        for model, key in ((EntityPerson, "persons"), (EntityPersonAlias, "aliases"),
                           (EntityOrganization, "orgs"), (EntityArtifact, "artifacts")):
            data = rows[key]
            for i in range(0, len(data), 1000):
                s.execute(sa.insert(model), data[i:i + 1000])

        # Containers from the replica's mailbox/account sightings, per source
        # (a gmail mailbox and a zoho account are distinct container kinds).
        s.execute(sa.delete(EntityContainer).where(EntityContainer.org == org))
        cq = (f"SELECT DISTINCT source, key FROM {SCHEMA}.source_payloads, "
              "jsonb_object_keys(sightings) AS key WHERE org=:o")
        params = {"o": org}
        if source is not None:
            cq += " AND source=:src"
            params["src"] = source
        containers = s.execute(sa.text(cq), params).all()
        if containers:
            s.execute(sa.insert(EntityContainer), [{
                "container_id": f"container:{src}.mailbox:{m}", "org": org,
                "source": src, "kind": f"{src}.mailbox", "source_key": m,
                "display_name": m, "registry_version": REGISTRY_VERSION,
            } for src, m in sorted(containers)])

    types = Counter(p["sender_type"] for p in rows["persons"])
    stats = {
        "events_scanned": n, "persons": len(rows["persons"]),
        "aliases": len(rows["aliases"]), "organizations": len(rows["orgs"]),
        "artifacts": len(rows["artifacts"]), "containers": len(containers),
        "sender_types": dict(types), "registry_version": REGISTRY_VERSION,
    }
    log.info("[l1] STAGE_SUMMARY stage=l1_registry org=%s persons=%d aliases=%d "
             "orgs=%d artifacts=%d containers=%d types=%s",
             org, stats["persons"], stats["aliases"], stats["organizations"],
             stats["artifacts"], stats["containers"], dict(types))
    log.info("[l1] DONE %s", stats)
    store.disconnect()
    return stats


def main() -> None:
    ap = argparse.ArgumentParser(description="L0 events → L1 entity registries (step 2)")
    ap.add_argument("--org", required=True)
    ap.add_argument("--source", default=None,
                    help="Debug only: restrict to one source. Omit to build the "
                         "cross-source spine over ALL the org's sources (correct default).")
    args = ap.parse_args()
    build(args.org, args.source)


if __name__ == "__main__":
    main()
