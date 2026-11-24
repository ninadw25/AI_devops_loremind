"""
Guard against writing one client's knowledge into another client's database.

lore-ingestion is org-agnostic: the control plane resolves a client's Neo4j from
the registry and injects it into the run's env. Nothing in that env says which
client it was resolved for, so a hand-run `--org clientA` with clientB's .env
loaded would silently write clientA's data into clientB's graph. The stamp +
guard exist to make that refuse instead of succeed.
"""

import pytest

from common.config import (
    ConnectionRoutingError,
    Settings,
    assert_connection_for_org,
    neo4j_credentials_for,
)


def _settings(**overrides) -> Settings:
    base = dict(
        neo4j_uri="neo4j+s://clientb.example:7687",
        neo4j_username="neo4j",
        neo4j_password="secret",
    )
    return Settings(**{**base, **overrides})


def test_guard_off_by_default_keeps_local_cli_working():
    """The documented CLI workflow must not break on a dev machine."""
    s = _settings(require_registry_connection=False, lore_connection_org="")
    assert_connection_for_org("ezobooks", s)  # does not raise


def test_unstamped_run_is_refused_when_required():
    """A hand-run has no control-plane stamp — the case this exists to catch."""
    s = _settings(require_registry_connection=True, lore_connection_org="")
    with pytest.raises(ConnectionRoutingError, match="no LORE_CONNECTION_ORG stamp"):
        assert_connection_for_org("ezobooks", s)


def test_connection_resolved_for_another_org_is_refused():
    """The actual disaster: clientA's run holding clientB's connection."""
    s = _settings(require_registry_connection=True, lore_connection_org="clientb")
    with pytest.raises(ConnectionRoutingError, match="resolved for org 'clientb'"):
        assert_connection_for_org("clienta", s)


def test_matching_stamp_passes():
    """A real control-plane run: stamp names the org being built."""
    s = _settings(require_registry_connection=True, lore_connection_org="ezobooks")
    assert_connection_for_org("ezobooks", s)  # does not raise


def test_credentials_helper_enforces_the_guard():
    """Callers must not be able to get credentials past the guard."""
    s = _settings(require_registry_connection=True, lore_connection_org="clientb")
    with pytest.raises(ConnectionRoutingError):
        neo4j_credentials_for("clienta", s)

    ok = neo4j_credentials_for("clientb", s)
    assert ok == ("neo4j+s://clientb.example:7687", "neo4j", "secret")
