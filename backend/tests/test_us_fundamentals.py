from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

from app.us_fundamentals import (FAMILY_WEIGHTS, LOCKED_HORIZONS, FundamentalPolicy,
    configuration_hash, construct_factors, evaluate_matched, holm_two_horizons, normalize_and_combine,
    select_asof_facts)

DECISION = datetime(2025, 6, 30, 23, tzinfo=timezone.utc)


def fact(security="a", concept="Revenues", value=100.0, start="2024-01-01", end="2024-12-31",
         public="2025-02-01T12:00:00Z", accession="1", unit="USD", currency="USD", amendment=False):
    return {"security_id": security, "taxonomy": "us-gaap", "concept": concept, "value": value,
        "unit": unit, "currency": currency, "period_start": start, "period_end": end,
        "accession_number": accession, "public_at": public, "is_amendment": amendment,
        "source_endpoint": "offline-fixture"}


def complete(security: str, scale: float = 1.0) -> list[dict]:
    rows = []
    concepts = {"Revenues": 100, "EarningsPerShareDiluted": 2,
        "OperatingIncomeLoss": 20, "NetIncomeLoss": 12,
        "NetCashProvidedByUsedInOperatingActivities": 18,
        "PaymentsToAcquirePropertyPlantAndEquipment": 5,
        "WeightedAverageNumberOfDilutedSharesOutstanding": 10}
    for concept, value in concepts.items():
        unit = "USD/shares" if concept == "EarningsPerShareDiluted" else ("shares" if "Shares" in concept else "USD")
        rows += [fact(security, concept, value * scale, unit=unit),
                 fact(security, concept, value * scale / 1.1, start="2023-01-01", end="2023-12-31",
                      public="2024-02-01T12:00:00Z", accession="0", unit=unit)]
    for concept, value in {"Assets": 200, "StockholdersEquity": 100,
                           "LongTermDebtCurrent": 5, "LongTermDebtNoncurrent": 35}.items():
        rows.append(fact(security, concept, value * scale, start=None, end="2025-03-31",
                         public="2025-05-01T12:00:00Z"))
    return rows


def test_availability_boundary_and_later_amendment_visibility():
    frame = pd.DataFrame([fact(value=100), fact(value=120, public="2025-07-01T00:00:00Z",
        accession="2", amendment=True)])
    selected, diag = select_asof_facts(frame, DECISION)
    assert selected.value.tolist() == [100]
    assert diag["not_yet_public"] == 1
    after, _ = select_asof_facts(frame, datetime(2025, 7, 2, tzinfo=timezone.utc))
    assert after.value.tolist() == [120]


def test_missing_availability_nonfinite_and_revision_diagnostics():
    frame = pd.DataFrame([fact(value=100), fact(value=101, accession="2"),
        fact(value=np.inf, accession="3"), fact(value=4, public=None, accession="4")])
    selected, diag = select_asof_facts(frame, DECISION)
    assert selected.value.tolist() == [101]
    assert diag == {"missing_availability": 1, "not_yet_public": 0,
                    "nonfinite_value": 1, "missing_unit": 0, "superseded_revision": 1}


def test_ttm_uses_four_nonoverlapping_quarters_but_never_ytd_overlap():
    rows = []
    for index, (start, end) in enumerate((("2024-04-01", "2024-06-30"),
        ("2024-07-01", "2024-09-30"), ("2024-10-01", "2024-12-31"),
        ("2025-01-01", "2025-03-31"))):
        rows.append(fact(concept="Revenues", value=25, start=start, end=end,
                         public=f"2025-0{min(index + 2, 5)}-15T00:00:00Z", accession=str(index)))
    rows.append(fact(concept="Revenues", value=75, start="2024-01-01", end="2024-09-30", accession="ytd"))
    factors, _ = construct_factors(pd.DataFrame(rows), DECISION, {"a": 10}, {"a": "USD"})
    # Revenue TTM exists, but growth is withheld without a prior comparable TTM.
    assert pd.isna(factors.loc[0, "revenue_growth"])
    # Adding an overlap to the quarter set does not create an invented fifth component.
    rows.append(fact(concept="Revenues", value=999, start="2024-01-01", end="2024-06-30", accession="half"))
    assert pd.isna(construct_factors(pd.DataFrame(rows), DECISION, {"a": 10}, {"a": "USD"})[0].loc[0, "revenue_growth"])


