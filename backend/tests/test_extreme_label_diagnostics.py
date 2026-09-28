from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

from app.eodhd_ingestion_cli import build_parser, execute
from app.extreme_label_diagnostics import _severity_key, _stage_accounting, diagnose_label_provenance
from app.research_scoring import EvidencePolicy
from model_readiness_fixture import create_research_fixture


def _case(symbol: str, raw: list[float], adjusted: list[float], *, region: str = "US"):
    dates = pd.bdate_range("2025-01-02", periods=len(raw))
    prices = pd.DataFrame([{
        "qualified_symbol": symbol, "trading_date": day, "currency": "USD",
        "close": close, "adjusted_close": adj, "volume": 1000 + index,
        "status": "available", "source": "deterministic-fixture",
        "retrieved_at": datetime(2025, 2, 1, tzinfo=timezone.utc),
    } for index, (day, close, adj) in enumerate(zip(dates, raw, adjusted))])
    prediction = pd.DataFrame([{
        "qualified_symbol": symbol, "region": region, "vintage_date": dates[0],
        "label_date": dates[-1], "entry_adjusted_close": adjusted[0],
        "exit_adjusted_close": adjusted[-1],
        "forward_return": adjusted[-1] / adjusted[0] - 1,
    }])
    return prediction, prices


@pytest.mark.parametrize(("symbol", "raw", "adjusted", "expected"), [
    ("ATPC.US", [.001] * 4 + [20], [.001] * 4 + [20], "near_zero_denominator_amplification"),
    ("SPLIT.US", [10, 10.1, 101, 101.5, 102], [10, 18, 33, 60, 120], "raw_price_split_like_discontinuity"),
    ("DISAGREE.US", [100, 10, 10, 10, 10], [10, 10, 10, 10, 120], "raw_adjusted_disagreement"),
    ("REUSE.US", [100, 101, 1, 1.1, 1200], [100, 101, 1, 1.1, 1200], "possible_ticker_reuse"),
    ("TINY.US", [.001, .0011, .00115, .0011, .0012], [.001, .0011, .00115, .0011, .0012], "near_zero_denominator_amplification"),
    ("REAL.US", [1, 2, 4, 8, 16], [1, 2, 4, 8, 16], "insufficient_evidence"),
    ("NOACTION.US", [100, 100, 1, 1, 1200], [100, 100, 1, 1, 1200], "possible_missing_split_adjustment"),
    ("MALFORMED.US", [1, 1, 1, 1, 20], [1, 1, 1000, 1, 20], "isolated_malformed_observation"),
])
def test_deterministic_provenance_patterns(symbol, raw, adjusted, expected):
    predictions, prices = _case(symbol, raw, adjusted)
    report = diagnose_label_provenance(
        predictions, prices,
        pd.DataFrame(columns=["qualified_symbol", "ex_date", "action_type", "value"]),
    )
    observation = report["observations"][0]
    assert expected in observation["diagnostic_reason_codes"]
    assert len(observation["neighbouring_sessions"]) <= 5
    assert report["thresholds"] == {
        "near_zero_adjusted_close": .01, "extreme_absolute_return": 10.0,
    }


def test_action_proximity_windows_are_reported_without_claiming_cause():
    predictions, prices = _case("ACTION.US", [1, 1, 1, 1, 20], [1, 1, 1, 1, 20])
    actions = pd.DataFrame([{"qualified_symbol": "ACTION.US", "ex_date": "2025-01-07",
                             "action_type": "split", "value": 20}])
    observation = diagnose_label_provenance(predictions, prices, actions)["observations"][0]
    assert observation["corporate_action_proximity_counts"] == {"7": 1, "30": 1, "90": 1}
    assert all(not reason.startswith("provider_") for reason in observation["diagnostic_reason_codes"])


def test_cli_is_read_only_and_refuses_unsafe_paths(tmp_path: Path):
    research, production = tmp_path / "research.duckdb", tmp_path / "production.duckdb"
    create_research_fixture(research, periods=170)
    production.write_bytes(b"production sentinel")
    before = (research.read_bytes(), production.read_bytes())
    args = build_parser().parse_args([
        "diagnose-extreme-labels", "--research-db", str(research),
        "--production-db", str(production), "--decision-at", "2026-09-27T00:00:00+00:00",
    ])
    report = execute(args)
    assert report["mode"] == "strictly_read_only"
    assert report["ranking"] == {"status": "withheld", "generated": False}
    assert all(item["unchanged"] for item in report["database_fingerprints"].values())
    assert before == (research.read_bytes(), production.read_bytes())

    missing = build_parser().parse_args([
        "diagnose-extreme-labels", "--research-db", str(research),
        "--production-db", str(tmp_path / "missing.duckdb"),
    ])
    with pytest.raises(ValueError, match="does not exist"):
        execute(missing)
    identical = build_parser().parse_args([
        "diagnose-extreme-labels", "--research-db", str(research),
        "--production-db", str(research),
    ])
    with pytest.raises(ValueError, match="identical or aliased"):
        execute(identical)


