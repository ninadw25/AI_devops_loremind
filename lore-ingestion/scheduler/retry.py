"""
scheduler/retry.py
───────────────────
Retry logic with exponential backoff, jitter, and circuit breaker patterns.
"""

from __future__ import annotations

import random
import time
from dataclasses import dataclass
from enum import Enum, auto
from functools import wraps
from typing import Any, Callable, Optional, TypeVar, Union
import logging


T = TypeVar("T")


class RetryMode(Enum):
    """Retry strategy modes."""
    FIXED = auto()
    LINEAR = auto()
    EXPONENTIAL = auto()
    DECORRELATED_JITTER = auto()  # AWS-style jitter


class CircuitState(Enum):
    """Circuit breaker states."""
    CLOSED = auto()
    OPEN = auto()
    HALF_OPEN = auto()


class RetryExhaustedError(Exception):
    """Raised when all retry attempts are exhausted."""
    pass


class CircuitBreakerOpenError(Exception):
    """Raised when circuit breaker is open."""
    pass


@dataclass(frozen=True)
class RetryPolicy:
    """Configuration for retry behavior.
    
    Args:
        max_retries: Maximum number of retry attempts
        base_delay: Initial delay between retries in seconds
        max_delay: Maximum delay cap in seconds
        mode: Backoff mode (fixed, linear, exponential, jitter)
        retryable_exceptions: Exception types to retry on (None = all)
        non_retryable: Exception types to never retry
        timeout: Overall timeout for operation including retries
        jitter: Random jitter factor (0.0-1.0) to add to delays
        on_retry: Callback function called on each retry
    """
    max_retries: int = 3
    base_delay: float = 1.0
    max_delay: float = 60.0
    mode: RetryMode = RetryMode.EXPONENTIAL
    retryable_exceptions: Optional[tuple[type[Exception], ...]] = None
    non_retryable: Optional[tuple[type[Exception], ...]] = None
    timeout: Optional[float] = None
    jitter: float = 0.1
    on_retry: Optional[Callable[[int, Exception, float], None]] = None
    
    def calculate_delay(self, attempt: int) -> float:
        """Calculate delay for a specific retry attempt."""
        if self.mode == RetryMode.FIXED:
            delay = self.base_delay
        elif self.mode == RetryMode.LINEAR:
            delay = self.base_delay * (attempt + 1)
        elif self.mode == RetryMode.EXPONENTIAL:
            delay = self.base_delay * (2 ** attempt)
        elif self.mode == RetryMode.DECORRELATED_JITTER:
            # AWS-style decorrelated jitter for better distributed backoff
            delay = random.uniform(
                self.base_delay,
                self.base_delay * (3 ** attempt) * 0.5
            )
        else:
            delay = self.exp_delay(attempt)
        
        # Apply jitter
        if self.jitter > 0:
            jitter_amount = delay * self.jitter * random.uniform(-1, 1)
            delay = delay + jitter_amount
        
        return min(delay, self.max_delay)
    
    def should_retry(self, exception: Exception) -> bool:
        """Determine if an exception should be retried."""
        # Check non-retryable first
        if self.non_retryable and isinstance(exception, self.non_retryable):
            return False
        
        # If no retryable set specified, retry on everything
        if self.retryable_exceptions is None:
            return True
        
        return isinstance(exception, self.retryable_exceptions)
    
    def execute(self, operation: Callable[..., T], *args, **kwargs) -> T:
        """Execute operation with retry logic."""
        last_exception: Optional[Exception] = None
        start_time = time.monotonic()
        
        for attempt in range(self.max_retries + 1):
            try:
                # Check timeout
                if self.timeout:
                    elapsed = time.monotonic() - start_time
                    if elapsed >= self.timeout:
                        raise RetryExhaustedError(
                            f"Operation timed out after {elapsed:.1f}s"
                        )
                
                return operation(*args, **kwargs)
                
            except Exception as e:
                last_exception = e
                
                # Don't retry on last attempt or non-retryable exceptions
                if attempt >= self.max_retries or not self.should_retry(e):
                    break
                
                # Calculate and apply delay
                delay = self.calculate_delay(attempt)
                
                if self.on_retry:
                    self.on_retry(attempt + 1, e, delay)
                
                time.sleep(delay)
        
        # All retries exhausted
        raise RetryExhaustedError(
            f"Operation failed after {self.max_retries + 1} attempts"
        ) from last_exception


