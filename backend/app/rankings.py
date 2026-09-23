from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd

from .evidence import EvidenceRepository
from .fred_macro import FRED_SERIES
from .fundamentals import FundamentalRepository
from .macro import MacroRepository
from .market_data import MarketDataRepository
from .prediction_store import PredictionVintageStore
from .sec_fundamentals import METRIC_CONCEPTS


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
    published_at: datetime | None = None,
    *,
    vintage_id: str | None = None,
) -> str:
    """Build and append one live ranking vintage."""
    as_of_date, ranking = build_latest_momentum_ranking(repository, top_k)
    captured_at = published_at or datetime.now(timezone.utc)
    macro_observations = MacroRepository(repository).point_in_time(captured_at)
    observed_series = {
        item["series_id"] for item in macro_observations
    }
    missing_series = [
        series_id
        for series_id in FRED_SERIES
        if series_id not in observed_series
    ]
    macro_context = {
        "captured_at": captured_at.isoformat(),
        "status": (
            "missing"
            if not macro_observations
            else "complete"
            if not missing_series
            else "partial"
        ),
        "expected_series": list(FRED_SERIES),
        "missing_series": missing_series,
        "observations": macro_observations,
    }
    fundamentals = FundamentalRepository(repository)
    fundamental_snapshots = []
    for ticker in ranking["ticker"].tolist():
        facts = fundamentals.point_in_time(ticker, captured_at)
        observed_metrics = {fact["metric"] for fact in facts}
        missing_metrics = [
            metric for metric in METRIC_CONCEPTS if metric not in observed_metrics
        ]
        fundamental_snapshots.append(
            {
                "ticker": ticker,
                "status": (
                    "missing"
                    if not facts
                    else "complete"
                    if not missing_metrics
                    else "partial"
                ),
                "expected_metrics": list(METRIC_CONCEPTS),
                "missing_metrics": missing_metrics,
                "facts": facts,
            }
        )
    fundamental_context = {
        "captured_at": captured_at.isoformat(),
        "tickers": fundamental_snapshots,
    }
    evidence = EvidenceRepository(repository)
    evidence_snapshots = []
    evidence_windows = {"filing": 90, "news": 30}
    for ticker in ranking["ticker"].tolist():
        ticker_snapshot: dict[str, Any] = {"ticker": ticker}
        for evidence_type, max_age_days in evidence_windows.items():
            availability = evidence.availability(
                ticker,
                evidence_type,
                captured_at,
                timedelta(days=max_age_days),
            )
            ticker_snapshot[evidence_type] = {
                "status": availability["status"],
                "max_age_days": max_age_days,
                "error": availability["error"],
                "items": evidence.point_in_time(
                    ticker,
                    captured_at,
                    evidence_type,
                )[:3],
            }
        evidence_snapshots.append(ticker_snapshot)
    evidence_context = {
        "captured_at": captured_at.isoformat(),
        "tickers": evidence_snapshots,
    }
    metadata: dict[str, Any] = {
        "lookback_trading_days": LOOKBACK_TRADING_DAYS,
        "top_k": top_k,
        "score_definition": "adjusted_close / adjusted_close_126_trading_days_ago - 1",
        "information_boundary": "prices through as_of_date only",
        "is_backtest": False,
        "macro_context": macro_context,
        "fundamental_context": fundamental_context,
        "evidence_context": evidence_context,
    }
    return PredictionVintageStore(repository).publish(
        STRATEGY_NAME,
        STRATEGY_VERSION,
        as_of_date,
        ranking,
        "score",
        metadata,
        vintage_id=vintage_id,
    )