def test_label_repair_plan_is_read_only_and_never_generates_ranking(tmp_path: Path):
    research, production = tmp_path / "research.duckdb", tmp_path / "production.duckdb"
    create_research_fixture(research, periods=310)
    production.write_bytes(b"production sentinel")
    before = research.read_bytes(), production.read_bytes()
    args = build_parser().parse_args(["plan-label-repair", "--research-db", str(research),
        "--production-db", str(production), "--decision-at", "2026-09-27T00:00:00+00:00"])
    report = execute(args)
    assert report["proposal_only"] and report["mode"] == "strictly_read_only"
    assert report["ranking"] == {"status": "withheld", "generated": False}
    accounting = report["stage_accounting"]
    assert accounting["raw_panel_labels"] - accounting["feature_withheld_distinct"] == \
        accounting["labels_after_feature_validation"]
    assert accounting["labels_after_feature_validation"] - accounting["label_withheld_distinct"] == \
        accounting["retained_labels"]
    assert accounting["raw_panel_labels"] - accounting["retained_labels"] == \
        accounting["total_withheld_distinct"]
    assert report["affected_rows"]["reason_counts_non_additive"] is True
    assert set(report["affected_symbols"]) >= {"by_region", "by_reason", "feature_stage", "label_stage"}
    assert all(value["unchanged"] for value in report["database_fingerprints"].values())
    assert before == (research.read_bytes(), production.read_bytes())


def test_bounded_output_and_aggregate_counts():
    all_predictions, all_prices = [], []
    for index in range(3):
        predictions, prices = _case(f"EXTREME{index}.US", [1, 1, 1, 1, 20 + index], [1, 1, 1, 1, 20 + index])
        all_predictions.append(predictions)
        all_prices.append(prices)
    report = diagnose_label_provenance(
        pd.concat(all_predictions), pd.concat(all_prices),
        pd.DataFrame(columns=["qualified_symbol", "ex_date"]), affected_limit=2,
    )
    assert report["affected_total"] == 3
    assert report["reported"] == 2
    assert report["truncated"] is True
    assert report["region_counts"] == {"US": 2}


def test_live_shape_stage_accounting_regression():
    accounting = _stage_accounting({
        "raw_panel_labels": 41_609,
        "feature_boundary_candidates": 325,
        "segment_feature_rows_withheld": 292,
        "label_eligible_rows": 41_317,
        "label_boundary_candidates": 45,
        "retained_label_rows": 41_271,
    })
    assert accounting == {
        "raw_panel_labels": 41_609,
        "feature_boundary_candidates": 325,
        "feature_withheld_distinct": 292,
        "labels_after_feature_validation": 41_317,
        "label_boundary_candidates": 45,
        "label_withheld_distinct": 46,
        "retained_labels": 41_271,
        "total_withheld_distinct": 338,
    }
    with pytest.raises(ValueError, match="inconsistent feature/label"):
        _stage_accounting({
            "raw_panel_labels": 41_609,
            "segment_feature_rows_withheld": 291,
            "label_eligible_rows": 41_317,
            "retained_label_rows": 41_271,
        })


def test_boundary_sample_severity_precedes_symbol_order():
    rows = pd.DataFrame([
        {"qualified_symbol": "AAA.US", "boundary_date": "2025-01-02", "raw_ratio": 20,
         "adjusted_ratio": 20, "reason_codes": ("adjusted_price_discontinuity",)},
        {"qualified_symbol": "SBET.US", "boundary_date": "2025-01-03", "raw_ratio": 11,
         "adjusted_ratio": 11, "reason_codes": ("unresolved_extreme_return",)},
        {"qualified_symbol": "ZZZ.US", "boundary_date": "2025-01-04", "raw_ratio": 100,
         "adjusted_ratio": 100, "reason_codes": ("zero_volume_discontinuity",)},
    ])
    ordered = sorted(rows.itertuples(index=False), key=_severity_key)
    assert [row.qualified_symbol for row in ordered] == ["SBET.US", "ZZZ.US", "AAA.US"]
