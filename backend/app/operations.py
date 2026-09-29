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

import duckdb

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


class OperationHistory:
    """Sanitized persistent operation journal stored only in the research database."""

    STATES = {"queued", "running", "interrupted", "retryable_failed", "permanently_failed", "completed", "cancelled"}

    def __init__(self, research_db: Path, production_db: Path, *, summary_limit: int = 500):
        self.path = Path(research_db).resolve()
        if self.path == Path(production_db).resolve():
            raise ValueError("research and production databases must be distinct")
        self.summary_limit = summary_limit
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with duckdb.connect(str(self.path)) as db:
            db.execute("""CREATE TABLE IF NOT EXISTS research_operations (
                operation_id VARCHAR PRIMARY KEY, operation_type VARCHAR NOT NULL,
                state VARCHAR NOT NULL, created_at TIMESTAMPTZ NOT NULL,
                started_at TIMESTAMPTZ, finished_at TIMESTAMPTZ, progress INTEGER NOT NULL,
                summary VARCHAR, strategy_version VARCHAR, configuration_version VARCHAR,
                failure_class VARCHAR)""")

    @staticmethod
    def _safe_token(value: str | None, maximum: int = 80) -> str | None:
        if value is None:
            return None
        return "".join(c for c in str(value) if c.isalnum() or c in "._- ")[:maximum]

    def create(self, kind: str, *, strategy_version: str = "unknown", configuration_version: str = "unknown") -> str:
        operation_id = uuid.uuid4().hex
        with duckdb.connect(str(self.path)) as db:
            db.execute("INSERT INTO research_operations VALUES (?, ?, 'queued', ?, NULL, NULL, 0, NULL, ?, ?, NULL)",
                       [operation_id, self._safe_token(kind), datetime.now(timezone.utc),
                        self._safe_token(strategy_version), self._safe_token(configuration_version)])
        return operation_id

    def update(self, operation_id: str, state: str, *, progress: int, summary: str | None = None,
               failure_class: str | None = None) -> None:
        if state not in self.STATES:
            raise ValueError("invalid operation state")
        now = datetime.now(timezone.utc)
        started = now if state == "running" else None
        finished = now if state in self.STATES - {"queued", "running"} else None
        # Summaries are bounded JSON/text assembled by trusted code; failure details are classifications only.
        safe_summary = self._safe_token(summary, self.summary_limit)
        safe_failure = self._safe_token(failure_class)
        with duckdb.connect(str(self.path)) as db:
            db.execute("""UPDATE research_operations SET state=?, progress=?,
                started_at=COALESCE(started_at, ?), finished_at=COALESCE(?, finished_at),
                summary=?, failure_class=? WHERE operation_id=?""",
                [state, max(0, min(100, progress)), started, finished, safe_summary, safe_failure, operation_id])

    def recover_interrupted(self) -> int:
        with duckdb.connect(str(self.path)) as db:
            result = db.execute("""UPDATE research_operations SET state='interrupted', progress=100,
                finished_at=?, failure_class='process_restart' WHERE state IN ('queued','running') RETURNING operation_id""",
                [datetime.now(timezone.utc)]).fetchall()
        return len(result)

    def recent(self, limit: int = 20) -> list[dict[str, Any]]:
        columns = ["operation_id", "operation_type", "state", "created_at", "started_at", "finished_at",
                   "progress", "summary", "strategy_version", "configuration_version", "failure_class"]
        with duckdb.connect(str(self.path), read_only=True) as db:
            rows = db.execute("SELECT * FROM research_operations ORDER BY created_at DESC LIMIT ?", [min(max(limit, 1), 50)]).fetchall()
        return [{key: (value.isoformat() if isinstance(value, datetime) else value) for key, value in zip(columns, row)} for row in rows]


class AssessmentJobs:
    """Bounded assessment runner with optional persistent sanitized history."""

    def __init__(self, *, timeout_seconds: float = 120, max_workers: int = 2,
                 clock: Callable[[], float] = time.monotonic, history: OperationHistory | None = None):
        self.timeout_seconds = timeout_seconds
        self._clock = clock
        self._pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="ops-assessment")
        self._jobs: dict[str, dict[str, Any]] = {}
        self._active: dict[str, str] = {}
        self._lock = threading.Lock()
        self._history = history
        if history:
            history.recover_interrupted()

    def start(self, kind: str, work: Callable[[], dict[str, Any]]) -> dict[str, Any]:
        with self._lock:
            active_id = self._active.get(kind)
            if active_id and self._jobs[active_id]["state"] in {"queued", "running"}:
                return self._public(self._jobs[active_id])
            job_id = uuid.uuid4().hex
            if self._history:
                job_id = self._history.create(kind)
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
                if self._history:
                    self._history.update(job_id, "cancelled", progress=100)
                return
            job.update(state="running", progress=10)
            if self._history:
                self._history.update(job_id, "running", progress=10)
        try:
            result = work()
            with self._lock:
                job = self._jobs[job_id]
                if job["cancel_requested"]:
                    job.update(state="cancelled", progress=100)
                    if self._history:
                        self._history.update(job_id, "cancelled", progress=100)
                elif self._clock() - job["started"] > self.timeout_seconds:
                    job.update(state="error", progress=100,
                               error=safe_error("assessment_timeout", "Assessment exceeded its time limit."))
                    if self._history:
                        self._history.update(job_id, "retryable_failed", progress=100,
                                             failure_class="assessment_timeout")
                else:
                    job.update(state="success", progress=100, result=result)
                    if self._history:
                        self._history.update(job_id, "completed", progress=100, summary="operation completed")
        except Exception:
            with self._lock:
                self._jobs[job_id].update(state="error", progress=100, error=safe_error())
                if self._history:
                    self._history.update(job_id, "retryable_failed", progress=100, failure_class="operation_failed")

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return None
            if job["state"] in {"queued", "running"} and self._clock() - job["started"] > self.timeout_seconds:
                job.update(state="error", progress=100,
                           error=safe_error("assessment_timeout", "Assessment exceeded its time limit."))
                if self._history:
                    self._history.update(job_id, "retryable_failed", progress=100,
                                         failure_class="assessment_timeout")
            return self._public(job)

    def cancel(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return None
            if job["state"] in {"queued", "running"}:
                job.update(cancel_requested=True, state="cancelled", progress=100)
                if self._history:
                    self._history.update(job_id, "cancelled", progress=100)
            return self._public(job)

    @staticmethod
    def _public(job: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in job.items()
                if key in {"job_id", "kind", "state", "progress", "result", "error"}}


def build_ingestion(research_db: Path, production_db: Path, token: str,
                    *, transport: Callable[..., Any] | None = None) -> EODHDIngestion:
    return EODHDIngestion(research_db, production_db,
                          EODHDClient(token, EODHDLimits(), transport=transport))
