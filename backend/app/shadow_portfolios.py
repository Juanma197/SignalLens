"""Milestone 19 prospective, immutable, research-only shadow portfolios."""
from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pandas as pd

from .horizon_evaluation import horizon_block_inference
from .model_readiness import ReadinessError, _frame, _same_file, _validate_schema, fingerprint
from .multifactor import FACTOR_WEIGHTS, MOMENTUM_DAYS, RISK_DAYS, TREND_DAYS
from .price_segments import crosses_boundary, detect_price_segments
from .research_observations import ObservationPolicy, build_model_ready_observations
from .research_scoring import EvidencePolicy, prepare_cross_section

LABEL = "RESEARCH SHADOW PORTFOLIO — NOT INVESTMENT ADVICE"
HORIZONS = (126, 252)
IMPLEMENTED_AT = datetime(2026, 9, 28, tzinfo=timezone.utc)
PROMOTION_GATES = {
    "minimum_fully_matured_vintages": 12,
    "mean_excess_return_positive": True,
    "median_excess_return_positive": True,
    "positive_period_rate_strictly_above": .5,
    "confidence_interval_excludes_zero": True,
    "temporal_stability": True,
    "regional_stability": True,
    "integrity": True,
    "single_configuration_series": True,
}


@dataclass(frozen=True)
class ShadowPolicy:
    maximum_selections: int = 3
    minimum_score: float = 55.0
    horizons: tuple[int, int] = HORIZONS


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def maturity_session(sessions: pd.DatetimeIndex, decision_at: datetime, horizon: int) -> pd.Timestamp | None:
    """Return the exact Nth strictly subsequent market session, or no incomplete label."""
    subsequent = sessions[sessions > pd.Timestamp(decision_at).tz_localize(None)]
    return pd.Timestamp(subsequent[horizon - 1]) if len(subsequent) >= horizon else None


def equal_weight_return(returns: dict[str, float], members: list[str]) -> float | None:
    """Require every member before calculating an equal-weight cohort return."""
    if not members or any(symbol not in returns for symbol in members):
        return None
    return float(np.mean([returns[symbol] for symbol in members]))


def strategy_manifest(decision_at: datetime, data_fingerprint: str, *, commit: str | None = None,
                      policy: ShadowPolicy = ShadowPolicy()) -> dict[str, Any]:
    """Build the canonical frozen manifest; its hash excludes decision/data identity."""
    configuration = {
        "feature_definitions": {
            "momentum": f"adjusted-close return over {MOMENTUM_DAYS} sessions",
            "trend": f"adjusted-close return over {TREND_DAYS} sessions",
            "risk_quality": f"{RISK_DAYS}-session volatility plus {MOMENTUM_DAYS}-session drawdown",
            "profitability": "neutral 0.5; point-in-time fundamentals unavailable",
            "balance_sheet": "neutral 0.5; point-in-time fundamentals unavailable",
        },
        "weights": dict(FACTOR_WEIGHTS),
        "thresholds": {"minimum_score": policy.minimum_score, "maximum_selections": policy.maximum_selections},
        "universe_rules": asdict(ObservationPolicy()),
        "eligibility": "validated active common_stock/ordinary_share; model-ready observation",
        "price_segmentation": "withhold feature or outcome crossing detected adjusted-price boundary",
        "fx_semantics": "latest available on/before price date; GBP identity; GBX/100",
        "horizon_definitions": [{"sessions": h, "baseline": "equal-weight complete eligible universe"} for h in policy.horizons],
        "tie_breaking": "composite_score descending, qualified_symbol ascending",
    }
    config_hash = hashlib.sha256(_json(configuration).encode()).hexdigest()
    if commit is None:
        commit = subprocess.run(["git", "rev-parse", "HEAD"], check=True, capture_output=True,
                                text=True).stdout.strip()
    return {"strategy_version": f"shadow-{config_hash[:12]}", "git_commit": commit,
            "configuration_hash": config_hash, **configuration,
            "decision_timestamp": pd.Timestamp(decision_at).isoformat(),
            "research_data_fingerprint": data_fingerprint,
            "promotion_requirements": PROMOTION_GATES}


