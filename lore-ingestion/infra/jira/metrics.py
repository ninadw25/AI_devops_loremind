"""Prometheus counters for the Jira ingestion pipeline.

Import-fail-safe: if prometheus_client is not installed the same names
are available as no-op stubs so all call sites work unchanged.

Usage:
    from infra.jira.metrics import JIRA_ISSUES_FETCHED, JIRA_EVENTS_EMITTED
    JIRA_ISSUES_FETCHED.labels(org="acme").inc()
"""
from __future__ import annotations

try:
    from prometheus_client import Counter, Gauge

    JIRA_ISSUES_FETCHED   = Counter("jira_issues_fetched_total",   "Total Jira issues fetched",             ["org"])
    JIRA_EVENTS_EMITTED   = Counter("jira_events_emitted_total",   "Total normalized events written",       ["org"])
    JIRA_API_ERRORS       = Counter("jira_api_errors_total",       "Total Jira API errors",                 ["org", "endpoint"])
    JIRA_PROJECTS_CRAWLED = Gauge(  "jira_projects_crawled",       "Projects crawled in last run",          ["org"])
    JIRA_OVERFLOWS        = Counter("jira_overflows_total",        "10k-window overflow events logged",     ["org", "project"])

    _PROMETHEUS_AVAILABLE = True

except ImportError:

    class _Noop:  # type: ignore[no-redef]
        def inc(self, amount: float = 1) -> None: ...
        def set(self, value: float) -> None: ...
        def labels(self, **kwargs: str) -> "_Noop": return self

    JIRA_ISSUES_FETCHED   = _Noop()
    JIRA_EVENTS_EMITTED   = _Noop()
    JIRA_API_ERRORS       = _Noop()
    JIRA_PROJECTS_CRAWLED = _Noop()
    JIRA_OVERFLOWS        = _Noop()

    _PROMETHEUS_AVAILABLE = False
