from __future__ import annotations

import hashlib

import duckdb
import numpy as np
import pandas as pd
import pytest

from app.eodhd_ingestion_cli import build_parser, execute
from app.horizon_evaluation import (LOCKED_HORIZONS, assess_horizon_evaluation,
    evaluate_horizon_frames, holm_bonferroni, horizon_block_inference)
from app.research_scoring import EvidencePolicy, walk_forward_evidence
from tests.model_readiness_fixture import DECISION, create_research_fixture
from tests.test_research_scoring import synthetic_frames


@pytest.mark.parametrize("horizon", LOCKED_HORIZONS)
def test_locked_horizon_boundaries_and_incomplete_final_labels(horizon):
    observations, prices, decision = synthetic_frames(periods=700)
    predictions, metrics = walk_forward_evidence(
        observations, prices, decision_at=decision, horizon_sessions=horizon)
    assert (predictions.label_date > predictions.vintage_date).all()
    sessions = pd.DatetimeIndex(sorted(prices.trading_date.unique()))
    positions = {day: index for index, day in enumerate(sessions)}
    assert all(positions[row.label_date] - positions[row.vintage_date] == horizon
               for row in predictions.itertuples())
    assert metrics["diagnostics"]["rows_removed"]["incomplete_final_period_decision_dates"] > 0
    assert predictions.label_date.max() <= prices.trading_date.max()


def test_future_data_and_segment_crossings_fail_closed():
    observations, prices, decision = synthetic_frames(periods=500)
    base, _ = walk_forward_evidence(observations, prices, decision_at=decision,
                                    horizon_sessions=63)
    future = prices.iloc[[0]].copy()
    future["trading_date"] = pd.Timestamp("2026-09-28")
    future["retrieved_at"] = pd.Timestamp("2026-09-29T00:00:00Z")
    future["adjusted_close"] = 1e9
    changed, _ = walk_forward_evidence(observations, pd.concat([prices, future]),
                                       decision_at=decision, horizon_sessions=63)
    pd.testing.assert_frame_equal(base, changed)

    symbol = observations.iloc[0].qualified_symbol
    crossed = prices.copy()
    boundary_date = base.iloc[0].label_date
    crossed.loc[crossed.qualified_symbol.eq(symbol)
                & crossed.trading_date.eq(boundary_date), "adjusted_close"] *= 100
    withheld, metrics = walk_forward_evidence(observations, crossed, decision_at=decision,
                                              horizon_sessions=63)
    assert len(withheld) < len(base)
    assert metrics["diagnostics"]["segment_label_rows_withheld"] > 0


def test_overlap_uses_horizon_aware_deterministic_blocks():
    values = np.linspace(-.01, .02, 40)
    for horizon in LOCKED_HORIZONS:
        first = horizon_block_inference(values, horizon, samples=500)
        assert first == horizon_block_inference(values, horizon, samples=500)
        assert first["block_vintages"] == int(np.ceil(horizon / 21))


def test_holm_correction_and_apparent_single_positive_failure():
    adjusted = holm_bonferroni({21: .02, 63: .2, 126: .3, 252: .4})
    assert adjusted[21]["raw_p_value"] == .02
    assert adjusted[21]["adjusted_p_value"] == .08
    assert not any(item["passed"] for item in adjusted.values())
    with pytest.raises(ValueError):
        holm_bonferroni({21: .01})


def test_weak_and_genuinely_stable_multi_horizon_fixtures():
    weak_obs, weak_prices, decision = synthetic_frames(periods=700)
    weak_prices["adjusted_close"] = 30.0
    weak = evaluate_horizon_frames(weak_obs, weak_prices, decision_at=decision)
    assert all(not row["passed"] for row in weak["results"].values())
    assert all(row["candidate_generation_authorized"] is False
               for row in weak["results"].values())

    observations, prices, decision = synthetic_frames(per_region=6, periods=900)
    policy = EvidencePolicy(minimum_coverage=.8, minimum_eligible=10,
        minimum_vintages=3, minimum_predictions=50, minimum_regions=5,
        minimum_region_vintages=2, minimum_excess_return=-1e-9,
        minimum_rank_correlation=-1e-9, minimum_positive_period_rate=.5)
    stable = evaluate_horizon_frames(observations, prices, decision_at=decision, policy=policy)
    assert set(map(int, stable["results"])) == set(LOCKED_HORIZONS)
    assert stable["horizons_locked_before_evaluation"]
    assert all(row["evaluation"]["vintages"] > 0 for row in stable["results"].values())


def test_read_only_cli_preserves_both_databases(tmp_path):
    research, production = tmp_path / "research.duckdb", tmp_path / "production.duckdb"
    create_research_fixture(research, periods=500, per_region=2)
    with duckdb.connect(str(production)) as db:
        db.execute("CREATE TABLE guard(value INTEGER)")
    digest = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
    before = digest(research), digest(production)
    report = assess_horizon_evaluation(research_db=research, production_db=production,
                                       decision_at=DECISION)
    assert report["candidates"] == [] and not report["ranking_generated"]
    assert report["frozen_21_session_baseline"]["predictions"] == 41271
    assert before == (digest(research), digest(production))
    args = build_parser().parse_args(["research-horizon-evaluation", "--research-db",
        str(research), "--production-db", str(production), "--decision-at", DECISION.isoformat()])
    assert execute(args, now=DECISION)["command"] == "research-horizon-evaluation"
