"""
scheduler/rate_limit.py
────────────────────────
Rate limiting and throttling mechanisms for API calls.

Implements:
- Token bucket algorithm for burst handling
- Sliding window rate limiter (async)
- Adaptive rate limiting based on 429 responses
- Rate limit tracking per source
"""

from __future__ import annotations

import asyncio
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional
from collections import deque


@dataclass
class TokenBucket:
    """Token bucket rate limiter for burst-friendly API throttling.
    
    Allows bursts up to `capacity` tokens, then refills at `refill_rate` 
    tokens per second. This provides smoother traffic patterns than 
    strict request-per-minute limits.
    
    Usage:
        bucket = TokenBucket(capacity=10, refill_rate=1.67)  # 100/min = 1.67/sec
        async with bucket:
            await api_call()
    """
    capacity: int = 100  # Maximum burst size
    refill_rate: float = 1.67  # Tokens per second (100/min = 1.67/sec)
    
    _tokens: float = field(default=0.0, init=False, repr=False)
    _last_refill: float = field(default_factory=time.monotonic, init=False, repr=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)
    
    def __post_init__(self):
        self._tokens = float(self.capacity)
    
    def _refill(self):
        """Add tokens based on elapsed time."""
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._tokens = min(
            self.capacity,
            self._tokens + elapsed * self.refill_rate
        )
        self._last_refill = now
    
    async def acquire(self, tokens: int = 1) -> float:
        """Acquire tokens. Returns wait time if any."""
        async with self._lock:
            self._refill()
            
            if self._tokens >= tokens:
                self._tokens -= tokens
                return 0.0
            
            # Calculate wait time
            deficit = tokens - self._tokens
            wait_time = deficit / self.refill_rate
            self._tokens = 0
            return wait_time
    
    async def __aenter__(self):
        wait = await self.acquire()
        if wait > 0:
            await asyncio.sleep(wait)
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass
    
    @property
    def tokens_available(self) -> float:
        """Check current token availability."""
        self._refill()
        return self._tokens


class SlidingWindowRateLimiter:
    """Sliding window implementation for strict rate limiting.
    
    More accurate than token bucket for exact rate compliance but
    less burst-friendly. Used for APIs with hard limits.
    """
    
    def __init__(self, max_calls: int, window_seconds: float):
        self.max_calls = max_calls
        self.window_seconds = window_seconds
        self._calls: deque[float] = deque()
        self._lock = asyncio.Lock()
    
    async def acquire(self) -> float:
        """Wait until a call is allowed. Returns wait time."""
        async with self._lock:
            now = time.monotonic()
            cutoff = now - self.window_seconds
            
            # Remove old calls outside window
            while self._calls and self._calls[0] <= cutoff:
                self._calls.popleft()
            
            if len(self._calls) >= self.max_calls:
                # Calculate wait time until oldest call exits window
                wait = self._calls[0] + self.window_seconds - now
                if wait > 0:
                    return wait
            
            self._calls.append(now)
            return 0.0
    
    async def __aenter__(self):
        wait = await self.acquire()
        if wait > 0:
            await asyncio.sleep(wait)
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass


@dataclass
class RateLimitEntry:
    """Rate limit state for a single source/endpoint."""
    # Current rate limit status
    requests_this_minute: int = 0
    requests_this_hour: int = 0
    requests_today: int = 0
    
    # Rate limit response tracking
    last_429_at: Optional[datetime] = None
    consecutive_429s: int = 0
    
    # Adaptive throttling
    current_rpm_limit: int = 0  # Adjusted based on 429s
    backoff_until: Optional[datetime] = None
    
    # Statistics
    total_requests: int = 0
    total_429s: int = 0
    
    def __post_init__(self):
        if self.current_rpm_limit == 0:
            self.current_rpm_limit = 100  # Default


