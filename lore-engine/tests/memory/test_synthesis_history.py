"""Verify QueryService.query and query_stream accept history_messages."""
import inspect
from services.query_service import QueryService


def test_query_accepts_history_messages():
    sig = inspect.signature(QueryService.query)
    assert "history_messages" in sig.parameters


def test_query_stream_accepts_history_messages():
    sig = inspect.signature(QueryService.query_stream)
    assert "history_messages" in sig.parameters