def _load_inputs(path: Path, cutoff: datetime) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    boundary = cutoff.astimezone(timezone.utc).replace(tzinfo=None)
    with duckdb.connect(str(path), read_only=True) as db:
        _validate_schema(db, boundary)
        retrieval = db.execute("""SELECT retrieval_id FROM security_master_retrievals
          WHERE status='completed' AND retrieved_at<=? ORDER BY retrieved_at DESC,retrieval_id DESC LIMIT 1""", [boundary]).fetchone()
        if not retrieval:
            raise ReadinessError("no completed catalogue available at cutoff")
        catalogue = _frame(db, """SELECT security_id,qualified_symbol,UPPER(primary_exchange) region,
          UPPER(currency) currency,(active AND instrument_type IN ('common_stock','ordinary_share')) eligible
          FROM security_listings WHERE retrieval_id=? ORDER BY qualified_symbol""", [retrieval[0]])
        prices = _frame(db, "SELECT * FROM global_price_observations")
        fx = _frame(db, "SELECT * FROM global_fx_observations")
        actions = _frame(db, "SELECT * FROM global_corporate_actions")
        failures = _frame(db, "SELECT qualified_symbol,error_code FROM eodhd_ingestion_checkpoints WHERE status='failed' AND error_code IS NOT NULL")
    return catalogue, prices, fx, actions, failures


def plan_shadow_vintage(*, research_db: Path, production_db: Path, cutoff: datetime,
                        policy: ShadowPolicy = ShadowPolicy()) -> dict[str, Any]:
    """Plan a monthly vintage while preserving both database files byte-for-byte."""
    if cutoff.tzinfo is None:
        raise ReadinessError("cutoff must be timezone-aware")
    if _same_file(research_db, production_db):
        raise ReadinessError("research and production paths are identical or aliased")
    before = fingerprint(research_db), fingerprint(production_db)
    if not before[0].exists:
        raise ReadinessError("research database does not exist")
    catalogue, prices, fx, actions, failures = _load_inputs(research_db, cutoff)
    dataset = build_model_ready_observations(catalogue=catalogue, prices=prices, fx=fx,
        actions=actions, failures=failures, decision_at=cutoff, policy=ObservationPolicy())
    segments = detect_price_segments(prices, actions)
    scores = prepare_cross_section(dataset.observations, prices, decision_at=cutoff,
                                   knowledge_cutoff=cutoff, segment_boundaries=segments)
    scores = scores.sort_values(["composite_score", "qualified_symbol"], ascending=[False, True])
    selected = scores.loc[scores.composite_score.ge(policy.minimum_score)].head(policy.maximum_selections)
    manifest = strategy_manifest(cutoff, before[0].sha256 or "")
    month = pd.Timestamp(cutoff).strftime("%Y-%m")
    with duckdb.connect(str(research_db), read_only=True) as db:
        tables = {x[0] for x in db.execute("SHOW TABLES").fetchall()}
        exists = "shadow_vintages" in tables and bool(db.execute(
            "SELECT count(*) FROM shadow_vintages WHERE vintage_month=?", [month]).fetchone()[0])
    after = fingerprint(research_db), fingerprint(production_db)
    if before != after:
        raise ReadinessError("database changed during read-only planning")
    score_fields = ["security_id", "qualified_symbol", "region", "currency", "composite_score",
                    "momentum_126d", "trend_21d", "annualized_volatility_63d", "max_drawdown_126d",
                    "reason_codes"]
    return {"command": "plan-shadow-vintage", "label": LABEL, "mode": "strictly_read_only",
            "cutoff": pd.Timestamp(cutoff).isoformat(), "vintage_month": month,
            "eligible_count": len(scores), "withheld_count": len(dataset.observations)-len(scores),
            "strategy_version": manifest["strategy_version"], "configuration_hash": manifest["configuration_hash"],
            "already_exists": exists, "proposed_selections": selected[score_fields].to_dict("records"),
            "scores": scores[score_fields].to_dict("records"), "manifest": manifest,
            "database_unchanged": True, "production_published": False}


