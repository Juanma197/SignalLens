"""Milestone 26: locked, US-only, point-in-time fundamental research.

No function in this module writes a database or emits an issuer ranking.  Factor
definitions, weights and horizons are constants so results cannot tune the
hypothesis after observing returns.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import duckdb
import numpy as np
import pandas as pd

from .horizon_evaluation import horizon_block_inference
from .model_readiness import ReadinessError, _frame, _same_file, _validate_schema, fingerprint
from .research_observations import ObservationPolicy, build_model_ready_observations
from .research_scoring import walk_forward_evidence

LOCKED_HORIZONS = (126, 252)
MINIMUM_CROSS_SECTION = 5
MINIMUM_FAMILIES = 3
MAX_SAMPLE = 10
FAMILY_WEIGHTS = {"growth": .20, "profitability": .25, "cash_flow": .20,
                  "leverage": .15, "dilution": .05, "valuation": .15}
PRICE_WEIGHT, FUNDAMENTAL_WEIGHT = .60, .40

# Ordered aliases are deliberately narrow.  A metric is withheld rather than
# guessed from a semantically adjacent concept.
CONCEPTS = {
    "revenue": ("RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet"),
    "eps": ("EarningsPerShareDiluted",), "operating_income": ("OperatingIncomeLoss",),
    "net_income": ("NetIncomeLoss",), "assets": ("Assets",),
    "equity": ("StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"),
    "operating_cash_flow": ("NetCashProvidedByUsedInOperatingActivities",),
    "capex": ("PaymentsToAcquirePropertyPlantAndEquipment",),
    "debt": ("LongTermDebtCurrent", "LongTermDebtNoncurrent"),
    "shares": ("WeightedAverageNumberOfDilutedSharesOutstanding", "EntityCommonStockSharesOutstanding"),
}
FLOW_METRICS = frozenset({"revenue", "eps", "operating_income", "net_income",
                          "operating_cash_flow", "capex", "shares"})
FACTOR_DIRECTIONS = {"revenue_growth": True, "eps_growth": True, "operating_margin": True,
    "net_margin": True, "return_on_assets": True, "return_on_equity": True,
    "trailing_free_cash_flow": True, "free_cash_flow_margin": True,
    "operating_cash_flow_growth": True, "debt_to_assets": False, "debt_to_equity": False,
    "free_cash_flow_to_debt": True, "diluted_share_growth": False,
    "earnings_yield": True, "book_to_market": True, "free_cash_flow_yield": True}
FACTOR_FAMILIES = {
    "revenue_growth": "growth", "eps_growth": "growth",
    "operating_margin": "profitability", "net_margin": "profitability",
    "return_on_assets": "profitability", "return_on_equity": "profitability",
    "trailing_free_cash_flow": "cash_flow", "free_cash_flow_margin": "cash_flow",
    "operating_cash_flow_growth": "cash_flow", "debt_to_assets": "leverage",
    "debt_to_equity": "leverage", "free_cash_flow_to_debt": "leverage",
    "diluted_share_growth": "dilution", "earnings_yield": "valuation",
    "book_to_market": "valuation", "free_cash_flow_yield": "valuation"}


@dataclass(frozen=True)
class FundamentalPolicy:
    flow_staleness_days: int = 550
    instant_staleness_days: int = 460
    minimum_cross_section: int = MINIMUM_CROSS_SECTION
    minimum_families: int = MINIMUM_FAMILIES
    denominator_epsilon: float = 1e-12


def configuration_hash(policy: FundamentalPolicy = FundamentalPolicy()) -> str:
    payload = {"version": "us-fundamentals-v1", "horizons": LOCKED_HORIZONS,
        "family_weights": FAMILY_WEIGHTS, "blend": [PRICE_WEIGHT, FUNDAMENTAL_WEIGHT],
        "concepts": CONCEPTS, "directions": FACTOR_DIRECTIONS, "policy": asdict(policy)}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def select_asof_facts(facts: pd.DataFrame, decision_at: datetime) -> tuple[pd.DataFrame, dict[str, int]]:
    """Select latest public revision for each economic fact; never use period end as availability."""
    required = {"security_id", "taxonomy", "concept", "value", "unit", "currency", "period_start",
        "period_end", "accession_number", "public_at", "is_amendment", "source_endpoint"}
    missing = required - set(facts)
    if missing: raise ValueError(f"facts missing columns: {sorted(missing)}")
    diagnostics: dict[str, int] = {}
    frame = facts.copy()
    public = pd.to_datetime(frame["public_at"], utc=True, errors="coerce")
    boundary = pd.Timestamp(decision_at)
    if boundary.tzinfo is None: raise ValueError("decision_at must be timezone-aware")
    boundary = boundary.tz_convert("UTC")
    masks = {
        "missing_availability": public.isna(), "not_yet_public": public.gt(boundary),
        "nonfinite_value": ~np.isfinite(pd.to_numeric(frame["value"], errors="coerce")),
        "missing_unit": frame["unit"].isna() | frame["unit"].astype(str).str.strip().eq(""),
    }
    keep = pd.Series(True, index=frame.index)
    for reason, mask in masks.items():
        effective = mask & keep; diagnostics[reason] = int(effective.sum()); keep &= ~mask
    frame = frame.loc[keep].copy(); frame["public_at"] = public.loc[keep]
    frame["period_start"] = pd.to_datetime(frame["period_start"], errors="coerce")
    frame["period_end"] = pd.to_datetime(frame["period_end"], errors="coerce")
    frame = frame.loc[frame["period_end"].notna()]
    # Revisions/amendments replace only the same concept/period/unit/currency identity.
    key = ["security_id", "taxonomy", "concept", "unit", "currency", "period_start", "period_end"]
    frame = frame.sort_values(key + ["public_at", "accession_number"], na_position="first")
    duplicate = frame.duplicated(key, keep="last")
    diagnostics["superseded_revision"] = int(duplicate.sum())
    return frame.loc[~duplicate].reset_index(drop=True), diagnostics


def _metric_rows(facts: pd.DataFrame, metric: str, currency: str | None) -> pd.DataFrame:
    rows = facts.loc[facts["concept"].isin(CONCEPTS[metric])].copy()
    if metric != "debt" and not rows.empty:
        preferred = next((concept for concept in CONCEPTS[metric] if rows.concept.eq(concept).any()), None)
        rows = rows.loc[rows.concept.eq(preferred)]
    if metric == "eps":
        expected = f"{str(currency).lower()}/shares"
        valid = rows["unit"].astype(str).str.lower().str.replace(" ", "").isin({expected, expected[:-1]})
    elif metric == "shares": valid = rows["unit"].astype(str).str.lower().isin({"shares", "share"})
    else:
        valid = rows["unit"].astype(str).str.upper().eq(str(currency).upper()) & rows["currency"].fillna(currency).astype(str).str.upper().eq(str(currency).upper())
    return rows.loc[valid].sort_values(["period_end", "public_at"])


def _flow(rows: pd.DataFrame, decision: pd.Timestamp, policy: FundamentalPolicy, offset_years: int = 0) -> float | None:
    """Latest annual or four non-overlapping discrete quarters for a target year."""
    if rows.empty: return None
    target = decision.tz_localize(None) - pd.DateOffset(years=offset_years)
    rows = rows.loc[rows.period_end.le(target)].copy()
    rows["days"] = (rows.period_end - rows.period_start).dt.days
    annual = rows.loc[rows.days.between(300, 400)]
    if not annual.empty:
        row = annual.iloc[-1]
        return float(row.value) if (target - row.period_end).days <= policy.flow_staleness_days else None
    # Only discrete quarters are additive.  YTD durations are intentionally not
    # mixed with quarters, preventing overlapping-period double counting.
    quarters = rows.loc[rows.days.between(70, 110)].drop_duplicates("period_end", keep="last").tail(4)
    if len(quarters) == 4 and (target - quarters.period_end.max()).days <= policy.flow_staleness_days:
        intervals = sorted(zip(quarters.period_start, quarters.period_end))
        if all(intervals[i][1] < intervals[i + 1][0] for i in range(3)):
            return float(quarters.value.sum())
    return None


def _instant(rows: pd.DataFrame, decision: pd.Timestamp, policy: FundamentalPolicy) -> float | None:
    rows = rows.loc[rows.period_end.le(decision.tz_localize(None))]
    if rows.empty or (decision.tz_localize(None) - rows.iloc[-1].period_end).days > policy.instant_staleness_days: return None
    return float(rows.iloc[-1].value)


def construct_factors(facts: pd.DataFrame, decision_at: datetime, prices: dict[str, float],
                      currencies: dict[str, str], policy: FundamentalPolicy = FundamentalPolicy()) -> tuple[pd.DataFrame, dict[str, Any]]:
    selected, removed = select_asof_facts(facts, decision_at)
    decision = pd.Timestamp(decision_at).tz_convert("UTC")
    output, withheld = [], {}
    for security_id in sorted(selected.security_id.astype(str).unique()):
        group = selected.loc[selected.security_id.astype(str).eq(security_id)]
        currency = currencies.get(security_id)
        values: dict[str, float | None] = {}
        incompatible = 0
        for metric in CONCEPTS:
            metric_rows = _metric_rows(group, metric, currency)
            incompatible += int(group.concept.isin(CONCEPTS[metric]).sum() - len(metric_rows))
            if metric == "debt":
                components = [_instant(metric_rows.loc[metric_rows.concept.eq(concept)], decision, policy)
                              for concept in CONCEPTS[metric]]
                present = [value for value in components if value is not None]
                values[metric] = sum(present) if len(present) == len(CONCEPTS[metric]) else None
            else:
                values[metric] = (_flow(metric_rows, decision, policy) if metric in FLOW_METRICS
                                  else _instant(metric_rows, decision, policy))
            if metric in {"revenue", "eps", "operating_cash_flow", "shares"}:
                values[f"prior_{metric}"] = _flow(metric_rows, decision, policy, 1)
        def ratio(a: float | None, b: float | None, *, positive=False) -> float | None:
            if a is None or b is None or not np.isfinite([a, b]).all(): return None
            if abs(b) <= policy.denominator_epsilon or (positive and b <= 0): return None
            return a / b
        fcf = None if values["operating_cash_flow"] is None or values["capex"] is None else values["operating_cash_flow"] - abs(values["capex"])
        market_cap = None
        price, shares = prices.get(security_id), values["shares"]
        if price is not None and shares is not None and price > 0 and shares > 0: market_cap = price * shares
        row = {"security_id": security_id,
            "revenue_growth": ratio(values["revenue"] - values["prior_revenue"], abs(values["prior_revenue"]), positive=True) if values["revenue"] is not None and values["prior_revenue"] is not None and values["prior_revenue"] > 0 else None,
            "eps_growth": ratio(values["eps"] - values["prior_eps"], abs(values["prior_eps"]), positive=True) if values["eps"] is not None and values["prior_eps"] is not None and values["eps"] >= 0 and values["prior_eps"] > 0 else None,
            "operating_margin": ratio(values["operating_income"], values["revenue"], positive=True),
            "net_margin": ratio(values["net_income"], values["revenue"], positive=True),
            "return_on_assets": ratio(values["net_income"], values["assets"], positive=True),
            "return_on_equity": ratio(values["net_income"], values["equity"], positive=True),
            "trailing_free_cash_flow": fcf,
            "free_cash_flow_margin": ratio(fcf, values["revenue"], positive=True),
            "operating_cash_flow_growth": ratio(values["operating_cash_flow"] - values["prior_operating_cash_flow"], abs(values["prior_operating_cash_flow"]), positive=True) if values["operating_cash_flow"] is not None and values["prior_operating_cash_flow"] is not None and values["prior_operating_cash_flow"] > 0 else None,
            "debt_to_assets": ratio(values["debt"], values["assets"], positive=True),
            "debt_to_equity": ratio(values["debt"], values["equity"], positive=True),
            "free_cash_flow_to_debt": ratio(fcf, values["debt"], positive=True),
            "diluted_share_growth": ratio(values["shares"] - values["prior_shares"], abs(values["prior_shares"]), positive=True) if values["shares"] is not None and values["prior_shares"] is not None and values["prior_shares"] > 0 else None,
            "earnings_yield": ratio(values["net_income"], market_cap, positive=True),
            "book_to_market": ratio(values["equity"], market_cap, positive=True) if values["equity"] is not None and values["equity"] > 0 else None,
            "free_cash_flow_yield": ratio(fcf, market_cap, positive=True)}
        output.append(row); withheld[security_id] = {"incompatible_unit_or_currency": incompatible}
    return pd.DataFrame(output), {"facts_removed_or_withheld": removed,
        "per_security": withheld, "interest_coverage": "unavailable_absent_defensible_interest_expense"}


def normalize_and_combine(frame: pd.DataFrame, policy: FundamentalPolicy = FundamentalPolicy()) -> tuple[pd.DataFrame, dict[str, Any]]:
    result = frame.copy(); coverage = {}
    for factor, higher in FACTOR_DIRECTIONS.items():
        source = result[factor] if factor in result else pd.Series(np.nan, index=result.index)
        valid = pd.to_numeric(source, errors="coerce").replace([np.inf, -np.inf], np.nan)
        count = int(valid.notna().sum()); coverage[factor] = count
        result[f"{factor}_rank"] = (valid.rank(method="average", pct=True, ascending=higher)
            if count >= policy.minimum_cross_section else np.nan)
    for family in FAMILY_WEIGHTS:
        columns = [f"{f}_rank" for f, value in FACTOR_FAMILIES.items() if value == family]
        result[f"{family}_score"] = result[columns].mean(axis=1, skipna=True)
        result.loc[result[columns].notna().sum(axis=1).eq(0), f"{family}_score"] = np.nan
    family_columns = [f"{f}_score" for f in FAMILY_WEIGHTS]
    result["valid_family_count"] = result[family_columns].notna().sum(axis=1)
    result["fundamental_score"] = sum(result[f"{f}_score"].fillna(0) * w for f, w in FAMILY_WEIGHTS.items()) / sum(
        result[f"{f}_score"].notna() * w for f, w in FAMILY_WEIGHTS.items())
    result.loc[result.valid_family_count.lt(policy.minimum_families), "fundamental_score"] = np.nan
    return result, {"factor_valid_companies": coverage, "minimum_cross_section": policy.minimum_cross_section,
        "minimum_factor_families": policy.minimum_families,
        "withheld_insufficient_families": int(result.fundamental_score.isna().sum())}


def evaluate_matched(predictions: pd.DataFrame, fundamentals: pd.DataFrame, horizon: int) -> dict[str, Any]:
    """Compare both hypotheses on exactly the same security/vintage rows."""
    keys = ["security_id", "vintage_date"]
    matched = predictions.merge(fundamentals[keys + ["fundamental_score"]], on=keys, how="inner")
    matched = matched.loc[matched.fundamental_score.notna()].copy()
    if matched.empty: return {"eligible_vintages": 0, "predictions": 0, "passed": False, "reason": "insufficient_fundamentals"}
    matched["price_rank"] = matched.groupby("vintage_date")["score"].rank(method="average", pct=True)
    matched["enhanced_score"] = 100 * (PRICE_WEIGHT * matched.price_rank + FUNDAMENTAL_WEIGHT * matched.fundamental_score)
    def evidence(column: str) -> tuple[dict[str, Any], pd.Series]:
        top = matched.loc[matched.groupby("vintage_date")[column].rank(method="first", ascending=False).le(3)]
        period = top.groupby("vintage_date").forward_return.mean()
        base = matched.groupby("vintage_date").forward_return.mean(); excess = period - base
        correlations = []
        for _, group in matched.groupby("vintage_date"):
            if group[column].nunique() > 1 and group.forward_return.nunique() > 1:
                correlations.append(group[column].rank().corr(group.forward_return.rank()))
        corr = pd.Series(correlations, dtype=float).dropna()
        halves = np.array_split(excess.sort_index(), 2)
        temporal = [float(part.mean()) for part in halves if len(part)]
        return {"mean_excess_return": float(excess.mean()), "positive_period_rate": float((excess > 0).mean()),
            "rank_correlation": float(corr.mean()) if len(corr) else None,
            "temporal_stability_half_means": temporal,
            "confidence_interval": horizon_block_inference(excess.tolist(), horizon)["confidence_interval"],
            "concentration_top_security": float(top.groupby("qualified_symbol").size().max() / len(top))}, excess
    price, price_excess = evidence("score"); enhanced, enhanced_excess = evidence("enhanced_score")
    incremental = enhanced_excess - price_excess
    inference = horizon_block_inference(incremental.tolist(), horizon)
    return {"eligible_vintages": int(matched.vintage_date.nunique()), "predictions": int(len(matched)),
        "matched_samples": True, "price_only": price, "enhanced": enhanced,
        "incremental_excess_return": float(incremental.mean()), "incremental_inference": inference,
        "passed": bool(incremental.mean() > 0 and inference["confidence_interval"] and inference["confidence_interval"][0] > 0)}


def holm_two_horizons(pvalues: dict[int, float | None]) -> dict[int, dict[str, Any]]:
    if set(pvalues) != set(LOCKED_HORIZONS): raise ValueError("exactly the two locked horizons are required")
    ordered = sorted((float(p), h) for h, p in pvalues.items() if p is not None and np.isfinite(p))
    adjusted, running = {}, 0.0
    for rank, (value, horizon) in enumerate(ordered):
        running = max(running, min(1.0, value * (len(LOCKED_HORIZONS) - rank)))
        adjusted[horizon] = running
    return {h: {"method": "holm_bonferroni_two_locked_horizons", "raw_p_value": pvalues[h],
        "adjusted_p_value": adjusted.get(h), "passed": adjusted.get(h, 1.0) <= .05} for h in LOCKED_HORIZONS}


def assess_us_fundamentals(*, research_db: Path, production_db: Path,
                           decision_at: datetime | None = None,
                           attribution_collector: Callable[[int, pd.DataFrame], None] | None = None) -> dict[str, Any]:
    research_db, production_db = Path(research_db), Path(production_db)
    before = {"research": fingerprint(research_db), "production": fingerprint(production_db)}
    if not before["research"].exists or not before["production"].exists: raise ReadinessError("both databases must exist")
    if research_db.is_symlink() or production_db.is_symlink() or _same_file(research_db, production_db):
        raise ReadinessError("database paths must be distinct, regular, non-aliased files")
    captured = decision_at or datetime.now(timezone.utc)
    if captured.tzinfo is None: raise ReadinessError("decision_at must be timezone-aware")
    boundary = pd.Timestamp(captured).tz_convert("UTC").tz_localize(None).to_pydatetime()
    with duckdb.connect(str(production_db), read_only=True) as db: db.execute("SELECT 1")
    with duckdb.connect(str(research_db), read_only=True) as db:
        _validate_schema(db, boundary)
        tables = {r[0] for r in db.execute("SHOW TABLES").fetchall()}
        if not {"sec_facts", "sec_checkpoints"} <= tables: raise ReadinessError("SEC normalized tables are absent")
        retrieval = db.execute("SELECT retrieval_id FROM security_master_retrievals WHERE status='completed' AND retrieved_at<=? ORDER BY retrieved_at DESC LIMIT 1", [boundary]).fetchone()
        catalogue = _frame(db, "SELECT security_id,qualified_symbol,UPPER(primary_exchange) region,UPPER(currency) currency,(active AND instrument_type IN ('common_stock','ordinary_share')) eligible FROM security_listings WHERE retrieval_id=?", [retrieval[0]])
        prices = _frame(db, "SELECT * FROM global_price_observations"); fx = _frame(db, "SELECT * FROM global_fx_observations")
        actions = _frame(db, "SELECT * FROM global_corporate_actions"); failures = _frame(db, "SELECT qualified_symbol,error_code FROM eodhd_ingestion_checkpoints WHERE status='failed'")
        facts = _frame(db, "SELECT * FROM sec_facts")
        checkpoints = _frame(db, "SELECT security_id,qualified_symbol,status FROM sec_checkpoints")
    dataset = build_model_ready_observations(catalogue=catalogue, prices=prices, fx=fx, actions=actions,
        failures=failures, decision_at=captured, policy=ObservationPolicy())
    us = dataset.observations.loc[dataset.observations.region.eq("US")].copy()
    results, pvalues, coverage = {}, {}, {}
    for horizon in LOCKED_HORIZONS:
        predictions, _ = walk_forward_evidence(us, prices, decision_at=captured, horizon_sessions=horizon, actions=actions)
        panels = []
        for vintage, group in predictions.groupby("vintage_date") if not predictions.empty else []:
            visible = prices.loc[prices.trading_date.eq(vintage)].set_index("qualified_symbol").adjusted_close.to_dict()
            ids = us.set_index("qualified_symbol").security_id.to_dict(); currencies = us.set_index("security_id").currency.to_dict()
            factor, diag = construct_factors(facts, pd.Timestamp(vintage).tz_localize("UTC").to_pydatetime(),
                {ids[s]: float(v) for s, v in visible.items() if s in ids}, currencies)
            normalized, cov = normalize_and_combine(factor); normalized["vintage_date"] = vintage
            panels.append(normalized); coverage[str(pd.Timestamp(vintage).date())] = cov
        fundamentals = pd.concat(panels, ignore_index=True) if panels else pd.DataFrame(columns=["security_id", "vintage_date", "fundamental_score"])
        if not predictions.empty: predictions["security_id"] = predictions.qualified_symbol.map(us.set_index("qualified_symbol").security_id)
        if attribution_collector is not None:
            keys = ["security_id", "vintage_date"]
            detail = predictions.merge(fundamentals, on=keys, how="inner")
            detail = detail.loc[detail.fundamental_score.notna()].copy()
            attribution_collector(horizon, detail)
        result = evaluate_matched(predictions, fundamentals, horizon); results[str(horizon)] = result
        pvalues[horizon] = result.get("incremental_inference", {}).get("raw_p_value")
    # Holm is locked to this milestone's two tests (not the old four-horizon family).
    correction = holm_two_horizons(pvalues)
    for horizon in LOCKED_HORIZONS:
        results[str(horizon)]["multiple_testing"] = correction[horizon]
        results[str(horizon)]["passed"] &= correction[horizon]["passed"]
    after = {"research": fingerprint(research_db), "production": fingerprint(production_db)}
    if before != after: raise ReadinessError("database changed during read-only evaluation")
    permanent = checkpoints.loc[checkpoints.status.eq("permanent_failure")]
    return {"command": "research-us-fundamentals-evaluation", "mode": "strictly_read_only",
        "scope": "US_only_current_membership_not_survivorship_free", "locked_horizons": list(LOCKED_HORIZONS),
        "configuration_hash": configuration_hash(), "results": results, "coverage_by_vintage": coverage,
        "permanently_unmapped": {"count": int(len(permanent)), "samples": sorted(permanent.qualified_symbol.astype(str))[:MAX_SAMPLE]},
        "integrity_failures": [], "database_fingerprints": {k: {"before": asdict(before[k]), "after": asdict(after[k]), "unchanged": before[k] == after[k]} for k in before},
        "rankings_generated": 0, "candidates": [], "message": "NO CANDIDATES GENERATED.",
        "explanation": "Fundamentals measure growth, profitability, cash generation, leverage, dilution and valuation. Investors may care because durable cash-generating businesses at defensible prices can differ from price momentum. Results must improve on the matched US price-only sample after dependence and multiple-test controls. Missing SEC data causes withholding, never a weak score. International securities remain on the unchanged price-only baseline. This research produces no recommendation or investment advice."}
