"""Fail-closed, research-only operator facade used by the web API."""
from __future__ import annotations

import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .eodhd_ingestion import EODHDClient, EODHDIngestion, EODHDLimits

LABEL = "RESEARCH ONLY — NOT INVESTMENT ADVICE"


def safe_error(code: str = "operation_failed", message: str | None = None) -> dict[str, str]:
    """Return a stable public error; never serialize exception or provider details."""
    return {"code": code, "message": message or "Research operation failed; details were redacted."}


def research_health(research_db: Path, production_db: Path, *, scheduler_enabled: bool = False,
                    configured: bool = True) -> dict[str, Any]:
    """Return process/config/path metadata only; never open or hash either database."""
    research = Path(os.path.abspath(research_db))
    production = Path(os.path.abspath(production_db))
    return {
        "label": LABEL,
        "status": "available",
        "api_available": True,
        "configuration_present": configured,
        "safe_path_resolution": research != production,
        "database_isolation_confirmed": research != production,
        "scheduler_enabled": scheduler_enabled,
        "research_database_exists": research.is_file(),
        "production_database_exists": production.is_file(),
        "production_publishing_available": False,
        "captured_at": datetime.now(timezone.utc).isoformat(),
    }


class AssessmentJobs:
    """In-process, bounded, non-persistent assessment runner with per-kind single flight."""

    def __init__(self, *, timeout_seconds: float = 120, max_workers: int = 2,
                 clock: Callable[[], float] = time.monotonic):
        self.timeout_seconds = timeout_seconds
        self._clock = clock
        self._pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="ops-assessment")
        self._jobs: dict[str, dict[str, Any]] = {}
        self._active: dict[str, str] = {}
        self._lock = threading.Lock()

    def start(self, kind: str, work: Callable[[], dict[str, Any]]) -> dict[str, Any]:
        with self._lock:
            active_id = self._active.get(kind)
            if active_id and self._jobs[active_id]["state"] in {"queued", "running"}:
                return self._public(self._jobs[active_id])
            job_id = uuid.uuid4().hex
            job = {"job_id": job_id, "kind": kind, "state": "queued", "progress": 0,
                   "started": self._clock(), "cancel_requested": False}
            self._jobs[job_id] = job
            self._active[kind] = job_id
            self._pool.submit(self._run, job_id, work)
            return self._public(job)

    def _run(self, job_id: str, work: Callable[[], dict[str, Any]]) -> None:
        with self._lock:
            job = self._jobs[job_id]
            if job["cancel_requested"]:
                job.update(state="cancelled", progress=100)
                return
            job.update(state="running", progress=10)
        try:
            result = work()
            with self._lock:
                job = self._jobs[job_id]
                if job["cancel_requested"]:
                    job.update(state="cancelled", progress=100)
                elif self._clock() - job["started"] > self.timeout_seconds:
                    job.update(state="error", progress=100,
                               error=safe_error("assessment_timeout", "Assessment exceeded its time limit."))
                else:
                    job.update(state="success", progress=100, result=result)
        except Exception:
            with self._lock:
                self._jobs[job_id].update(state="error", progress=100, error=safe_error())

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return None
            if job["state"] in {"queued", "running"} and self._clock() - job["started"] > self.timeout_seconds:
                job.update(state="error", progress=100,
                           error=safe_error("assessment_timeout", "Assessment exceeded its time limit."))
            return self._public(job)

    def cancel(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return None
            if job["state"] in {"queued", "running"}:
                job.update(cancel_requested=True, state="cancelled", progress=100)
            return self._public(job)

    @staticmethod
    def _public(job: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in job.items()
                if key in {"job_id", "kind", "state", "progress", "result", "error"}}


def build_ingestion(research_db: Path, production_db: Path, token: str,
                    *, transport: Callable[..., Any] | None = None) -> EODHDIngestion:
    return EODHDIngestion(research_db, production_db,
                          EODHDClient(token, EODHDLimits(), transport=transport))
