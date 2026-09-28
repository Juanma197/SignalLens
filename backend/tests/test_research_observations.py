from datetime import datetime, timezone
import resource
from time import perf_counter

import numpy as np
import pandas as pd
import pytest

import app.research_observations as observations_module

from app.research_observations import (
    ObservationValidationError,
    build_model_ready_observations,
)


DECISION = datetime(2026, 9, 28, 20, tzinfo=timezone.utc)
REGIONS = [
    ("US", "USD", "ALPHA.US"),
    ("LSE", "GBX", "BRAVO.LSE"),
    ("TO", "CAD", "CHARLIE.TO"),
    ("XETRA", "EUR", "DELTA.XETRA"),
    ("PA", "EUR", "ECHO.PA"),
]


def fixtures():
    days = pd.bdate_range(end="2026-09-25", periods=130)
    catalogue = pd.DataFrame(
        [
            {
                "security_id": f"security-{region.lower()}",
                "qualified_symbol": symbol,
                "region": region,
                "currency": currency,
                "eligible": True,
            }
            for region, currency, symbol in REGIONS
        ]
    )
    price_rows = []
    for offset, (_, currency, symbol) in enumerate(REGIONS):
        for index, day in enumerate(days):
            close = 50.0 + offset + index / 10
            price_rows.append(
                {
                    "qualified_symbol": symbol,
                    "trading_date": day.date(),
                    "currency": currency,
                    "open": close - .1,
                    "high": close + .2,
                    "low": close - .2,
                    "close": close,
                    "adjusted_close": close * 1.01,
                    "volume": 100_000 + index,
                    "status": "available",
                    "source": "sanitized-fixture",
                    "retrieved_at": "2026-09-26T08:00:00Z",
                }
            )
    fx = pd.DataFrame(
        [
            {
                "base_currency": currency,
                "quote_currency": "GBP",
                "observed_on": day.date(),
                "rate": {"USD": .75, "CAD": .55, "EUR": .86}[currency],
                "available_at": f"{(day + pd.Timedelta(days=1)).date()}T00:00:00Z",
            }
            for currency in ("USD", "CAD", "EUR")
            for day in days
        ]
    )
    actions = pd.DataFrame(
        [
            {
                "qualified_symbol": "ALPHA.US",
                "ex_date": days[-30].date(),
                "action_type": "cash_distribution",
                "value": .25,
            }
        ]
    )
    failures = pd.DataFrame(columns=["qualified_symbol", "error_code"])
    return catalogue, pd.DataFrame(price_rows), fx, actions, failures


def build(parts):
    catalogue, prices, fx, actions, failures = parts
    return build_model_ready_observations(
        catalogue=catalogue,
        prices=prices,
        fx=fx,
        actions=actions,
        failures=failures,
        decision_at=DECISION,
    )


def test_five_region_fixture_builds_point_in_time_model_ready_observations():
    result = build(fixtures())
    assert result.report["label"] == "RESEARCH ONLY — NOT INVESTMENT ADVICE"
    assert result.report["membership_basis"] == "current_catalogue_not_survivorship_free"
    assert result.report["eligible_by_region"] == {
        "LSE": 1, "PA": 1, "TO": 1, "US": 1, "XETRA": 1
    }
    assert result.report["ranking_published"] is False
    assert result.report["top_three_available"] is True
    assert result.report["highest_conviction_available"] is False
    assert result.observations["eligible"].all()
    assert result.observations["local_momentum_126d"].notna().all()
    assert result.observations["gbp_momentum_126d"].notna().all()
    assert set(result.observations["price_semantics"]) == {
        "provider_adjusted_close_total_return_input"
    }


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        (lambda p: p[2].drop(p[2].loc[p[2].base_currency == "USD"].index), "missing_fx"),
        (lambda p: p[2].assign(rate=lambda x: np.where(x.base_currency == "USD", -1, x.rate)), "invalid_fx"),
        (lambda p: p[1].drop(p[1].loc[p[1].qualified_symbol == "CHARLIE.TO"].index[:-20]), "insufficient_history"),
        (lambda p: p[1].assign(adjusted_close=lambda x: np.where(x.qualified_symbol == "ECHO.PA", -1, x.adjusted_close)), "invalid_price_history"),
        (lambda p: p[1].assign(retrieved_at="2026-10-01T00:00:00Z"), "missing_price_history"),
    ],
)
def test_security_evidence_failures_are_withheld(mutation, reason):
    parts = list(fixtures())
    target = mutation(parts)
    if isinstance(target, pd.DataFrame):
        if reason in {"missing_fx", "invalid_fx"}:
            parts[2] = target
        else:
            parts[1] = target
    result = build(parts)
    assert result.report["exclusions"][reason] >= 1
    assert not result.observations.loc[
        result.observations.exclusion_reasons.map(lambda values: reason in values), "eligible"
    ].any()


def test_stale_prices_and_fx_fail_closed():
    parts = list(fixtures())
    parts[1]["trading_date"] = pd.to_datetime(parts[1]["trading_date"]) - pd.Timedelta(days=20)
    stale_prices = build(parts)
    assert stale_prices.report["exclusions"]["stale_price"] == 5

    parts = list(fixtures())
    parts[2]["observed_on"] = pd.to_datetime(parts[2]["observed_on"]) - pd.Timedelta(days=20)
    stale_fx = build(parts)
    assert stale_fx.report["exclusions"]["stale_fx"] == 4


