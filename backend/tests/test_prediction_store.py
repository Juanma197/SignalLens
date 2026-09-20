from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from app.market_data import MarketDataRepository
from app.prediction_store import PredictionVintageStore


def sample_predictions() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"ticker": "AAA", "rank": 1, "score": 0.91},
            {"ticker": "BBB", "rank": 2, "score": 0.74},
            {"ticker": "CCC", "rank": 3, "score": 0.63},
        ]
    )


def test_prediction_vintages_are_append_only(tmp_path: Path) -> None:
    store = PredictionVintageStore(
        MarketDataRepository(tmp_path / "research.duckdb")
    )
    predictions = sample_predictions()

    first_id = store.publish(
        "momentum_126d",
        "1.0.0",
        date(2026, 9, 18),
        predictions,
        "score",
        {"top_k": 3, "cost_bps_per_side": 10},
    )
    second_predictions = predictions.copy()
    second_predictions.loc[0, "score"] = 0.99
    second_id = store.publish(
        "momentum_126d",
        "1.0.0",
        date(2026, 9, 18),
        second_predictions,
        "score",
        {"top_k": 3, "cost_bps_per_side": 10},
    )

    assert first_id != second_id
    assert store.count() == 2
    assert store.get(first_id)["predictions"][0]["score"] == 0.91
    assert store.get(second_id)["predictions"][0]["score"] == 0.99


def test_prediction_vintage_round_trip_preserves_provenance(
    tmp_path: Path,
) -> None:
    store = PredictionVintageStore(
        MarketDataRepository(tmp_path / "research.duckdb")
    )
    vintage_id = store.publish(
        "logistic_baseline",
        "1.0.0",
        date(2026, 7, 31),
        sample_predictions(),
        "score",
        {
            "feature_set": ["momentum_21d", "momentum_126d"],
            "training_through": "2026-06-30",
        },
    )

    stored = store.get(vintage_id)

    assert stored["strategy_name"] == "logistic_baseline"
    assert stored["strategy_version"] == "1.0.0"
    assert stored["as_of_date"] == date(2026, 7, 31)
    assert stored["metadata"]["training_through"] == "2026-06-30"
    assert [item["ticker"] for item in stored["predictions"]] == [
        "AAA",
        "BBB",
        "CCC",
    ]


def test_prediction_vintage_rejects_invalid_rankings(tmp_path: Path) -> None:
    store = PredictionVintageStore(
        MarketDataRepository(tmp_path / "research.duckdb")
    )

    duplicate_ticker = sample_predictions()
    duplicate_ticker.loc[1, "ticker"] = "AAA"
    with pytest.raises(ValueError, match="duplicate tickers"):
        store.publish(
            "momentum_126d",
            "1.0.0",
            date(2026, 9, 18),
            duplicate_ticker,
            "score",
        )

    duplicate_rank = sample_predictions()
    duplicate_rank.loc[1, "rank"] = 1
    with pytest.raises(ValueError, match="duplicate ranks"):
        store.publish(
            "momentum_126d",
            "1.0.0",
            date(2026, 9, 18),
            duplicate_rank,
            "score",
        )

    with pytest.raises(ValueError, match="empty"):
        store.publish(
            "momentum_126d",
            "1.0.0",
            date(2026, 9, 18),
            pd.DataFrame(columns=["ticker", "rank", "score"]),
            "score",
        )
