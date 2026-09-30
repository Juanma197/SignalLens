import hashlib
import time
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.config import Settings
from app.database_backup import (BackupProfile, PROFILE_REQUIRED_TABLES,
                                 restore_backup, restore_plan)
from tests.model_readiness_fixture import create_research_fixture

DECISION = datetime(2026, 9, 28, 20, tzinfo=timezone.utc)


def research_backup(path: Path) -> tuple[str, int]:
    with duckdb.connect(str(path)) as connection:
        for table in PROFILE_REQUIRED_TABLES[BackupProfile.RESEARCH]:
            connection.execute(f'CREATE TABLE "{table}" (id INTEGER)')
    payload = path.read_bytes()
    return hashlib.sha256(payload).hexdigest(), len(payload)


def staging(tmp_path: Path, **overrides) -> Settings:
    values = dict(environment="staging", staging_mode=True,
                  api_token="x" * 32, persistent_volume_path=tmp_path,
                  research_database_path=tmp_path / "research" / "research.duckdb",
                  database_path=tmp_path / "production" / "production.duckdb",
                  backup_path=tmp_path / "backups", scheduler_enabled=False,
                  _env_file=None)
    values.update(overrides)
    return Settings(**values)


def test_staging_rejects_every_write_and_does_not_call_endpoint(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "settings", staging(tmp_path))
    monkeypatch.setattr(main, "create_shadow_vintage", lambda **_: pytest.fail("endpoint called"))
    response = TestClient(main.app).post("/api/v1/operations/shadow/create", json={},
        headers={"Authorization": "Bearer " + "x" * 32})
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "staging_read_only"


def _completed_job(client: TestClient, kind: str) -> dict:
    started = client.post(
        f"/api/v1/operations/assessments/{kind}",
        params={"decision_at": DECISION.isoformat()},
        headers={"Authorization": "Bearer " + "x" * 32},
    )
    assert started.status_code == 202
    job = started.json()
    for _ in range(500):
        if job["state"] not in {"queued", "running"}:
            break
        time.sleep(.01)
        job = client.get(
            f"/api/v1/operations/assessments/jobs/{job['job_id']}",
            headers={"Authorization": "Bearer " + "x" * 32},
        ).json()
    assert job["state"] == "success", job
    return job["result"]


def test_staging_read_only_assessments_succeed_without_changing_databases(
    tmp_path, monkeypatch
):
    configured = staging(tmp_path)
    configured.research_database_path.parent.mkdir(parents=True)
    configured.database_path.parent.mkdir(parents=True)
    create_research_fixture(configured.research_database_path, periods=300, per_region=2)
    with duckdb.connect(str(configured.database_path)) as connection:
        connection.execute("CREATE TABLE production_guard(value INTEGER)")
    before = (configured.research_database_path.read_bytes(),
              configured.database_path.read_bytes())
    monkeypatch.setattr(main, "settings", configured)
    monkeypatch.setattr(main, "assessment_jobs", None)
    client = TestClient(main.app)

    unauthorized = client.post(
        "/api/v1/operations/assessments/model-readiness",
        params={"decision_at": DECISION.isoformat()},
    )
    assert unauthorized.status_code == 401
    readiness = _completed_job(client, "model-readiness")
    scoring = _completed_job(client, "research-scoring")

    assert all(item["unchanged"] for item in readiness["database_fingerprints"].values())
    assert all(item["unchanged"] for item in scoring["database_fingerprints"].values())
    assert before == (configured.research_database_path.read_bytes(),
                      configured.database_path.read_bytes())


