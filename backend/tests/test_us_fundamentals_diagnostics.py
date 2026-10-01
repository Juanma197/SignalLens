from __future__ import annotations

import os
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import pytest

from app.eodhd_ingestion_cli import build_parser, execute
from app.us_fundamentals import (FACTOR_DIRECTIONS, FACTOR_FAMILIES,
    FAMILY_WEIGHTS, configuration_hash, normalize_and_combine)
from app.us_fundamentals_diagnostics import (OBSERVED_HEADLINE, accounting_checks,
    adjust_pvalues, coverage_diagnosis, factor_evidence, leave_one_family_out,
    reconcile_frozen_headline, validate_directions)


def panel() -> pd.DataFrame:
    rows = []
    for month, vintage in enumerate(pd.date_range("2020-01-31", periods=26, freq="ME")):
        for number in range(8):
            row = {"security_id": f"s{number}", "qualified_symbol": f"S{number}.US",
                "vintage_date": vintage, "score": number + month / 100,
                "forward_return": (number - 3) / 100 + ((-1) ** month) / 1000,
                "fundamental_score": number / 7}
            for family in FAMILY_WEIGHTS: row[f"{family}_score"] = number / 7
            for factor in FACTOR_DIRECTIONS: row[f"{factor}_rank"] = number / 7
            rows.append(row)
    return pd.DataFrame(rows)


def test_observed_headline_is_exactly_frozen():
    assert configuration_hash() == "3441bfb9e8055846bd2223673d3a76ce7c16b892463ce4b4e25a6281161d9a36"
    assert OBSERVED_HEADLINE[126]["predictions"] == 4670
    assert OBSERVED_HEADLINE[252]["eligible_vintages"] == 97
    assert OBSERVED_HEADLINE[126]["incremental_excess_return"] == -.0416297


def test_reconciliation_rejects_a_subtly_different_sample():
    results = {}
    for horizon, row in OBSERVED_HEADLINE.items():
        results[str(horizon)] = {"eligible_vintages": row["eligible_vintages"],
            "predictions": row["predictions"],
            "price_only": {"mean_excess_return": row["price_only_mean_excess"],
                "concentration_top_security": row["price_only_concentration"]},
            "enhanced": {"mean_excess_return": row["enhanced_mean_excess"],
                "concentration_top_security": row["enhanced_concentration"]},
            "incremental_excess_return": row["incremental_excess_return"],
            "incremental_inference": {"confidence_interval": row["confidence_interval"]},
            "multiple_testing": {"raw_p_value": row["raw_p_value"],
                "adjusted_p_value": row["adjusted_p_value"]}}
    assert all(reconcile_frozen_headline(results).values())
    results["126"]["predictions"] -= 1
    with pytest.raises(Exception, match="reconciliation failed"):
        reconcile_frozen_headline(results)


def test_factor_attribution_is_bounded_and_accounts_for_concentration():
    report = factor_evidence(panel(), "revenue_growth_rank", weight=.2, horizon=126)
    assert report["label"] == "exploratory_non_confirmatory"
    assert len(report["best_vintages"]) <= 3 and len(report["worst_vintages"]) <= 3
    assert len(report["concentration"]["top_symbols"]) <= 10
    assert 0 < report["concentration"]["largest_1"] <= report["concentration"]["largest_10"] <= 1
    assert report["selected_top_group_overlap"] == 1


def test_missingness_and_sparse_early_coverage_are_not_scored():
    frame = panel()
    early = frame.vintage_date.eq(frame.vintage_date.min())
    for family in list(FAMILY_WEIGHTS)[:4]: frame.loc[early, f"{family}_score"] = np.nan
    coverage = coverage_diagnosis(frame)
    assert coverage["coverage_by_year"]["2020"] < coverage["coverage_by_year"]["2022"]
    raw = pd.DataFrame([{factor: (None if i == 0 else float(i))
                         for factor in FACTOR_DIRECTIONS} for i in range(6)])
    normalized, _ = normalize_and_combine(raw)
    assert normalized.loc[0, [f"{f}_rank" for f in FACTOR_DIRECTIONS]].isna().all()


def test_direction_validation_detects_inversion_and_missing_score():
    frame = pd.DataFrame({"revenue_growth": [1., 2., np.nan],
                          "revenue_growth_rank": [1., 0., .5]})
    result = validate_directions(frame)
    assert result["status"] == "defect_detected"
    assert {item["defect"] for item in result["defects"]} == {
        "direction_or_percentile_reversed", "missing_value_received_score"}


def test_leave_one_out_accounts_for_all_families_and_multiplicity():
    result = leave_one_family_out(panel(), 126)
    assert result["exploratory_comparisons"] == len(FAMILY_WEIGHTS)
    assert result["cannot_be_used_as_confirmation"]
    assert result["candidates_generated"] == 0
    assert all("adjusted_p_value" in row for row in result["comparisons"].values())
    adjusted = adjust_pvalues({"a": .01, "b": .04, "c": None})
    assert adjusted == {"a": .02, "b": .04, "c": None}


def test_accounting_diagnostics_retain_extremes_and_are_aggregate_only():
    frame = pd.DataFrame({"net_income": [-1], "equity": [-2],
        "trailing_free_cash_flow": [-3], "assets": [0], "debt": [0],
        "revenue_growth": [11], "eps_growth": [0], "operating_cash_flow_growth": [0],
        "diluted_share_growth": [2]})
    result = accounting_checks(frame, {"alternative_taxonomy_concepts": 2,
        "annual_ttm_quarterly": {"annual": 1, "ttm": 0, "quarterly": 0}})
    assert result["negative_earnings"] == result["negative_equity"] == 1
    assert result["near_zero_denominators"] == 2
    assert result["extreme_growth_ratios"] == result["share_count_discontinuities"] == 1
    assert result["valuation_price_basis"] == "historical_price_at_decision_vintage"


def test_cli_requires_paths_and_rejects_hardlinks(tmp_path: Path):
    parser = build_parser()
    args = parser.parse_args(["research-us-fundamentals-diagnostics"])
    with pytest.raises(ValueError, match="explicit"):
        execute(args)
    research = tmp_path / "research.duckdb"
    with duckdb.connect(str(research)): pass
    production = tmp_path / "production.duckdb"
    os.link(research, production)
    args = parser.parse_args(["research-us-fundamentals-diagnostics",
        "--research-db", str(research), "--production-db", str(production)])
    with pytest.raises(Exception, match="distinct"):
        execute(args)