def test_denominators_negatives_currency_and_point_in_time_valuation():
    rows = complete("a")
    factors, diagnostics = construct_factors(pd.DataFrame(rows), DECISION, {"a": 10}, {"a": "USD"})
    assert factors.loc[0, "earnings_yield"] == pytest.approx(12 / 100)
    assert factors.loc[0, "book_to_market"] == pytest.approx(1.0)
    assert factors.loc[0, "free_cash_flow_yield"] == pytest.approx(13 / 100)
    assert factors.loc[0, "debt_to_equity"] == pytest.approx(.4)
    assert diagnostics["interest_coverage"].startswith("unavailable")
    bad = pd.DataFrame(complete("b")); bad.loc[bad.concept.eq("StockholdersEquity"), "value"] = -1
    bad.loc[bad.concept.eq("Revenues"), "unit"] = "EUR"
    withheld, diag = construct_factors(bad, DECISION, {"b": 10}, {"b": "USD"})
    assert pd.isna(withheld.loc[0, "book_to_market"])
    assert pd.isna(withheld.loc[0, "operating_margin"])
    assert diag["per_security"]["b"]["incompatible_unit_or_currency"] > 0


def test_staleness_and_missing_prices_fail_closed():
    old = pd.DataFrame(complete("a")); old["period_end"] = "2020-12-31"
    frame, _ = construct_factors(old, DECISION, {}, {"a": "USD"})
    assert frame.drop(columns="security_id").isna().all().all()


def test_robust_us_only_ranks_minimum_coverage_and_missing_fairness():
    rows = []
    for i in range(6):
        row = {"security_id": str(i), **{factor: float(i) for factor in
            ("revenue_growth", "operating_margin", "trailing_free_cash_flow", "debt_to_assets")}}
        rows.append(row)
    rows[-1]["revenue_growth"] = 1e100
    ranked, coverage = normalize_and_combine(pd.DataFrame(rows))
    assert ranked.revenue_growth_rank.between(0, 1).all()
    assert ranked.loc[5, "revenue_growth_rank"] == 1
    assert ranked.fundamental_score.notna().all()
    sparse, sparse_coverage = normalize_and_combine(pd.DataFrame(rows[:4]))
    assert sparse.fundamental_score.isna().all()
    assert sparse_coverage["withheld_insufficient_families"] == 4


def test_configuration_and_weights_are_frozen():
    assert LOCKED_HORIZONS == (126, 252)
    assert sum(FAMILY_WEIGHTS.values()) == pytest.approx(1)
    assert configuration_hash() == configuration_hash()
    assert configuration_hash(FundamentalPolicy(minimum_cross_section=6)) != configuration_hash()
    adjusted = holm_two_horizons({126: .03, 252: .04})
    assert adjusted[126]["adjusted_p_value"] == .06
    assert not any(value["passed"] for value in adjusted.values())


def prediction_frames(strength: float) -> tuple[pd.DataFrame, pd.DataFrame]:
    predictions, fundamentals = [], []
    for vintage in pd.date_range("2020-01-31", periods=18, freq="ME"):
        for i in range(8):
            security = str(i); fundamental = i / 7
            predictions.append({"security_id": security, "qualified_symbol": f"S{i}",
                "vintage_date": vintage, "score": 0.0,
                "forward_return": strength * fundamental})
            fundamentals.append({"security_id": security, "vintage_date": vintage,
                                 "fundamental_score": fundamental})
    return pd.DataFrame(predictions), pd.DataFrame(fundamentals)


def test_matched_samples_weak_fails_and_strong_fundamentals_improve():
    predictions, fundamentals = prediction_frames(0)
    weak = evaluate_matched(predictions, fundamentals, 126)
    assert weak["matched_samples"] and not weak["passed"]
    predictions, fundamentals = prediction_frames(.1)
    strong = evaluate_matched(predictions, fundamentals, 126)
    assert strong["predictions"] == len(predictions)
    assert strong["incremental_excess_return"] > 0
    assert strong["passed"]


def test_future_fact_does_not_change_historical_factor():
    base = pd.DataFrame(complete("a"))
    first = construct_factors(base, DECISION, {"a": 10}, {"a": "USD"})[0]
    future = fact("a", "NetIncomeLoss", 999999, public="2026-01-01T00:00:00Z", accession="future")
    second = construct_factors(pd.concat([base, pd.DataFrame([future])]), DECISION, {"a": 10}, {"a": "USD"})[0]
    pd.testing.assert_frame_equal(first, second)
