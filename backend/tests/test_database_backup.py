from datetime import datetime, timedelta, timezone
import os
from pathlib import Path

import duckdb
import pandas as pd
import pytest

import app.database_backup as backup_module
import app.monthly_cycle as monthly_cycle
from app.config import Settings
from app.database_backup import (backup_status, create_backup,
                                 create_initial_research_backup, restore_backup,
                                 verify_backup, BackupProfile)
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


def provider_shaped_research_database(path: Path) -> None:
    """Minimal initialized EODHD research contract, deliberately no production tables."""
    tables = {
        "security_master_retrievals": "retrieval_id VARCHAR",
        "security_listings": "retrieval_id VARCHAR, qualified_symbol VARCHAR",
        "global_price_observations": "qualified_symbol VARCHAR, trading_date DATE",
        "global_fx_observations": "base_currency VARCHAR, observed_on DATE",
        "global_corporate_actions": "qualified_symbol VARCHAR, ex_date DATE",
        "global_ingestion_runs": "run_id VARCHAR, status VARCHAR",
        "global_ingestion_failures": "run_id VARCHAR, error_code VARCHAR",
        "eodhd_ingestion_checkpoints": "stage VARCHAR, qualified_symbol VARCHAR",
        "eodhd_catalogue_validations": "validated_at TIMESTAMP, status VARCHAR",
        "research_operations": "operation_id VARCHAR, state VARCHAR",
    }
    with duckdb.connect(str(path)) as connection:
        for table, columns in tables.items():
            connection.execute(f'CREATE TABLE "{table}" ({columns})')


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
                        lambda _path, **_kwargs: (_ for _ in ()).throw(ValueError("invalid")))
    with pytest.raises(ValueError, match="invalid"):
        create_backup(source, directory)

    assert source.read_bytes() == before
    assert not list(directory.iterdir())


def test_windows_publication_skips_only_unsupported_directory_open(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source database" / "research database.duckdb"
    source.parent.mkdir()
    provider_shaped_research_database(source)
    directory = tmp_path / "managed backups with spaces"
    real_open = backup_module.os.open
    opened: list[Path] = []

    def windows_open(path, flags, *args, **kwargs):
        opened_path = Path(path)
        opened.append(opened_path)
        if opened_path == directory.resolve():
            raise PermissionError(13, "Windows does not permit os.open on directories")
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(backup_module, "_IS_WINDOWS", True)
    monkeypatch.setattr(backup_module.os, "open", windows_open)

    result = create_backup(source, directory, profile=BackupProfile.RESEARCH)

    assert result.is_file()
    assert directory.resolve() not in opened
    assert any(path.suffix == ".tmp" for path in opened)
    assert result in opened
    assert verify_backup(result, profile=BackupProfile.RESEARCH).path == result


def test_posix_publication_fsyncs_parent_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "live.duckdb"
    database_with_vintage(source)
    directory = tmp_path / "backups"
    real_open = backup_module.os.open
    opened: list[Path] = []

    def recording_open(path, flags, *args, **kwargs):
        opened.append(Path(path))
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(backup_module, "_IS_WINDOWS", False)
    monkeypatch.setattr(backup_module.os, "open", recording_open)

    create_backup(source, directory)

    assert directory.resolve() in opened


def test_post_publication_failure_removes_only_managed_artifact_and_temporary_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "research.duckdb"
    provider_shaped_research_database(source)
    directory = tmp_path / "backups"
    directory.mkdir()
    manual = directory / "manual backup.duckdb"
    manual.write_bytes(b"operator-owned")
    real_verify = backup_module.verify_backup
    verification_count = 0

    def fail_published_verification(path: Path, **kwargs):
        nonlocal verification_count
        verification_count += 1
        if verification_count == 2:
            raise OSError("published-file read failed")
        return real_verify(path, **kwargs)

    monkeypatch.setattr(backup_module, "verify_backup", fail_published_verification)

    with pytest.raises(OSError, match="published-file read failed"):
        create_backup(source, directory, profile=BackupProfile.RESEARCH)

    assert manual.read_bytes() == b"operator-owned"
    assert list(directory.iterdir()) == [manual]


def test_file_permission_failure_is_not_treated_as_windows_directory_limitation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "live.duckdb"
    database_with_vintage(source)
    directory = tmp_path / "backups"
    real_open = backup_module.os.open

    def deny_temporary_file(path, flags, *args, **kwargs):
        if Path(path).suffix == ".tmp":
            raise PermissionError(13, "temporary file denied", str(path))
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(backup_module, "_IS_WINDOWS", True)
    monkeypatch.setattr(backup_module.os, "open", deny_temporary_file)

    with pytest.raises(PermissionError, match="temporary file denied"):
        create_backup(source, directory)

    assert list(directory.iterdir()) == []


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


def test_backup_status_distinguishes_configuration_and_validation(tmp_path: Path) -> None:
    directory = tmp_path / "backups"
    assert backup_status(None, configured=False)["status"] == "not_configured"
    assert backup_status(directory)["status"] == "missing"
    directory.mkdir()
    invalid_managed = directory / "signallens-backup-manual.duckdb"
    with duckdb.connect(str(invalid_managed)) as connection:
        connection.execute("CREATE TABLE unrelated(value INTEGER)")
    assert backup_status(directory)["status"] == "unvalidated"
    invalid_managed.unlink()
    source = tmp_path / "research.duckdb"
    provider_shaped_research_database(source)
    create_initial_research_backup(source, tmp_path / "production.duckdb", directory,
                                   authorized=True)
    assert backup_status(directory)["status"] == "validated"


def test_initial_research_backup_requires_authorization_and_preserves_manual_files(tmp_path: Path) -> None:
    research = tmp_path / "research.duckdb"
    production = tmp_path / "production.duckdb"
    directory = tmp_path / "backups"
    provider_shaped_research_database(research)
    database_with_vintage(production, "PRODUCTION")
    directory.mkdir()
    manual = directory / "manual-backup.duckdb"
    manual.write_bytes(b"operator-owned")
    research_before, production_before = research.read_bytes(), production.read_bytes()

    with pytest.raises(PermissionError):
        create_initial_research_backup(research, production, directory)
    result = create_initial_research_backup(
        research, production, directory, authorized=True
    )

    assert manual.read_bytes() == b"operator-owned"
    assert research.read_bytes() == research_before
    assert production.read_bytes() == production_before
    verification = verify_backup(result, profile=BackupProfile.RESEARCH)
    assert verification.byte_count == result.stat().st_size
    assert verification.sha256 == backup_module.hashlib.sha256(result.read_bytes()).hexdigest()
    assert backup_status(directory)["status"] == "validated"
    with duckdb.connect(str(result), read_only=True) as connection:
        assert "prediction_records" not in {
            row[0] for row in connection.execute("SHOW TABLES").fetchall()
        }
    with pytest.raises(FileExistsError):
        create_initial_research_backup(research, production, directory, authorized=True)


def test_invalid_research_database_fails_closed_without_artifacts(tmp_path: Path) -> None:
    research = tmp_path / "corrupt-research.duckdb"
    production = tmp_path / "production.duckdb"
    backups = tmp_path / "backups"
    research.write_bytes(b"not a duckdb database")
    database_with_vintage(production, "PRODUCTION")
    before_research, before_production = research.read_bytes(), production.read_bytes()

    with pytest.raises(duckdb.Error):
        create_initial_research_backup(research, production, backups, authorized=True)

    assert research.read_bytes() == before_research
    assert production.read_bytes() == before_production
    assert list(backups.iterdir()) == []
