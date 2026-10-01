"""Milestone 28: locked, prospective-only US dilution-overlay paper research."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pandas as pd

from .model_readiness import ReadinessError, _same_file, fingerprint
from .shadow_portfolios import maturity_session

SPEC_PATH = Path(__file__).with_name("prospective_us_shadow_v1.json")
AUTHORIZATION_PHRASE = "I AUTHORIZE RESEARCH-ONLY PROSPECTIVE SHADOW CREATION"
LABEL = "PAPER RESEARCH SELECTIONS — NOT INVESTMENT ADVICE"
PLAN_TTL = timedelta(minutes=10)
MAX_OUTPUT_ROWS = 25


def canonical_specification() -> dict[str, Any]:
    return json.loads(SPEC_PATH.read_text(encoding="utf-8"))


def configuration_hash(specification: dict[str, Any] | None = None) -> str:
    payload = json.dumps(specification or canonical_specification(), sort_keys=True,
                         separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode()).hexdigest()


SPECIFICATION = canonical_specification()
CONFIGURATION_HASH = configuration_hash()
STRATEGY_VERSION = SPECIFICATION["semantic_version"]
REGISTRATION_AT = datetime.fromisoformat(SPECIFICATION["registration_timestamp"].replace("Z", "+00:00"))


def _paths(research: Path, production: Path) -> tuple[Any, Any]:
    if not research.is_file() or not production.is_file():
        raise ReadinessError("explicit existing research and production database paths are required")
    if research.is_symlink() or production.is_symlink() or _same_file(research, production):
        raise ReadinessError("database paths must be distinct, non-aliased regular files")
    return fingerprint(research), fingerprint(production)


def score_inputs(prices: pd.DataFrame, dilution: pd.DataFrame, *, decision_at: datetime,
                 maximum_age_days: int = 550) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Create the locked 90/10 score from information known at decision time."""
    if decision_at.tzinfo is None:
        raise ReadinessError("decision timestamp must be timezone-aware")
    required_price = {"security_id", "qualified_symbol", "price_percentile", "decision_price",
                      "model_ready", "price_date"}
    required_dilution = {"security_id", "diluted_share_growth", "period_end", "filed_at",
                         "retrieved_at", "reliable", "compatible", "provenance"}
    if required_price - set(prices) or required_dilution - set(dilution):
        raise ReadinessError("prospective score input schema is incomplete")
    merged = prices.merge(dilution, on="security_id", how="left", suffixes=("", "_dilution"))
    cutoff = pd.Timestamp(decision_at)
    for column in ("filed_at", "retrieved_at"):
        merged[column] = pd.to_datetime(merged[column], utc=True, errors="coerce")
    merged["period_end"] = pd.to_datetime(merged.period_end, errors="coerce")
    growth = pd.to_numeric(merged.diluted_share_growth, errors="coerce")
    age = (cutoff.tz_localize(None) - merged.period_end).dt.days
    merged["dilution_available"] = (growth.notna() & np.isfinite(growth) & merged.reliable.eq(True)
        & merged.compatible.eq(True) & merged.filed_at.le(cutoff) & merged.retrieved_at.le(cutoff)
        & age.between(0, maximum_age_days))
    merged["eligible"] = merged.model_ready.eq(True) & merged.dilution_available
    reasons = []
    for row in merged.itertuples():
        row_reasons = []
        if not bool(row.model_ready): row_reasons.append("price_not_model_ready")
        if not bool(row.dilution_available): row_reasons.append("dilution_unavailable")
        reasons.append(row_reasons)
    merged["withholding_reasons"] = reasons
    eligible = merged.loc[merged.eligible].copy()
    # Lower share growth (including buybacks) is better. Stable symbol is the rank tie breaker.
    eligible["dilution_percentile"] = eligible.diluted_share_growth.rank(
        method="average", pct=True, ascending=False)
    eligible["prospective_score"] = .9 * pd.to_numeric(eligible.price_percentile) + .1 * eligible.dilution_percentile
    eligible = eligible.sort_values(["prospective_score", "qualified_symbol"], ascending=[False, True])
    selected = eligible.head(3).copy()
    return eligible, selected


def _plan_payload(decision_at: datetime, eligible: pd.DataFrame, selected: pd.DataFrame,
                  generated_at: datetime, readiness: dict[str, Any]) -> dict[str, Any]:
    payload = {"strategy_version": STRATEGY_VERSION, "configuration_hash": CONFIGURATION_HASH,
        "decision_at": pd.Timestamp(decision_at).isoformat(), "vintage_month": decision_at.strftime("%Y-%m"),
        "generated_at": pd.Timestamp(generated_at).isoformat(), "eligible_ids": eligible.security_id.astype(str).tolist(),
        "selected_ids": selected.security_id.astype(str).tolist(), "readiness": readiness}
    payload["plan_id"] = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return payload


