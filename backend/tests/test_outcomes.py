from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pandas as pd
from fastapi.testclient import TestClient

from app.config import get_settings
from app.market_data import MarketDataRepository
from app.outcomes import evaluate_prediction_vintage
from app.prediction_store import PredictionVintageStore
from app.rankings import publish_latest_momentum_ranking


def repository_with_prices(path: Path) -> tuple[MarketDataRepository, pd.DatetimeIndex]:
    repository = MarketDataRepository(path)
    dates = pd.bdate_range("2025-12-01", periods=170)
    growth = {"AAA": 0.001, "BBB": 0.002, "CCC": -0.0005}
    rows = []
    for ticker, daily_return in growth.items():
        for index, trading_date in enumerate(dates):
            price = 100.0 * (1.0 + daily_return) ** index
            rows.append(
                [
                    ticker,
                    trading_date.date(),
                    price,
                    price,
                    price,
                    price,
                    price,
                    1_000_000,
                    "test",
                    datetime(2026, 9, 20),
                ]
            )
    with repository.connect() as connection:
        connection.executemany(
            "INSERT INTO price_bars VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
    return repository, dates


def predictions() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"ticker": "BBB", "rank": 1, "score": 0.8},
            {"ticker": "AAA", "rank": 2, "score": 0.4},
            {"ticker": "CCC", "rank": 3, "score": -0.1},
        ]
    )


def test_latest_vintage_remains_pending_without_future_prices(
    tmp_path: Path,
) -> None:
    repository, _ = repository_with_prices(tmp_path / "outcomes.duckdb")
    vintage_id = publish_latest_momentum_ranking(repository)

    result = evaluate_prediction_vintage(repository, vintage_id)

    assert result["status"] == "pending"
    assert result["completed_predictions"] == 0
    assert result["mean_realized_return"] is None
    assert {row["status"] for row in result["outcomes"]} == {"pending"}
    assert {row["available_post_signal_closes"] for row in result["outcomes"]} == {0}


def test_completed_outcomes_use_next_close_and_21_return_intervals(
    tmp_path: Path,
) -> None:
    repository, dates = repository_with_prices(tmp_path / "outcomes.duckdb")
    store = PredictionVintageStore(repository)
    vintage_id = store.publish(
        "momentum_126d",
        "1.0.0",
        dates[130],
        predictions(),
        "score",
        {"is_backtest": False},
    )
    original = store.get(vintage_id)

    result = evaluate_prediction_vintage(repository, vintage_id)
    preserved = store.get(vintage_id)

    assert result["status"] == "completed"
    assert result["completed_predictions"] == 3
    assert result["outcomes"][0]["entry_date"] == dates[131].date()
    assert result["outcomes"][0]["exit_date"] == dates[152].date()
    assert abs(
        result["outcomes"][0]["realized_return"] - ((1.002**21) - 1.0)
    ) < 1e-12
    assert preserved == original


def test_latest_outcome_api_reports_pending_vintage(
    tmp_path: Path,
    monkeypatch,
) -> None:
    database_path = tmp_path / "outcomes.duckdb"
    repository, _ = repository_with_prices(database_path)
    vintage_id = publish_latest_momentum_ranking(repository)

    monkeypatch.setenv("SIGNALLENS_DATABASE_PATH", str(database_path))
    get_settings.cache_clear()
    import app.main as main

    main.settings = get_settings()
    response = TestClient(main.app).get("/api/v1/rankings/latest/outcomes")
    get_settings.cache_clear()

    assert response.status_code == 200
    body = response.json()
    assert body["vintage_id"] == vintage_id
    assert body["status"] == "pending"
    assert body["horizon_trading_days"] == 21
    assert body["completed_predictions"] == 0
