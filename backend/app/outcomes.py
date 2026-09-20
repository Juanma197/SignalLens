from __future__ import annotations

from typing import Any

from .market_data import MarketDataRepository
from .prediction_store import PredictionVintageStore


def evaluate_prediction_vintage(
    repository: MarketDataRepository,
    vintage_id: str,
    horizon_trading_days: int = 21,
) -> dict[str, Any]:
    """Evaluate a stored vintage from later prices without mutating predictions."""
    if horizon_trading_days < 1:
        raise ValueError("horizon_trading_days must be positive")

    vintage = PredictionVintageStore(repository).get(vintage_id)
    if vintage is None:
        raise ValueError(f"Unknown prediction vintage: {vintage_id}")

    outcomes = []
    for prediction in vintage["predictions"]:
        ticker = prediction["ticker"]
        with repository.connect() as connection:
            prices = connection.execute(
                """
                SELECT trading_date, adjusted_close
                FROM price_bars
                WHERE ticker = ? AND trading_date > ?
                ORDER BY trading_date
                """,
                [ticker, vintage["as_of_date"]],
            ).fetchall()

        required_closes = horizon_trading_days + 1
        if len(prices) < required_closes:
            outcomes.append(
                {
                    "ticker": ticker,
                    "rank": prediction["rank"],
                    "status": "pending",
                    "entry_date": prices[0][0] if prices else None,
                    "exit_date": None,
                    "realized_return": None,
                    "available_post_signal_closes": len(prices),
                    "required_post_signal_closes": required_closes,
                }
            )
            continue

        entry_date, entry_price = prices[0]
        exit_date, exit_price = prices[horizon_trading_days]
        outcomes.append(
            {
                "ticker": ticker,
                "rank": prediction["rank"],
                "status": "completed",
                "entry_date": entry_date,
                "exit_date": exit_date,
                "realized_return": float(exit_price / entry_price - 1.0),
                "available_post_signal_closes": len(prices),
                "required_post_signal_closes": required_closes,
            }
        )

    completed_returns = [
        row["realized_return"]
        for row in outcomes
        if row["realized_return"] is not None
    ]
    completed = len(completed_returns)
    return {
        "vintage_id": vintage_id,
        "as_of_date": vintage["as_of_date"],
        "horizon_trading_days": horizon_trading_days,
        "status": "completed" if completed == len(outcomes) else "pending",
        "completed_predictions": completed,
        "total_predictions": len(outcomes),
        "mean_realized_return": (
            float(sum(completed_returns) / completed) if completed else None
        ),
        "outcomes": outcomes,
    }