def validate_prospective_boundary(decision_at: datetime) -> None:
    if decision_at.strftime("%Y-%m") <= "2026-09":
        raise ReadinessError("September 2026 and earlier vintages cannot be reconstructed")
    if decision_at.tzinfo is None or decision_at <= REGISTRATION_AT:
        raise ReadinessError("first decision vintage must be strictly after registration")


def build_plan(prices: pd.DataFrame, dilution: pd.DataFrame, *, decision_at: datetime,
               generated_at: datetime, session_ready: bool, fx_ready: bool) -> dict[str, Any]:
    validate_prospective_boundary(decision_at)
    eligible, selected = score_inputs(prices, dilution, decision_at=decision_at)
    readiness = {"complete_month_end_session": bool(session_ready), "fx_ready": bool(fx_ready),
                 "confirmed": bool(session_ready and fx_ready)}
    payload = _plan_payload(decision_at, eligible, selected, generated_at, readiness)
    fields = ["security_id", "qualified_symbol", "price_percentile", "dilution_percentile",
              "prospective_score", "decision_price", "price_date", "diluted_share_growth",
              "period_end", "filed_at", "retrieved_at", "provenance", "withholding_reasons"]
    return {"command": "plan-prospective-us-shadow", "mode": "strictly_read_only", "label": LABEL,
        **payload, "registration_timestamp": SPECIFICATION["registration_timestamp"],
        "eligible_count": len(eligible), "paper_selection_count": len(selected),
        "proposed_paper_selections": selected[fields].head(3).to_dict("records"),
        "ranking_families": ["price", "dilution"], "diagnostic_only_families":
        ["leverage", "profitability", "cash_flow", "growth", "valuation"],
        "production_publication": False, "broker_action": False, "validated": False}


def _schema(db: duckdb.DuckDBPyConnection) -> None:
    db.execute("""CREATE TABLE IF NOT EXISTS prospective_us_shadow_vintages(
      vintage_id VARCHAR PRIMARY KEY, strategy_version VARCHAR, vintage_month VARCHAR,
      decision_at TIMESTAMPTZ, configuration_hash VARCHAR, plan_id VARCHAR UNIQUE,
      manifest_json JSON, selections_json JSON, eligible_json JSON, created_at TIMESTAMPTZ,
      label VARCHAR, UNIQUE(strategy_version,vintage_month))""")
    db.execute("""CREATE TABLE IF NOT EXISTS prospective_us_shadow_cohorts(
      cohort_id VARCHAR PRIMARY KEY, vintage_id VARCHAR, strategy_version VARCHAR,
      horizon_sessions INTEGER, price_only_json JSON, eligible_baseline_json JSON,
      outcome_json JSON, evaluated_at TIMESTAMPTZ)""")


def create_from_plan(*, research_db: Path, production_db: Path, plan: dict[str, Any],
                     prices: pd.DataFrame, dilution: pd.DataFrame, authorization: str,
                     now: datetime) -> dict[str, Any]:
    if authorization != AUTHORIZATION_PHRASE:
        raise PermissionError("exact research-only authorization phrase is required")
    before_research, before_production = _paths(research_db, production_db)
    decision_at = datetime.fromisoformat(plan["decision_at"])
    rebuilt = build_plan(prices, dilution, decision_at=decision_at,
        generated_at=datetime.fromisoformat(plan["generated_at"]),
        session_ready=plan["readiness"]["complete_month_end_session"], fx_ready=plan["readiness"]["fx_ready"])
    locked_plan_fields = ("strategy_version", "configuration_hash", "decision_at", "vintage_month",
                          "generated_at", "eligible_ids", "selected_ids", "readiness", "plan_id")
    if any(plan.get(key) != rebuilt.get(key) for key in locked_plan_fields):
        raise ReadinessError("plan is modified or does not match current inputs")
    if now - datetime.fromisoformat(plan["generated_at"]) > PLAN_TTL:
        raise ReadinessError("plan has expired")
    if not rebuilt["readiness"]["confirmed"]:
        raise ReadinessError("complete month-end session and FX readiness are required")
    eligible, selected = score_inputs(prices, dilution, decision_at=decision_at)
    # Price-only comparator uses exactly the same eligible cohort and decision date.
    price_only = eligible.sort_values(["price_percentile", "qualified_symbol"], ascending=[False, True]).head(3)
    vintage_id = f"{STRATEGY_VERSION}:{decision_at:%Y-%m}"
    with duckdb.connect(str(research_db)) as db:
        _schema(db)
        incompatible = db.execute("SELECT configuration_hash FROM prospective_us_shadow_vintages WHERE strategy_version=? AND configuration_hash<>? LIMIT 1",
                                  [STRATEGY_VERSION, CONFIGURATION_HASH]).fetchone()
        if incompatible:
            raise ReadinessError("strategy version configuration is immutable; register a new version")
        existing = db.execute("SELECT configuration_hash FROM prospective_us_shadow_vintages WHERE strategy_version=? AND vintage_month=?",
                              [STRATEGY_VERSION, decision_at.strftime("%Y-%m")]).fetchone()
        if existing:
            if existing[0] != CONFIGURATION_HASH: raise ReadinessError("strategy version configuration is immutable")
            return {"command": "create-prospective-us-shadow", "status": "already_exists", "mutated": False}
        if db.execute("SELECT count(*) FROM prospective_us_shadow_vintages WHERE plan_id=?", [plan["plan_id"]]).fetchone()[0]:
            raise ReadinessError("plan has already been used")
        db.begin()
        try:
            manifest = {"specification": SPECIFICATION, "configuration_hash": CONFIGURATION_HASH}
            db.execute("INSERT INTO prospective_us_shadow_vintages VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                [vintage_id, STRATEGY_VERSION, decision_at.strftime("%Y-%m"), decision_at,
                 CONFIGURATION_HASH, plan["plan_id"], json.dumps(manifest),
                 selected.to_json(orient="records", date_format="iso"), eligible.to_json(orient="records", date_format="iso"), now, LABEL])
            for horizon in (126, 252):
                db.execute("INSERT INTO prospective_us_shadow_cohorts VALUES (?,?,?,?,?,?,?,?)",
                    [f"{vintage_id}:{horizon}", vintage_id, STRATEGY_VERSION, horizon,
                     price_only.to_json(orient="records", date_format="iso"),
                     eligible.to_json(orient="records", date_format="iso"), None, None])
            db.commit()
        except Exception:
            db.rollback(); raise
    if fingerprint(production_db) != before_production:
        raise ReadinessError("production database changed during research creation")
    return {"command": "create-prospective-us-shadow", "status": "created", "mutated": True,
        "vintage_id": vintage_id, "paper_selection_count": len(selected), "cohorts": [126, 252],
        "production_unchanged": True, "production_published": False, "broker_action": False}