@pytest.mark.parametrize("method,path", [
    ("post", "/api/v1/operations/incremental-refresh/execute"),
    ("post", "/api/v1/operations/shadow/plan"),
    ("post", "/api/v1/operations/shadow/create"),
    ("post", "/api/v1/operations/shadow/evaluate-matured"),
    ("post", "/api/v1/admin/monthly-cycle"),
    ("put", "/api/v1/watchlist/AAPL"),
    ("delete", "/api/v1/watchlist/AAPL"),
    ("post", "/api/v1/operations/assessments/not-read-only"),
])
def test_staging_mutation_guard_remains_fail_closed(tmp_path, monkeypatch, method, path):
    monkeypatch.setattr(main, "settings", staging(tmp_path))
    response = TestClient(main.app).request(
        method.upper(), path, json={},
        headers={"Authorization": "Bearer " + "x" * 32},
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "staging_read_only"


def test_liveness_and_missing_database_readiness_are_bounded(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "settings", staging(tmp_path))
    client = TestClient(main.app)
    assert client.get("/api/v1/health").status_code == 200
    response = client.get("/api/v1/ready")
    assert response.status_code == 503
    assert response.json() == {"status": "not_initialized", "configuration_valid": True,
        "paths_distinct": True, "volume_mounted": True,
        "research_database_exists": False, "provider_requests": False}
    summary = client.get("/api/v1/operations/summary",
                         headers={"Authorization": "Bearer " + "x" * 32}).json()
    assert summary["overall"] == "not_initialized"


def test_staging_configuration_refuses_alias_and_scheduler(tmp_path):
    common = tmp_path / "same.duckdb"
    with pytest.raises(ValueError, match="distinct"):
        staging(tmp_path, research_database_path=common, database_path=common)
    with pytest.raises(ValueError, match="scheduler"):
        staging(tmp_path, scheduler_enabled=True)


def test_research_restore_requires_plan_authorization_and_exact_artifact(tmp_path):
    source, target, production = tmp_path / "upload.duckdb", tmp_path / "research.duckdb", tmp_path / "prod.duckdb"
    sha256, byte_count = research_backup(source)
    production.write_bytes(b"production-preserved")
    production_before = production.read_bytes()
    source_before = source.read_bytes()
    plan = restore_plan(source, target, production_database_path=production)
    assert plan["automatic_restore"] is False and plan["profile"] == "research"
    with pytest.raises(PermissionError):
        restore_backup(source, target, profile=BackupProfile.RESEARCH,
                       expected_sha256=sha256, expected_byte_count=byte_count)
    result = restore_backup(source, target, authorized=True, expected_sha256=sha256,
        expected_byte_count=byte_count, production_database_path=production,
        profile=BackupProfile.RESEARCH)
    assert result == target and target.read_bytes() == source_before
    assert production.read_bytes() == production_before and source.read_bytes() == source_before
    with pytest.raises(FileExistsError):
        restore_backup(source, target, authorized=True, expected_sha256=sha256,
            expected_byte_count=byte_count, production_database_path=production,
            profile=BackupProfile.RESEARCH)


def test_research_restore_refuses_wrong_profile_corruption_and_production(tmp_path):
    wrong, destination, production = tmp_path / "wrong.duckdb", tmp_path / "target.duckdb", tmp_path / "prod.duckdb"
    with duckdb.connect(str(wrong)) as connection:
        connection.execute("CREATE TABLE price_bars(id INTEGER)")
    with pytest.raises(ValueError, match="missing required tables"):
        restore_plan(wrong, destination, production_database_path=production)
    with pytest.raises(ValueError, match="production"):
        restore_plan(production, destination, production_database_path=production)
    corrupt = tmp_path / "corrupt.duckdb"
    corrupt.write_bytes(b"not a database")
    with pytest.raises(duckdb.Error):
        restore_plan(corrupt, destination, production_database_path=production)


def test_health_responses_redact_paths_and_secrets(tmp_path, monkeypatch):
    secret = "super-secret-" + "z" * 32
    monkeypatch.setattr(main, "settings", staging(tmp_path, api_token=secret))
    client = TestClient(main.app)
    rendered = client.get("/api/v1/health").text + client.get("/api/v1/ready").text
    assert secret not in rendered and str(tmp_path) not in rendered
