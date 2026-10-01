"""Milestone 27 read-only attribution for the frozen US fundamentals experiment.

The routines in this module are diagnostics, not a scoring model.  They accept
the exact matched rows produced by Milestone 26 and never impute a missing
factor.  Issuer output is deliberately bounded and symbols must already be the
qualified, sanitized symbols in the research catalogue.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .horizon_evaluation import horizon_block_inference
from .model_readiness import ReadinessError, _same_file, fingerprint
from .us_fundamentals import (FACTOR_DIRECTIONS, FACTOR_FAMILIES,
    FAMILY_WEIGHTS, FUNDAMENTAL_WEIGHT, LOCKED_HORIZONS, PRICE_WEIGHT,
    assess_us_fundamentals, configuration_hash)

OBSERVED_HEADLINE = {
    126: {"eligible_vintages": 101, "predictions": 4670,
          "price_only_mean_excess": .0159358, "enhanced_mean_excess": -.0256939,
          "incremental_excess_return": -.0416297,
          "confidence_interval": [-.1209685, .0224712], "raw_p_value": .8653,
          "adjusted_p_value": 1.0, "enhanced_concentration": .1056,
          "price_only_concentration": .0495},
    252: {"eligible_vintages": 97, "predictions": 4265,
          "price_only_mean_excess": -.0292988, "enhanced_mean_excess": -.0815601,
          "incremental_excess_return": -.0522613,
          "confidence_interval": [-.1400109, .0202068], "raw_p_value": .8920,
          "adjusted_p_value": 1.0, "enhanced_concentration": .1168,
          "price_only_concentration": .0515},
}
MAX_ITEMS = 10


def reconcile_frozen_headline(results: dict[str, Any], *, tolerance: float = 5e-5) -> dict[str, bool]:
    """Assert that diagnostics use the exact operator-observed Milestone 26 sample."""
    checks: dict[str, bool] = {}
    for horizon, expected in OBSERVED_HEADLINE.items():
        actual = results[str(horizon)]
        values = {"eligible_vintages": actual.get("eligible_vintages"),
            "predictions": actual.get("predictions"),
            "price_only_mean_excess": actual.get("price_only", {}).get("mean_excess_return"),
            "enhanced_mean_excess": actual.get("enhanced", {}).get("mean_excess_return"),
            "incremental_excess_return": actual.get("incremental_excess_return"),
            "raw_p_value": actual.get("multiple_testing", {}).get("raw_p_value"),
            "adjusted_p_value": actual.get("multiple_testing", {}).get("adjusted_p_value"),
            "enhanced_concentration": actual.get("enhanced", {}).get("concentration_top_security"),
            "price_only_concentration": actual.get("price_only", {}).get("concentration_top_security")}
        for name, target in expected.items():
            if name == "confidence_interval":
                observed = actual.get("incremental_inference", {}).get("confidence_interval")
                ok = observed is not None and len(observed) == 2 and all(
                    abs(float(a) - float(b)) <= tolerance for a, b in zip(observed, target))
            elif name in {"eligible_vintages", "predictions"}:
                ok = values[name] == target
            else: ok = values[name] is not None and abs(float(values[name]) - float(target)) <= tolerance
            checks[f"{horizon}_{name}"] = ok
    failed = sorted(name for name, passed in checks.items() if not passed)
    if failed: raise ReadinessError(f"Milestone 26 headline/sample reconciliation failed: {failed}")
    return checks


def adjust_pvalues(pvalues: dict[str, float | None]) -> dict[str, float | None]:
    """Holm adjustment across *all* exploratory comparisons."""
    finite = sorted((float(p), key) for key, p in pvalues.items()
                    if p is not None and np.isfinite(p))
    out: dict[str, float | None] = {key: None for key in pvalues}
    running = 0.0
    for index, (value, key) in enumerate(finite):
        running = max(running, min(1.0, value * (len(finite) - index)))
        out[key] = running
    return out


def _safe_float(value: Any) -> float | None:
    return None if value is None or not np.isfinite(value) else float(value)


def _quantile_evidence(group: pd.DataFrame, score: str) -> tuple[float | None, list[float | None], bool | None]:
    valid = group[[score, "forward_return"]].dropna()
    if len(valid) < 5 or valid[score].nunique() < 2:
        return None, [], None
    bins = pd.qcut(valid[score].rank(method="first"), min(5, len(valid)), labels=False)
    means = valid.assign(_bin=bins).groupby("_bin").forward_return.mean()
    spread = means.iloc[-1] - means.iloc[0]
    monotonic = bool(means.is_monotonic_increasing)
    return float(spread), [_safe_float(v) for v in means], monotonic


def factor_evidence(matched: pd.DataFrame, score: str, *, weight: float,
                    horizon: int, top_n: int = 3) -> dict[str, Any]:
    """Bounded exploratory evidence for one factor or family score."""
    required = {"security_id", "qualified_symbol", "vintage_date", "forward_return",
                "score", "fundamental_score", score}
    missing = required - set(matched)
    if missing:
        raise ValueError(f"matched rows missing columns: {sorted(missing)}")
    valid = matched.loc[pd.to_numeric(matched[score], errors="coerce").notna()].copy()
    total_by_vintage = matched.groupby("vintage_date").size()
    valid_by_vintage = valid.groupby("vintage_date").size().reindex(total_by_vintage.index, fill_value=0)
    coverage = valid_by_vintage / total_by_vintage
    correlations, spreads, monotonic, vintage_spreads = [], [], [], {}
    for vintage, group in valid.groupby("vintage_date", sort=True):
        if group[score].nunique() > 1 and group.forward_return.nunique() > 1:
            correlations.append(group[score].rank().corr(group.forward_return.rank()))
        spread, _, mono = _quantile_evidence(group, score)
        if spread is not None:
            spreads.append(spread); vintage_spreads[str(pd.Timestamp(vintage).date())] = spread
        if mono is not None: monotonic.append(mono)
    ordered = pd.Series(vintage_spreads, dtype=float).sort_values()
    selected = valid.loc[valid.groupby("vintage_date")[score].rank(method="first", ascending=False).le(top_n)]
    selected_counts = selected.groupby("qualified_symbol").size().sort_values(ascending=False)
    total_selected = max(int(len(selected)), 1)
    contributions = (valid[score] * weight).abs().sort_values(ascending=False)
    denominator = float(contributions.sum()) or 1.0
    halves = np.array_split(pd.Series(vintage_spreads, dtype=float), 2)
    overlap = 0.0
    if len(selected):
        price_top = matched.loc[matched.groupby("vintage_date")["score"].rank(
            method="first", ascending=False).le(top_n), ["security_id", "vintage_date"]]
        overlap = len(selected.merge(price_top, on=["security_id", "vintage_date"])) / len(selected)
    return {
        "label": "exploratory_non_confirmatory", "valid_observations": int(len(valid)),
        "coverage_by_vintage": {str(pd.Timestamp(k).date()): float(v) for k, v in coverage.items()},
        "withheld": {"missing_or_nonfinite": int(len(matched) - len(valid))},
        "mean_rank_correlation": _safe_float(pd.Series(correlations).mean()),
        "top_minus_bottom_quantile_return_spread": _safe_float(pd.Series(spreads).mean()),
        "quantile_monotonic_vintage_rate": _safe_float(pd.Series(monotonic, dtype=float).mean()),
        "positive_vintage_rate": _safe_float((ordered > 0).mean()) if len(ordered) else None,
        "half_means": [_safe_float(part.mean()) for part in halves if len(part)],
        "worst_vintages": [{"vintage": k, "spread": float(v)} for k, v in ordered.head(3).items()],
        "best_vintages": [{"vintage": k, "spread": float(v)} for k, v in ordered.tail(3).items()],
        "frozen_composite_contribution": _safe_float((valid[score] * weight).mean()),
        "final_score_contribution": _safe_float((valid[score] * weight * FUNDAMENTAL_WEIGHT * 100).mean()),
        "concentration": {"largest_1": float(contributions.head(1).sum() / denominator),
            "largest_3": float(contributions.head(3).sum() / denominator),
            "largest_10": float(contributions.head(10).sum() / denominator),
            "top_security_share": float(selected_counts.head(1).sum() / total_selected),
            "top_symbols": [{"qualified_symbol": str(k), "selections": int(v)}
                            for k, v in selected_counts.head(MAX_ITEMS).items()]},
        "price_score_rank_correlation": _safe_float(valid[score].rank().corr(valid["score"].rank())),
        "selected_top_group_overlap": float(overlap), "horizon_sessions": horizon,
    }


def leave_one_family_out(matched: pd.DataFrame, horizon: int) -> dict[str, Any]:
    """Attribution counterfactuals; results are explicitly never model candidates."""
    frame = matched.copy()
    frame["price_rank"] = frame.groupby("vintage_date")["score"].rank(method="average", pct=True)
    comparisons, pvalues = {}, {}
    for omitted in FAMILY_WEIGHTS:
        included = [family for family in FAMILY_WEIGHTS if family != omitted]
        numerator = sum(frame[f"{f}_score"].fillna(0) * FAMILY_WEIGHTS[f] for f in included)
        denominator = sum(frame[f"{f}_score"].notna() * FAMILY_WEIGHTS[f] for f in included)
        alternative = numerator / denominator.replace(0, np.nan)
        enhanced = 100 * (PRICE_WEIGHT * frame.price_rank + FUNDAMENTAL_WEIGHT * alternative)
        selected = frame.loc[enhanced.groupby(frame.vintage_date).rank(method="first", ascending=False).le(3)]
        excess = selected.groupby("vintage_date").forward_return.mean() - frame.groupby("vintage_date").forward_return.mean()
        inference = horizon_block_inference(excess.dropna().tolist(), horizon)
        comparisons[omitted] = {"mean_excess_return": _safe_float(excess.mean()),
            "raw_p_value": inference.get("raw_p_value"), "label": "exploratory_non_confirmatory"}
        pvalues[omitted] = inference.get("raw_p_value")
    adjusted = adjust_pvalues(pvalues)
    for family in comparisons: comparisons[family]["adjusted_p_value"] = adjusted[family]
    return {"exploratory_comparisons": len(comparisons), "multiplicity_method": "holm_all_leave_one_family_out",
        "cannot_be_used_as_confirmation": True, "production_weights_unchanged": True,
        "candidates_generated": 0, "comparisons": comparisons}


def coverage_diagnosis(matched: pd.DataFrame) -> dict[str, Any]:
    family_columns = [f"{f}_score" for f in FAMILY_WEIGHTS]
    available = [c for c in family_columns if c in matched]
    counts = matched[available].notna().sum(axis=1)
    passing = counts.ge(3)
    years = pd.to_datetime(matched.vintage_date).dt.year
    year_coverage = passing.groupby(years).mean()
    halves = np.array_split(year_coverage, 2)
    result = {"companies_passing_three_families": int(matched.loc[passing, "security_id"].nunique()),
        "vintages_passing_three_families": int(matched.loc[passing, "vintage_date"].nunique()),
        "coverage_by_family": {f: float(matched[f"{f}_score"].notna().mean()) for f in FAMILY_WEIGHTS
                               if f"{f}_score" in matched},
        "coverage_by_year": {str(k): float(v) for k, v in year_coverage.items()},
        "early_late_coverage": [_safe_float(part.mean()) for part in halves if len(part)],
        "missing_never_scored": True}
    if "score" in matched:
        result["coverage_price_score_rank_correlation"] = _safe_float(pd.Series(passing.astype(float)).corr(matched.score.rank()))
    if "price_history_sessions" in matched:
        result["coverage_price_history_correlation"] = _safe_float(pd.Series(passing.astype(float)).corr(matched.price_history_sessions))
    return result


def accounting_checks(factors: pd.DataFrame, facts_diagnostics: dict[str, Any] | None = None) -> dict[str, Any]:
    """Aggregate-only accounting warning counts (no issuer facts are returned)."""
    def count(column: str, predicate) -> int:
        return int(predicate(pd.to_numeric(factors.get(column, pd.Series(dtype=float)), errors="coerce")).sum())
    return {"negative_earnings": count("net_income", lambda x: x < 0),
        "negative_equity": count("equity", lambda x: x < 0),
        "negative_free_cash_flow": count("trailing_free_cash_flow", lambda x: x < 0),
        "near_zero_denominators": sum(count(c, lambda x: x.abs() <= 1e-12) for c in ("assets", "equity", "debt")),
        "extreme_growth_ratios": sum(count(c, lambda x: x.abs() > 10) for c in ("revenue_growth", "eps_growth", "operating_cash_flow_growth")),
        "debt_free": count("debt", lambda x: x == 0),
        "share_count_discontinuities": count("diluted_share_growth", lambda x: x.abs() > 1),
        "alternative_taxonomy_concepts": int((facts_diagnostics or {}).get("alternative_taxonomy_concepts", 0)),
        "annual_ttm_quarterly": (facts_diagnostics or {}).get("annual_ttm_quarterly", "reported_by_point_in_time_constructor"),
        "stale_facts": int((facts_diagnostics or {}).get("stale_facts", 0)),
        "amendments_revisions": int((facts_diagnostics or {}).get("amendments_revisions", 0)),
        "valuation_price_basis": "historical_price_at_decision_vintage",
        "incompatible_units_or_currencies": int((facts_diagnostics or {}).get("incompatible_units_or_currencies", 0)),
        "duplicate_or_overlapping_periods": int((facts_diagnostics or {}).get("duplicate_or_overlapping_periods", 0))}


def validate_directions(frame: pd.DataFrame) -> dict[str, Any]:
    """Fail closed if a frozen rank reverses its declared economic direction."""
    defects = []
    for factor, higher_desirable in FACTOR_DIRECTIONS.items():
        rank = f"{factor}_rank"
        if factor not in frame or rank not in frame: continue
        valid = frame[[factor, rank]].dropna()
        if not valid.empty:
            correlation = valid[factor].rank().corr(valid[rank].rank())
            if correlation is not None and ((higher_desirable and correlation <= 0) or
                                            (not higher_desirable and correlation >= 0)):
                defects.append({"factor": factor, "defect": "direction_or_percentile_reversed"})
    missing_scored = 0
    for factor in FACTOR_DIRECTIONS:
        rank = f"{factor}_rank"
        if factor in frame and rank in frame:
            missing_scored += int((frame[factor].isna() & frame[rank].notna()).sum())
    if missing_scored: defects.append({"defect": "missing_value_received_score", "count": missing_scored})
    return {"status": "defect_detected" if defects else "no_calculation_defect_detected",
            "checks": len(FACTOR_DIRECTIONS), "defects": defects}


def diagnose_matched(matched_by_horizon: dict[int, pd.DataFrame]) -> dict[str, Any]:
    horizons = {}
    for horizon in LOCKED_HORIZONS:
        frame = matched_by_horizon[horizon]
        factors = {}
        for factor, family in FACTOR_FAMILIES.items():
            column = f"{factor}_rank" if f"{factor}_rank" in frame else factor
            factors[factor] = factor_evidence(frame, column,
                weight=FAMILY_WEIGHTS[family], horizon=horizon)
        families = {family: factor_evidence(frame, f"{family}_score",
                    weight=weight, horizon=horizon) for family, weight in FAMILY_WEIGHTS.items()}
        horizons[str(horizon)] = {"factors": factors, "families": families,
            "coverage": coverage_diagnosis(frame), "leave_one_family_out": leave_one_family_out(frame, horizon),
            "direction_validation": validate_directions(frame)}
    return {"label": "EXPLORATORY DIAGNOSTICS — NOT A NEW MODEL",
        "configuration_hash": configuration_hash(), "scope": "US_only_current_membership_not_survivorship_free",
        "headline_evidence": OBSERVED_HEADLINE, "horizons": horizons,
        "rankings_generated": 0, "candidates": [], "message": "NO CANDIDATES GENERATED."}


def plain_finance_explanation(attribution: dict[str, Any]) -> dict[str, Any]:
    family_rows = []
    for horizon, report in attribution.items():
        for family, evidence in report["families"].items():
            family_rows.append((family, horizon, evidence["top_minus_bottom_quantile_return_spread"]))
    helped = sorted({family for family, _, value in family_rows if value is not None and value > 0})
    hurt = sorted({family for family, _, value in family_rows if value is not None and value < 0})
    concentrations = [e["concentration"]["largest_10"] for report in attribution.values()
                      for e in report["families"].values()]
    defects = [d for report in attribution.values() for d in report["direction_validation"]["defects"]]
    return {"helping_families": helped, "detracting_families": hurt,
        "deterioration": "concentrated_warning" if concentrations and max(concentrations) > .5 else "broad_or_inconclusive",
        "coverage_through_time": "See coverage_by_year and early_late_coverage; no causal inference is made.",
        "calculation_or_data_defect": "detected" if defects else "none_detected_by_bounded_checks",
        "honest_conclusion": "defect" if defects else "failed_frozen_hypothesis_with_inconclusive_attribution",
        "evidence_needed_before_another_model": "Pre-register a distinct economic hypothesis, transformations, coverage, horizons, multiplicity family and gates; evaluate on independent point-in-time evidence.",
        "investment_action": "none"}


def assess_us_fundamentals_diagnostics(*, research_db: Path, production_db: Path,
                                       decision_at: datetime | None = None) -> dict[str, Any]:
    """Run the frozen pipeline read-only, reconcile it, and return bounded diagnosis metadata."""
    research_db, production_db = Path(research_db), Path(production_db)
    before = {"research": fingerprint(research_db), "production": fingerprint(production_db)}
    if not before["research"].exists or not before["production"].exists:
        raise ReadinessError("both database paths must exist")
    if research_db.is_symlink() or production_db.is_symlink() or _same_file(research_db, production_db):
        raise ReadinessError("database paths must be distinct, regular, non-aliased files")
    matched: dict[int, pd.DataFrame] = {}
    frozen = assess_us_fundamentals(research_db=research_db, production_db=production_db,
                                    decision_at=decision_at or datetime.now(timezone.utc),
                                    attribution_collector=lambda horizon, frame: matched.__setitem__(horizon, frame))
    if frozen["configuration_hash"] != configuration_hash():
        raise ReadinessError("frozen Milestone 26 configuration did not reconcile")
    reconciliation = reconcile_frozen_headline(frozen["results"])
    after = {"research": fingerprint(research_db), "production": fingerprint(production_db)}
    if before != after: raise ReadinessError("database changed during diagnostics")
    attribution = diagnose_matched(matched)
    return {"command": "research-us-fundamentals-diagnostics", "mode": "strictly_read_only",
        "label": "EXPLORATORY DIAGNOSTICS — NOT A NEW MODEL", "configuration_hash": configuration_hash(),
        "frozen_evaluation_reconciled": True, "reconciliation_checks": reconciliation,
        "evaluation_results": frozen["results"],
        "attribution": attribution["horizons"],
        "plain_finance_explanation": plain_finance_explanation(attribution["horizons"]),
        "mapping_and_permanent_failure_exclusions": frozen.get("permanently_unmapped", {}),
        "observed_live_headline": OBSERVED_HEADLINE,
        "diagnostic_status": "Run output is attribution only; no factor, weight, or model is changed.",
        "classification_metadata": "unavailable_no_defensible_point_in_time_classification",
        "database_fingerprints": {k: {"before": asdict(before[k]), "after": asdict(after[k]),
            "unchanged": before[k] == after[k]} for k in before},
        "rankings_generated": 0, "candidates": [], "message": "NO CANDIDATES GENERATED."}