def status(*, research_db: Path, production_db: Path) -> dict[str, Any]:
    before = _paths(research_db, production_db)
    rows: list[tuple] = []; cohorts: list[tuple] = []
    with duckdb.connect(str(research_db), read_only=True) as db:
        tables = {row[0] for row in db.execute("SHOW TABLES").fetchall()}
        if "prospective_us_shadow_vintages" in tables:
            rows = db.execute("SELECT vintage_month,decision_at,configuration_hash,selections_json FROM prospective_us_shadow_vintages ORDER BY decision_at").fetchall()
            cohorts = db.execute("SELECT horizon_sessions,count(*),count(outcome_json) FROM prospective_us_shadow_cohorts GROUP BY horizon_sessions ORDER BY horizon_sessions").fetchall()
    if before != _paths(research_db, production_db): raise ReadinessError("database changed during read-only status")
    maturity = {str(h): {"total": int(total), "completed": int(done), "immature": int(total-done)} for h,total,done in cohorts}
    return {"command": "prospective-us-shadow-status", "label": "PROSPECTIVE PAPER RESEARCH ONLY",
        "warning": "NOT VALIDATED — NOT INVESTMENT ADVICE.", "strategy_version": STRATEGY_VERSION,
        "configuration_hash": CONFIGURATION_HASH, "registration_timestamp": SPECIFICATION["registration_timestamp"],
        "next_eligible_month_end": SPECIFICATION["first_permissible_vintage"], "current_readiness": "requires_explicit_plan",
        "vintages": len(rows), "completed_vintages": min((v["completed"] for v in maturity.values()), default=0),
        "minimum_completed_vintages": 12, "paper_selection_count": sum(len(json.loads(r[3])) for r in rows),
        "cohort_maturity": maturity, "promotion_gates": SPECIFICATION["promotion_requirements"],
        "evidence_immature": True, "production_unchanged": True}


def evaluate_maturity(sessions: pd.DatetimeIndex, decision_at: datetime, horizon: int,
                      returns: dict[str, float], selected: list[str], price_only: list[str],
                      eligible: list[str]) -> dict[str, Any]:
    maturity = maturity_session(sessions, decision_at, horizon)
    if maturity is None:
        return {"matured": False, "reason": "exact_session_not_reached"}
    required = set(selected) | set(price_only) | set(eligible)
    if not required or not required.issubset(returns):
        return {"matured": True, "complete": False, "reason": "incomplete_outcomes"}
    mean = lambda members: float(np.mean([returns[x] for x in members]))
    overlay, price, baseline = mean(selected), mean(price_only), mean(eligible)
    return {"matured": True, "complete": True, "maturity_date": str(maturity.date()),
        "overlay_return": overlay, "price_only_return": price, "equal_weight_return": baseline,
        "incremental_vs_price_only": overlay-price, "excess_vs_equal_weight": overlay-baseline}
