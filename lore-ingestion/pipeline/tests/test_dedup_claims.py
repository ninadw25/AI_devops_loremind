"""Dedup funnel (build step 5): the pure blocking → adjudication core.

Pins the two deterministic rules this pass is scoped to, and the guardrails
that keep it from ever risking a false merge:

  1. same external_id -> CORROBORATES, regardless of anything else;
  2. same subject_person + same date (+ compatible amount) -> CORROBORATES;
  3. a claim never corroborates itself within the same conversation (that's
     build_rows's exact-statement collapse, not dedup's job);
  4. conflicting amounts for the same subject+date block the merge (a real
     discrepancy must never be silently corroborated);
  5. an external_id shared by more than a handful of claims, or shared across
     claims months apart, is a recurring account id, not a transaction id —
     never merged (the CN33451 false-merge this pass was built to catch).
"""
from builders.dedup_claims import adjudicate, find_candidates, resolve


def _c(claim_id, kind="Decision", conv="c1", **attrs):
    occurred_at = attrs.pop("occurred_at", None)
    return {"claim_id": claim_id, "kind": kind, "conversation_id": conv,
            "occurred_at": occurred_at, "attributes": attrs}


def test_same_external_id_corroborates():
    a = _c("a", external_id="TXN-99")
    b = _c("b", conv="c2", external_id="TXN-99")
    assert adjudicate(a, b) == "CORROBORATES"


def test_same_subject_and_date_corroborates():
    a = _c("a", subject_person="Tanvi Chaughule", date="2025-11-01", amount="INR 20330")
    b = _c("b", conv="c2", subject_person="Tanvi Chaughule", date="2025-11-01",
          amount="INR 20330")
    assert adjudicate(a, b) == "CORROBORATES"


def test_conflicting_amount_blocks_merge():
    a = _c("a", subject_person="Tanvi Chaughule", date="2025-11-01", amount="INR 20330")
    b = _c("b", conv="c2", subject_person="Tanvi Chaughule", date="2025-11-01",
          amount="INR 24000")
    assert adjudicate(a, b) is None, "conflicting amounts must never silently corroborate"


def test_recurring_external_id_group_is_never_merged():
    claims = [_c(str(i), conv=f"c{i}", external_id="CN33451",
                occurred_at=f"2025-0{i}-01") for i in range(1, 6)]
    assert find_candidates(claims) == [], \
        "5 claims sharing one external_id is an account id, not a transaction id"


def test_external_id_match_far_apart_in_time_does_not_corroborate():
    a = _c("a", external_id="AA1", occurred_at="2025-01-01")
    b = _c("b", conv="c2", external_id="AA1", occurred_at="2025-09-01")
    assert adjudicate(a, b) is None, "same id 8 months apart is not the same transaction"


def test_pure_digit_external_id_never_merges():
    a = _c("a", external_id="10746", occurred_at="2026-04-10")
    b = _c("b", conv="c2", external_id="10746", occurred_at="2026-04-16")
    assert adjudicate(a, b) is None, "a bare digit string is an employee/phone id, not a transaction ref"


def test_different_kind_never_merges():
    a = _c("a", kind="Decision", external_id="TXN-99")
    b = _c("b", kind="Fact", conv="c2", external_id="TXN-99")
    assert adjudicate(a, b) is None


def test_same_conversation_pair_never_a_candidate():
    claims = [_c("a", subject_person="X", date="2025-11-01"),
             _c("b", subject_person="X", date="2025-11-01")]   # both conv=c1
    assert find_candidates(claims) == []


def test_resolve_end_to_end_produces_sorted_undirected_edge():
    claims = [_c("z", conv="c2", external_id="TXN-1"),
             _c("a", conv="c1", external_id="TXN-1")]
    rows = resolve(claims, "ez")
    assert rows == [{"org": "ez", "src_claim_id": "a", "dst_claim_id": "z",
                     "rel_type": "CORROBORATES"}]
