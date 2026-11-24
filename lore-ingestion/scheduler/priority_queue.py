"""
scheduler/priority_queue.py
────────────────────────────
Priority queue implementation for sync jobs with consideration for:
- Priority levels (1-10, lower = higher priority)
- Schedule times
- Dependencies
- Rate limit status
"""

from __future__ import annotations

import heapq
import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

from .models import SyncJob, SyncStatus, SourceConfig


@dataclass(order=False, frozen=True)
class QueueEntry:
    """Entry in the priority queue. Comparable by priority."""
    priority: int
    scheduled_at: datetime
    created_at: datetime
    job_id: str
    entry_data: Any = field(compare=False)  # The actual job or data
    
    def __lt__(self, other):
        # Higher importance jobs come first (lower priority number = higher importance)
        if self.priority != other.priority:
            return self.priority < other.priority
        # Earlier scheduled come first
        if self.scheduled_at != other.scheduled_at:
            return self.scheduled_at < other.scheduled_at
        # Older jobs first
        return self.created_at < other.created_at
    
    def __le__(self, other):
        return self == other or self < other
    
    def __gt__(self, other):
        return other < self
    
    def __ge__(self, other):
        return self == other or other < self


class PriorityQueue:
    """Thread-safe priority queue for sync jobs.
    
    Supports:
    - Priority-based ordering (1-10)
    - Scheduled delays (jobs only available after scheduled_at)
    - Dependency tracking (jobs wait for dependencies)
    - Rate limit awareness
    
    Note: This is not a Celery/RabbitMQ replacement but a runtime
    scheduling queue that works within a single process. For distributed
    deployments, this feeds into Celery tasks.
    """
    
    def __init__(self):
        self._heap: list[QueueEntry] = []
        self._jobs: dict[str, SyncJob] = {}
        self._lock = threading.RLock()
        self._completed_sources: set[str] = set()
        self._failed_sources: set[str] = set()
    
    def add(self, job: SyncJob) -> None:
        """Add a job to the queue."""
        with self._lock:
            entry = QueueEntry(
                priority=job.config.priority,
                scheduled_at=job.scheduled_at or datetime.utcnow(),
                created_at=job.created_at,
                job_id=job.job_id,
                entry_data=job,
            )
            heapq.heappush(self._heap, entry)
            self._jobs[job.job_id] = job
            job.mark_scheduled()
    
    def get_ready_job(self, now: Optional[datetime] = None) -> Optional[SyncJob]:
        """Get the highest priority job that's ready to execute.
        
        Returns None if:
        - No jobs available
        - Top job not yet scheduled
        - Top job has unmet dependencies
        - All jobs blocked
        """
        now = now or datetime.utcnow()
        
        with self._lock:
            # Filter ready jobs from heap
            ready = []
            delayed = []
            
            while self._heap:
                entry = heapq.heappop(self._heap)
                job = entry.entry_data
                
                # Check if job is actually ready
                if entry.scheduled_at > now:
                    # Job scheduled for future
                    delayed.append(entry)
                    continue
                
                # Check dependencies
                if not self._dependencies_met(job.config):
                    # Dependencies not met, keep in queue
                    delayed.append(entry)
                    continue
                
                # Job is ready
                ready.append(entry)
                break
            
            # Restore heap
            for entry in delayed:
                heapq.heappush(self._heap, entry)
            
            if not ready:
                return None
            
            return ready[0].entry_data
    
    def _dependencies_met(self, config: SourceConfig) -> bool:
        """Check if all dependencies are satisfied."""
        # Dependencies that must succeed
        for dep in config.depends_on:
            if dep not in self._completed_sources:
                # Dependency not yet run or failed
                if dep in self._failed_sources:
                    # Failed dependency blocks this job
                    return False
                # Still running
                return False
        return True
    
    def mark_completed(self, job: SyncJob) -> None:
        """Mark a source as completed."""
        with self._lock:
            self._completed_sources.add(job.config.name)
    
    def mark_failed(self, job: SyncJob) -> None:
        """Mark a source as failed."""
        with self._lock:
            self._failed_sources.add(job.config.name)
    
    def peek_next(self, now: Optional[datetime] = None) -> Optional[tuple[SyncJob, float]]:
        """Peek at next job and when it will be ready (seconds).
        
        Returns (job, wait_seconds) or None if queue empty.
        """
        now = now or datetime.utcnow()
        
        with self._lock:
            if not self._heap:
                return None
            
            entry = self._heap[0]
            wait = max(0, (entry.scheduled_at - now).total_seconds())
            return entry.entry_data, wait
    
    def reschedule(self, job: SyncJob, delay_seconds: float) -> None:
        """Reschedule a job after a delay (e.g., rate limit backoff)."""
        with self._lock:
            job.mark_scheduled(datetime.utcnow() + timedelta(seconds=delay_seconds))
            
            # Re-add to heap
            entry = QueueEntry(
                priority=job.config.priority,
                scheduled_at=job.scheduled_at,
                created_at=job.created_at,
                job_id=job.job_id,
                entry_data=job,
            )
            heapq.heappush(self._heap, entry)
    
    def cancel(self, job_id: str) -> bool:
        """Cancel a pending job. Returns True if cancelled."""
        with self._lock:
            if job_id not in self._jobs:
                return False
            
            job = self._jobs[job_id]
            if job.status not in (SyncStatus.PENDING, SyncStatus.SCHEDULED, SyncStatus.RATE_LIMITED):
                return False
            
            job.status = SyncStatus.CANCELLED
            
            # Remove from heap (lazy: mark as cancelled, skip on get)
            # Full heap rebuild would be O(n log n)
            return True
    
    def get_status(self) -> dict:
        """Get queue status for monitoring."""
        with self._lock:
            total = len(self._jobs)
            by_status = {}
            for job in self._jobs.values():
                status = job.status.name
                by_status[status] = by_status.get(status, 0) + 1
            
            return {
                "total_jobs": total,
                "by_status": by_status,
                "ready_candidates": len(self._heap),
                "completed_sources": list(self._completed_sources),
                "failed_sources": list(self._failed_sources),
            }
    
    def get_job(self, job_id: str) -> Optional[SyncJob]:
        """Get job by ID."""
        with self._lock:
            return self._jobs.get(job_id)
    
    def list_jobs(self, status: Optional[SyncStatus] = None) -> list[SyncJob]:
        """List jobs, optionally filtered by status."""
        with self._lock:
            jobs = list(self._jobs.values())
            if status:
                jobs = [j for j in jobs if j.status == status]
            return jobs


