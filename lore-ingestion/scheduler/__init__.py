"""
scheduler — Batch Sync Scheduler with Rate Limit Handling
─────────────────────────────────────────────────────

Periodically syncs data from external sources (Gmail, Slack, Jira, GitLab)
while respecting API rate limits and handling failures gracefully.

Building blocks (composed directly by connectors/runners; there is no
top-level orchestrator class yet):
    - TokenBucket / SlidingWindowRateLimiter: rate-limit governors
    - RateLimitTracker: monitors and adapts to API rate-limit (429) responses
    - PriorityQueue / WeightedFairQueue: order/share work across sources
    - RetryPolicy: exponential backoff with jitter for transient failures
    - SourceConfig / SyncJob / RateLimitConfig: config + job data models

Usage:
    from scheduler import RateLimitTracker, RetryPolicy

    tracker = RateLimitTracker(source="jira", requests_per_minute=...)
    policy = RetryPolicy(max_retries=5)
"""

from .rate_limit import (
    TokenBucket,
    SlidingWindowRateLimiter,
    RateLimitTracker,
    RateLimitAwareClient,
    RateLimitedError,
)
from .priority_queue import PriorityQueue, DependencyResolver, WeightedFairQueue
from .retry import (
    RetryPolicy,
    RetryMode,
    CircuitBreaker,
    CircuitState,
    RetryExhaustedError,
    CircuitBreakerOpenError,
    with_retry,
    classify_exception,
)
from .models import (
    SyncStrategy,
    SyncStatus,
    RateLimitConfig,
    SourceConfig,
    SourceState,
    SyncJob,
)

__all__ = [
    # rate_limit
    "TokenBucket",
    "SlidingWindowRateLimiter",
    "RateLimitTracker",
    "RateLimitAwareClient",
    "RateLimitedError",
    # priority_queue
    "PriorityQueue",
    "DependencyResolver",
    "WeightedFairQueue",
    # retry
    "RetryPolicy",
    "RetryMode",
    "CircuitBreaker",
    "CircuitState",
    "RetryExhaustedError",
    "CircuitBreakerOpenError",
    "with_retry",
    "classify_exception",
    # models
    "SyncStrategy",
    "SyncStatus",
    "RateLimitConfig",
    "SourceConfig",
    "SourceState",
    "SyncJob",
]
