"""Explicit UTC, disabled-by-default scheduler primitives with no network dependency."""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

JOB_SCHEDULES_UTC = {
    "routine_freshness": "15 06 * * *",
    "incremental_refresh": "30 06 * * 1-5",
    "month_end_shadow_plan": "00 18 28-31 * *",
    "matured_shadow_evaluation": "00 19 * * 1-5",
    "backup": "00 05 * * *",
}


@dataclass
class SchedulerService:
    enabled: bool = False
    staging_mode: bool = False
    timeout_seconds: float = 120
    retry_limit: int = 2
    request_budget: int = 100
    _completed: set[str] = field(default_factory=set)
    _locks: dict[str, threading.Lock] = field(default_factory=dict)

    def run(self, job: str, scheduled_for: datetime, operation: Callable[[], Any], *, estimated_requests: int = 0) -> dict[str, Any]:
        if scheduled_for.tzinfo is None or scheduled_for.utcoffset() is None:
            return {"status": "rejected", "job": job, "reason": "utc_timestamp_required", "mutated": False}
        scheduled_for = scheduled_for.astimezone(timezone.utc)
        key = f"{job}:{scheduled_for.isoformat()}"
        if not self.enabled or self.staging_mode:
            return {"status": "disabled", "job": job, "mutated": False}
        if job not in JOB_SCHEDULES_UTC or estimated_requests > self.request_budget:
            return {"status": "blocked", "job": job, "mutated": False}
        lock = self._locks.setdefault(job, threading.Lock())
        if not lock.acquire(blocking=False):
            return {"status": "locked", "job": job, "mutated": False}
        try:
            if key in self._completed:
                return {"status": "already_completed", "job": job, "mutated": False}
            for attempt in range(self.retry_limit + 1):
                pool = ThreadPoolExecutor(max_workers=1)
                future = pool.submit(operation)
                try:
                    value = future.result(timeout=self.timeout_seconds)
                    self._completed.add(key)
                    pool.shutdown(wait=False, cancel_futures=True)
                    return {"status": "completed", "job": job, "result": value, "attempts": attempt + 1}
                except TimeoutError:
                    future.cancel()
                    pool.shutdown(wait=False, cancel_futures=True)
                except Exception:
                    pool.shutdown(wait=False, cancel_futures=True)
                if attempt == self.retry_limit:
                    return {"status": "retry_limit_reached", "job": job, "mutated": False,
                            "attempts": attempt + 1}
        finally:
            lock.release()

    def due_runs(self, scheduled: list[datetime], now: datetime) -> list[datetime]:
        """Return uncompleted missed occurrences oldest-first for explicit recovery."""
        return [at for at in sorted(scheduled) if at <= now and not any(k.endswith(at.astimezone(timezone.utc).isoformat()) for k in self._completed)]

    def routine_freshness(self, at, operation): return self.run("routine_freshness", at, operation)
    def incremental_refresh(self, at, operation): return self.run("incremental_refresh", at, operation)
    def month_end_shadow_plan(self, at, operation): return self.run("month_end_shadow_plan", at, operation)
    def matured_shadow_evaluation(self, at, operation): return self.run("matured_shadow_evaluation", at, operation)
    def backup(self, at, operation): return self.run("backup", at, operation)
