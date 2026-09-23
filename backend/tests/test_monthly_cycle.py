from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

from app.market_data import MarketDataRepository
from app.config import get_settings
from app.monthly_cycle import (
    CycleStages,
    monthly_vintage_id,
    production_stages,
    run_monthly_cycle,
)
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
    assert second == {
        "cycle_key": "2026-09",
        "status": "already_completed",
        "vintage_id": first["vintage_id"],
        "stages": {},
    }
    assert PredictionVintageStore(repository).count() == 1


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
