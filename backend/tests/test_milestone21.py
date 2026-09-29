from datetime import datetime, timedelta, timezone
import hashlib

import duckdb
import pytest

from app.autonomous_operations import ResearchOrchestrator, STAGES
from app.config import Settings
from app.notifications import OfflineNotificationSink
from app.operations import OperationHistory
from app.research_scheduler import SchedulerService

NOW = datetime(2026, 10, 1, tzinfo=timezone.utc)


def test_persistent_lifecycle_and_restart_recovery(tmp_path):
    research, production = tmp_path / "research.duckdb", tmp_path / "production.duckdb"
    production.write_bytes(b"production guard")
    before = hashlib.sha256(production.read_bytes()).digest()
    history = OperationHistory(research, production, summary_limit=20)
    operation = history.create("refresh", strategy_version="v1", configuration_version="cfg1")
    history.update(operation, "running", progress=10)
    assert OperationHistory(research, production).recover_interrupted() == 1
    row = history.recent()[0]
    assert row["state"] == "interrupted" and row["failure_class"] == "process_restart"
    assert hashlib.sha256(production.read_bytes()).digest() == before


def test_orchestration_order_and_fail_closed():
    calls = []
    stages = {name: (lambda stage=name: calls.append(stage) or {"status": "ok"}) for name in STAGES}
    assert ResearchOrchestrator(stages).run()["status"] == "completed"
    assert calls == list(STAGES)
    calls.clear()
    stages["coverage_validation"] = lambda: {"status": "not_ready"}
    result = ResearchOrchestrator(stages).run()
    assert result["status"] == "blocked" and result["failed_stage"] == "coverage_validation"
    assert "model_readiness" not in calls


def test_scheduler_retry_budget_idempotency_and_missed_runs():
    calls = []
    scheduler = SchedulerService(enabled=True, retry_limit=1)
    def flaky():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("safe retry")
        return "ok"
    assert scheduler.incremental_refresh(NOW, flaky)["attempts"] == 2
    assert scheduler.incremental_refresh(NOW, flaky)["status"] == "already_completed"
    assert scheduler.run("backup", NOW, lambda: None, estimated_requests=101)["status"] == "blocked"
    assert scheduler.due_runs([NOW - timedelta(days=1), NOW], NOW) == [NOW - timedelta(days=1)]


def test_notifications_are_bounded_and_redacted():
    sink = OfflineNotificationSink()
    sink.send("backup_failure", "/data/private.duckdb authorization=secret " + "x" * 1000)
    message = sink.messages[0]["message"]
    assert len(message) <= 400 and "/data" not in message and "secret" not in message


def test_staging_configuration_prevents_scheduler_writes():
    assert SchedulerService(enabled=True, staging_mode=True).backup(NOW, lambda: pytest.fail("write"))["status"] == "disabled"
    with pytest.raises(ValueError, match="staging mode"):
        Settings(staging_mode=True, scheduler_enabled=True, _env_file=None)
