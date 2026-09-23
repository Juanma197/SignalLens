from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

import pandas as pd


REQUIRED_FUNDAMENTAL_METRICS = (
    "revenue",
    "net_income",
    "cash",
    "liabilities",
)


@dataclass(frozen=True)
class ScoredBacktestResult:
    predictions: pd.DataFrame
    summary: dict[str, Any]


def audit_fundamental_coverage(
    fundamental_facts: pd.DataFrame,
    evaluation_dates: Iterable[pd.Timestamp],
    tickers: Iterable[str],
) -> pd.DataFrame:
    """Measure what SignalLens had actually retrieved by each vintage date."""
    dates = sorted(pd.to_datetime(list(evaluation_dates)))
    universe = tuple(sorted(set(tickers)))
    denominator = len(universe) * len(REQUIRED_FUNDAMENTAL_METRICS)
    if not dates:
        return pd.DataFrame(
            columns=[
                "as_of_date",
                "available_factor_slots",
                "required_factor_slots",
                "coverage_rate",
                "complete_tickers",
            ]
        )

    required = {
        "ticker", "metric", "available_at", "retrieved_at", "period_end"
    }
    missing = required.difference(fundamental_facts.columns)
    if missing and not fundamental_facts.empty:
        raise ValueError(f"Missing fundamental columns: {sorted(missing)}")

    facts = fundamental_facts.copy()
    if not facts.empty:
        facts["available_at"] = pd.to_datetime(facts["available_at"], utc=True)
        facts["retrieved_at"] = pd.to_datetime(facts["retrieved_at"], utc=True)
        facts["period_end"] = pd.to_datetime(facts["period_end"])
        facts = facts.loc[
            facts["ticker"].isin(universe)
            & facts["metric"].isin(REQUIRED_FUNDAMENTAL_METRICS)
        ]

    rows = []
    for value in dates:
        boundary = pd.Timestamp(value)
        if boundary.tzinfo is None:
            boundary_utc = boundary.tz_localize("UTC")
        else:
            boundary_utc = boundary.tz_convert("UTC")
        visible = facts.loc[
            facts["available_at"].le(boundary_utc)
            & facts["retrieved_at"].le(boundary_utc)
        ]
        latest = visible.sort_values(
            ["ticker", "metric", "available_at", "retrieved_at", "period_end"],
            ascending=[True, True, False, False, False],
        ).drop_duplicates(["ticker", "metric"])
        counts = latest.groupby("ticker")["metric"].nunique()
        available = int(counts.sum())
        rows.append(
            {
                "as_of_date": boundary,
                "available_factor_slots": available,
                "required_factor_slots": denominator,
                "coverage_rate": available / denominator if denominator else 0.0,
                "complete_tickers": int(
                    counts.eq(len(REQUIRED_FUNDAMENTAL_METRICS)).sum()
                ),
            }
        )
    return pd.DataFrame(rows)


def multifactor_score_backtest(
    scored_vintages: pd.DataFrame,
    *,
    top_k: int = 3,
    transaction_cost_bps_per_side: float = 10.0,
) -> ScoredBacktestResult:
    """Evaluate already point-in-time-safe composite scores."""
    if top_k < 1:
        raise ValueError("top_k must be positive")
    if transaction_cost_bps_per_side < 0:
        raise ValueError("transaction costs cannot be negative")
    required = {
        "ticker",
        "as_of_date",
        "entry_date",
        "exit_date",
        "forward_return",
        "composite_score",
        "eligible",
    }
    missing = required.difference(scored_vintages.columns)
    if missing:
        raise ValueError(f"Missing scored-vintage columns: {sorted(missing)}")
    if scored_vintages.empty:
        return ScoredBacktestResult(
            pd.DataFrame(),
            {"prediction_rows": 0, "prediction_months": 0},
        )

    frame = scored_vintages.copy()
    for column in ("as_of_date", "entry_date", "exit_date"):
        frame[column] = pd.to_datetime(frame[column])
    frame = frame.loc[frame["eligible"].astype(bool)].copy()
    if frame.empty:
        return ScoredBacktestResult(
            pd.DataFrame(),
            {"prediction_rows": 0, "prediction_months": 0},
        )

    frame["rank"] = (
        frame.groupby("as_of_date")["composite_score"]
        .rank(method="first", ascending=False)
        .astype(int)
    )
    cost = 2.0 * transaction_cost_bps_per_side / 10_000.0
    frame["net_forward_return"] = frame["forward_return"] - cost
    selected = frame.loc[frame["rank"].le(top_k)]
    selected_monthly = selected.groupby("as_of_date")[
        "net_forward_return"
    ].mean()
    universe_monthly = frame.groupby("as_of_date")["forward_return"].mean()
    comparison = pd.concat(
        [
            selected_monthly.rename("selected"),
            universe_monthly.rename("universe"),
        ],
        axis=1,
    ).dropna()
    comparison["excess"] = comparison["selected"] - comparison["universe"]
    wealth = (1.0 + selected_monthly).cumprod()
    drawdown = wealth / wealth.cummax() - 1.0

    summary = {
        "strategy": "multifactor_research",
        "prediction_rows": int(len(frame)),
        "prediction_months": int(frame["as_of_date"].nunique()),
        "first_prediction_date": frame["as_of_date"].min(),
        "last_prediction_date": frame["as_of_date"].max(),
        "top_k": int(top_k),
        "top_k_mean_net_return": float(selected_monthly.mean()),
        "universe_mean_return": float(universe_monthly.mean()),
        "top_k_mean_excess_return": float(comparison["excess"].mean()),
        "top_k_monthly_win_rate": float(
            (comparison["excess"] > 0).mean()
        ),
        "positive_return_rate": float((selected_monthly > 0).mean()),
        "maximum_drawdown": float(drawdown.min()),
        "worst_month": float(selected_monthly.min()),
        "best_month": float(selected_monthly.max()),
        "transaction_cost_bps_per_side": float(
            transaction_cost_bps_per_side
        ),
    }
    columns = [
        "ticker",
        "as_of_date",
        "entry_date",
        "exit_date",
        "composite_score",
        "rank",
        "forward_return",
        "net_forward_return",
    ]
    return ScoredBacktestResult(
        frame.loc[:, columns]
        .sort_values(["as_of_date", "rank"])
        .reset_index(drop=True),
        summary,
    )
