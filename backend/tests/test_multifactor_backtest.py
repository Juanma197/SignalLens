import pandas as pd
import pytest

from app.multifactor_backtest import (
    audit_fundamental_coverage,
    multifactor_score_backtest,
)


def test_coverage_respects_retrieval_time() -> None:
    facts = pd.DataFrame(
        [
            {
                "ticker": "AAA",
                "metric": metric,
                "available_at": "2026-03-01T00:00:00Z",
                "retrieved_at": "2026-09-22T00:00:00Z",
                "period_end": "2025-12-31",
            }
            for metric in ("revenue", "net_income", "cash", "liabilities")
        ]
    )
    coverage = audit_fundamental_coverage(
        facts,
        [pd.Timestamp("2026-08-31"), pd.Timestamp("2026-09-30")],
        ["AAA"],
    )

    assert coverage.loc[0, "coverage_rate"] == 0.0
    assert coverage.loc[0, "complete_tickers"] == 0
    assert coverage.loc[1, "coverage_rate"] == 1.0
    assert coverage.loc[1, "complete_tickers"] == 1


def test_scored_backtest_ranks_only_eligible_names_and_charges_costs() -> None:
    rows = []
    for month in pd.date_range("2025-01-31", periods=3, freq="ME"):
        for ticker, score, forward_return, eligible in (
            ("AAA", 90.0, 0.08, True),
            ("BBB", 70.0, 0.03, True),
            ("CCC", 99.0, -0.20, False),
        ):
            rows.append(
                {
                    "ticker": ticker,
                    "as_of_date": month,
                    "entry_date": month + pd.offsets.BDay(1),
                    "exit_date": month + pd.offsets.BDay(22),
                    "forward_return": forward_return,
                    "composite_score": score,
                    "eligible": eligible,
                }
            )

    result = multifactor_score_backtest(
        pd.DataFrame(rows),
        top_k=1,
        transaction_cost_bps_per_side=10,
    )

    assert set(result.predictions["ticker"]) == {"AAA", "BBB"}
    assert (
        result.predictions.loc[
            result.predictions["ticker"].eq("AAA"), "rank"
        ]
        == 1
    ).all()
    selected = result.predictions.loc[result.predictions["rank"].eq(1)]
    assert selected["net_forward_return"].iloc[0] == pytest.approx(0.078)
    assert result.summary["prediction_months"] == 3
    assert result.summary["top_k_mean_net_return"] == pytest.approx(0.078)


def test_backtest_validates_inputs() -> None:
    frame = pd.DataFrame(
        {
            "ticker": ["AAA"],
            "as_of_date": [pd.Timestamp("2026-01-31")],
        }
    )
    with pytest.raises(ValueError, match="Missing scored-vintage columns"):
        multifactor_score_backtest(frame)
    with pytest.raises(ValueError, match="top_k"):
        multifactor_score_backtest(pd.DataFrame(), top_k=0)
    with pytest.raises(ValueError, match="transaction"):
        multifactor_score_backtest(
            pd.DataFrame(), transaction_cost_bps_per_side=-1
        )
