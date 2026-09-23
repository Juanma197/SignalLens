from datetime import datetime, timezone

import pandas as pd
import pytest

from app.multifactor import RiskPolicy, build_multifactor_scores


def price_history(
    ticker: str,
    *,
    daily_return: float,
    periods: int = 150,
    shock_at: int | None = None,
    shock_return: float = 0.0,
) -> pd.DataFrame:
    dates = pd.bdate_range("2026-01-01", periods=periods)
    closes = [100.0]
    for index in range(1, periods):
        change = shock_return if index == shock_at else daily_return
        closes.append(closes[-1] * (1.0 + change))
    return pd.DataFrame(
        {
            "ticker": ticker,
            "trading_date": dates,
            "adjusted_close": closes,
        }
    )


def fact(
    ticker: str,
    metric: str,
    value: float,
    *,
    available_at: str = "2026-06-01T00:00:00Z",
    retrieved_at: str | None = None,
    period_end: str = "2026-03-31",
) -> dict:
    return {
        "ticker": ticker,
        "metric": metric,
        "value": value,
        "available_at": available_at,
        "retrieved_at": retrieved_at or available_at,
        "period_end": period_end,
    }


def complete_facts(
    ticker: str,
    *,
    revenue: float,
    net_income: float,
    cash: float,
    liabilities: float,
) -> list[dict]:
    return [
        fact(ticker, "revenue", revenue),
        fact(ticker, "net_income", net_income),
        fact(ticker, "cash", cash),
        fact(ticker, "liabilities", liabilities),
    ]


def test_multifactor_score_rewards_quality_and_momentum() -> None:
    prices = pd.concat(
        [
            price_history("QUALITY", daily_return=0.004),
            price_history("WEAK", daily_return=0.001),
        ],
        ignore_index=True,
    )
    fundamentals = pd.DataFrame(
        complete_facts(
            "QUALITY",
            revenue=1_000,
            net_income=220,
            cash=500,
            liabilities=400,
        )
        + complete_facts(
            "WEAK",
            revenue=1_000,
            net_income=20,
            cash=50,
            liabilities=800,
        )
    )

    result = build_multifactor_scores(prices, fundamentals)

    assert result["ticker"].tolist() == ["QUALITY", "WEAK"]
    assert result.loc[0, "composite_score"] > result.loc[1, "composite_score"]
    assert result["eligible"].all()


def test_scoring_is_point_in_time_and_ignores_future_facts() -> None:
    prices = pd.concat(
        [
            price_history("AAA", daily_return=0.002),
            price_history("BBB", daily_return=0.002),
        ],
        ignore_index=True,
    )
    fundamentals = pd.DataFrame(
        complete_facts(
            "AAA",
            revenue=1_000,
            net_income=100,
            cash=200,
            liabilities=400,
        )
        + complete_facts(
            "BBB",
            revenue=1_000,
            net_income=100,
            cash=200,
            liabilities=400,
        )
        + [
            fact(
                "BBB",
                "net_income",
                900,
                available_at="2027-01-01T00:00:00Z",
                period_end="2026-12-31",
            )
        ]
    )

    result = build_multifactor_scores(
        prices,
        fundamentals,
        as_of=pd.Timestamp("2026-07-29"),
    ).set_index("ticker")

    assert result.loc["AAA", "profit_margin"] == pytest.approx(0.1)
    assert result.loc["BBB", "profit_margin"] == pytest.approx(0.1)


def test_scoring_ignores_facts_retrieved_after_the_boundary() -> None:
    prices = pd.concat(
        [
            price_history("AAA", daily_return=0.002),
            price_history("BBB", daily_return=0.002),
        ],
        ignore_index=True,
    )
    fundamentals = pd.DataFrame(
        complete_facts(
            "AAA",
            revenue=1_000,
            net_income=100,
            cash=200,
            liabilities=400,
        )
        + complete_facts(
            "BBB",
            revenue=1_000,
            net_income=100,
            cash=200,
            liabilities=400,
        )
        + [
            fact(
                "BBB",
                "net_income",
                900,
                available_at="2026-05-01T00:00:00Z",
                retrieved_at="2026-09-22T00:00:00Z",
            )
        ]
    )

    result = build_multifactor_scores(
        prices,
        fundamentals,
        as_of=pd.Timestamp("2026-07-29"),
    ).set_index("ticker")

    assert result.loc["BBB", "profit_margin"] == pytest.approx(0.1)


def test_missing_fundamentals_receive_explicit_penalty() -> None:
    prices = pd.concat(
        [
            price_history("COMPLETE", daily_return=0.002),
            price_history("MISSING", daily_return=0.002),
        ],
        ignore_index=True,
    )
    fundamentals = pd.DataFrame(
        complete_facts(
            "COMPLETE",
            revenue=1_000,
            net_income=100,
            cash=200,
            liabilities=400,
        )
    )

    result = build_multifactor_scores(prices, fundamentals).set_index("ticker")

    assert result.loc["COMPLETE", "missing_factor_count"] == 0
    assert result.loc["MISSING", "missing_factor_count"] == 2
    assert (
        result.loc["COMPLETE", "composite_score"]
        > result.loc["MISSING", "composite_score"]
    )


def test_extreme_drawdown_fails_risk_policy() -> None:
    prices = pd.concat(
        [
            price_history("STABLE", daily_return=0.001),
            price_history(
                "CRASH",
                daily_return=0.003,
                shock_at=140,
                shock_return=-0.75,
            ),
        ],
        ignore_index=True,
    )
    fundamentals = pd.DataFrame(
        complete_facts(
            "STABLE",
            revenue=1_000,
            net_income=100,
            cash=200,
            liabilities=400,
        )
        + complete_facts(
            "CRASH",
            revenue=1_000,
            net_income=100,
            cash=200,
            liabilities=400,
        )
    )

    result = build_multifactor_scores(
        prices,
        fundamentals,
        risk_policy=RiskPolicy(
            max_annualized_volatility=10.0,
            max_peak_to_trough_drawdown=0.65,
        ),
    ).set_index("ticker")

    assert bool(result.loc["STABLE", "eligible"]) is True
    assert bool(result.loc["CRASH", "eligible"]) is False
    assert result.loc["CRASH", "risk_reason"] == "126-day drawdown exceeds policy"


def test_requires_complete_price_schema() -> None:
    with pytest.raises(ValueError, match="Missing price columns"):
        build_multifactor_scores(
            pd.DataFrame({"ticker": ["AAA"]}),
            pd.DataFrame(),
        )
