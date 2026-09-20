from __future__ import annotations

from typing import Any

import pandas as pd

from .market_data import MarketDataRepository
from .prediction_store import PredictionVintageStore


STRATEGY_NAME = "momentum_126d"
STRATEGY_VERSION = "1.0.0"
LOOKBACK_TRADING_DAYS = 126


def build_latest_momentum_ranking(
    repository: MarketDataRepository,
    top_k: int = 3,
) -> tuple[pd.Timestamp, pd.DataFrame]:
    """Rank the latest common market snapshot using past adjusted prices only."""
    if top_k < 1:
        raise ValueError("top_k must be positive")

    with repository.connect() as connection:
        prices = connection.execute(
            """
            SELECT ticker, trading_date, adjusted_close
            FROM price_bars
            ORDER BY ticker, trading_date
            """
        ).fetch_df()

    if prices.empty:
        raise ValueError("No market data is available for ranking")

    prices["trading_date"] = pd.to_datetime(prices["trading_date"])
    prices = prices.sort_values(["ticker", "trading_date"]).reset_index(drop=True)
    prices["score"] = prices.groupby("ticker")["adjusted_close"].transform(
        lambda values: values / values.shift(LOOKBACK_TRADING_DAYS) - 1.0
    )

    latest_date = prices["trading_date"].max()
    snapshot = prices.loc[
        prices["trading_date"].eq(latest_date),
        ["ticker", "score"],
    ].dropna()

    if len(snapshot) < top_k:
        raise ValueError(
            f"Only {len(snapshot)} tickers have complete data at {latest_date.date()}"
        )

    ranking = (
        snapshot.sort_values(["score", "ticker"], ascending=[False, True])
        .head(top_k)
        .reset_index(drop=True)
    )
    ranking["rank"] = range(1, len(ranking) + 1)
    return latest_date, ranking.loc[:, ["ticker", "rank", "score"]]


def publish_latest_momentum_ranking(
    repository: MarketDataRepository,
    top_k: int = 3,
) -> str:
    """Build and append one live ranking vintage."""
    as_of_date, ranking = build_latest_momentum_ranking(repository, top_k)
    metadata: dict[str, Any] = {
        "lookback_trading_days": LOOKBACK_TRADING_DAYS,
        "top_k": top_k,
        "score_definition": "adjusted_close / adjusted_close_126_trading_days_ago - 1",
        "information_boundary": "prices through as_of_date only",
        "is_backtest": False,
    }
    return PredictionVintageStore(repository).publish(
        STRATEGY_NAME,
        STRATEGY_VERSION,
        as_of_date,
        ranking,
        "score",
        metadata,
    )
