from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import pytest

from app.eodhd_ingestion_cli import build_parser, execute
from app.research_scoring import (
    EvidencePolicy, assess_research_scoring, build_research_ranking,
    evaluate_evidence, prepare_cross_section, walk_forward_evidence,
)
from tests.model_readiness_fixture import DECISION, create_research_fixture


REGIONS = (("US", "USD"), ("LSE", "GBX"), ("TO", "CAD"),
           ("XETRA", "EUR"), ("PA", "EUR"))


def synthetic_frames(per_region: int = 6, periods: int = 420):
    dates = pd.bdate_range(end="2026-09-25", periods=periods)
    observation_rows, price_rows = [], []
    decision = datetime(2026, 9, 28, 20, tzinfo=timezone.utc)
    for region_index, (region, currency) in enumerate(REGIONS):
        for index in range(per_region):
            symbol = f"S{region_index}{index}.{region}"
            observation_rows.append({"security_id": symbol, "qualified_symbol": symbol,
                "region": region, "currency": currency, "eligible": True,
                "decision_at": decision, "exclusion_reasons": []})
            # Persistent, deterministic slopes make trailing features predictive.
            growth = .0001 + index * .00035
            values = 30 * np.exp(growth * np.arange(periods))
            for day, close in zip(dates, values):
                price_rows.append({"qualified_symbol": symbol, "trading_date": day,
                    "adjusted_close": close, "status": "available",
                    "retrieved_at": day.tz_localize("UTC") + pd.Timedelta(hours=20)})
    return pd.DataFrame(observation_rows), pd.DataFrame(price_rows), decision


def permissive(**changes):
    values = dict(minimum_coverage=.8, minimum_eligible=10, minimum_vintages=3,
        minimum_predictions=50, minimum_regions=5, minimum_region_vintages=2,
        minimum_excess_return=-1e-9, minimum_rank_correlation=-1e-9,
        minimum_positive_period_rate=.5, minimum_candidate_score=0)
    values.update(changes)
    return EvidencePolicy(**values)


def test_only_explicitly_eligible_observations_are_scored():
    observations, prices, decision = synthetic_frames()
    observations.loc[0, "eligible"] = False
    withheld = observations.loc[0, "qualified_symbol"]
    scores = prepare_cross_section(observations, prices, decision_at=decision)
    assert withheld not in set(scores.qualified_symbol)
    assert len(scores) == len(observations) - 1


def test_future_price_retrieval_cannot_change_features_or_labels():
    observations, prices, decision = synthetic_frames()
    base = prepare_cross_section(observations, prices, decision_at=decision)
    future = prices.iloc[[0]].copy()
    future["trading_date"] = pd.Timestamp("2026-09-28")
    future["retrieved_at"] = pd.Timestamp("2026-09-29", tz="UTC")
    future["adjusted_close"] = 10_000_000
    changed = prepare_cross_section(observations, pd.concat([prices, future]), decision_at=decision)
    pd.testing.assert_frame_equal(base, changed)
    predictions, metrics = walk_forward_evidence(observations, prices, decision_at=decision)
    assert metrics["valid"]
    assert (predictions.label_date > predictions.vintage_date).all()


def test_passing_synthetic_evidence_is_research_only_and_explainable():
    observations, prices, decision = synthetic_frames()
    scores = prepare_cross_section(observations, prices, decision_at=decision)
    _, metrics = walk_forward_evidence(observations, prices, decision_at=decision)
    gates = evaluate_evidence(observations=observations, evaluation=metrics,
                              integrity_ok=True, policy=permissive())
    assert gates["passed"], gates
    ranking = build_research_ranking(scores, gates, policy=permissive(), synthetic=True)
    assert ranking["status"] == "research_ranking_available"
    assert ranking["synthetic"] and ranking["research_only"]
    assert ranking["highest_conviction_candidate"] == ranking["candidates"][0]
    assert all(candidate["components"] and candidate["reason_codes"]
               for candidate in ranking["candidates"])


@pytest.mark.parametrize(("metric", "reason"), [
    ({"valid": False}, "invalid_walk_forward"),
    ({"valid": True, "vintages": 20, "predictions": 1000, "mean_excess_return": -.1,
      "rank_correlation": .2, "positive_period_rate": .8,
      "regional_excess_return": {r: .1 for r, _ in REGIONS}}, "baseline_underperformance"),
    ({"valid": True, "vintages": 20, "predictions": 1000, "mean_excess_return": .1,
      "rank_correlation": .2, "positive_period_rate": .8,
      "regional_excess_return": {**{r: .1 for r, _ in REGIONS}, "PA": -.1}}, "regional_instability"),
])
def test_weak_evidence_withholds_without_candidates(metric, reason):
    observations, prices, decision = synthetic_frames()
    gates = evaluate_evidence(observations=observations, evaluation=metric,
                              integrity_ok=True, policy=permissive())
    ranking = build_research_ranking(
        prepare_cross_section(observations, prices, decision_at=decision), gates,
        policy=permissive())
    assert reason in gates["reasons"]
    assert ranking["status"] == "ranking_withheld"
    assert ranking["candidates"] == [] and ranking["highest_conviction_candidate"] is None


def test_inadequate_coverage_excludes_withheld_from_denominator_metrics():
    observations, _, _ = synthetic_frames()
    observations.loc[:15, "eligible"] = False
    metrics = {"valid": True, "vintages": 20, "predictions": 1000,
        "mean_excess_return": .1, "rank_correlation": .2, "positive_period_rate": .8,
        "regional_excess_return": {r: .1 for r, _ in REGIONS}}
    gates = evaluate_evidence(observations=observations, evaluation=metrics,
                              integrity_ok=True, policy=permissive(minimum_coverage=.9))
    assert "inadequate_coverage" in gates["reasons"]


def test_deterministic_ties_and_zero_to_three_candidate_limit():
    observations, prices, decision = synthetic_frames(per_region=2)
    scores = prepare_cross_section(observations, prices, decision_at=decision)
    scores["composite_score"] = 60
    scores = scores.sort_values("qualified_symbol").reset_index(drop=True)
    gates = {"passed": True, "reasons": []}
    for count in range(4):
        policy = permissive(maximum_candidates=count, minimum_candidate_score=0)
        first = build_research_ranking(scores, gates, policy=policy, synthetic=True)
        second = build_research_ranking(scores, gates, policy=policy, synthetic=True)
        assert first == second and len(first["candidates"]) == count
        assert [x["qualified_symbol"] for x in first["candidates"]] == sorted(
            x["qualified_symbol"] for x in first["candidates"])


def test_read_only_cli_preserves_both_database_fingerprints(tmp_path: Path):
    research, production = tmp_path / "research.duckdb", tmp_path / "production.duckdb"
    create_research_fixture(research)
    with duckdb.connect(str(production)) as db:
        db.execute("CREATE TABLE guard(value INTEGER)")
    digest = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
    before = digest(research), digest(production)
    report = assess_research_scoring(research_db=research, production_db=production,
                                     decision_at=DECISION)
    assert report["ranking"]["status"] == "ranking_withheld"
    assert report["prior_readiness_result_implies_gate_pass"] is False
    assert all(x["unchanged"] for x in report["database_fingerprints"].values())
    assert before == (digest(research), digest(production))
    args = build_parser().parse_args(["research-scoring", "--research-db", str(research),
        "--production-db", str(production), "--decision-at", DECISION.isoformat()])
    assert execute(args, now=DECISION)["command"] == "research-scoring"