def _schema(db: duckdb.DuckDBPyConnection) -> None:
    db.execute("""CREATE TABLE IF NOT EXISTS shadow_vintages(vintage_id VARCHAR PRIMARY KEY,
      vintage_month VARCHAR UNIQUE, decision_at TIMESTAMPTZ, cutoff TIMESTAMPTZ,
      strategy_version VARCHAR, configuration_hash VARCHAR, manifest_json JSON,
      scores_json JSON, selections_json JSON, eligible_count INTEGER, withheld_count INTEGER,
      created_at TIMESTAMPTZ, label VARCHAR)""")
    db.execute("""CREATE TABLE IF NOT EXISTS shadow_cohorts(cohort_id VARCHAR PRIMARY KEY,
      vintage_id VARCHAR, horizon_sessions INTEGER, entry_date DATE, target_maturity_date DATE,
      outcome_json JSON, evaluated_at TIMESTAMPTZ)""")


def create_shadow_vintage(*, research_db: Path, production_db: Path, cutoff: datetime,
                          authorized: bool, now: datetime | None = None) -> dict[str, Any]:
    if not authorized:
        raise PermissionError("explicit --authorize-research-shadow is required")
    now = now or datetime.now(timezone.utc)
    if cutoff > now or cutoff < IMPLEMENTED_AT:
        raise ReadinessError("prospective cutoff must be after implementation and not in the future")
    plan = plan_shadow_vintage(research_db=research_db, production_db=production_db, cutoff=cutoff)
    if plan["already_exists"]:
        return {**plan, "command": "create-shadow-vintage", "status": "already_exists", "mutated": False}
    prices = _load_inputs(research_db, cutoff)[1]
    visible = prices.loc[(pd.to_datetime(prices.retrieved_at, utc=True) <= pd.Timestamp(cutoff))
                         & prices.status.eq("available")].copy()
    visible["trading_date"] = pd.to_datetime(visible.trading_date)
    entries = visible.sort_values("trading_date").groupby("qualified_symbol").tail(1)
    entry = {r.qualified_symbol: {"entry_date": str(r.trading_date.date()),
             "decision_price": float(r.adjusted_close)} for r in entries.itertuples()}
    scores = [{**row, **entry.get(row["qualified_symbol"], {})} for row in plan.pop("scores")]
    selections = [{**row, **entry.get(row["qualified_symbol"], {})} for row in plan["proposed_selections"]]
    vintage_id = f"{plan['strategy_version']}:{plan['vintage_month']}"
    with duckdb.connect(str(research_db)) as db:
        db.begin()
        try:
            _schema(db)
            if db.execute("SELECT count(*) FROM shadow_vintages WHERE vintage_month=?", [plan["vintage_month"]]).fetchone()[0]:
                db.rollback(); return {**plan, "command": "create-shadow-vintage", "status": "already_exists", "mutated": False}
            db.execute("INSERT INTO shadow_vintages VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", [vintage_id,
                plan["vintage_month"], cutoff, cutoff, plan["strategy_version"], plan["configuration_hash"],
                _json(plan["manifest"]), _json(scores), _json(selections), plan["eligible_count"],
                plan["withheld_count"], now, LABEL])
            for horizon in HORIZONS:
                db.execute("INSERT INTO shadow_cohorts VALUES (?,?,?,?,?,?,?)", [f"{vintage_id}:{horizon}",
                    vintage_id, horizon, max(x["entry_date"] for x in scores) if scores else None,
                    None, None, None])
            db.commit()
        except Exception:
            db.rollback(); raise
    return {**plan, "command": "create-shadow-vintage", "status": "created", "mutated": True,
            "vintage_id": vintage_id, "cohorts": list(HORIZONS), "selection_count": len(selections),
            "production_published": False, "broker_interaction": False}


def shadow_status(*, research_db: Path, production_db: Path) -> dict[str, Any]:
    if _same_file(research_db, production_db): raise ReadinessError("production path refused")
    with duckdb.connect(str(research_db), read_only=True) as db:
        tables = {x[0] for x in db.execute("SHOW TABLES").fetchall()}
        if "shadow_vintages" not in tables: return {"command":"shadow-status", "vintages": [], "cohorts": []}
        vintages = _frame(db, "SELECT vintage_id,vintage_month,decision_at,strategy_version,configuration_hash,eligible_count,withheld_count,label FROM shadow_vintages ORDER BY decision_at")
        cohorts = _frame(db, "SELECT * FROM shadow_cohorts ORDER BY vintage_id,horizon_sessions")
    return {"command":"shadow-status", "label":LABEL, "vintages":vintages.to_dict("records"), "cohorts":cohorts.to_dict("records")}


