from datetime import datetime, timedelta, timezone
import hashlib

import duckdb
import pytest
from fastapi.testclient import TestClient

import app.main as main
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


def test_operator_summary_uses_coverage_when_operation_journal_is_empty(tmp_path, monkeypatch):
    research, production = tmp_path / "research.duckdb", tmp_path / "production.duckdb"
    production.write_bytes(b"production guard")
    with duckdb.connect(str(research)) as db:
        db.execute("CREATE TABLE security_master_retrievals(retrieval_id VARCHAR, retrieved_at TIMESTAMP, status VARCHAR)")
        db.execute("CREATE TABLE security_listings(retrieval_id VARCHAR, security_id VARCHAR, qualified_symbol VARCHAR, primary_exchange VARCHAR, currency VARCHAR, instrument_type VARCHAR, active BOOLEAN)")
        db.execute("CREATE TABLE global_price_observations(qualified_symbol VARCHAR, trading_date DATE, currency VARCHAR, exchange VARCHAR, retrieved_at TIMESTAMP, status VARCHAR)")
        db.execute("CREATE TABLE global_fx_observations(base_currency VARCHAR, quote_currency VARCHAR, observed_on DATE, rate DOUBLE, retrieved_at TIMESTAMP)")
        db.execute("CREATE TABLE global_ingestion_runs(run_id VARCHAR, started_at TIMESTAMP, finished_at TIMESTAMP, status VARCHAR, attempted INTEGER, completed INTEGER, failed INTEGER, report_json VARCHAR)")
        db.execute("CREATE TABLE eodhd_ingestion_checkpoints(stage VARCHAR, qualified_symbol VARCHAR, status VARCHAR, error_code VARCHAR, updated_at TIMESTAMP)")
        db.execute("CREATE TABLE research_operations(operation_id VARCHAR, operation_type VARCHAR, state VARCHAR, created_at TIMESTAMPTZ, started_at TIMESTAMPTZ, finished_at TIMESTAMPTZ, progress INTEGER, summary VARCHAR, strategy_version VARCHAR, configuration_version VARCHAR, failure_class VARCHAR)")
        db.execute("INSERT INTO security_master_retrievals VALUES ('r1', '2026-09-28', 'completed')")
        listings = [("r1", f"s{i}", f"S{i}.US", "US", "USD", "common_stock", True) for i in range(500)]
        db.executemany("INSERT INTO security_listings VALUES (?,?,?,?,?,?,?)", listings)
        db.executemany("INSERT INTO eodhd_ingestion_checkpoints VALUES ('prices', ?, 'completed', NULL, '2026-09-28')", [(f"S{i}.US",) for i in range(499)])
        db.execute("INSERT INTO eodhd_ingestion_checkpoints VALUES ('prices', 'S499.US', 'failed', 'invalid_provider_payload', '2026-09-28')")
        db.execute("INSERT INTO global_price_observations VALUES ('S0.US','2026-09-25','USD','US','2026-09-28','available')")
        db.execute("INSERT INTO global_fx_observations VALUES ('USD','GBP','2026-09-25',.75,'2026-09-28')")
        db.execute("INSERT INTO global_ingestion_runs VALUES ('run','2026-09-28','2026-09-28','partial',500,499,1,'{\"pending\":0}')")
    configured = Settings(database_path=production, research_database_path=research,
                          backup_path=tmp_path / "backups", _env_file=None)
    monkeypatch.setattr(main, "settings", configured)
    before = research.read_bytes(), production.read_bytes()

    response = TestClient(main.app).get("/api/v1/operations/summary")

    assert response.status_code == 200
    body = response.json()
    assert body["counts"] == {"selected": 500, "model_ready": "not_assessed", "withheld": "not_assessed"}
    assert body["security_progress"] == {"attempted": 500, "completed": 499, "pending": 0,
        "actual_failed": 1, "permanently_failed": 1, "retryable_or_other_failed": 0}
    assert body["latest_price_date"] == "2026-09-25"
    assert body["latest_fx_date"] == "2026-09-25"
    assert body["journal_evidence"] == "not_recorded"
    assert body["readiness"] == body["scoring"] == "not_assessed"
    assert before == (research.read_bytes(), production.read_bytes())
