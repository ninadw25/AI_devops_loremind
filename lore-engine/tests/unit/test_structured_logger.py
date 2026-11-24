"""
Unit tests for StructuredLogger (core/logging.py).

Verifies kwargs become structured log fields, all log levels work,
exception captures exc_info, and no regressions on the stdlib adapter.
"""

from __future__ import annotations

import logging

import pytest

from core.logging import StructuredLogger, get_logger


@pytest.fixture
def log() -> StructuredLogger:
    return get_logger("test.structured")


def test_get_logger_returns_structured_logger():
    assert isinstance(get_logger("any.name"), StructuredLogger)


def test_all_log_levels_exist(log):
    for method in ("debug", "info", "warning", "error", "exception", "critical"):
        assert callable(getattr(log, method)), f"Missing method: {method}"


def test_info_kwargs_become_record_attributes(log, caplog):
    with caplog.at_level(logging.INFO, logger="test.structured"):
        log.info("evt", user_id="alice", count=42)
    assert len(caplog.records) == 1
    record = caplog.records[0]
    assert record.getMessage() == "evt"
    assert record.user_id == "alice"
    assert record.count == 42


def test_warning_kwargs_on_record(log, caplog):
    with caplog.at_level(logging.WARNING, logger="test.structured"):
        log.warning("slow_query", latency_ms=950, threshold=500)
    record = caplog.records[0]
    assert record.levelno == logging.WARNING
    assert record.latency_ms == 950


def test_error_kwargs_on_record(log, caplog):
    with caplog.at_level(logging.ERROR, logger="test.structured"):
        log.error("request_failed", status=500, path="/api/q")
    record = caplog.records[0]
    assert record.levelno == logging.ERROR
    assert record.status == 500


def test_critical_emits_at_critical_level(log, caplog):
    with caplog.at_level(logging.CRITICAL, logger="test.structured"):
        log.critical("db_down", component="postgres")
    assert caplog.records[0].levelno == logging.CRITICAL
    assert caplog.records[0].component == "postgres"


def test_exception_captures_exc_info(log, caplog):
    with caplog.at_level(logging.ERROR, logger="test.structured"):
        try:
            raise ValueError("boom")
        except ValueError:
            log.exception("unhandled", context="test")
    record = caplog.records[0]
    assert record.exc_info is not None
    assert record.exc_info[0] is ValueError
    assert record.context == "test"


def test_no_kwargs_does_not_set_extra(log, caplog):
    with caplog.at_level(logging.INFO, logger="test.structured"):
        log.info("plain_message")
    record = caplog.records[0]
    assert record.getMessage() == "plain_message"
    assert not hasattr(record, "some_field")


def test_debug_below_info_threshold_not_emitted(log, caplog):
    with caplog.at_level(logging.INFO, logger="test.structured"):
        log.debug("should_not_appear", x=1)
    assert caplog.records == []


def test_debug_emitted_at_debug_level(log, caplog):
    with caplog.at_level(logging.DEBUG, logger="test.structured"):
        log.debug("debug_event", trace_id="abc")
    assert caplog.records[0].levelno == logging.DEBUG
    assert caplog.records[0].trace_id == "abc"
