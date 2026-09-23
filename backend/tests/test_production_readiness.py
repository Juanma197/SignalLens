from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.market_data import MarketDataRepository
from app.prediction_store import PredictionVintageStore
from app.production_readiness import run_preflight
import app.production_readiness as readiness


def settings_for(path: Path, volume: Path, backup: Path, **overrides) -> Settings:
    values = dict(
        database_path=path,
        persistent_volume_path=volume,
        backup_path=backup,
        sec_user_agent="SignalLens ops ops@example.com",
        fred_api_key="not-reported",
        api_token="x" * 32,
        _env_file=None,
    )
    values.update(overrides)
    return Settings(**values)


def initialized_database(path: Path) -> None:
    PredictionVintageStore(MarketDataRepository(path))


def test_preflight_reports_missing_secrets_without_values(tmp_path: Path) -> None:
    path = tmp_path / "volume" / "signal.duckdb"
    initialized_database(path)
    backup = tmp_path / "backup.duckdb"
    backup.touch()
    report = run_preflight(settings_for(
        path, path.parent, backup, sec_user_agent="", fred_api_key="", api_token=None
    ))
    secrets = next(item for item in report["checks"] if item["name"] == "required_secrets")
    assert secrets["status"] == "fail"
    assert "SIGNALLENS_FRED_API_KEY" in secrets["detail"]
    assert "not-reported" not in str(report)


def test_preflight_rejects_missing_database_and_volume(tmp_path: Path) -> None:
    path = tmp_path / "missing-volume" / "signal.duckdb"
    backup = tmp_path / "backup.duckdb"
    backup.touch()
    report = run_preflight(settings_for(path, path.parent, backup))
    assert report["status"] == "not_ready"
    failures = {item["name"] for item in report["checks"] if item["status"] == "fail"}
    assert {"database_exists", "persistent_volume", "database_readable"} <= failures


def test_preflight_rejects_unwritable_volume(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "volume" / "signal.duckdb"
    initialized_database(path)
    backup = tmp_path / "backup.duckdb"
    backup.touch()

    def deny_write(*_args, **_kwargs):
        raise PermissionError("denied")

    monkeypatch.setattr(readiness.tempfile, "mkstemp", deny_write)
    report = run_preflight(settings_for(path, path.parent, backup))
    volume = next(item for item in report["checks"] if item["name"] == "persistent_volume")
    assert volume["status"] == "fail"
    assert "PermissionError" in volume["detail"]


def test_preflight_requires_a_recent_backup(tmp_path: Path) -> None:
    path = tmp_path / "volume" / "signal.duckdb"
    initialized_database(path)
    report = run_preflight(settings_for(path, path.parent, tmp_path / "no-backups"),
                           now=datetime(2026, 10, 5, tzinfo=timezone.utc))
    backup = next(item for item in report["checks"] if item["name"] == "latest_backup")
    assert backup["status"] == "fail"


def test_admin_trigger_refuses_to_create_a_missing_database(
    tmp_path: Path, monkeypatch
) -> None:
    import app.main as main

    token = "x" * 32
    path = tmp_path / "volume" / "missing.duckdb"
    path.parent.mkdir()
    monkeypatch.setattr(
        main,
        "settings",
        settings_for(path, path.parent, tmp_path / "missing-backup", api_token=token),
    )

    response = TestClient(main.app).post(
        "/api/v1/admin/monthly-cycle",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 503
    assert not path.exists()
