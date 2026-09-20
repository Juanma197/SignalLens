from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pandas as pd
from fastapi.testclient import TestClient

from app.config import get_settings
from app.market_data import MarketDataRepository
from app.prediction_store import PredictionVintageStore
from app.rankings import (
    build_latest_momentum_ranking,
    publish_latest_momentum_ranking,
)


def repository_with_prices(path: Path) -> MarketDataRepository:
    repository = MarketDataRepository(path)
    dates = pd.bdate_range("2026-01-02", periods=140)
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
            """
            INSERT INTO price_bars
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
    return repository


def test_latest_ranking_uses_only_available_126_day_momentum(
    tmp_path: Path,
) -> None:
    repository = repository_with_prices(tmp_path / "rankings.duckdb")

    as_of_date, ranking = build_latest_momentum_ranking(repository)

    assert as_of_date == pd.Timestamp("2026-07-16")
    assert ranking["ticker"].tolist() == ["BBB", "AAA", "CCC"]
    assert ranking["rank"].tolist() == [1, 2, 3]
    assert ranking["score"].tolist() == sorted(
        ranking["score"].tolist(),
        reverse=True,
    )


def test_published_ranking_is_retrievable_as_latest(tmp_path: Path) -> None:
    repository = repository_with_prices(tmp_path / "rankings.duckdb")

    vintage_id = publish_latest_momentum_ranking(repository)
    stored = PredictionVintageStore(repository).get_latest("momentum_126d")

    assert stored["vintage_id"] == vintage_id
    assert stored["metadata"]["is_backtest"] is False
    assert stored["metadata"]["lookback_trading_days"] == 126
    assert [row["ticker"] for row in stored["predictions"]] == [
        "BBB",
        "AAA",
        "CCC",
    ]


def test_latest_ranking_api_returns_published_vintage(
    tmp_path: Path,
    monkeypatch,
) -> None:
    database_path = tmp_path / "rankings.duckdb"
    repository = repository_with_prices(database_path)
    vintage_id = publish_latest_momentum_ranking(repository)

    monkeypatch.setenv("SIGNALLENS_DATABASE_PATH", str(database_path))
    get_settings.cache_clear()
    import app.main as main

    main.settings = get_settings()
    response = TestClient(main.app).get("/api/v1/rankings/latest")
    get_settings.cache_clear()

    assert response.status_code == 200
    body = response.json()
    assert body["vintage_id"] == vintage_id
    assert body["is_demo"] is False
    assert body["strategy"] == "momentum_126d"
    assert [item["ticker"] for item in body["rankings"]] == [
        "BBB",
        "AAA",
        "CCC",
    ]
    assert "not investment advice" in body["disclaimer"].lower()
