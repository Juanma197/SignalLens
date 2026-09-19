from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from pandas.testing import assert_series_equal

from app.research import FEATURE_COLUMNS, build_research_dataset_from_prices


def make_prices() -> pd.DataFrame:
    dates = pd.bdate_range("2024-01-02", periods=340)
    rows = []
    for ticker, daily_growth in (("AAA", 0.0012), ("BBB", 0.0006), ("CCC", 0.0009)):
        for index, trading_date in enumerate(dates):
            cycle = 1.0 + 0.015 * np.sin(index / 11.0 + len(ticker))
            close = 100.0 * np.exp(daily_growth * index) * cycle
            rows.append(
                {
                    "ticker": ticker,
                    "trading_date": trading_date,
                    "adjusted_close": close,
                    "volume": 1_000_000 + index * 1_000 + ord(ticker[0]) * 100,
                }
            )
    return pd.DataFrame(rows)


def test_dataset_uses_monthly_point_in_time_features_and_delayed_entry() -> None:
    prices = make_prices()
    dataset = build_research_dataset_from_prices(prices, horizon_trading_days=21)

    assert not dataset.empty
    assert not dataset[list(FEATURE_COLUMNS)].isna().any().any()
    assert set(dataset["outperformed_universe"].unique()) <= {0, 1}

    calendars = {
        ticker: list(group.sort_values("trading_date")["trading_date"])
        for ticker, group in prices.groupby("ticker")
    }
    for row in dataset.head(12).itertuples():
        calendar = calendars[row.ticker]
        position = calendar.index(row.as_of_date)
        assert row.entry_date == calendar[position + 1]
        assert row.exit_date == calendar[position + 22]

        later_same_month = [
            value
            for value in calendar
            if value > row.as_of_date and value.to_period("M") == row.as_of_date.to_period("M")
        ]
        assert later_same_month == []


def test_future_price_changes_do_not_change_features_at_prediction_time() -> None:
    prices = make_prices()
    original = build_research_dataset_from_prices(prices)
    chosen = original.iloc[len(original) // 2]
    as_of_date = chosen["as_of_date"]
    ticker = chosen["ticker"]

    changed_prices = prices.copy()
    future_mask = (
        (changed_prices["ticker"] == ticker)
        & (changed_prices["trading_date"] > as_of_date)
    )
    changed_prices.loc[future_mask, "adjusted_close"] *= 3.0
    changed = build_research_dataset_from_prices(changed_prices)

    original_row = original[
        (original["ticker"] == ticker) & (original["as_of_date"] == as_of_date)
    ].iloc[0]
    changed_row = changed[
        (changed["ticker"] == ticker) & (changed["as_of_date"] == as_of_date)
    ].iloc[0]

    assert_series_equal(
        original_row[list(FEATURE_COLUMNS)],
        changed_row[list(FEATURE_COLUMNS)],
        check_names=False,
    )


def test_invalid_research_inputs_are_rejected() -> None:
    prices = make_prices()

    duplicate = pd.concat([prices, prices.iloc[[0]]], ignore_index=True)
    with pytest.raises(ValueError, match="Duplicate"):
        build_research_dataset_from_prices(duplicate)

    with pytest.raises(ValueError, match="horizon"):
        build_research_dataset_from_prices(prices, horizon_trading_days=0)

    with pytest.raises(ValueError, match="Missing price columns"):
        build_research_dataset_from_prices(prices.drop(columns=["volume"]))
