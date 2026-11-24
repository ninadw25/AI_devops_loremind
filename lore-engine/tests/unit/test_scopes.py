"""
tests/unit/test_scopes.py
─────────────────────────
Fail-closed invariants for domain/scopes.py — the single owner of the
unstructured-path content-scope gate. See docs/QUERY_PIPELINE_V2.md §4.

The Cypher fragment is asserted structurally (it runs inside Neo4j); the
Python mirror is_visible() is exercised directly against every ACL case.
"""

from core.types import UserScopes
from domain.scopes import (
    SCOPE_PARAM, SUPER_ADMIN_PARAM, scope_gate, scope_params, is_visible,
    unstructured_source_filter, jira_source_filter, is_unstructured, source_label,
)


# ── Cypher fragment shape ─────────────────────────────────────────────

def test_gate_has_all_three_clauses():
    g = scope_gate("node")
    assert "size(coalesce(node.scope_ids, [])) > 0" in g          # empty/absent → invisible
    assert f"any(s IN node.scope_ids WHERE s IN ${SCOPE_PARAM})" in g   # OR-set
    assert f"all(s IN coalesce(node.scope_all_ids, []) WHERE s IN ${SCOPE_PARAM})" in g  # AND-set


def test_gate_has_super_admin_bypass():
    # the content super-admin disjunct must be present and short-circuit the gate
    assert f"${SUPER_ADMIN_PARAM}" in scope_gate("node")


def test_gate_respects_variable_name():
    assert scope_gate("m").count("m.scope_ids") >= 1
    assert "node.scope_ids" not in scope_gate("m")


def test_scope_params_binds_param_name():
    assert scope_params(UserScopes(["a", "b"])) == {
        SCOPE_PARAM: ["a", "b"],
        SUPER_ADMIN_PARAM: False,
    }


def test_scope_params_binds_super_admin():
    assert scope_params(UserScopes(["a"], super_admin=True)) == {
        SCOPE_PARAM: ["a"],
        SUPER_ADMIN_PARAM: True,
    }
    # a super-admin with no membership scopes still binds the bypass flag
    assert scope_params(UserScopes([], super_admin=True))[SUPER_ADMIN_PARAM] is True


# ── Python mirror: is_visible() ───────────────────────────────────────

def test_empty_scope_ids_invisible_to_everyone():
    # fail-closed: a node with no scope_ids is invisible even to a broadly
    # authorized caller.
    assert is_visible([], None, ["any", "scope"]) is False
    assert is_visible(None, None, ["any", "scope"]) is False


def test_super_admin_sees_everything():
    # content super-admin bypass: visible regardless of scopes, even with empty
    # scope_ids on the node and an empty allowed set on the caller.
    assert is_visible([], None, [], super_admin=True) is True
    assert is_visible(None, None, [], super_admin=True) is True
    assert is_visible(["proj"], ["sec1", "sec2"], [], super_admin=True) is True


def test_empty_allowed_sees_nothing():
    # fail-closed the other direction: a caller with no scopes sees nothing.
    assert is_visible(["s1"], None, []) is False


def test_or_set_one_match_is_enough():
    assert is_visible(["s1", "s2"], None, ["s2"]) is True


def test_or_set_no_match_invisible():
    assert is_visible(["s1", "s2"], None, ["s3"]) is False


def test_and_set_requires_all():
    # scope_all_ids is an AND-set: caller must hold every entry.
    assert is_visible(["proj"], ["sec1", "sec2"], ["proj", "sec1"]) is False
    assert is_visible(["proj"], ["sec1", "sec2"], ["proj", "sec1", "sec2"]) is True


def test_and_set_empty_passes_when_or_set_matches():
    assert is_visible(["proj"], [], ["proj"]) is True
    assert is_visible(["proj"], None, ["proj"]) is True


# ── Unstructured-source filter (Jira separation) ──────────────────────

def test_source_filter_fragment_shape():
    f = unstructured_source_filter("m")
    assert "NOT s STARTS WITH 'jira:'" in f
    assert "m.scope_ids" in f


def test_pure_jira_node_excluded():
    assert is_unstructured(["jira:project:OPS", "jira:seclevel:1"]) is False


def test_gmail_slack_outlook_teams_included():
    assert is_unstructured(["slack:channel:C1"]) is True
    assert is_unstructured(["outlook:thread:abc"]) is True
    assert is_unstructured(["teams:channel:T1:C1"]) is True
    assert is_unstructured(["person:deadbeef"]) is True   # Gmail participant id


def test_empty_scope_ids_not_unstructured():
    # no scopes → not servable anywhere (fail-closed handles visibility too)
    assert is_unstructured([]) is False
    assert is_unstructured(None) is False


def test_jira_source_filter_is_mirror():
    f = jira_source_filter("n")
    assert "s STARTS WITH 'jira:'" in f and "NOT" not in f
    # the two filters partition the graph: a node is in exactly one engine
    assert "NOT s STARTS WITH 'jira:'" in unstructured_source_filter("n")


def test_source_label():
    assert source_label(["slack:channel:C1"]) == "slack"
    assert source_label(["teams:channel:T:C"]) == "teams"
    assert source_label(["ms365:channel:C"]) == "teams"
    assert source_label(["outlook:thread:x"]) == "outlook"
    assert source_label(["jira:project:OPS"]) == "jira"
    assert source_label(["person:abc"]) == "gmail"   # Gmail = participant ids
    assert source_label([]) == "gmail"
