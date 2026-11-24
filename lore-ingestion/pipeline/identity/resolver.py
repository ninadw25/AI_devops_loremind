"""
pipeline/identity/resolver.py
─────────────────────────────
The centralized resolver (CENTRALIZED_IDENTITY.md §1, §4).

A person is a stable surrogate id; every native key and every email is an alias pointing at
it. `resolve(si)` decides: attach `si` to an existing person or mint a new one.

Tiers (deterministic-first):
  Tier 1   alias hit on native key OR email   → bind new keys (>1 hit ⇒ merge)
  Tier 2a  email-less INTERNAL                → bounded name match vs the closed roster
  Tier 2b  open-world external (DEFERRED)     → suggestion to the review queue, never auto-merge
  mint     otherwise

Invariants:
  • person_id is opaque + stable across runs (seeded from a prior identity.json).
  • never merge across the human boundary (functional/automation isolated forever).
  • conservative: unsure ⇒ review queue, not merge.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from common.logger import get_logger
from common import tier2_scoring as t2
from common.email_canon import canonical_email
from pipeline.identity.source_identity import (
    SourceIdentity, INTERNAL, EXTERNAL, FUNCTIONAL, AUTOMATION,
)

log = get_logger("identity.resolver")


# ── kind algebra: the human boundary ────────────────────────────────────────
def _class(kind: str) -> str:
    """human / functional / automation. Merges only allowed within the same class."""
    return "human" if kind in (INTERNAL, EXTERNAL) else kind


def _mergeable(a: str, b: str) -> bool:
    return _class(a) == _class(b)


def _promote_kind(a: str, b: str) -> str:
    """When two records link, internal beats external (directory is authoritative)."""
    if a == INTERNAL or b == INTERNAL:
        return INTERNAL
    return a or b


def _domains(emails) -> set[str]:
    return {e.split("@")[1] for e in emails if "@" in e}


@dataclass
class IdentityStore:
    org: str
    alias: dict[str, str] = field(default_factory=dict)      # alias_key → person_id
    persons: dict[str, dict] = field(default_factory=dict)   # person_id → record
    review: list[dict] = field(default_factory=list)         # conflicts + Tier-2b suggestions
    _counter: int = 0
    _name_counts: Counter = field(default_factory=Counter)   # normalized name → freq (rare-name bonus)

    # ── id allocation ──────────────────────────────────────────────────────
    def _new_pid(self) -> str:
        self._counter += 1
        return f"p:{self.org}:{self._counter:05d}"

    def _redirect(self, pid: str) -> str:
        """Follow merged_into chains to the surviving person_id."""
        seen = set()
        while pid in self.persons and self.persons[pid].get("merged_into"):
            if pid in seen:  # cycle guard (should never happen)
                break
            seen.add(pid)
            pid = self.persons[pid]["merged_into"]
        return pid

    # ── core ops ───────────────────────────────────────────────────────────
    def mint(self, si: SourceIdentity, keys: list[str]) -> str:
        pid = self._new_pid()
        self.persons[pid] = {
            "person_id": pid,
            "canonical_name": "",
            "kind": si.kind,
            "is_internal": si.kind == INTERNAL,
            "emails": [],
            "names": [],
            "sources": {},
            "native_keys": [],
            "merged_into": None,
        }
        self._attach(pid, si, keys)
        return pid

    def _bind_keys(self, keys: list[str], pid: str) -> None:
        for k in keys:
            existing = self.alias.get(k)
            if existing and self._redirect(existing) != pid:
                # key already owned by a different (non-mergeable) person — don't hijack it
                self.review.append({"type": "alias_conflict", "key": k,
                                    "held_by": self._redirect(existing), "wanted_by": pid})
                continue
            self.alias[k] = pid

    def _attach(self, pid: str, si: SourceIdentity, keys: list[str]) -> None:
        rec = self.persons[pid]
        self._bind_keys(keys, pid)
        rec["kind"] = _promote_kind(rec["kind"], si.kind)
        rec["is_internal"] = rec["kind"] == INTERNAL
        # emails
        canon = [c for c in (canonical_email(e) for e in si.emails) if c]
        rec["emails"] = sorted(set(rec["emails"]) | set(canon))
        # names (+ frequency table for rare-name bonus)
        for n in si.names:
            if n and n not in rec["names"]:
                rec["names"].append(n)
            if n:
                self._name_counts[t2.normalize_name(n)] += 1
        rec["canonical_name"] = rec["canonical_name"] or (rec["names"][0] if rec["names"] else "")
        # source native key + native_keys list
        rec["sources"][si.source] = si.native_key
        for k in (si.native_key, *si.extra_keys):
            if k and k not in rec["native_keys"]:
                rec["native_keys"].append(k)

    def merge(self, pids: list[str], keys: list[str], si: SourceIdentity) -> str:
        """Two+ existing persons proven to be the same human → survivor = lowest counter."""
        pids = sorted({self._redirect(p) for p in pids})
        survivor = pids[0]
        for victim in pids[1:]:
            v = self.persons[victim]
            # fold victim into survivor
            s = self.persons[survivor]
            s["emails"] = sorted(set(s["emails"]) | set(v["emails"]))
            for n in v["names"]:
                if n not in s["names"]:
                    s["names"].append(n)
            for src, k in v["sources"].items():
                s["sources"].setdefault(src, k)
            for k in v["native_keys"]:
                if k not in s["native_keys"]:
                    s["native_keys"].append(k)
            s["kind"] = _promote_kind(s["kind"], v["kind"])
            s["is_internal"] = s["kind"] == INTERNAL
            v["merged_into"] = survivor
            # repoint victim's alias keys
            for k, owner in list(self.alias.items()):
                if owner == victim:
                    self.alias[k] = survivor
        self._attach(survivor, si, keys)
        return survivor

    # ── the resolver ───────────────────────────────────────────────────────
    def resolve(self, si: SourceIdentity) -> str | None:
        keys = si.alias_keys()
        if not keys:
            return None

        # Tier 1 — alias hits (native or email), de-referenced through merges
        hits = {self._redirect(self.alias[k]) for k in keys if k in self.alias}
        mergeable = {p for p in hits if _mergeable(self.persons[p]["kind"], si.kind)}

        if len(mergeable) == 1:
            return self._attach_and_return(mergeable.pop(), si, keys)
        if len(mergeable) > 1:
            return self.merge(list(mergeable), keys, si)
        if hits and not mergeable:
            # an email/native key collides with a different class (e.g. a person and a
            # functional mailbox sharing an address) — keep them separate, flag it.
            self.review.append({"type": "kind_conflict", "native_key": si.native_key,
                                "kind": si.kind, "collides_with": sorted(hits)})

        # Tier 2a — email-less internal → bounded match against the closed roster
        if si.kind == INTERNAL and not si.emails:
            pid = self._bounded_roster_match(si)
            if pid:
                return self._attach_and_return(pid, si, keys)

        # Tier 2b — email-LESS external, no deterministic link: DEFERRED.
        # An external WITH an email just mints (its email is its key; a later source sharing
        # it merges at Tier 1b). Only an email-less external is a Tier-2b suggestion — and we
        # never auto-merge, only enqueue for an operator.
        if si.kind == EXTERNAL and not si.emails:
            sugg = self._suggest_external(si)
            if sugg:
                self.review.append(sugg)

        return self.mint(si, keys)

    def _attach_and_return(self, pid: str, si: SourceIdentity, keys: list[str]) -> str:
        self._attach(pid, si, keys)
        return pid

    # ── Tier 2 helpers ───────────────────────────────────────────────────────
    def _candidate(self, names, kind, emails) -> t2.Candidate:
        canon = {c for c in (canonical_email(e) for e in emails) if c}
        return t2.Candidate(person_id="?", names=list(names), kind=kind,
                            emails=canon, work_domains=_domains(canon))

    def _bounded_roster_match(self, si: SourceIdentity) -> str | None:
        """Score si against every internal person; merge only on a confident bounded hit."""
        a = self._candidate(si.names, INTERNAL, si.emails)
        best_pid, best = None, None
        for pid, rec in self.persons.items():
            if rec.get("merged_into") or rec["kind"] != INTERNAL:
                continue
            b = self._candidate(rec["names"], INTERNAL, rec["emails"])
            res = t2.score(a, b, name_counts=dict(self._name_counts), bounded=True)
            if res.decision == "merge" and (best is None or res.score > best.score):
                best_pid, best = pid, res
            elif res.decision == "review":
                self.review.append({"type": "tier2a_review", "source": si.source,
                                    "native_key": si.native_key, "names": si.names,
                                    "candidate": pid, "score": round(res.score, 2),
                                    "reason": res.reason})
        if best_pid:
            log.info("tier2a merge: %s (%s) → %s  [%s]", si.native_key, si.names, best_pid, best.reason)
        return best_pid

    def _suggest_external(self, si: SourceIdentity) -> dict | None:
        """A name-only suggestion for an operator (Tier 2b is never auto-applied)."""
        a = self._candidate(si.names, EXTERNAL, si.emails)
        for pid, rec in self.persons.items():
            if rec.get("merged_into") or rec["kind"] != EXTERNAL:
                continue
            b = self._candidate(rec["names"], EXTERNAL, rec["emails"])
            res = t2.score(a, b, name_counts=dict(self._name_counts), bounded=False)
            if res.decision in ("merge", "review"):  # suggest, do not apply
                return {"type": "tier2b_suggestion", "source": si.source,
                        "native_key": si.native_key, "names": si.names,
                        "candidate": pid, "score": round(res.score, 2), "decision": res.decision}
        return None  # noqa: handled per-candidate above

    # ── serialization ──────────────────────────────────────────────────────
    # Bump on any breaking change to the artifact shape (backend reads this).
    SCHEMA_VERSION = 1

    def to_artifact(self) -> dict:
        live = [p for p in self.persons.values() if not p.get("merged_into")]
        tomb = [{"person_id": p["person_id"], "merged_into": p["merged_into"]}
                for p in self.persons.values() if p.get("merged_into")]
        return {
            "schema_version": self.SCHEMA_VERSION,
            "org": self.org,
            "persons": sorted(live, key=lambda r: r["person_id"]),
            "alias_table": dict(sorted(self.alias.items())),
            "tombstones": sorted(tomb, key=lambda r: r["person_id"]),
            "counts": {"persons": len(live),
                       "by_kind": dict(Counter(p["kind"] for p in live)),
                       "alias_keys": len(self.alias),
                       "merges": len(tomb)},
        }

    @classmethod
    def from_artifact(cls, data: dict) -> "IdentityStore":
        """Seed from a prior run so person_ids stay stable (persistence)."""
        store = cls(org=data.get("org", "org"))
        store.alias = dict(data.get("alias_table", {}))
        for rec in data.get("persons", []):
            store.persons[rec["person_id"]] = rec
            for n in rec.get("names", []):
                store._name_counts[t2.normalize_name(n)] += 1
        # restore the counter high-water mark from existing ids
        for pid in store.persons:
            try:
                store._counter = max(store._counter, int(pid.rsplit(":", 1)[1]))
            except (ValueError, IndexError):
                pass
        return store
