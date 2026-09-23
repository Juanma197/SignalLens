from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

from app.market_data import MarketDataRepository
from app.config import get_settings
from app.monthly_cycle import (
    CycleStages,
    dry_run_monthly_cycle,
    monthly_vintage_id,
    production_stages,
    run_monthly_cycle,
    run_production_monthly_cycle,
)
from app.config import Settings
from app.prediction_store import PredictionVintageStore


def publisher(repository: MarketDataRepository, calls: list[str]):
    def publish(_captured_at: datetime) -> str:
        calls.append("publish")
        return PredictionVintageStore(repository).publish(
            "momentum_126d",
            "1.0.0",
            pd.Timestamp("2026-09-22"),
            pd.DataFrame([{"ticker": "AAA", "rank": 1, "score": 0.25}]),
            "score",
        )

    return publish


def stages(repository: MarketDataRepository, calls: list[str]) -> CycleStages:
    def stage(name: str):
        def run(_captured_at: datetime) -> dict:
            calls.append(name)
            return {"status": "completed"}

        return run

    return CycleStages(
        prices=stage("prices"),
        filings=stage("filings"),
        fundamentals=stage("fundamentals"),
        macro=stage("macro"),
        news=stage("news"),
        publish=publisher(repository, calls),
    )


def test_monthly_cycle_runs_in_order_and_is_idempotent(tmp_path: Path) -> None:
    repository = MarketDataRepository(tmp_path / "cycle.duckdb")
    calls: list[str] = []
    cycle_stages = stages(repository, calls)
    now = datetime(2026, 9, 23, 9, tzinfo=timezone.utc)

    first = run_monthly_cycle(repository, cycle_stages, now=now)
    second = run_monthly_cycle(repository, cycle_stages, now=now)

    assert calls == ["prices", "filings", "fundamentals", "macro", "news", "publish"]
    assert first["status"] == "completed"
    assert {key: value for key, value in second.items() if key != "run_id"} == {
        "cycle_key": "2026-09",
        "status": "already_completed",
        "vintage_id": first["vintage_id"],
        "stages": {},
    }
    assert PredictionVintageStore(repository).count() == 1


def test_completed_production_month_is_read_only_no_op_without_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = MarketDataRepository(tmp_path / "cycle.duckdb")
    now = datetime(2026, 9, 23, 9, tzinfo=timezone.utc)
    first = run_monthly_cycle(repository, stages(repository, []), now=now)
    assert monthly_vintage_id("2026-09") == "5dcce39d-f98a-55b1-9010-279501142186"
    before_count = PredictionVintageStore(repository).count()
    monkeypatch.setattr(
        "app.monthly_cycle.create_backup",
        lambda *_args, **_kwargs: pytest.fail("completed month must not be backed up"),
    )
    settings = Settings(
        database_path=repository.path,
        persistent_volume_path=tmp_path,
        backup_path=tmp_path / "backups",
        sec_user_agent="SignalLens test test@example.com",
        fred_api_key="test-key",
        api_token="x" * 32,
        _env_file=None,
    )

    result = run_production_monthly_cycle(
        repository, stages(repository, []), now=now, settings=settings
    )

    assert result["status"] == "already_completed"
    assert result["vintage_id"] == first["vintage_id"]
    assert PredictionVintageStore(repository).count() == before_count == 1


def test_dry_run_does_not_mutate_database(tmp_path: Path) -> None:
    path = tmp_path / "dry-run.duckdb"
    repository = MarketDataRepository(path)
    PredictionVintageStore(repository)
    before = path.read_bytes()

    result = dry_run_monthly_cycle(
        path, now=datetime(2026, 10, 5, tzinfo=timezone.utc)
    )

    assert result["mode"] == "dry_run"
    assert result["stages"][-1] == "publish_momentum_126d"
    assert path.read_bytes() == before


def test_failed_cycle_is_recorded_and_can_be_retried(tmp_path: Path) -> None:
    repository = MarketDataRepository(tmp_path / "retry.duckdb")
    calls: list[str] = []
    cycle_stages = stages(repository, calls)
    original = cycle_stages.fundamentals
    failed_once = False

    def flaky(captured_at: datetime) -> dict:
        nonlocal failed_once
        if not failed_once:
            failed_once = True
            raise RuntimeError("temporary provider failure")
        return original(captured_at)

    cycle_stages = CycleStages(**{**cycle_stages.__dict__, "fundamentals": flaky})
    now = datetime(2026, 9, 23, 9, tzinfo=timezone.utc)

    with pytest.raises(RuntimeError, match="temporary provider failure"):
        run_monthly_cycle(repository, cycle_stages, now=now)
    result = run_monthly_cycle(repository, cycle_stages, now=now)

    assert result["status"] == "completed"
    with repository.connect() as connection:
        row = connection.execute(
            "SELECT status, vintage_id, error FROM monthly_research_cycles"
        ).fetchone()
    assert row == ("completed", result["vintage_id"], None)
    with repository.connect() as connection:
        attempts = connection.execute(
            "SELECT status, failed_stage, error_code FROM monthly_cycle_runs ORDER BY started_at"
        ).fetchall()
    assert attempts[0] == ("failed", "fundamentals", "RuntimeError")
    assert attempts[1][0] == "completed"


def test_cycle_rejects_non_momentum_publication(tmp_path: Path) -> None:
    repository = MarketDataRepository(tmp_path / "strategy.duckdb")
    cycle_stages = stages(repository, [])

    def wrong_publish(_captured_at: datetime) -> str:
        return PredictionVintageStore(repository).publish(
            "multifactor_v1", "0.1.0", pd.Timestamp("2026-09-22"),
            pd.DataFrame([{"ticker": "AAA", "rank": 1, "score": 0.5}]), "score",
        )

    cycle_stages = CycleStages(**{**cycle_stages.__dict__, "publish": wrong_publish})
    with pytest.raises(RuntimeError, match="live momentum strategy"):
        run_monthly_cycle(
            repository,
            cycle_stages,
            now=datetime(2026, 9, 23, tzinfo=timezone.utc),
        )


def test_production_publisher_recovers_existing_monthly_vintage(
    tmp_path: Path, monkeypatch
) -> None:
    repository = MarketDataRepository(tmp_path / "crash-recovery.duckdb")
    captured_at = datetime(2026, 9, 23, tzinfo=timezone.utc)
    reserved_id = monthly_vintage_id("2026-09")
    PredictionVintageStore(repository).publish(
        "momentum_126d",
        "1.0.0",
        pd.Timestamp("2026-09-22"),
        pd.DataFrame([{"ticker": "AAA", "rank": 1, "score": 0.25}]),
        "score",
        vintage_id=reserved_id,
    )
    monkeypatch.setenv("SIGNALLENS_SEC_USER_AGENT", "SignalLens test test@example.com")
    monkeypatch.setenv("SIGNALLENS_FRED_API_KEY", "test-key")
    get_settings.cache_clear()

    recovered_id = production_stages(repository).publish(captured_at)
    get_settings.cache_clear()

    assert recovered_id == reserved_id
    assert PredictionVintageStore(repository).count() == 1
