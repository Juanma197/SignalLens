"""Offline scheduler primitives. No external scheduler is configured by this module."""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable


@dataclass
class SchedulerService:
    """Process-local locking/idempotency for externally triggered offline jobs."""
    enabled: bool = False
    _completed: set[str] = field(default_factory=set)
    _locks: dict[str, threading.Lock] = field(default_factory=dict)

    def run(self, job: str, scheduled_for: datetime, operation: Callable[[], Any]) -> dict[str, Any]:
        key = f"{job}:{scheduled_for.isoformat()}"
        if not self.enabled:
            return {"status": "disabled", "job": job, "mutated": False}
        lock = self._locks.setdefault(job, threading.Lock())
        if not lock.acquire(blocking=False):
            return {"status": "locked", "job": job, "mutated": False}
        try:
            if key in self._completed:
                return {"status": "already_completed", "job": job, "mutated": False}
            value = operation()
            self._completed.add(key)
            return {"status": "completed", "job": job, "result": value}
        finally:
            lock.release()

    def incremental_refresh(self, at: datetime, operation: Callable[[], Any]) -> dict[str, Any]:
        return self.run("incremental_refresh", at, operation)

    def month_end_shadow_plan(self, at: datetime, operation: Callable[[], Any]) -> dict[str, Any]:
        return self.run("month_end_shadow_plan", at, operation)

    def matured_shadow_evaluation(self, at: datetime, operation: Callable[[], Any]) -> dict[str, Any]:
        return self.run("matured_shadow_evaluation", at, operation)
