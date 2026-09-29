import hashlib
from pathlib import Path

import duckdb
import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.config import Settings
from app.database_backup import (BackupProfile, PROFILE_REQUIRED_TABLES,
                                 restore_backup, restore_plan)


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
