from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.backtest import walk_forward_backtest
from app.research import FEATURE_COLUMNS


def make_dataset() -> pd.DataFrame:
    months = pd.date_range("2017-01-31", periods=72, freq="ME")
    tickers = ("AAA", "BBB", "CCC", "DDD", "EEE", "FFF")
    rows = []

    for month_index, as_of_date in enumerate(months):
        monthly_scores = []
        for ticker_index, ticker in enumerate(tickers):
            score = (
                0.45 * np.sin(month_index / 5.0)
                + 0.3 * ticker_index
                + 0.02 * month_index
            )
            monthly_scores.append((ticker, ticker_index, score))

        median_score = float(np.median([item[2] for item in monthly_scores]))
        for ticker, ticker_index, score in monthly_scores:
            label = int(score > median_score)
            feature_values = {
                feature: score + feature_index * 0.01
                for feature_index, feature in enumerate(FEATURE_COLUMNS)
            }
            rows.append(
                {
                    "ticker": ticker,
                    "as_of_date": as_of_date,
                    "entry_date": as_of_date + pd.offsets.BDay(1),
                    "exit_date": as_of_date + pd.offsets.BDay(22),
                    "forward_return": 0.025 + ticker_index * 0.002
                    if label
                    else -0.012 + ticker_index * 0.001,
                    "outperformed_universe": label,
                    **feature_values,
                }
            )

    return pd.DataFrame(rows)


def test_walk_forward_predictions_use_only_observable_labels() -> None:
    result = walk_forward_backtest(make_dataset(), min_training_months=36)

    assert not result.predictions.empty
    assert (
        result.predictions["training_through_exit_date"]
        < result.predictions["as_of_date"]
    ).all()
    assert result.predictions["predicted_probability"].between(0, 1).all()
    assert result.summary["prediction_months"] > 0
    assert result.summary["prediction_rows"] == len(result.predictions)


def test_each_month_has_unique_ranks_and_transaction_costs() -> None:
    result = walk_forward_backtest(
        make_dataset(),
        min_training_months=36,
        top_k=3,
        transaction_cost_bps_per_side=10,
    )

    for _, month in result.predictions.groupby("as_of_date"):
        assert sorted(month["rank"].tolist()) == list(range(1, len(month) + 1))

    cost_difference = (
        result.predictions["forward_return"]
        - result.predictions["net_forward_return"]
    )
    assert np.allclose(cost_difference, 0.002)
    assert result.summary["top_k"] == 3
    assert result.summary["transaction_cost_bps_per_side"] == 10.0


def test_backtest_arguments_and_columns_are_validated() -> None:
    dataset = make_dataset()

    with pytest.raises(ValueError, match="at least 12"):
        walk_forward_backtest(dataset, min_training_months=11)
    with pytest.raises(ValueError, match="top_k"):
        walk_forward_backtest(dataset, top_k=0)
    with pytest.raises(ValueError, match="transaction"):
        walk_forward_backtest(dataset, transaction_cost_bps_per_side=-1)
    with pytest.raises(ValueError, match="Missing backtest columns"):
        walk_forward_backtest(dataset.drop(columns=["exit_date"]))