class CircuitBreaker:
    """Circuit breaker pattern for handling cascading failures.
    
    Opens after `failure_threshold` failures within `window_seconds`.
    Stays open for `timeout_seconds` before entering half-open state.
    
    Usage:
        breaker = CircuitBreaker(
            failure_threshold=5,
            timeout_seconds=60,
            success_threshold=3
        )
        
        @breaker.protected
        def risky_operation():
            return api.call()
    """
    
    def __init__(
        self,
        failure_threshold: int = 5,
        success_threshold: int = 3,
        window_seconds: float = 60.0,
        timeout_seconds: float = 30.0,
        half_open_max_calls: int = 3,
    ):
        self.failure_threshold = failure_threshold
        self.success_threshold = success_threshold
        self.window_seconds = window_seconds
        self.timeout_seconds = timeout_seconds
        self.half_open_max_calls = half_open_max_calls
        
        self._state = CircuitState.CLOSED
        self._failures: list[float] = []
        self._successes = 0
        self._half_open_calls = 0
        self._last_failure_time: Optional[float] = None
        self._open_time: Optional[float] = None
    
    def _current_time(self) -> float:
        return time.monotonic()
    
    def _clean_failures(self):
        """Remove failures outside the window."""
        cutoff = self._current_time() - self.window_seconds
        self._failures = [f for f in self._failures if f > cutoff]
    
    def _should_open(self) -> bool:
        """Check if failure threshold reached."""
        self._clean_failures()
        return len(self._failures) >= self.failure_threshold
    
    def record_success(self):
        """Record a successful call."""
        if self._state == CircuitState.HALF_OPEN:
            self._successes += 1
            if self._successes >= self.success_threshold:
                self._state = CircuitState.CLOSED
                self._successes = 0
                self._half_open_calls = 0
        elif self._state == CircuitState.CLOSED:
            # Reset on success in closed state
            self._failures = []
    
    def record_failure(self):
        """Record a failed call."""
        self._failures.append(self._current_time())
        self._last_failure_time = self._current_time()
        
        if self._state == CircuitState.HALF_OPEN:
            # Any failure in half-open returns to open
            self._state = CircuitState.OPEN
            self._open_time = self._current_time()
            self._half_open_calls = 0
        elif self._state == CircuitState.CLOSED and self._should_open():
            self._state = CircuitState.OPEN
            self._open_time = self._current_time()
    
    def can_execute(self) -> bool:
        """Check if execution is allowed."""
        if self._state == CircuitState.OPEN:
            # Check if timeout has elapsed
            if self._open_time and (self._current_time() - self._open_time) > self.timeout_seconds:
                self._state = CircuitState.HALF_OPEN
                self._successes = 0
                self._half_open_calls = 0
                return True
            return False
        
        if self._state == CircuitState.HALF_OPEN:
            if self._half_open_calls < self.half_open_max_calls:
                return True
            return False
        
        return True
    
    @property
    def state(self) -> CircuitState:
        """Get current circuit state."""
        if self._state == CircuitState.OPEN:
            # Recheck timeout
            self.can_execute()
        return self._state
    
    def __call__(self, func: Callable[..., T]) -> Callable[..., T]:
        """Decorator for protected operations."""
        @wraps(func)
        def wrapper(*args, **kwargs) -> T:
            if not self.can_execute():
                raise CircuitBreakerOpenError(
                    f"Circuit breaker is {self._state.name}"
                )
            
            self._half_open_calls += 1
            try:
                result = func(*args, **kwargs)
                self.record_success()
                return result
            except Exception as e:
                self.record_failure()
                raise
        
        return wrapper


def with_retry(
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    mode: RetryMode = RetryMode.EXPONENTIAL,
    retryable: Optional[tuple[type[Exception], ...]] = None,
    non_retryable: Optional[tuple[type[Exception], ...]] = None,
    jitter: float = 0.1,
    logger: Optional[logging.Logger] = None,
):
    """Decorator to add retry logic to a function.
    
    Usage:
        @with_retry(max_retries=5, retryable=(ConnectionError, TimeoutError))
        def fetch_data():
            return api.get()
    """
    policy = RetryPolicy(
        max_retries=max_retries,
        base_delay=base_delay,
        max_delay=max_delay,
        mode=mode,
        retryable_exceptions=retryable,
        non_retryable=non_retryable,
        jitter=jitter,
    )
    
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        def wrapper(*args, **kwargs) -> T:
            def on_retry(attempt: int, error: Exception, delay: float):
                if logger:
                    logger.warning(
                        f"[{func.__name__}] Retry {attempt}/{max_retries} "
                        f"after {delay:.1f}s due to {type(error).__name__}: {error}"
                    )
            
            # Add retry callback
            policy_with_callback = RetryPolicy(
                **{k: v for k, v in policy.__dict__.items() if not k.startswith('_')},
                on_retry=on_retry,
            )
            
            return policy_with_callback.execute(func, *args, **kwargs)
        
        return wrapper
    return decorator


def classify_exception(error: Exception) -> tuple[bool, Optional[float]]:
    """Classify exception as retryable and extract retry-after if present.
    
    Returns:
        (should_retry, retry_after_seconds)
    """
    error_text = str(error).lower()
    error_type = type(error).__name__.lower()
    
    # Rate limit errors - always retry with backoff
    if "429" in error_text or "rate limit" in error_text:
        # Try to extract retry-after
        retry_after = None
        for word in error_text.split():
            if word.replace('.', '').isdigit():
                try:
                    retry_after = float(word)
                    break
                except ValueError:
                    pass
        return True, retry_after
    
    # Transient network errors - retry
    transient_errors = [
        'timeout', 'connection', 'temporarily', 'unavailable',
        'service unavailable', 'gateway', 'temp', 'broken pipe',
        'connection reset', 'dns', 'network'
    ]
    if any(err in error_text or err in error_type for err in transient_errors):
        return True, None
    
    # Authentication errors - don't retry
    if any(err in error_text for err in ['unauthorized', 'forbidden', 'auth', '401', '403']):
        return False, None
    
    # Not found errors - don't retry
    if any(err in error_text for err in ['not found', '404', 'no such']):
        return False, None
    
    # Bad request - likely a bug, don't retry
    if 'bad request' in error_text or '400' in error_text:
        return False, None
    
    # Default: retry once
    return True, None