class DependencyResolver:
    """Resolves and validates job dependencies."""
    
    @staticmethod 
    def validate_no_cycles(configs: list[SourceConfig]) -> bool:
        """Check that source dependencies have no cycles."""
        # Build adjacency list
        graph: dict[str, list[str]] = {}
        for config in configs:
            graph[config.name] = config.depends_on
        
        # DFS cycle detection
        visited = set()
        path = set()
        
        def has_cycle(node: str) -> bool:
            if node in path:
                return True
            if node in visited:
                return False
            
            visited.add(node)
            path.add(node)
            
            for dep in graph.get(node, []):
                if has_cycle(dep):
                    return True
            
            path.remove(node)
            return False
        
        for config in configs:
            if has_cycle(config.name):
                return False
        return True
    
    @staticmethod
    def calculate_execution_order(configs: list[SourceConfig]) -> list[list[str]]:
        """Calculate parallelizable execution levels.
        
        Returns list of lists where each inner list contains
        jobs that can run in parallel.
        """
        # Build dependency graph
        dependents: dict[str, set[str]] = {c.name: set(c.depends_on) for c in configs}
        
        levels = []
        completed = set()
        remaining = set(configs)
        
        while remaining:
            level = []
            for config in list(remaining):
                if dependents[config.name].issubset(completed):
                    level.append(config.name)
            
            if not level:
                # Dependency cycle or missing dependency
                break
            
            levels.append(level)
            completed.update(level)
            remaining = {c for c in remaining if c.name not in completed}
        
        return levels


class WeightedFairQueue:
    """Weighted fair queueing for rate-limited resources.
    
    Ensures each source gets fair access when multiple sources
    contend for the same rate-limited resource.
    """
    
    def __init__(self):
        self._queues: dict[str, PriorityQueue] = {}
        self._weights: dict[str, float] = {}
        self._virtual_time: float = 0.0
        self._last_service: dict[str, float] = {}
        self._lock = threading.RLock()
    
    def register_source(
        self, 
        name: str, 
        weight: float = 1.0,
        queue: Optional[PriorityQueue] = None
    ) -> None:
        """Register a source with weight."""
        with self._lock:
            self._weights[name] = weight
            self._queues[name] = queue or PriorityQueue()
            self._last_service[name] = 0.0
    
    def enqueue(self, source: str, job: SyncJob) -> None:
        """Add job to source's queue."""
        with self._lock:
            if source not in self._queues:
                self.register_source(source)
            self._queues[source].add(job)
    
    def select_next(self) -> Optional[tuple[str, SyncJob]]:
        """Select next job using weighted fair queueing.
        
        The source with the earliest virtual finish time wins.
        """
        with self._lock:
            best_source = None
            best_finish_time = float('inf')
            
            for source, queue in self._queues.items():
                job = queue.get_ready_job()
                if job is None:
                    continue
                
                weight = self._weights.get(source, 1.0)
                # Virtual finish time = virtual start + length/weight
                # Assuming equal job length (1 unit)
                finish_time = max(
                    self._virtual_time,
                    self._last_service.get(source, 0.0)
                ) + (1.0 / weight)
                
                if finish_time < best_finish_time:
                    best_finish_time = finish_time
                    best_source = source
            
            if best_source is None:
                return None
            
            job = self._queues[best_source].get_ready_job()
            self._last_service[best_source] = best_finish_time
            self._virtual_time = best_finish_time
            
            return best_source, job
