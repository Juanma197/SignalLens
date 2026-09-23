from datetime import datetime, timedelta, timezone
import os
from pathlib import Path

import duckdb
import pandas as pd
import pytest

import app.database_backup as backup_module
import app.monthly_cycle as monthly_cycle
from app.config import Settings
from app.database_backup import create_backup, restore_backup, verify_backup
from app.market_data import MarketDataRepository
from app.monthly_cycle import CycleStages, run_production_monthly_cycle
from app.prediction_store import PredictionVintageStore


def database_with_vintage(path: Path, ticker: str = "AAA") -> MarketDataRepository:
    repository = MarketDataRepository(path)
    PredictionVintageStore(repository).publish(
        "momentum_126d", "1.0.0", pd.Timestamp("2026-08-31"),
        pd.DataFrame([{"ticker": ticker, "rank": 1, "score": 0.5}]), "score",
    )
    return repository


def production_settings(path: Path, backups: Path, **overrides) -> Settings:
    values = {
        "database_path": path,
        "persistent_volume_path": path.parent,
        "backup_path": backups,
        "sec_user_agent": "SignalLens test test@example.com",
        "fred_api_key": "test-key",
        "api_token": "x" * 32,
        "_env_file": None,
    }
    values.update(overrides)
    return Settings(**values)


def test_successful_backup_is_read_only_validated(tmp_path: Path) -> None:
    source = tmp_path / "live.duckdb"
    database_with_vintage(source)
    before = source.read_bytes()

    result = create_backup(source, tmp_path / "backups")

    assert verify_backup(result).path == result
    assert source.read_bytes() == before
    with duckdb.connect(str(result), read_only=True) as connection:
        assert connection.execute("SELECT ticker FROM prediction_records").fetchone() == ("AAA",)


def test_invalid_backup_is_rejected(tmp_path: Path) -> None:
    invalid = tmp_path / "invalid.duckdb"
    with duckdb.connect(str(invalid)) as connection:
        connection.execute("CREATE TABLE unrelated(value INTEGER)")

    with pytest.raises(ValueError, match="missing required tables"):
        verify_backup(invalid)


def test_retention_keeps_only_configured_validated_backups(tmp_path: Path) -> None:
    source = tmp_path / "live.duckdb"
    database_with_vintage(source)
    directory = tmp_path / "backups"
    start = datetime(2026, 9, 20, tzinfo=timezone.utc)
    for offset in range(4):
        create_backup(source, directory, retention_count=3,
                      now=start + timedelta(seconds=offset))

    assert len(list(directory.glob("signallens-backup-*.duckdb"))) == 3


def test_failed_validation_never_changes_live_or_publishes_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "live.duckdb"
    database_with_vintage(source)
    before = source.read_bytes()
    directory = tmp_path / "backups"

    monkeypatch.setattr(backup_module, "verify_backup",
                        lambda _path: (_ for _ in ()).throw(ValueError("invalid")))
    with pytest.raises(ValueError, match="invalid"):
        create_backup(source, directory)

    assert source.read_bytes() == before
    assert not list(directory.iterdir())


def test_failed_backup_blocks_monthly_mutation(tmp_path: Path, monkeypatch) -> None:
    repository = database_with_vintage(tmp_path / "live.duckdb")
    settings = production_settings(repository.path, tmp_path / "backups")
    calls: list[str] = []
    stage = lambda _now: calls.append("mutated") or {}
    stages = CycleStages(stage, stage, stage, stage, stage,
                         lambda _now: calls.append("published") or "id")
    monkeypatch.setattr(monthly_cycle, "create_backup",
                        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk full")))

    with pytest.raises(monthly_cycle.ProductionBackupError) as error:
        run_production_monthly_cycle(repository, stages, settings=settings)

    assert error.value.error_code == "OSError"
    assert calls == []
    with repository.connect() as connection:
        assert "monthly_research_cycles" not in {
            row[0] for row in connection.execute("SHOW TABLES").fetchall()
        }


def test_stale_backup_is_replaced_before_freshness_check_and_cycle_runs(
    tmp_path: Path,
) -> None:
    repository = database_with_vintage(tmp_path / "live.duckdb")
    backups = tmp_path / "backups"
    captured_at = datetime.now(timezone.utc)
    stale = create_backup(repository.path, backups, now=captured_at - timedelta(days=10))
    stale_timestamp = (captured_at - timedelta(days=10)).timestamp()
    os.utime(stale, (stale_timestamp, stale_timestamp))
    calls: list[str] = []
    stage = lambda _now: calls.append("stage") or {}

    def publish(_now: datetime) -> str:
        calls.append("publish")
        return PredictionVintageStore(repository).publish(
            "momentum_126d", "1.0.0", pd.Timestamp(captured_at),
            pd.DataFrame([{"ticker": "AAA", "rank": 1, "score": 0.6}]), "score",
        )

    result = run_production_monthly_cycle(
        repository,
        CycleStages(stage, stage, stage, stage, stage, publish),
        now=captured_at,
        settings=production_settings(repository.path, backups),
    )

    assert result["status"] == "completed"
    assert calls == ["stage"] * 5 + ["publish"]
    assert Path(result["backup"]) != stale


def test_non_backup_preflight_failure_blocks_backup_and_cycle_mutations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = database_with_vintage(tmp_path / "live.duckdb")
    calls: list[str] = []
    stage = lambda _now: calls.append("mutated") or {}
    monkeypatch.setattr(
        monthly_cycle,
        "create_backup",
        lambda *_args, **_kwargs: calls.append("backup") or tmp_path / "backup.duckdb",
    )

    with pytest.raises(monthly_cycle.ProductionPreflightError):
        run_production_monthly_cycle(
            repository,
            CycleStages(stage, stage, stage, stage, stage, lambda _now: "unused"),
            settings=production_settings(
                repository.path, tmp_path / "backups", fred_api_key=""
            ),
        )

    assert calls == []
    with repository.connect() as connection:
        assert "monthly_research_cycles" not in {
            row[0] for row in connection.execute("SHOW TABLES").fetchall()
        }


def test_restore_recreates_database_without_overwriting(tmp_path: Path) -> None:
    source = tmp_path / "live.duckdb"
    database_with_vintage(source, "RESTORED")
    backup = create_backup(source, tmp_path / "backups")
    restored = tmp_path / "restore" / "signal.duckdb"

    restore_backup(backup, restored)
    with duckdb.connect(str(restored), read_only=True) as connection:
        assert connection.execute("SELECT ticker FROM prediction_records").fetchone() == ("RESTORED",)
    with pytest.raises(FileExistsError):
        restore_backup(backup, restored)