class RateLimitTracker:
    """Tracks rate limits across multiple sources and provides 
    adaptive throttling based on observed 429 responses.
    
    This is the main rate limit coordinator for the scheduler.
    """
    
    def __init__(
        self,
        default_rpm: int = 100,
        backoff_multiplier: float = 2.0,
        recovery_factor: float = 1.1,
    ):
        self.default_rpm = default_rpm
        self.backoff_multiplier = backoff_multiplier
        self.recovery_factor = recovery_factor
        
        self._limits: dict[str, RateLimitEntry] = {}
        self._lock = threading.RLock()
        self._minute_window: dict[str, deque[datetime]] = {}
        self._hour_window: dict[str, deque[datetime]] = {}
    
    def _get_or_create_entry(self, source_name: str) -> RateLimitEntry:
        """Get or create rate limit entry for source."""
        if source_name not in self._limits:
            self._limits[source_name] = RateLimitEntry(
                current_rpm_limit=self.default_rpm
            )
            self._minute_window[source_name] = deque()
            self._hour_window[source_name] = deque()
        return self._limits[source_name]
    
    def record_request(self, source_name: str) -> None:
        """Record a request to track current usage."""
        now = datetime.utcnow()
        with self._lock:
            entry = self._get_or_create_entry(source_name)
            entry.total_requests += 1
            entry.requests_this_minute += 1
            entry.requests_this_hour += 1
            entry.requests_today += 1
            
            self._minute_window[source_name].append(now)
            self._hour_window[source_name].append(now)
            
            # Clean old entries
            self._clean_windows(source_name, now)
    
    def _clean_windows(self, source_name: str, now: datetime):
        """Remove expired entries from time windows."""
        minute_cutoff = now - timedelta(minutes=1)
        hour_cutoff = now - timedelta(hours=1)
        
        while (self._minute_window[source_name] and 
               self._minute_window[source_name][0] < minute_cutoff):
            self._minute_window[source_name].popleft()
            
        while (self._hour_window[source_name] and 
               self._hour_window[source_name][0] < hour_cutoff):
            self._hour_window[source_name].popleft()
    
    def record_rate_limited(
        self, 
        source_name: str,
        retry_after_seconds: Optional[int] = None,
    ) -> float:
        """Record receiving a 429 response. Returns suggested backoff."""
        now = datetime.utcnow()
        with self._lock:
            entry = self._get_or_create_entry(source_name)
            entry.last_429_at = now
            entry.consecutive_429s += 1
            entry.total_429s += 1
            
            # Adaptive throttling: reduce RPM limit
            new_limit = max(
                10,  # Minimum floor
                int(entry.current_rpm_limit / self.backoff_multiplier)
            )
            entry.current_rpm_limit = new_limit
            
            # Calculate backoff
            if retry_after_seconds:
                backoff = retry_after_seconds
            else:
                # Exponential backoff: 30s, 60s, 120s, etc.
                backoff = min(
                    300,  # Max 5 minutes
                    30 * (2 ** entry.consecutive_429s)
                )
            
            entry.backoff_until = now + timedelta(seconds=backoff)
            return backoff
    
    def record_success(self, source_name: str) -> None:
        """Record successful request to potentially recover rate."""
        with self._lock:
            entry = self._get_or_create_entry(source_name)
            
            # Gradually restore rate limit after sustained success
            if entry.consecutive_429s > 0:
                entry.consecutive_429s -= 1
            
            # Slowly increase limit back toward default
            if entry.current_rpm_limit < self.default_rpm:
                entry.current_rpm_limit = min(
                    self.default_rpm,
                    int(entry.current_rpm_limit * self.recovery_factor)
                )
    
    def get_wait_time(self, source_name: str, planned_requests: int = 1) -> float:
        """Get required wait time before next request(s)."""
        now = datetime.utcnow()
        with self._lock:
            entry = self._get_or_create_entry(source_name)
            
            # Check if in backoff period
            if entry.backoff_until and now < entry.backoff_until:
                return (entry.backoff_until - now).total_seconds()
            
            # Check RPM limit
            current_rpm = len(self._minute_window.get(source_name, []))
            if current_rpm + planned_requests > entry.current_rpm_limit:
                # Wait until oldest request exits minute window
                minute_window = self._minute_window.get(source_name, deque())
                if minute_window:
                    oldest = minute_window[0]
                    return max(0.0, 60.0 - (now - oldest).total_seconds())
            
            return 0.0
    
    def can_make_request(self, source_name: str, planned_requests: int = 1) -> bool:
        """Check if requests can be made without hitting rate limit."""
        return self.get_wait_time(source_name, planned_requests) == 0.0
    
    def get_rate_limit_status(self, source_name: str) -> dict:
        """Get current rate limit status for monitoring."""
        with self._lock:
            entry = self._limits.get(source_name, RateLimitEntry())
            return {
                "source": source_name,
                "current_rpm": entry.current_rpm_limit,
                "requests_this_minute": len(self._minute_window.get(source_name, [])),
                "consecutive_429s": entry.consecutive_429s,
                "total_429s": entry.total_429s,
                "backoff_active": (
                    entry.backoff_until is not None and 
                    datetime.utcnow() < entry.backoff_until
                ),
            }
    
    def get_all_statuses(self) -> dict[str, dict]:
        """Get rate limit status for all sources."""
        with self._lock:
            return {
                name: self.get_rate_limit_status(name) 
                for name in self._limits.keys()
            }


class RateLimitAwareClient:
    """Wraps an API client with automatic rate limit handling."""
    
    def __init__(
        self,
        source_name: str,
        tracker: RateLimitTracker,
        logger=None,
    ):
        self.source_name = source_name
        self.tracker = tracker
        self.logger = logger
    
    async def execute_with_rate_limit(
        self,
        operation: callable,
        *args,
        **kwargs
    ):
        """Execute operation with rate limit awareness."""
        # Wait for rate limit clearance
        wait_time = self.tracker.get_wait_time(self.source_name)
        if wait_time > 0:
            if self.logger:
                self.logger.debug(
                    f"[{self.source_name}] Rate limit wait: {wait_time:.1f}s"
                )
            await asyncio.sleep(wait_time)
        
        # Record the request
        self.tracker.record_request(self.source_name)
        
        try:
            result = await operation(*args, **kwargs)
            self.tracker.record_success(self.source_name)
            return result
            
        except Exception as e:
            error_str = str(e).lower()
            if "429" in error_str or "rate limit" in error_str:
                # Extract retry-after if available
                retry_after = None
                if hasattr(e, 'resp') and hasattr(e.resp, 'get'):
                    retry_after = e.resp.get('Retry-After')
                
                backoff = self.tracker.record_rate_limited(
                    self.source_name,
                    retry_after
                )
                if self.logger:
                    self.logger.warning(
                        f"[{self.source_name}] Rate limited. Backing off {backoff:.1f}s"
                    )
                raise RateLimitedError(f"Rate limited: backoff {backoff}s") from e
            raise


class RateLimitedError(Exception):
    """Raised when a request hits rate limit."""
    pass
