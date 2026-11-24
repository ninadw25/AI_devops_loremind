"""Control-API observability: log parsing, error classification, path safety.

The DB-reading functions are covered by running them against the real
substrate; what needs pinning here is the text/path logic, where a mistake is
either a security hole (path traversal) or an unusable dashboard (real failures
buried under DEBUG noise).

  1. a caller-supplied log name can never escape the log directory;
  2. structured lines are classified by their PARSED level, not by grepping
     the message — bare "429" matches hex claim ids and Neo4j batch payloads;
  3. unparseable lines (tracebacks) are still caught, via anchored phrases only.
"""
from pathlib import Path

import pytest

from control.observability import (ALERT_LEVELS, LOG_DIR, LOG_LINE,
                                   RAW_ALERT_PAT, read_log)


@pytest.mark.parametrize("attack", [
    "../../../../etc/passwd",
    "/etc/passwd",
    "../.env",
    "subdir/../../.env",
])
def test_log_name_cannot_escape_the_log_directory(attack):
    """The invariant that matters: whatever the caller sends, the file we
    would open resolves INSIDE the log directory. (Starlette percent-decodes
    path params before they arrive, so `%2f` tricks reduce to these.)"""
    out = read_log(attack, lines=1)
    resolved = (LOG_DIR / out["name"]).resolve()
    assert str(resolved).startswith(str(LOG_DIR.resolve())), \
        f"{attack!r} escaped to {resolved}"
    assert Path(out["name"]).name == out["name"], "name must be a bare basename"


def test_structured_line_parses_into_level_and_logger():
    ln = "[2026-07-23 14:50:56] [WARNING] [runners.extract_claims] [claims] abc failed: schema"
    m = LOG_LINE.match(ln)
    assert m, "standard logger format must parse"
    ts, level, logger_name, msg = m.groups()
    assert level.strip() == "WARNING" and logger_name == "runners.extract_claims"
    assert level.strip() in ALERT_LEVELS


def test_debug_lines_are_not_alerts_even_when_text_looks_scary():
    # A real line from neo4j.io DEBUG — the hex payload contains "429".
    ln = "[2026-07-23 14:50:56] [DEBUG] [neo4j.io] C: RUN 'MATCH (c:Claim {claim_id: 429abc}'"
    m = LOG_LINE.match(ln)
    assert m and m.group(2).strip() not in ALERT_LEVELS, \
        "level decides, not the message text"


@pytest.mark.parametrize("line", [
    "Traceback (most recent call last):",
    "botocore.errorfactory.ThrottlingException: rate exceeded",
    "HTTP/1.1 429 Too Many Requests",
    "psycopg.OperationalError: connection refused",
])
def test_raw_alert_patterns_catch_unparsed_failures(line):
    assert RAW_ALERT_PAT.search(line)


@pytest.mark.parametrize("line", [
    "HTTP Request: POST /v1/chat/completions \"HTTP/1.1 200 OK\"",
    "claim_id: 429ab3cc911a81",
    "UNWIND $batch AS item MATCH (c:Claim {claim_id: item.claim_id})",
])
def test_raw_alert_patterns_ignore_ordinary_traffic(line):
    assert not RAW_ALERT_PAT.search(line), f"false positive on: {line}"
