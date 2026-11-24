"""
common/tier2_scoring.py
───────────────────────
Tier-2 fuzzy identity scoring (Identity_resolution.md §5).

This is the EXTERNALS-only matcher. Internal employees are resolved
deterministically by directory/SSO + email (Tier 1) and never reach here.
Two external Person records that share NO email and NO native-id link are
scored here to decide whether they are the same human (a contractor/vendor
appearing in both Gmail and Slack, say).

Design stance (do not soften):
  • A name match only opens CANDIDACY. Corroborators decide. Never merge on
    name alone.
  • Scoring is additive LOG-ODDS — interpretable and auditable per merge,
    which matters more than raw accuracy at this layer.
  • Hard vetoes (-inf): name below floor, distinct verified emails on both
    sides, either side not an external person.
  • A wrong merge mis-attributes content (privacy + correctness), so the bias
    is conservative: when in doubt → review queue, not merge.

Pure functions only — no IO, no env reads. The reconciler stage supplies the
corroborator signals (pulled from Neo4j / the registry) and consumes Decision.
"""

from __future__ import annotations

import math
import unicodedata
from dataclasses import dataclass, field
from typing import Iterable

# Fuzzy name similarity. Prefer rapidfuzz (already a dependency for the
# validator); fall back to stdlib so this module always imports.
try:
    from rapidfuzz import fuzz

    def _name_ratio(a: str, b: str) -> float:
        return float(fuzz.token_set_ratio(a, b))
except Exception:  # pragma: no cover - fallback path
    from difflib import SequenceMatcher

    def _name_ratio(a: str, b: str) -> float:
        return SequenceMatcher(None, a, b).ratio() * 100.0


NEG_INF = float("-inf")

_HONORIFICS = {"mr", "mrs", "ms", "miss", "dr", "prof", "sir", "madam"}
_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "phd", "md"}


# ── Weights & thresholds (settings, not literals — tune from the audit log) ──
@dataclass(frozen=True)
class Weights:
    name_match: float = 1.0          # capped contribution of the name itself
    rare_name_bonus_cap: float = 1.5  # a rare name helps, but never alone crosses T_high
    shared_org: float = 1.5
    per_shared_topic: float = 0.7    # ×count, capped
    shared_topic_cap: float = 2.0
    neighborhood: float = 1.5        # × Jaccard overlap of internal contacts
    domain_copresence: float = 1.0
    temporal_overlap: float = 0.5
    handle_similarity: float = 0.5
    domain_conflict_penalty: float = -2.0  # distinct *work* domains on each side


@dataclass(frozen=True)
class Thresholds:
    name_floor: float = 92.0   # rapidfuzz token_set_ratio floor for candidacy
    t_low: float = 1.0         # below → keep separate
    t_high_open: float = 4.0   # Tier 2b externals — strict (common-name problem)
    t_high_bounded: float = 2.5  # Tier 2a against a closed internal roster


@dataclass
class Candidate:
    """The minimal view of a Person the scorer needs."""
    person_id: str
    names: list[str]
    kind: str = "external_person"          # only external_person is eligible
    emails: set[str] = field(default_factory=set)      # canonical, verified
    work_domains: set[str] = field(default_factory=set)
    handles: set[str] = field(default_factory=set)
    topics: set[str] = field(default_factory=set)      # Topic node ids
    orgs: set[str] = field(default_factory=set)        # Organization node ids
    contacts: set[str] = field(default_factory=set)    # internal person_ids they interact with
    active_weeks: set[str] = field(default_factory=set)  # e.g. {"2026-W21", ...}


@dataclass
class ScoreResult:
    score: float
    name_ratio: float
    components: dict[str, float]
    decision: str  # "merge" | "review" | "separate" | "veto"
    reason: str

    @property
    def probability(self) -> float:
        if self.score == NEG_INF:
            return 0.0
        return 1.0 / (1.0 + math.exp(-self.score))


# ── Name handling ────────────────────────────────────────────────────────────
def normalize_name(name: str) -> str:
    """NFKC, strip accents, drop honorifics/suffixes, collapse to lowercase tokens."""
    if not name:
        return ""
    n = unicodedata.normalize("NFKC", name)
    n = "".join(c for c in unicodedata.normalize("NFKD", n) if not unicodedata.combining(c))
    tokens = [t for t in "".join(ch if ch.isalnum() or ch.isspace() else " " for ch in n).lower().split()
              if t not in _HONORIFICS and t not in _SUFFIXES]
    return " ".join(tokens)


