"""
scheduler/models.py
───────────────────
Data models for the batch sync scheduler.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum, auto
from typing import Callable, Optional, Any
import uuid


class SyncStrategy(Enum):
    """Sync strategy types."""
    INCREMENTAL = auto()  # Only fetch changes since last sync
    FULL = auto()         # Full fetch regardless of watermark
    DELTA = auto()        # Incremental but with conflict detection


class SyncStatus(Enum):
    """Current status of a sync job."""
    PENDING = auto()
    SCHEDULED = auto()
    RUNNING = auto()
    COMPLETED = auto()
    FAILED = auto()
    RATE_LIMITED = auto()
    CANCELLED = auto()


@dataclass(frozen=True)
class RateLimitConfig:
    """Rate limit configuration for a source."""
    # Token bucket settings
    requests_per_minute: int = 100
    burst_size: int = 10  # Allow burst of requests
    
    # Exponential backoff settings
    base_backoff_seconds: float = 30.0
    max_backoff_seconds: float = 600.0  # 10 minutes
    max_retries: int = 5
    
    # Rate limit response handling
    respect_retry_after: bool = True  # Honor Retry-After header
    adaptive_throttling: bool = True  # Auto-adjust based on 429 responses
    
    # Per-source specific settings
    requests_per_hour: Optional[int] = None
    daily_quota: Optional[int] = None


@dataclass(frozen=True)
class SourceConfig:
    """Configuration for a data source sync."""
    # Identification
    name: str
    source_type: str = ""  # gmail, slack, jira, gitlab
    org: str = ""
    
    # Scheduling
    sync_interval_minutes: int = 360  # Default 6 hours
    priority: int = 5  # Lower = higher priority (1-10)
    sync_strategy: SyncStrategy = SyncStrategy.INCREMENTAL
    
    # Enablement
    enabled: bool = True
    skip_on_failure: bool = False  # Continue to next source if this fails
    
    # Rate limiting
    rate_limit: RateLimitConfig = field(default_factory=RateLimitConfig)
    
    # Custom options
    options: dict[str, Any] = field(default_factory=dict)
    
    # Dependencies - must run after these sources complete
    depends_on: list[str] = field(default_factory=list)
    
    def __post_init__(self):
        if not self.source_type:
            object.__setattr__(self, 'source_type', self.name.split('_')[0])


@dataclass(frozen=True)
class SourceState:
    """Persistent state for a source sync."""
    name: str
    last_sync_at: Optional[datetime] = None
    last_success_at: Optional[datetime] = None
    last_failure_at: Optional[datetime] = None
    last_error: str = ""
    
    # Rate limit tracking
    current_rpm: float = 0.0  # Current requests per minute observed
    consecutive_429s: int = 0
    backoff_until: Optional[datetime] = None
    
    # Watermark data (source-specific)
    watermark: dict[str, Any] = field(default_factory=dict)
    
    # Statistics
    total_syncs: int = 0
    successful_syncs: int = 0
    failed_syncs: int = 0
    items_processed: int = 0


@dataclass
class SyncJob:
    """A sync job instance."""
    config: SourceConfig
    
    # Job identity
    job_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    
    # State
    status: SyncStatus = SyncStatus.PENDING
    created_at: datetime = field(default_factory=datetime.utcnow)
    scheduled_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    
    # Execution tracking
    attempt_count: int = 0
    current_backoff_seconds: float = 0.0
    
    # Results
    items_fetched: int = 0
    items_processed: int = 0
    items_failed: int = 0
    error_message: str = ""
    
    # Function to execute (set at runtime)
    execute_fn: Optional[Callable[[SyncJob], Any]] = None
    
    def mark_scheduled(self, scheduled_at: Optional[datetime] = None):
        self.status = SyncStatus.SCHEDULED
        self.scheduled_at = scheduled_at or datetime.utcnow()
    
    def mark_running(self):
        self.status = SyncStatus.RUNNING
        self.started_at = datetime.utcnow()
        self.attempt_count += 1
    
    def mark_completed(self, items_processed: int = 0):
        self.status = SyncStatus.COMPLETED
        self.completed_at = datetime.utcnow()
        self.items_processed = items_processed
    
    def mark_failed(self, error: str):
        self.status = SyncStatus.FAILED
        self.completed_at = datetime.utcnow()
        self.error_message = error
    
    def mark_rate_limited(self, backoff_seconds: float):
        self.status = SyncStatus.RATE_LIMITED
        self.current_backoff_seconds = backoff_seconds
    
    @property
    def is_ready(self) -> bool:
        """Check if job is ready to run based on state and backoff."""
        if self.status not in (SyncStatus.PENDING, SyncStatus.FAILED, SyncStatus.RATE_LIMITED):
            return False
        if self.scheduled_at and datetime.utcnow() < self.scheduled_at:
            return False
        return True
    
    @property
    def duration_seconds(self) -> float:
        """Get job duration in seconds."""
        if not self.started_at:
            return 0.0
        end = self.completed_at or datetime.utcnow()
        return (end - self.started_at).total_seconds()
    
    @property
    def should_retry(self) -> bool:
        """Check if job should be retried."""
        if self.status == SyncStatus.COMPLETED:
            return False
        return self.attempt_count < self.config.rate_limit.max_retries
