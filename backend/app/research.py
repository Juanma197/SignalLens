from __future__ import annotations

from math import sqrt

import pandas as pd

from .market_data import MarketDataRepository


FEATURE_COLUMNS = (
    "momentum_5d",
    "momentum_21d",
    "momentum_63d",
    "momentum_126d",
    "volatility_21d",
    "price_to_sma_50",
    "price_to_sma_200",
    "volume_ratio_20d",
)


def build_research_dataset(
    repository: MarketDataRepository,
    horizon_trading_days: int = 21,
) -> pd.DataFrame:
    """Build monthly point-in-time features and forward labels from stored prices."""
    if horizon_trading_days < 1:
        raise ValueError("horizon_trading_days must be positive")

    with repository.connect() as connection:
        prices = connection.execute(
            """
            SELECT ticker, trading_date, adjusted_close, volume
            FROM price_bars
            ORDER BY ticker, trading_date
            """
        ).fetch_df()

    return build_research_dataset_from_prices(prices, horizon_trading_days)


def build_research_dataset_from_prices(
    prices: pd.DataFrame,
    horizon_trading_days: int = 21,
) -> pd.DataFrame:
    """Create leakage-safe monthly observations from a daily price frame.

    Features use information available at each day's close. A prediction formed
    after that close enters at the following trading day's adjusted close and
    exits after the requested number of trading-day holding periods.
    """
    if horizon_trading_days < 1:
        raise ValueError("horizon_trading_days must be positive")

    required = {"ticker", "trading_date", "adjusted_close", "volume"}
    missing = required - set(prices.columns)
    if missing:
        raise ValueError(f"Missing price columns: {', '.join(sorted(missing))}")
    if prices.empty:
        return _empty_dataset()

    frame = prices.loc[:, sorted(required)].copy()
    frame["trading_date"] = pd.to_datetime(frame["trading_date"])
    frame = frame.sort_values(["ticker", "trading_date"]).reset_index(drop=True)

    if frame.duplicated(["ticker", "trading_date"]).any():
        raise ValueError("Duplicate ticker/trading_date rows in research input")
    if (frame["adjusted_close"] <= 0).any():
        raise ValueError("Research input contains non-positive adjusted prices")
    if (frame["volume"] < 0).any():
        raise ValueError("Research input contains negative volume")

    grouped = frame.groupby("ticker", sort=False)

    for window in (5, 21, 63, 126):
        frame[f"momentum_{window}d"] = grouped["adjusted_close"].transform(
            lambda values, periods=window: values / values.shift(periods) - 1.0
        )

    frame["volatility_21d"] = grouped["adjusted_close"].transform(
        lambda values: (values / values.shift(1) - 1.0)
        .rolling(21, min_periods=21)
        .std(ddof=1)
        * sqrt(252)
    )
    frame["price_to_sma_50"] = grouped["adjusted_close"].transform(
        lambda values: values / values.rolling(50, min_periods=50).mean() - 1.0
    )
    frame["price_to_sma_200"] = grouped["adjusted_close"].transform(
        lambda values: values / values.rolling(200, min_periods=200).mean() - 1.0
    )
    frame["volume_ratio_20d"] = grouped["volume"].transform(
        lambda values: values / values.rolling(20, min_periods=20).mean()
    )

    frame["entry_date"] = grouped["trading_date"].shift(-1)
    frame["exit_date"] = grouped["trading_date"].shift(-(horizon_trading_days + 1))
    entry_close = grouped["adjusted_close"].shift(-1)
    exit_close = grouped["adjusted_close"].shift(-(horizon_trading_days + 1))
    frame["forward_return"] = exit_close / entry_close - 1.0

    next_date = grouped["trading_date"].shift(-1)
    frame["is_month_end"] = (
        next_date.isna()
        | (frame["trading_date"].dt.to_period("M") != next_date.dt.to_period("M"))
    )

    complete_columns = [*FEATURE_COLUMNS, "entry_date", "exit_date", "forward_return"]
    dataset = frame.loc[frame["is_month_end"]].dropna(subset=complete_columns).copy()

    if dataset.empty:
        return _empty_dataset()

    dataset["universe_median_forward_return"] = dataset.groupby(
        "trading_date"
    )["forward_return"].transform("median")
    dataset["outperformed_universe"] = (
        dataset["forward_return"] > dataset["universe_median_forward_return"]
    ).astype(int)
    dataset = dataset.rename(columns={"trading_date": "as_of_date"})

    output_columns = [
        "ticker",
        "as_of_date",
        "entry_date",
        "exit_date",
        *FEATURE_COLUMNS,
        "forward_return",
        "universe_median_forward_return",
        "outperformed_universe",
    ]
    return dataset.loc[:, output_columns].sort_values(
        ["as_of_date", "ticker"]
    ).reset_index(drop=True)


def _empty_dataset() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            "ticker",
            "as_of_date",
            "entry_date",
            "exit_date",
            *FEATURE_COLUMNS,
            "forward_return",
            "universe_median_forward_return",
            "outperformed_universe",
        ]
    )