def best_name_ratio(a_names: Iterable[str], b_names: Iterable[str]) -> float:
    """Highest token_set_ratio across all name pairs after normalization."""
    a_norm = {normalize_name(n) for n in a_names if n}
    b_norm = {normalize_name(n) for n in b_names if n}
    best = 0.0
    for a in a_norm:
        for b in b_norm:
            if a and b:
                best = max(best, _name_ratio(a, b))
    return best


# ── Individual signals (each returns log-odds) ───────────────────────────────
def rare_name_bonus(name: str, name_counts: dict[str, int], w: Weights) -> float:
    """Rare names are strong evidence; common names are noise.

    name_counts maps normalized-name → registry-wide frequency.
    """
    norm = normalize_name(name)
    freq = name_counts.get(norm, 1)
    if freq <= 2:
        return w.rare_name_bonus_cap
    if freq >= 10:
        return 0.0
    # 3..9 → linearly fade the bonus toward 0
    return w.rare_name_bonus_cap * (10 - freq) / 8.0


def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def corroborator_logodds(a: Candidate, b: Candidate, w: Weights) -> dict[str, float]:
    """All corroborating signals, each as a named log-odds contribution."""
    comp: dict[str, float] = {}

    if a.orgs & b.orgs:
        comp["shared_org"] = w.shared_org

    shared_topics = len(a.topics & b.topics)
    if shared_topics:
        comp["shared_topics"] = min(shared_topics * w.per_shared_topic, w.shared_topic_cap)

    overlap = _jaccard(a.contacts, b.contacts)
    if overlap > 0:
        comp["neighborhood"] = w.neighborhood * overlap

    if a.work_domains & b.work_domains:
        comp["domain_copresence"] = w.domain_copresence

    if a.active_weeks & b.active_weeks:
        comp["temporal_overlap"] = w.temporal_overlap

    if a.handles & b.handles:
        comp["handle_similarity"] = w.handle_similarity

    # Penalty: each side has a distinct work domain and they don't overlap.
    if a.work_domains and b.work_domains and not (a.work_domains & b.work_domains):
        comp["domain_conflict"] = w.domain_conflict_penalty

    return comp


# ── Hard vetoes ──────────────────────────────────────────────────────────────
def hard_veto(a: Candidate, b: Candidate, name_ratio: float, t: Thresholds,
              *, allow_internal: bool = False) -> str | None:
    """Return a veto reason, or None if the pair may be scored.

    By default only `external_person` pairs are eligible (Tier 2b). Tier 2a — a bounded
    match of an email-less internal against the closed internal roster — passes
    `allow_internal=True` so `internal_person` pairs are eligible too. Functional /
    automation are NEVER eligible on either path (the human-boundary veto holds).
    """
    eligible = {"external_person"} | ({"internal_person"} if allow_internal else set())
    if a.kind not in eligible or b.kind not in eligible:
        return "not_eligible"  # functional/automation never merge; internals gated by flag
    if name_ratio < t.name_floor:
        return "name_below_floor"
    # Distinct verified emails on BOTH sides with no overlap → decisively different people.
    if a.emails and b.emails and not (a.emails & b.emails):
        return "email_conflict"
    return None


# ── The scorer ───────────────────────────────────────────────────────────────
def score(
    a: Candidate,
    b: Candidate,
    name_counts: dict[str, int] | None = None,
    weights: Weights | None = None,
    thresholds: Thresholds | None = None,
    *,
    bounded: bool = False,
) -> ScoreResult:
    """Score a candidate pair and return a merge/review/separate decision.

    bounded=True uses the lower Tier-2a threshold (closed internal-roster match,
    only used when a source hides an internal's email). Default is open-world 2b.
    """
    w = weights or Weights()
    t = thresholds or Thresholds()
    name_counts = name_counts or {}

    name_ratio = best_name_ratio(a.names, b.names)

    veto = hard_veto(a, b, name_ratio, t, allow_internal=bounded)
    if veto:
        return ScoreResult(NEG_INF, name_ratio, {}, "veto", veto)

    components: dict[str, float] = {
        "name_match": w.name_match,
        "rare_name": max(
            rare_name_bonus(next(iter(a.names), ""), name_counts, w),
            rare_name_bonus(next(iter(b.names), ""), name_counts, w),
        ),
    }
    components.update(corroborator_logodds(a, b, w))

    total = sum(components.values())
    t_high = t.t_high_bounded if bounded else t.t_high_open

    if total >= t_high:
        decision, reason = "merge", f"score {total:.2f} ≥ T_high {t_high}"
    elif total >= t.t_low:
        decision, reason = "review", f"T_low {t.t_low} ≤ score {total:.2f} < T_high {t_high}"
    else:
        decision, reason = "separate", f"score {total:.2f} < T_low {t.t_low}"

    return ScoreResult(total, name_ratio, components, decision, reason)
