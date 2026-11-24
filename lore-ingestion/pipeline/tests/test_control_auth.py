"""The control API must refuse to serve when its shared secret is unset.

`POST /runs` starts a full ingestion run using a service-account key handed to
it in the request body. An earlier version treated a blank
CONTROL_INTERNAL_SECRET as "dev mode" and accepted every caller, so an
unconfigured deploy was silently world-writable. These pin fail-closed.
"""
import asyncio

import pytest
from fastapi import HTTPException

import common.config as config_mod
from control.auth import require_internal


def _call(header):
    return asyncio.run(require_internal(x_internal_auth=header))


@pytest.fixture
def secret(monkeypatch):
    """Set the configured control secret, bypassing the settings cache."""
    def _set(value):
        s = config_mod.get_settings()
        monkeypatch.setattr(s, "control_internal_secret", value, raising=False)
    return _set


def test_unset_secret_refuses_everyone(secret):
    secret("")
    for header in (None, "", "anything"):
        with pytest.raises(HTTPException) as e:
            _call(header)
        assert e.value.status_code == 503, "must not fall back to permissive"
        assert "CONTROL_INTERNAL_SECRET" in e.value.detail


def test_wrong_secret_is_rejected(secret):
    secret("s3cret")
    for header in (None, "", "wrong"):
        with pytest.raises(HTTPException) as e:
            _call(header)
        assert e.value.status_code == 401


def test_correct_secret_is_accepted(secret):
    secret("s3cret")
    assert _call("s3cret") is None
