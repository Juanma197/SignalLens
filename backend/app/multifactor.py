from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


STRATEGY_NAME = "multifactor_research"
STRATEGY_VERSION = "0.1.1"
MOMENTUM_DAYS = 126
TREND_DAYS = 21
RISK_DAYS = 63

FACTOR_WEIGHTS = {
    "momentum": 0.40,
    "trend": 0.15,
    "profitability": 0.20,
    "balance_sheet": 0.10,
    "risk_quality": 0.15,
}


@dataclass(frozen=True)
class RiskPolicy:
    max_annualized_volatility: float = 1.50
    max_peak_to_trough_drawdown: float = 0.65
    missing_factor_penalty: float = 5.0


def _percentile(values: pd.Series, higher_is_better: bool = True) -> pd.Series:
    """Return stable cross-sectional scores in [0, 1], neutral for missing data."""
    result = pd.Series(0.5, index=values.index, dtype=float)
    observed = values.dropna()
    if observed.empty:
        return result
    if observed.nunique() == 1:
        result.loc[observed.index] = 0.5
        return result
    result.loc[observed.index] = observed.rank(
        method="average", pct=True, ascending=higher_is_better
    )
    return result


def _latest_fundamentals(
    fundamental_facts: pd.DataFrame,
    as_of: pd.Timestamp,
) -> pd.DataFrame:
    metrics = ["revenue", "net_income", "cash", "liabilities"]
    if fundamental_facts.empty:
        return pd.DataFrame(columns=metrics)

    required = {
        "ticker", "metric", "value", "available_at", "retrieved_at", "period_end"
    }
    missing = required.difference(fundamental_facts.columns)
    if missing:
        raise ValueError(f"Missing fundamental columns: {sorted(missing)}")

    facts = fundamental_facts.copy()
    facts["available_at"] = pd.to_datetime(facts["available_at"], utc=True)
    facts["retrieved_at"] = pd.to_datetime(facts["retrieved_at"], utc=True)
    facts["period_end"] = pd.to_datetime(facts["period_end"])
    boundary = pd.Timestamp(as_of)
    if boundary.tzinfo is None:
        boundary = boundary.tz_localize("UTC")
    else:
        boundary = boundary.tz_convert("UTC")
    facts = facts.loc[
        facts["available_at"].le(boundary)
        & facts["retrieved_at"].le(boundary)
    ]
    facts = facts.loc[facts["metric"].isin(metrics)]
    facts = facts.sort_values(
        ["ticker", "metric", "available_at", "retrieved_at", "period_end"],
        ascending=[True, True, False, False, False],
    ).drop_duplicates(["ticker", "metric"])
    if facts.empty:
        return pd.DataFrame(columns=metrics)
    return facts.pivot(index="ticker", columns="metric", values="value")


def build_multifactor_scores(
    prices: pd.DataFrame,
    fundamental_facts: pd.DataFrame,
    *,
    as_of: pd.Timestamp | None = None,
    risk_policy: RiskPolicy = RiskPolicy(),
) -> pd.DataFrame:
    """Build point-in-time factor scores without publishing a ranking vintage."""
    required = {"ticker", "trading_date", "adjusted_close"}
    missing = required.difference(prices.columns)
    if missing:
        raise ValueError(f"Missing price columns: {sorted(missing)}")
    if prices.empty:
        raise ValueError("No price history is available")

    frame = prices.loc[:, list(required)].copy()
    frame["trading_date"] = pd.to_datetime(frame["trading_date"])
    frame = frame.sort_values(["ticker", "trading_date"])
    latest_date = pd.Timestamp(as_of) if as_of is not None else frame["trading_date"].max()
    frame = frame.loc[frame["trading_date"].le(latest_date)]

    rows: list[dict[str, float | str | pd.Timestamp]] = []
    for ticker, group in frame.groupby("ticker", sort=True):
        closes = group["adjusted_close"].astype(float).dropna()
        if len(closes) <= MOMENTUM_DAYS or closes.iloc[-1] <= 0:
            continue
        recent = closes.iloc[-(MOMENTUM_DAYS + 1):]
        risk_window = closes.pct_change().dropna().iloc[-RISK_DAYS:]
        running_peak = recent.cummax()
        drawdown = recent / running_peak - 1.0
        rows.append(
            {
                "ticker": ticker,
                "as_of": group["trading_date"].iloc[-1],
                "momentum_126d": closes.iloc[-1] / closes.iloc[-(MOMENTUM_DAYS + 1)] - 1.0,
                "trend_21d": closes.iloc[-1] / closes.iloc[-(TREND_DAYS + 1)] - 1.0,
                "annualized_volatility_63d": risk_window.std(ddof=0) * np.sqrt(252),
                "max_drawdown_126d": drawdown.min(),
            }
        )

    scores = pd.DataFrame(rows).set_index("ticker")
    if scores.empty:
        raise ValueError("No ticker has sufficient history for multifactor scoring")

    fundamentals = _latest_fundamentals(fundamental_facts, latest_date)
    scores = scores.join(fundamentals, how="left")
    scores["profit_margin"] = scores["net_income"] / scores["revenue"].where(
        scores["revenue"].gt(0)
    )
    scores["cash_to_liabilities"] = scores["cash"] / scores["liabilities"].where(
        scores["liabilities"].gt(0)
    )

    scores["momentum_factor"] = _percentile(scores["momentum_126d"])
    scores["trend_factor"] = _percentile(scores["trend_21d"])
    scores["profitability_factor"] = _percentile(scores["profit_margin"])
    scores["balance_sheet_factor"] = _percentile(scores["cash_to_liabilities"])
    scores["risk_quality_factor"] = (
        _percentile(scores["annualized_volatility_63d"], higher_is_better=False)
        + _percentile(scores["max_drawdown_126d"])
    ) / 2.0

    raw = sum(
        scores[f"{name}_factor"] * weight
        for name, weight in FACTOR_WEIGHTS.items()
    ) * 100.0
    missing_fundamentals = scores[
        ["profit_margin", "cash_to_liabilities"]
    ].isna().sum(axis=1)
    scores["missing_factor_count"] = missing_fundamentals
    scores["composite_score"] = (
        raw - missing_fundamentals * risk_policy.missing_factor_penalty
    ).clip(0.0, 100.0)
    scores["eligible"] = (
        scores["annualized_volatility_63d"].le(
            risk_policy.max_annualized_volatility
        )
        & scores["max_drawdown_126d"].ge(
            -risk_policy.max_peak_to_trough_drawdown
        )
    )
    scores["risk_reason"] = ""
    scores.loc[
        scores["annualized_volatility_63d"].gt(
            risk_policy.max_annualized_volatility
        ),
        "risk_reason",
    ] = "annualized volatility exceeds policy"
    scores.loc[
        scores["max_drawdown_126d"].lt(
            -risk_policy.max_peak_to_trough_drawdown
        ),
        "risk_reason",
    ] = "126-day drawdown exceeds policy"

    return (
        scores.reset_index()
        .sort_values(
            ["eligible", "composite_score", "ticker"],
            ascending=[False, False, True],
        )
        .reset_index(drop=True)
    )
