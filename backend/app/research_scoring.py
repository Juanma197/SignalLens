"""Milestone 16 point-in-time research scoring and evidence gates.

This module extends the existing model-ready observation, multifactor and
walk-forward research architecture.  It is deliberately read-only and cannot
publish a production ranking.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pandas as pd

from .model_readiness import ReadinessError, _frame, _same_file, _validate_schema, fingerprint
from .multifactor import FACTOR_WEIGHTS, MOMENTUM_DAYS, RISK_DAYS, TREND_DAYS
from .multifactor_backtest import multifactor_score_backtest
from .research_evaluation import bootstrap_mean_ci
from .research_observations import EXPECTED_REGIONS, ObservationPolicy, build_model_ready_observations

REASON_CODES = frozenset({
    "inadequate_coverage", "inadequate_history", "invalid_walk_forward",
    "baseline_underperformance", "insufficient_discrimination",
    "regional_instability", "temporal_instability", "integrity_failure",
})
ZERO_VINTAGE_REASON_CODES = frozenset({
    "no_eligible_securities", "no_visible_prices_by_evaluation_cutoff",
    "insufficient_feature_history", "no_complete_label_periods",
    "no_feature_eligible_rows", "no_label_eligible_rows",
})
CANDIDATE_REASON_CODES = frozenset({
    "POSITIVE_MOMENTUM", "POSITIVE_TREND", "LOWER_REGIONAL_RISK",
    "FUNDAMENTALS_UNAVAILABLE",
})


@dataclass(frozen=True)
class EvidencePolicy:
    minimum_coverage: float = .90
    minimum_eligible: int = 100
    minimum_vintages: int = 12
    minimum_predictions: int = 500
    minimum_regions: int = 5
    minimum_region_vintages: int = 3
    minimum_excess_return: float = 0.0
    minimum_rank_correlation: float = 0.0
    minimum_positive_period_rate: float = .50
    maximum_candidates: int = 3
    minimum_candidate_score: float = 55.0

    def __post_init__(self) -> None:
        if not 0 <= self.minimum_coverage <= 1:
            raise ValueError("minimum_coverage must be between zero and one")
        if not 0 <= self.maximum_candidates <= 3:
            raise ValueError("maximum_candidates must be between zero and three")


def _rank(values: pd.Series, *, higher: bool = True) -> pd.Series:
    """Stable, tie-neutral percentile; never uses another decision date."""
    if values.nunique(dropna=True) <= 1:
        return pd.Series(.5, index=values.index)
    return values.rank(method="average", pct=True, ascending=higher)


def prepare_cross_section(
    observations: pd.DataFrame, prices: pd.DataFrame, *, decision_at: datetime,
    knowledge_cutoff: datetime | None = None,
) -> pd.DataFrame:
    """Prepare region-neutral factors for explicitly model-ready observations.

    Price rows must have been retrieved by the decision timestamp. Prices are
    local adjusted closes: ratios are currency invariant, avoiding a false
    comparison between USD, CAD, EUR, GBP and GBX price levels.
    """
    required = {"qualified_symbol", "region", "currency", "eligible", "decision_at"}
    missing = required - set(observations)
    if missing:
        raise ValueError(f"Missing observation columns: {sorted(missing)}")
    boundary = pd.Timestamp(decision_at)
    if boundary.tzinfo is None:
        raise ValueError("decision_at must be timezone-aware")
    boundary = boundary.tz_convert("UTC")
    ready = observations.loc[observations["eligible"].eq(True)].copy()  # noqa: E712
    ready = ready.loc[pd.to_datetime(ready["decision_at"], utc=True).eq(boundary)]
    if ready.empty:
        return pd.DataFrame()

    # ``knowledge_cutoff`` is deliberately separate from the feature boundary.
    # A historical database is commonly backfilled in one ingestion run, so its
    # audit timestamp is later than the observation's effective trading date.
    # Evaluation may use records loaded by the final evaluation cutoff, but the
    # feature boundary below still excludes every later trading observation.
    known_at = pd.Timestamp(knowledge_cutoff or decision_at)
    if known_at.tzinfo is None:
        raise ValueError("knowledge_cutoff must be timezone-aware")
    known_at = known_at.tz_convert("UTC")
    frame = prices.copy()
    frame["retrieved_at"] = pd.to_datetime(frame["retrieved_at"], utc=True)
    frame["trading_date"] = pd.to_datetime(frame["trading_date"])
    frame = frame.loc[
        frame["qualified_symbol"].isin(ready["qualified_symbol"])
        & frame["retrieved_at"].le(known_at)
        & frame["trading_date"].le(boundary.tz_localize(None))
        & frame["status"].eq("available")
    ].sort_values(["qualified_symbol", "trading_date"])
    rows: list[dict[str, Any]] = []
    identity = ready.set_index("qualified_symbol")
    for symbol, group in frame.groupby("qualified_symbol", sort=True):
        closes = pd.to_numeric(group["adjusted_close"], errors="coerce").dropna()
        if len(closes) <= MOMENTUM_DAYS:
            continue
        returns = closes.pct_change().dropna().iloc[-RISK_DAYS:]
        recent = closes.iloc[-(MOMENTUM_DAYS + 1):]
        rows.append({
            "security_id": identity.loc[symbol, "security_id"],
            "qualified_symbol": symbol,
            "region": identity.loc[symbol, "region"],
            "currency": identity.loc[symbol, "currency"],
            "decision_at": boundary,
            "feature_available_at": min(boundary, group["retrieved_at"].max()),
            "momentum_126d": closes.iloc[-1] / closes.iloc[-(MOMENTUM_DAYS + 1)] - 1,
            "trend_21d": closes.iloc[-1] / closes.iloc[-(TREND_DAYS + 1)] - 1,
            "annualized_volatility_63d": returns.std(ddof=0) * np.sqrt(252),
            "max_drawdown_126d": (recent / recent.cummax() - 1).min(),
        })
    result = pd.DataFrame(rows)
    if result.empty:
        return result
    factor_specs = {
        "momentum": ("momentum_126d", True), "trend": ("trend_21d", True),
        "risk_quality": ("annualized_volatility_63d", False),
    }
    # Normalize inside each market, preventing currency/market composition from
    # determining scores. Average regional percentiles with a global tie-break.
    for name, (column, higher) in factor_specs.items():
        regional = result.groupby("region", group_keys=False)[column].transform(
            lambda values: _rank(values, higher=higher)
        )
        global_rank = _rank(result[column], higher=higher)
        result[f"{name}_factor"] = .8 * regional + .2 * global_rank
    # Fundamentals/catalysts are honestly unavailable in the M14/15 market-data
    # schema. Use the existing multifactor neutral convention, not invented data.
    result["profitability_factor"] = .5
    result["balance_sheet_factor"] = .5
    result["max_drawdown_factor"] = result.groupby("region", group_keys=False)[
        "max_drawdown_126d"
    ].transform(lambda values: _rank(values, higher=True))
    result["risk_quality_factor"] = (
        result["risk_quality_factor"] + result["max_drawdown_factor"]
    ) / 2
    result["composite_score"] = 100 * sum(
        result[f"{name}_factor"] * weight for name, weight in FACTOR_WEIGHTS.items()
    )
    result["reason_codes"] = result.apply(
        lambda row: [
            code for code, active in (
                ("POSITIVE_MOMENTUM", row.momentum_126d > 0),
                ("POSITIVE_TREND", row.trend_21d > 0),
                ("LOWER_REGIONAL_RISK", row.risk_quality_factor >= .5),
                ("FUNDAMENTALS_UNAVAILABLE", True),
            ) if active
        ], axis=1,
    )
    return result.sort_values(
        ["composite_score", "qualified_symbol"], ascending=[False, True]
    ).reset_index(drop=True)


def walk_forward_evidence(
    observations: pd.DataFrame, prices: pd.DataFrame, *, decision_at: datetime,
    horizon_sessions: int = 21,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Evaluate month-end vintages from a bounded historical panel.

    Membership is the current eligible catalogue (and therefore is explicitly
    not survivorship-free). Database ingestion timestamps are bounded by the
    final evaluation cutoff; effective trading dates provide each historical
    feature/label boundary. This distinction is required for a backfilled
    research database and never makes a future-dated price visible early.
    """
    eligible = set(observations.loc[observations["eligible"].eq(True), "qualified_symbol"])
    frame = prices.loc[prices["qualified_symbol"].isin(eligible)].copy()
    frame["retrieved_at"] = pd.to_datetime(frame["retrieved_at"], utc=True)
    frame["trading_date"] = pd.to_datetime(frame["trading_date"])
    boundary = pd.Timestamp(decision_at).tz_convert("UTC")
    frame = frame.loc[frame["retrieved_at"].le(boundary) & frame["status"].eq("available")]
    dates = pd.DatetimeIndex(sorted(frame["trading_date"].unique()))
    # Last observed session in each calendar month. Requiring both the complete
    # feature lookback and forward horizon excludes the incomplete final period.
    month_ends = (
        pd.Series(range(len(dates)), index=dates.to_period("M"))
        .groupby(level=0).last().astype(int).tolist()
    ) if len(dates) else []
    possible = [index for index in month_ends if index >= MOMENTUM_DAYS]
    vintage_indexes = [index for index in possible if index + horizon_sessions < len(dates)]
    identity = observations.set_index("qualified_symbol")
    close_lookup = frame.set_index(["qualified_symbol", "trading_date"])["adjusted_close"]
    rows = []
    feature_rows = label_rows = 0
    removed_missing_feature = removed_missing_label = 0
    for index in vintage_indexes:
        vintage_date, label_date = pd.Timestamp(dates[index]), pd.Timestamp(dates[index + horizon_sessions])
        # Rebuild features at each cutoff. Retrieval availability remains explicit.
        vintage_obs = observations.copy()
        vintage_at = vintage_date.tz_localize("UTC") + pd.Timedelta(hours=23)
        vintage_obs["decision_at"] = vintage_at
        scores = prepare_cross_section(
            vintage_obs, frame, decision_at=vintage_at.to_pydatetime(),
            knowledge_cutoff=boundary.to_pydatetime(),
        )
        feature_rows += len(scores)
        removed_missing_feature += max(0, len(eligible) - len(scores))
        for score in scores.itertuples(index=False):
            try:
                entry = float(close_lookup.loc[(score.qualified_symbol, vintage_date)])
                exit_value = float(close_lookup.loc[(score.qualified_symbol, label_date)])
            except KeyError:
                removed_missing_label += 1
                continue
            label_rows += 1
            rows.append({
                "qualified_symbol": score.qualified_symbol,
                "region": identity.loc[score.qualified_symbol, "region"],
                "vintage_date": vintage_date,
                "label_date": label_date,
                "score": score.composite_score,
                "forward_return": exit_value / entry - 1,
            })
    predictions = pd.DataFrame(rows)
    def date_value(value: Any) -> str | None:
        return pd.Timestamp(value).date().isoformat() if value is not None else None

    zero_reasons: list[str] = []
    if not eligible:
        zero_reasons.append("no_eligible_securities")
    if frame.empty:
        zero_reasons.append("no_visible_prices_by_evaluation_cutoff")
    if not possible:
        zero_reasons.append("insufficient_feature_history")
    if possible and not vintage_indexes:
        zero_reasons.append("no_complete_label_periods")
    if vintage_indexes and not feature_rows:
        zero_reasons.append("no_feature_eligible_rows")
    if feature_rows and not label_rows:
        zero_reasons.append("no_label_eligible_rows")
    diagnostics = {
        "cadence": "calendar_month_end_session",
        "selected_securities": int(len(observations)),
        "model_ready_securities": int(len(eligible)),
        "loaded_securities": int(frame["qualified_symbol"].nunique()) if not frame.empty else 0,
        "available_price_date_range": {
            "earliest": date_value(frame["trading_date"].min()) if not frame.empty else None,
            "latest": date_value(frame["trading_date"].max()) if not frame.empty else None,
        },
        "possible_decision_dates": int(len(possible)),
        "generated_decision_vintages": int(len(vintage_indexes)),
        "feature_eligible_rows": int(feature_rows),
        "label_eligible_rows": int(label_rows),
        "rows_removed": {
            "not_model_ready_at_final_cutoff": int(len(observations) - len(eligible)),
            "missing_feature_history": int(removed_missing_feature),
            "incomplete_or_missing_label": int(removed_missing_label),
            "incomplete_final_period_decision_dates": int(len(possible) - len(vintage_indexes)),
        },
        "earliest_feature_date": date_value(dates[vintage_indexes[0]]) if vintage_indexes else None,
        "latest_feature_date": date_value(dates[vintage_indexes[-1]]) if vintage_indexes else None,
        "earliest_label_date": date_value(dates[vintage_indexes[0] + horizon_sessions]) if vintage_indexes else None,
        "latest_label_date": date_value(dates[vintage_indexes[-1] + horizon_sessions]) if vintage_indexes else None,
        "zero_vintage_reason_codes": zero_reasons,
        "membership_basis": "current_catalogue_not_survivorship_free",
        "availability_semantics": "loaded_by_final_cutoff_effective_by_trading_date",
        "historical_fx_semantics": "model_ready_asof_fx_for_each_price_date",
    }
    if predictions.empty:
        return predictions, {"valid": False, "vintages": 0, "predictions": 0,
                             "diagnostics": diagnostics}
    predictions["score_rank"] = predictions.groupby("vintage_date")["score"].rank(pct=True)
    predictions["return_rank"] = predictions.groupby("vintage_date")["forward_return"].rank(pct=True)
    correlations = predictions.groupby("vintage_date").apply(
        lambda x: x["score_rank"].corr(x["return_rank"]), include_groups=False
    ).dropna()
    top = predictions.loc[predictions.groupby("vintage_date")["score"].rank(
        method="first", ascending=False
    ).le(3)]
    top_period = top.groupby("vintage_date")["forward_return"].mean()
    baseline = predictions.groupby("vintage_date")["forward_return"].mean()
    excess = top_period - baseline
    backtest_input = predictions.rename(columns={
        "qualified_symbol": "ticker", "vintage_date": "as_of_date",
        "label_date": "exit_date", "score": "composite_score",
    }).copy()
    backtest_input["entry_date"] = backtest_input["as_of_date"] + pd.Timedelta(days=1)
    backtest_input["eligible"] = True
    backtest = multifactor_score_backtest(
        backtest_input, top_k=3, transaction_cost_bps_per_side=0
    )
    regions = predictions.groupby("region").apply(
        lambda x: x.nlargest(max(1, len(x) // max(1, x["vintage_date"].nunique()) * 3), "score")["forward_return"].mean()
        - x["forward_return"].mean(), include_groups=False
    )
    metrics = {
        "valid": bool((predictions["label_date"] > predictions["vintage_date"]).all()),
        "vintages": int(predictions["vintage_date"].nunique()),
        "predictions": int(len(predictions)),
        "mean_excess_return": backtest.summary["top_k_mean_excess_return"],
        "excess_return_95_ci": bootstrap_mean_ci(excess, seed=16),
        "positive_period_rate": float((excess > 0).mean()),
        "rank_correlation": float(correlations.mean()) if len(correlations) else None,
        "calibration": {"method": "score_quintile_observed_return", "bins": [
            {"quintile": int(key), "mean_return": float(value)}
            for key, value in predictions.assign(quintile=pd.qcut(
                predictions["score_rank"], 5, labels=False, duplicates="drop"
            )).groupby("quintile")["forward_return"].mean().items()
        ]},
        "regional_excess_return": {str(k): float(v) for k, v in regions.items()},
        "regional_vintages": {
            str(k): int(v) for k, v in predictions.groupby("region")["vintage_date"].nunique().items()
        },
        "diagnostics": diagnostics,
    }
    return predictions, metrics


def evaluate_evidence(
    *, observations: pd.DataFrame, evaluation: dict[str, Any], integrity_ok: bool,
    policy: EvidencePolicy = EvidencePolicy(),
) -> dict[str, Any]:
    eligible = observations.loc[observations["eligible"].eq(True)]
    coverage = len(eligible) / len(observations) if len(observations) else 0
    region_counts = eligible["region"].value_counts()
    correlation = evaluation.get("rank_correlation")
    checks = {
        "eligible_universe_coverage": coverage >= policy.minimum_coverage
            and len(eligible) >= policy.minimum_eligible,
        "historical_sample_size": evaluation.get("vintages", 0) >= policy.minimum_vintages
            and evaluation.get("predictions", 0) >= policy.minimum_predictions,
        "valid_walk_forward": bool(evaluation.get("valid")),
        "baseline_performance": evaluation.get("mean_excess_return", -np.inf) > policy.minimum_excess_return,
        "discrimination": correlation is not None and correlation > policy.minimum_rank_correlation,
        "temporal_stability": evaluation.get("positive_period_rate", 0) >= policy.minimum_positive_period_rate,
        "regional_stability": len(region_counts) >= policy.minimum_regions
            and all(value > policy.minimum_excess_return for value in evaluation.get("regional_excess_return", {}).values())
            and len(evaluation.get("regional_excess_return", {})) >= policy.minimum_regions
            and all(value >= policy.minimum_region_vintages
                    for value in evaluation.get("regional_vintages", {}).values())
            and len(evaluation.get("regional_vintages", {})) >= policy.minimum_regions,
        "integrity": integrity_ok,
    }
    reason_map = {
        "eligible_universe_coverage": "inadequate_coverage", "historical_sample_size": "inadequate_history",
        "valid_walk_forward": "invalid_walk_forward", "baseline_performance": "baseline_underperformance",
        "discrimination": "insufficient_discrimination", "temporal_stability": "temporal_instability",
        "regional_stability": "regional_instability", "integrity": "integrity_failure",
    }
    failed = [reason_map[name] for name, passed in checks.items() if not passed]
    return {"passed": not failed, "checks": checks, "reasons": failed,
            "coverage": coverage, "policy": asdict(policy)}


def build_research_ranking(
    scores: pd.DataFrame, gates: dict[str, Any], *, policy: EvidencePolicy = EvidencePolicy(),
    synthetic: bool = False,
) -> dict[str, Any]:
    if not gates["passed"]:
        return {"status": "ranking_withheld", "reasons": gates["reasons"], "candidates": [],
                "highest_conviction_candidate": None}
    candidates = scores.loc[scores["composite_score"].ge(policy.minimum_candidate_score)].head(
        policy.maximum_candidates
    )
    payload = [{
        "rank": index + 1, "security_id": row.security_id,
        "qualified_symbol": row.qualified_symbol, "region": row.region,
        "score": round(float(row.composite_score), 6), "components": {
            name: round(float(getattr(row, f"{name}_factor")), 6)
            for name in FACTOR_WEIGHTS
        }, "reason_codes": row.reason_codes,
    } for index, row in enumerate(candidates.itertuples(index=False))]
    return {"status": "research_ranking_available", "synthetic": synthetic,
            "research_only": True, "not_investment_advice": True, "candidates": payload,
            "highest_conviction_candidate": payload[0] if payload else None}


def assess_research_scoring(*, research_db: Path, production_db: Path,
                            decision_at: datetime | None = None,
                            policy: EvidencePolicy = EvidencePolicy()) -> dict[str, Any]:
    """Read and score an existing research database without changing either DB."""
    research_db, production_db = Path(research_db), Path(production_db)
    before = {"research": fingerprint(research_db), "production": fingerprint(production_db)}
    if not before["research"].exists:
        raise ReadinessError("research database does not exist; refusing to create it")
    if _same_file(research_db, production_db):
        raise ReadinessError("research and production database paths are identical or aliased")
    captured = decision_at or datetime.now(timezone.utc)
    if captured.tzinfo is None:
        raise ReadinessError("decision_at must be timezone-aware")
    boundary = pd.Timestamp(captured).tz_convert("UTC").tz_localize(None).to_pydatetime()
    with duckdb.connect(str(research_db), read_only=True) as connection:
        _validate_schema(connection, boundary)
        retrieval = connection.execute("""SELECT retrieval_id FROM security_master_retrievals
            WHERE status='completed' AND retrieved_at<=? ORDER BY retrieved_at DESC,retrieval_id DESC LIMIT 1""",
            [boundary]).fetchone()
        if not retrieval:
            raise ReadinessError("no completed active catalogue exists at the decision boundary")
        catalogue = _frame(connection, """SELECT security_id,qualified_symbol,
            UPPER(primary_exchange) region,UPPER(currency) currency,
            (active AND instrument_type IN ('common_stock','ordinary_share')) eligible
            FROM security_listings WHERE retrieval_id=?""", [retrieval[0]])
        prices = _frame(connection, "SELECT * FROM global_price_observations")
        fx = _frame(connection, "SELECT * FROM global_fx_observations")
        actions = _frame(connection, "SELECT * FROM global_corporate_actions")
        failures = _frame(connection, """SELECT qualified_symbol,error_code FROM eodhd_ingestion_checkpoints
            WHERE status='failed' AND error_code IS NOT NULL""")
    dataset = build_model_ready_observations(catalogue=catalogue, prices=prices, fx=fx,
        actions=actions, failures=failures, decision_at=captured, policy=ObservationPolicy())
    scores = prepare_cross_section(dataset.observations, prices, decision_at=captured)
    _, evaluation = walk_forward_evidence(dataset.observations, prices, decision_at=captured)
    after = {"research": fingerprint(research_db), "production": fingerprint(production_db)}
    unchanged = all(before[key] == after[key] for key in before)
    if not unchanged:
        raise ReadinessError("database fingerprint changed during read-only research scoring")
    gates = evaluate_evidence(observations=dataset.observations, evaluation=evaluation,
                              integrity_ok=unchanged, policy=policy)
    ranking = build_research_ranking(scores, gates, policy=policy)
    return {"command": "research-scoring", "mode": "strictly_read_only",
        "decision_at": pd.Timestamp(captured).isoformat(), "label": "RESEARCH ONLY — NOT INVESTMENT ADVICE",
        "prior_readiness_result_implies_gate_pass": False,
        "survivorship_free": False,
        "membership_basis": "current_catalogue_not_survivorship_free",
        "model_ready_securities": int(dataset.observations.eligible.sum()),
        "withheld_securities": int((~dataset.observations.eligible).sum()),
        "feature_evidence": {"available": ["adjusted_close_momentum_126d", "trend_21d",
            "volatility_63d", "drawdown_126d", "historical_fx_converted_observations"],
            "unavailable": ["point_in_time_fundamentals", "valuation", "quality", "catalyst", "sentiment"]},
        "current_scoring_cross_section": {
            "purpose": "potential_candidates_only_if_all_evidence_gates_pass",
            "rows": int(len(scores)),
        },
        "historical_evaluation_panel": {
            "purpose": "evidence_evaluation_only_never_candidate_selection",
            **evaluation["diagnostics"],
        },
        "evaluation": evaluation, "evidence_gates": gates, "ranking": ranking,
        "database_fingerprints": {key: {"before": asdict(before[key]), "after": asdict(after[key]),
            "unchanged": before[key] == after[key]} for key in before}}