def test_permanent_provider_failure_is_never_promoted_to_observation():
    parts = list(fixtures())
    parts[4] = pd.DataFrame(
        [{"qualified_symbol": "ALPHA.US", "error_code": "invalid_provider_payload"}]
    )
    result = build(parts)
    row = result.observations.loc[result.observations.qualified_symbol == "ALPHA.US"].iloc[0]
    assert not row.eligible
    assert row.exclusion_reasons == ["permanent_provider_failure"]


@pytest.mark.parametrize("kind", ["price", "fx"])
def test_duplicate_observations_reject_the_entire_dataset(kind):
    parts = list(fixtures())
    index = 1 if kind == "price" else 2
    parts[index] = pd.concat([parts[index], parts[index].iloc[[0]]], ignore_index=True)
    with pytest.raises(ObservationValidationError, match="Duplicate"):
        build(parts)


def test_missing_region_and_invalid_action_reject_or_withhold():
    parts = list(fixtures())
    parts[0] = parts[0].loc[parts[0].region != "PA"]
    with pytest.raises(ObservationValidationError, match="all five regions"):
        build(parts)

    parts = list(fixtures())
    parts[3].loc[0, "value"] = -1
    result = build(parts)
    assert result.report["exclusions"]["invalid_corporate_action"] == 1


def test_ten_year_500_security_fixture_uses_one_vectorized_fx_lookup_per_security(
    monkeypatch,
):
    """Guard algorithmic shape without making wall-clock speed the main assertion."""
    days = pd.bdate_range("2016-09-26", "2026-09-25")
    region_currency = [
        ("US", "USD"), ("LSE", "GBX"), ("TO", "CAD"),
        ("XETRA", "EUR"), ("PA", "EUR"),
    ]
    security_numbers = np.arange(500)
    catalogue = pd.DataFrame(
        {
            "security_id": [f"scale-{number}" for number in security_numbers],
            "qualified_symbol": [
                f"SCALE{number:03d}.{region_currency[number % 5][0]}"
                for number in security_numbers
            ],
            "region": [region_currency[number % 5][0] for number in security_numbers],
            "currency": [region_currency[number % 5][1] for number in security_numbers],
            "eligible": True,
        }
    )
    symbols = np.repeat(catalogue["qualified_symbol"].to_numpy(), len(days))
    currencies = np.repeat(catalogue["currency"].to_numpy(), len(days))
    dates = np.tile(days.to_numpy(), len(catalogue))
    close = (
        40.0 + np.tile(np.arange(len(days), dtype=np.float32), len(catalogue)) / 100.0
    ).astype(np.float32)
    prices = pd.DataFrame(
        {
            "qualified_symbol": symbols,
            "trading_date": dates,
            "currency": currencies,
            "open": close - .1,
            "high": close + .2,
            "low": close - .2,
            "close": close,
            "adjusted_close": close,
            "volume": np.full(len(symbols), 100_000, dtype=np.int32),
            "status": "available",
            "source": "synthetic-scale-fixture",
            "retrieved_at": "2026-09-26T08:00:00Z",
        }
    )
    for column in ("qualified_symbol", "currency", "status", "source"):
        prices[column] = prices[column].astype("category")
    fx = pd.DataFrame(
        {
            "base_currency": np.repeat(["USD", "CAD", "EUR"], len(days)),
            "quote_currency": "GBP",
            "observed_on": np.tile(days.to_numpy(), 3),
            "rate": np.repeat([.75, .55, .86], len(days)),
            "available_at": np.tile(days + pd.Timedelta(hours=18), 3),
        }
    )

    lookup_sizes = []
    original_lookup = observations_module._FxPointInTimeIndex.lookup

    def counted_lookup(self, base_currency, price_dates, maximum_age_days):
        lookup_sizes.append(len(price_dates))
        return original_lookup(self, base_currency, price_dates, maximum_age_days)

    monkeypatch.setattr(observations_module._FxPointInTimeIndex, "lookup", counted_lookup)
    started = perf_counter()
    result = build_model_ready_observations(
        catalogue=catalogue,
        prices=prices,
        fx=fx,
        actions=pd.DataFrame(columns=["qualified_symbol", "ex_date", "action_type", "value"]),
        failures=pd.DataFrame(columns=["qualified_symbol", "error_code"]),
        decision_at=DECISION,
    )
    elapsed = perf_counter() - started

    assert len(result.observations) == 500
    assert result.observations["eligible"].all()
    assert len(prices) >= 1_300_000
    assert len(fx) >= 7_500
    # 400 non-GBP securities, exactly one vectorized lookup for each. A return
    # to one FX-frame scan per price row would make this assertion fail.
    assert lookup_sizes == [len(days)] * 400
    assert elapsed < 30
    print(
        f"scale_seconds={elapsed:.3f} "
        f"scale_peak_rss_mib={resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024:.1f}"
    )
