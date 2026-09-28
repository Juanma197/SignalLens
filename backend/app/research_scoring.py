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
from .research_evaluation import bootstrap_mean_ci
from .research_observations import EXPECTED_REGIONS, ObservationPolicy, build_model_ready_observations

REASON_CODES = frozenset({
    "inadequate_coverage", "inadequate_history", "invalid_walk_forward",
    "baseline_underperformance", "insufficient_discrimination",
    "regional_instability", "temporal_instability", "integrity_failure",
    "label_integrity_failure", "return_concentration", "insufficient_group_size",
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
    minimum_correlation_group_size: int = 2
    near_zero_adjusted_close: float = .01
    extreme_absolute_return: float = 10.0
    maximum_top_observation_contribution: float = .25
    maximum_candidates: int = 3
    minimum_candidate_score: float = 55.0

    def __post_init__(self) -> None:
        if not 0 <= self.minimum_coverage <= 1:
            raise ValueError("minimum_coverage must be between zero and one")
        if not 0 <= self.maximum_candidates <= 3:
            raise ValueError("maximum_candidates must be between zero and three")
        if self.minimum_correlation_group_size < 2:
            raise ValueError("minimum_correlation_group_size must be at least two")
        if self.near_zero_adjusted_close <= 0 or self.extreme_absolute_return <= 0:
            raise ValueError("label-integrity thresholds must be positive")
        if not 0 <= self.maximum_top_observation_contribution <= 1:
            raise ValueError("maximum contribution must be between zero and one")


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
    horizon_sessions: int = 21, actions: pd.DataFrame | None = None,
    near_zero_adjusted_close: float = .01, extreme_absolute_return: float = 10.0,
    minimum_correlation_group_size: int = 2,
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
    duplicate_prices = frame.duplicated(["qualified_symbol", "trading_date"], keep=False)
    close_lookup = frame.loc[~duplicate_prices].set_index(
        ["qualified_symbol", "trading_date"]
    )["adjusted_close"]
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
            with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
                forward_return = exit_value / entry - 1
            rows.append({
                "qualified_symbol": score.qualified_symbol,
                "region": identity.loc[score.qualified_symbol, "region"],
                "vintage_date": vintage_date,
                "label_date": label_date,
                "score": score.composite_score, "entry_adjusted_close": entry,
                "exit_adjusted_close": exit_value, "forward_return": forward_return,
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
        "duplicate_price_keys": int(duplicate_prices.sum()),
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
    metrics = _evaluate_predictions(
        predictions, diagnostics, actions=actions, near_zero=near_zero_adjusted_close,
        extreme=extreme_absolute_return, minimum_group=minimum_correlation_group_size,
    )
    return predictions, metrics


def _summary(values: pd.Series) -> dict[str, Any]:
    """Bounded decimal-return distribution summary (never serializes raw payloads)."""
    numeric = pd.to_numeric(values, errors="coerce")
    finite = numeric[np.isfinite(numeric)]
    result: dict[str, Any] = {"count": int(len(numeric)), "finite": int(len(finite)),
                              "nonfinite": int(len(numeric) - len(finite))}
    for name, quantile in (("minimum", 0), ("p01", .01), ("p05", .05),
                           ("median", .5), ("p95", .95), ("p99", .99), ("maximum", 1)):
        result[name] = float(finite.quantile(quantile)) if len(finite) else None
    return result


def _trimmed_mean(values: pd.Series, proportion: float = .1) -> float | None:
    finite = np.sort(pd.to_numeric(values, errors="coerce").to_numpy(dtype=float))
    finite = finite[np.isfinite(finite)]
    trim = int(len(finite) * proportion)
    kept = finite[trim:len(finite) - trim] if trim and len(finite) > 2 * trim else finite
    return float(kept.mean()) if len(kept) else None


def _evaluate_predictions(predictions: pd.DataFrame, diagnostics: dict[str, Any], *,
                          actions: pd.DataFrame | None = None,
                          near_zero: float = .01, extreme: float = 10.0,
                          minimum_group: int = 2) -> dict[str, Any]:
    """Validate labels first, then calculate all evidence at the vintage level."""
    labels = predictions.copy()
    finite = np.isfinite(labels[["score", "entry_adjusted_close", "exit_adjusted_close",
                                 "forward_return"]].to_numpy(dtype=float)).all(axis=1)
    invalid_denominator = (~np.isfinite(labels["entry_adjusted_close"])) | \
        labels["entry_adjusted_close"].le(0)
    near_zero_denominator = labels["entry_adjusted_close"].abs().le(near_zero)
    duplicate_labels = labels.duplicated(["qualified_symbol", "vintage_date"], keep=False)
    extreme_mask = labels["forward_return"].abs().gt(extreme) & np.isfinite(labels["forward_return"])
    valid_mask = finite & ~invalid_denominator & ~near_zero_denominator & ~duplicate_labels
    valid = labels.loc[valid_mask].copy()

    action_keys: set[tuple[str, pd.Timestamp]] = set()
    if actions is not None and not actions.empty and {"qualified_symbol", "ex_date"} <= set(actions):
        action_keys = {(str(row.qualified_symbol), pd.Timestamp(row.ex_date))
                       for row in actions[["qualified_symbol", "ex_date"]].itertuples(index=False)}
    samples = []
    for row in labels.loc[extreme_mask].sort_values("forward_return", key=lambda x: x.abs(),
                                                    ascending=False).head(10).itertuples():
        proximity = any(symbol == row.qualified_symbol and
                        row.vintage_date - pd.Timedelta(days=7) <= day <= row.label_date + pd.Timedelta(days=7)
                        for symbol, day in action_keys)
        reason = "near_zero_denominator" if abs(row.entry_adjusted_close) <= near_zero else \
            ("corporate_action_proximity" if proximity else "unresolved_extreme_return")
        samples.append({"qualified_symbol": str(row.qualified_symbol),
                        "vintage_date": pd.Timestamp(row.vintage_date).date().isoformat(),
                        "label_date": pd.Timestamp(row.label_date).date().isoformat(),
                        "reason": reason, "corporate_action_within_7_days": proximity})

    group_sizes = valid.groupby("vintage_date").size()
    eligible_groups = group_sizes[group_sizes.ge(minimum_group)].index
    correlations, insufficient = [], []
    for vintage, group in valid.groupby("vintage_date"):
        if len(group) < minimum_group or group["score"].nunique() < 2 or group["forward_return"].nunique() < 2:
            insufficient.append(pd.Timestamp(vintage).date().isoformat())
            continue
        correlations.append(float(group["score"].rank(pct=True).corr(
            group["forward_return"].rank(pct=True))))
    valid["score_rank"] = valid.groupby("vintage_date")["score"].rank(pct=True)
    valid["return_rank"] = valid.groupby("vintage_date")["forward_return"].rank(pct=True)
    top = valid.loc[valid.groupby("vintage_date")["score"].rank(method="first", ascending=False).le(3)]
    top_period = top.groupby("vintage_date")["forward_return"].mean()
    baseline = valid.groupby("vintage_date")["forward_return"].mean()
    excess = top_period - baseline
    region_period = []
    for (region, vintage), group in valid.groupby(["region", "vintage_date"]):
        selected = group.nlargest(min(3, len(group)), "score")
        region_period.append((region, vintage, selected.forward_return.mean() - group.forward_return.mean()))
    region_frame = pd.DataFrame(region_period, columns=["region", "vintage_date", "excess"])
    regions = region_frame.groupby("region")["excess"].mean() if len(region_frame) else pd.Series(dtype=float)
    abs_contribution = valid["forward_return"].abs().sort_values(ascending=False)
    total_abs = float(abs_contribution.sum())
    concentration = {f"top_{count}": (float(abs_contribution.head(count).sum() / total_abs)
                                      if total_abs else 0.0) for count in (1, 5, 10)}
    per_region = {str(region): _summary(group["forward_return"])
                  for region, group in valid.groupby("region")}
    diagnostics["label_integrity"] = {
        "units": "decimal_return", "forward_returns": _summary(labels["forward_return"]),
        "excess_returns": _summary(excess), "invalid_denominators": int(invalid_denominator.sum()),
        "nonfinite_labels": int((~finite).sum()),
        "near_zero_denominators": int(near_zero_denominator.sum()),
        "duplicate_labels": int(duplicate_labels.sum()), "unresolved_extreme_returns": int(extreme_mask.sum()),
        "thresholds": {"near_zero_adjusted_close": near_zero, "extreme_absolute_return": extreme},
        "observations_beyond_threshold": int(extreme_mask.sum()), "per_region": per_region,
        "largest_absolute_contributors": samples, "absolute_contribution_concentration": concentration,
        "vintage_security_count": _summary(group_sizes),
        "correlation_groups": {"eligible": int(len(eligible_groups)),
                               "insufficient_group_size": insufficient[:10],
                               "insufficient_count": int(len(insufficient))},
        "corporate_action_proximity_count": int(sum(x["corporate_action_within_7_days"] for x in samples)),
    }
    integrity_ok = not ((~finite).any() or invalid_denominator.any() or near_zero_denominator.any()
                        or duplicate_labels.any() or extreme_mask.any()
                        or diagnostics.get("duplicate_price_keys", 0))
    calibration = []
    if len(valid):
        # score_rank is already a bounded percentile. Direct binning avoids
        # qcut's small-sample variance path and its NumPy underflow warning.
        quintile = np.minimum((valid["score_rank"] * 5).apply(np.ceil).astype(int) - 1, 4)
        calibrated = valid.assign(quintile=quintile)
        calibration = [{"quintile": int(key), "mean_return": float(group.mean()),
                        "median_return": float(group.median())}
                       for key, group in calibrated.groupby("quintile")["forward_return"]]
    metrics = {
        "valid": bool((labels["label_date"] > labels["vintage_date"]).all()) and integrity_ok,
        "vintages": int(predictions["vintage_date"].nunique()),
        "predictions": int(len(predictions)),
        "mean_excess_return": float(excess.mean()) if len(excess) else None,
        "median_excess_return": float(excess.median()) if len(excess) else None,
        "trimmed_mean_excess_return": _trimmed_mean(excess),
        "equal_weight_per_vintage_mean_excess_return": float(excess.mean()) if len(excess) else None,
        "excess_return_95_ci": bootstrap_mean_ci(excess, seed=16),
        "positive_period_rate": float((excess > 0).mean()) if len(excess) else None,
        "rank_correlation": float(np.mean(correlations)) if correlations else None,
        "rank_correlation_state": "valid" if correlations else "insufficient_group_size",
        "calibration": {"method": "score_rank_quintile_pooled_observed_decimal_return",
                        "bins": calibration},
        "regional_excess_return": {str(k): float(v) for k, v in regions.items()},
        "regional_vintages": {
            str(k): int(v) for k, v in valid.groupby("region")["vintage_date"].nunique().items()
        },
        "diagnostics": diagnostics,
    }
    return metrics


def evaluate_evidence(
    *, observations: pd.DataFrame, evaluation: dict[str, Any], integrity_ok: bool,
    policy: EvidencePolicy = EvidencePolicy(),
) -> dict[str, Any]:
    eligible = observations.loc[observations["eligible"].eq(True)]
    coverage = len(eligible) / len(observations) if len(observations) else 0
    region_counts = eligible["region"].value_counts()
    correlation = evaluation.get("rank_correlation")
    label_diagnostics = evaluation.get("diagnostics", {}).get("label_integrity", {})
    concentration = label_diagnostics.get("absolute_contribution_concentration", {})
    mean_excess = evaluation.get("mean_excess_return")
    positive_rate = evaluation.get("positive_period_rate")
    checks = {
        "eligible_universe_coverage": coverage >= policy.minimum_coverage
            and len(eligible) >= policy.minimum_eligible,
        "historical_sample_size": evaluation.get("vintages", 0) >= policy.minimum_vintages
            and evaluation.get("predictions", 0) >= policy.minimum_predictions,
        "valid_walk_forward": bool(evaluation.get("valid")),
        "baseline_performance": mean_excess is not None and np.isfinite(mean_excess)
            and mean_excess > policy.minimum_excess_return,
        "discrimination": correlation is not None and correlation > policy.minimum_rank_correlation,
        "label_integrity": label_diagnostics.get("nonfinite_labels", 0) == 0
            and label_diagnostics.get("invalid_denominators", 0) == 0
            and label_diagnostics.get("near_zero_denominators", 0) == 0
            and label_diagnostics.get("duplicate_labels", 0) == 0
            and label_diagnostics.get("unresolved_extreme_returns", 0) == 0,
        "contribution_concentration": concentration.get("top_1", 1) <=
            policy.maximum_top_observation_contribution,
        "correlation_group_size": evaluation.get("rank_correlation_state") !=
            "insufficient_group_size",
        "temporal_stability": positive_rate is not None and np.isfinite(positive_rate)
            and positive_rate >= policy.minimum_positive_period_rate,
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
        "label_integrity": "label_integrity_failure",
        "contribution_concentration": "return_concentration",
        "correlation_group_size": "insufficient_group_size",
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
    _, evaluation = walk_forward_evidence(
        dataset.observations, prices, decision_at=captured, actions=actions,
        near_zero_adjusted_close=policy.near_zero_adjusted_close,
        extreme_absolute_return=policy.extreme_absolute_return,
        minimum_correlation_group_size=policy.minimum_correlation_group_size,
    )
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