def evaluate_matured_shadows(*, research_db: Path, production_db: Path, as_of: datetime) -> dict[str, Any]:
    """Persist only complete cohort outcomes; existing outcomes are immutable."""
    if as_of.tzinfo is None: raise ReadinessError("as-of must be timezone-aware")
    if _same_file(research_db, production_db): raise ReadinessError("production path refused")
    with duckdb.connect(str(research_db)) as db:
        _schema(db)
        prices = _frame(db, "SELECT * FROM global_price_observations")
        actions = _frame(db, "SELECT * FROM global_corporate_actions")
        pending = db.execute("""SELECT c.cohort_id,c.vintage_id,c.horizon_sessions,v.decision_at,
          v.strategy_version,v.scores_json,v.selections_json FROM shadow_cohorts c JOIN shadow_vintages v
          USING(vintage_id) WHERE c.outcome_json IS NULL ORDER BY v.decision_at,c.horizon_sessions""").fetchall()
        prices["trading_date"] = pd.to_datetime(prices.trading_date); prices["retrieved_at"] = pd.to_datetime(prices.retrieved_at, utc=True)
        prices = prices.loc[prices.status.eq("available") & prices.retrieved_at.le(pd.Timestamp(as_of))]
        sessions = pd.DatetimeIndex(sorted(prices.trading_date.unique()))
        segments = detect_price_segments(prices, actions)
        results=[]
        for cohort_id,vintage_id,horizon,decision_at,version,scores_json,selections_json in pending:
            maturity = maturity_session(sessions, decision_at, horizon)
            if maturity is None: continue
            scores, selections = json.loads(scores_json), json.loads(selections_json)
            returns={}; exclusions={}
            for row in scores:
                symbol=row["qualified_symbol"]; entry=pd.Timestamp(row["entry_date"])
                hit=prices.loc[prices.qualified_symbol.eq(symbol)&prices.trading_date.eq(maturity), "adjusted_close"]
                if hit.empty or crosses_boundary(segments,symbol,entry,maturity): exclusions[symbol]="missing_or_segment_boundary"; continue
                returns[symbol]=float(hit.iloc[-1])/float(row["decision_price"])-1
            shadow_return = equal_weight_return(returns, [r["qualified_symbol"] for r in selections])
            baseline_return = equal_weight_return(returns, [r["qualified_symbol"] for r in scores])
            complete = shadow_return is not None and baseline_return is not None
            outcome={"matured":True,"complete":complete,"maturity_date":str(maturity.date()),
              "individual_returns":returns,"integrity_exclusions":exclusions,
              "shadow_return":shadow_return if complete else None,
              "baseline_return":baseline_return if complete else None,
              "excess_return":shadow_return-baseline_return if complete else None}
            db.execute("UPDATE shadow_cohorts SET target_maturity_date=?,outcome_json=?,evaluated_at=? WHERE cohort_id=? AND outcome_json IS NULL",
                       [maturity.date(),_json(outcome),as_of,cohort_id]); results.append({"cohort_id":cohort_id,**outcome,"strategy_version":version,"horizon":horizon})
    series={}
    for version in sorted({r["strategy_version"] for r in results}):
      for horizon in HORIZONS:
        vals=[r["excess_return"] for r in results if r["strategy_version"]==version and r["horizon"]==horizon and r["complete"] and r["excess_return"] is not None]
        inf=horizon_block_inference(vals,horizon)
        series[f"{version}:{horizon}"]={"independent_matured_vintages":len(vals),"mean_excess":float(np.mean(vals)) if vals else None,"median_excess":float(np.median(vals)) if vals else None,"positive_period_rate":float(np.mean(np.array(vals)>0)) if vals else None,"inference":inf,"promotion_requirements":PROMOTION_GATES,"promotion_authorized":False}
    return {"command":"evaluate-matured-shadows","label":LABEL,"newly_evaluated":results,"series":series,"incomplete_outcomes_excluded":True,"versions_blended":False}
