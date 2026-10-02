"""Operator-controlled prospective paper portfolios and read-only validation ledger.

This module is deliberately a reporting layer over the frozen Milestone 28/29
implementation.  It does not define another score, eligibility rule, clock or
authorization path.
"""
from __future__ import annotations

import calendar
import json
from dataclasses import asdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pandas as pd

from .model_readiness import ReadinessError, fingerprint
from .prospective_us_shadow import (CONFIGURATION_HASH, SPECIFICATION,
    STRATEGY_VERSION, _paths, _table_names, plan_from_databases)

LABELS = ["PAPER RESEARCH ONLY", "NO BROKER ACTIVITY",
          "INTERIM RETURNS ARE NOT VALIDATION", "INSUFFICIENT PROSPECTIVE EVIDENCE"]
INTERIM_HORIZONS = (21, 63)
CONFIRMATORY_HORIZONS = (126, 252)
MAX_VINTAGES = 100


def _fingerprints(research_db: Path, production_db: Path) -> dict[str, Any]:
    research, production = _paths(research_db, production_db)
    return {"research": asdict(research), "production": asdict(production)}


def _blocker(exc: Exception) -> str:
    text = str(exc).lower()
    if "future" in text: return "DECISION_IN_FUTURE"
    if "month-end" in text or "exact us session" in text: return "MONTH_END_SESSION_INCOMPLETE"
    if "price" in text or "catalogue" in text: return "PRICE_OR_SESSION_DATA_STALE"
    if "fx" in text: return "FX_DATA_INCOMPLETE"
    if "sec" in text or "mapping" in text or "checkpoint" in text: return "SEC_READINESS_INCOMPLETE"
    if "dilution" in text: return "DILUTION_EVIDENCE_INCOMPLETE"
    return "READINESS_INPUT_INCOMPLETE"


def plan_monthly_cycle(*, research_db: Path, production_db: Path,
                       decision_at: datetime, session_date: date,
                       now: datetime | None = None) -> dict[str, Any]:
    """Plan without mutation and expose every blocker as a stable reason code."""
    before = _fingerprints(research_db, production_db)
    blockers: list[str] = []
    plan: dict[str, Any] | None = None
    try:
        plan = plan_from_databases(research_db=research_db, production_db=production_db,
            decision_at=decision_at, session_date=session_date, now=now)
        readiness = plan["readiness"]
        if not readiness.get("fx_ready"): blockers.append("FX_DATA_INCOMPLETE")
    except (ReadinessError, ValueError) as exc:
        blockers.append(_blocker(exc))
    after = _fingerprints(research_db, production_db)
    if before != after:
        raise ReadinessError("database changed during read-only monthly-cycle planning")
    base = {"command": "plan-prospective-monthly-cycle", "mode": "strictly_read_only",
        "labels": LABELS, "model_version": STRATEGY_VERSION,
        "configuration_hash": CONFIGURATION_HASH,
        "registration_timestamp": SPECIFICATION["registration_timestamp"],
        "decision_at": decision_at.isoformat(), "us_session_date": session_date.isoformat(),
        "completed_us_month_end_session": bool(plan and plan["readiness"]["complete_month_end_session"]),
        "blocking_reason_codes": sorted(set(blockers)), "ready": bool(plan and not blockers),
        "eligible_count": 0, "scored_count": 0, "withheld_count": 0,
        "proposed_paper_selections": [], "price_only_top3_comparator": [],
        "equal_weight_eligible_universe": [], "plan_id": None, "plan_identifier": None,
        "expires_at": None, "database_fingerprints": {"before": before, "after": after,
        "unchanged": True}, "production_publication": False, "broker_action": False}
    if not plan:
        return base
    selected = plan["proposed_paper_selections"]
    # Comparator membership is reported by the frozen price component on the
    # identical eligible population. Full membership is retained in the token/
    # eventual vintage; public planning stays bounded.
    prices, dilution = _load_plan_inputs(research_db, production_db, decision_at, session_date)
    from .prospective_us_shadow import score_inputs
    eligible, _ = score_inputs(prices, dilution, decision_at=decision_at)
    price_only = eligible.sort_values(["price_percentile", "qualified_symbol"],
                                      ascending=[False, True]).head(3)
    timestamps = {**plan["readiness"].get("latest_required_timestamps", {}),
        "latest_price_session": max((str(x) for x in prices.price_date), default=None),
        "latest_dilution_filed_at": max((str(x) for x in dilution.filed_at), default=None),
        "latest_dilution_retrieved_at": max((str(x) for x in dilution.retrieved_at), default=None),
        "latest_required_fx_session": session_date.isoformat() if plan["readiness"].get("fx_ready") else None,
    }
    return {**base, "ready": not blockers, "completed_us_month_end_session": True,
        "latest_required_timestamps": timestamps,
        "sec_mapping_and_checkpoint_readiness": {
            "ready": True, "permanently_unmapped": plan["readiness"].get("permanently_unmapped", 0)},
        "dilution_evidence_coverage": {"available": len(dilution),
            "missing": plan["readiness"].get("missing_dilution", 0)},
        "eligible_count": len(eligible), "scored_count": len(eligible),
        "withheld_count": int(plan["readiness"].get("missing_dilution", 0)),
        "proposed_paper_selections": selected,
        "price_only_top3_comparator": price_only[["security_id", "qualified_symbol",
            "price_percentile", "decision_price", "price_date"]].to_dict("records"),
        "equal_weight_eligible_universe": eligible.qualified_symbol.astype(str).tolist()[:100],
        "equal_weight_membership_count": len(eligible), "plan_id": plan["plan_id"],
        "plan_identifier": plan["plan_identifier"], "expires_at": plan["expires_at"]}


def _load_plan_inputs(research_db: Path, production_db: Path, decision_at: datetime,
                      session_date: date):
    from .prospective_us_shadow import database_inputs
    prices, dilution, _ = database_inputs(research_db=research_db,
        production_db=production_db, decision_at=decision_at, session_date=session_date)
    return prices, dilution


def vintage_list(*, research_db: Path, production_db: Path) -> dict[str, Any]:
    before = _fingerprints(research_db, production_db); items: list[dict[str, Any]] = []
    with duckdb.connect(str(research_db), read_only=True) as db:
        if "prospective_us_shadow_vintages" in _table_names(db):
            rows = db.execute("""SELECT vintage_id,strategy_version,vintage_month,decision_at,
                configuration_hash,selections_json FROM prospective_us_shadow_vintages
                ORDER BY decision_at DESC LIMIT ?""", [MAX_VINTAGES]).fetchall()
            for row in rows:
                selections = json.loads(row[5])
                items.append({"vintage_id": row[0], "version": row[1], "vintage_month": row[2],
                    "decision_at": row[3].isoformat(), "session_date": row[3].date().isoformat(),
                    "configuration_hash": row[4], "selection_count": len(selections),
                    "selections": [{"rank": i + 1, "qualified_symbol": x["qualified_symbol"],
                        "research_brief_href": f"/research?symbol={x['qualified_symbol']}"}
                        for i, x in enumerate(selections)]})
    if before != _fingerprints(research_db, production_db): raise ReadinessError("database changed during report")
    return {"command": "paper-vintage-list", "mode": "strictly_read_only", "labels": LABELS,
            "vintages": items, "count": len(items), "bounded": True}


def vintage_detail(*, research_db: Path, production_db: Path, vintage_id: str) -> dict[str, Any]:
    before = _fingerprints(research_db, production_db)
    with duckdb.connect(str(research_db), read_only=True) as db:
        if "prospective_us_shadow_vintages" not in _table_names(db): raise ReadinessError("paper vintage not found")
        row = db.execute("""SELECT vintage_id,strategy_version,decision_at,configuration_hash,
            manifest_json,selections_json,eligible_json,created_at FROM prospective_us_shadow_vintages
            WHERE vintage_id=?""", [vintage_id]).fetchone()
        if not row: raise ReadinessError("paper vintage not found")
        cohorts = db.execute("SELECT horizon_sessions,price_only_json FROM prospective_us_shadow_cohorts WHERE vintage_id=? ORDER BY horizon_sessions", [vintage_id]).fetchall()
    if before != _fingerprints(research_db, production_db): raise ReadinessError("database changed during report")
    selections, eligible = json.loads(row[5]), json.loads(row[6])
    for i, item in enumerate(selections):
        item["rank"] = i + 1; item["research_brief_href"] = f"/research?symbol={item['qualified_symbol']}"
    return {"command": "paper-vintage-detail", "mode": "strictly_read_only", "labels": LABELS,
        "vintage_id": row[0], "version": row[1], "decision_at": row[2].isoformat(),
        "session_date": row[2].date().isoformat(), "configuration_hash": row[3],
        "manifest": json.loads(row[4]), "selected_securities": selections,
        "price_only_top3_comparator": json.loads(cohorts[0][1]) if cohorts else [],
        "equal_weight_universe_membership": [x["qualified_symbol"] for x in eligible],
        "created_at": row[7].isoformat(), "cash": 0, "broker": None, "trades": [],
        "designation": "zero-cash/no-broker/no-trade paper research"}


def _returns(db, members: list[dict], target: date) -> tuple[dict[str, float], list[dict[str, Any]]]:
    values: dict[str, float] = {}; rows = []
    for member in members:
        symbol, entry = str(member["qualified_symbol"]), float(member["decision_price"])
        found = db.execute("""SELECT adjusted_close,status FROM global_price_observations
            WHERE qualified_symbol=? AND trading_date=? AND retrieved_at IS NOT NULL
            ORDER BY retrieved_at DESC LIMIT 1""", [symbol, target]).fetchone()
        if found and found[0] is not None and str(found[1]) == "available":
            result = float(found[0]) / entry - 1; values[symbol] = result
            rows.append({"qualified_symbol": symbol, "return": result, "state": "complete"})
        else:
            rows.append({"qualified_symbol": symbol, "return": None,
                "state": "delisted_or_security_action" if found else "missing_outcome"})
    return values, rows


def mark_to_market(*, research_db: Path, production_db: Path,
                   vintage_id: str | None = None, as_of: datetime | None = None) -> dict[str, Any]:
    before = _fingerprints(research_db, production_db); cutoff = as_of or datetime.now(timezone.utc)
    reports = []
    with duckdb.connect(str(research_db), read_only=True) as db:
        if "prospective_us_shadow_vintages" not in _table_names(db):
            return {"command": "paper-mark-to-market", "mode": "strictly_read_only", "labels": LABELS, "vintages": []}
        query = "SELECT vintage_id,decision_at,selections_json,eligible_json FROM prospective_us_shadow_vintages"
        params: list[Any] = []
        if vintage_id: query += " WHERE vintage_id=?"; params.append(vintage_id)
        query += " ORDER BY decision_at DESC LIMIT ?"; params.append(MAX_VINTAGES)
        vintages = db.execute(query, params).fetchall()
        for vid, decision, selected_raw, eligible_raw in vintages:
            selected, eligible = json.loads(selected_raw), json.loads(eligible_raw)
            cohort = db.execute("SELECT price_only_json FROM prospective_us_shadow_cohorts WHERE vintage_id=? LIMIT 1", [vid]).fetchone()
            price_only = json.loads(cohort[0]) if cohort else []
            sessions = [x[0] for x in db.execute("""SELECT DISTINCT session_date FROM global_exchange_sessions
                WHERE UPPER(exchange) IN ('US','NYSE','NASDAQ') AND is_open=true AND session_date>?
                AND session_date<=? ORDER BY session_date""", [decision.date(), cutoff.date()]).fetchall()]
            checkpoints = []
            for horizon in (21, 63, 126, 252):
                mature = len(sessions) >= horizon
                target = sessions[horizon - 1] if mature else None
                checkpoints.append(_checkpoint(db, horizon, target, len(sessions), selected, price_only, eligible))
            if sessions:
                checkpoints.insert(0, _checkpoint(db, "latest", sessions[-1], len(sessions), selected, price_only, eligible))
            reports.append({"vintage_id": vid, "decision_at": decision.isoformat(),
                "completed_session_count": len(sessions), "checkpoints": checkpoints})
    if before != _fingerprints(research_db, production_db): raise ReadinessError("database changed during mark-to-market")
    return {"command": "paper-mark-to-market", "mode": "strictly_read_only", "labels": LABELS,
            "as_of": cutoff.isoformat(), "vintages": reports}


def _checkpoint(db, horizon, target, count, selected, price_only, eligible):
    validation = horizon in CONFIRMATORY_HORIZONS
    base = {"horizon_sessions": horizon, "exact_session_count": count if horizon == "latest" else min(count, int(horizon)),
        "maturity_state": "mature" if target else "immature", "target_session": str(target) if target else None,
        "validation_credit_state": "eligible_confirmatory_outcome" if target and validation else "zero_validation_credit"}
    if not target: return {**base, "data_completeness_state": "not_due"}
    selected_values, selected_rows = _returns(db, selected, target)
    price_values, price_rows = _returns(db, price_only, target)
    universe_values, universe_rows = _returns(db, eligible, target)
    complete = bool(selected) and len(selected_values) == len(selected) and len(price_values) == len(price_only) and len(universe_values) == len(eligible)
    mean = lambda values: float(np.mean(list(values.values()))) if values else None
    sr, pr, ur = mean(selected_values), mean(price_values), mean(universe_values)
    return {**base, "data_completeness_state": "complete" if complete else "incomplete",
        "selected_portfolio_return": sr if complete else None,
        "price_only_comparator_return": pr if complete else None,
        "equal_weight_universe_return": ur if complete else None,
        "excess_vs_price_only": sr-pr if complete else None,
        "excess_vs_equal_weight": sr-ur if complete else None,
        "constituent_returns": {"selected": selected_rows, "price_only": price_rows, "universe": universe_rows},
        "missing_delisted_security_action_handling": "cohort withheld unless every member has an exact-session available price"}


def validation_ledger(*, research_db: Path, production_db: Path,
                      as_of: datetime | None = None) -> dict[str, Any]:
    mtm = mark_to_market(research_db=research_db, production_db=production_db, as_of=as_of)
    outcomes = {126: [], 252: []}
    for vintage in mtm["vintages"]:
        for cp in vintage["checkpoints"]:
            h = cp["horizon_sessions"]
            if h in outcomes and cp.get("data_completeness_state") == "complete": outcomes[h].append(cp)
    horizons = {}; all_selected: list[str] = []
    for vintage in mtm["vintages"]:
        latest = next((x for x in vintage["checkpoints"] if x["horizon_sessions"] == "latest"), None)
        if latest:
            all_selected.extend(x["qualified_symbol"] for x in latest.get("constituent_returns", {}).get("selected", []))
    for h, rows in outcomes.items():
        def stats(key):
            values = [r[key] for r in rows]; wins=sum(v>0 for v in values); losses=sum(v<0 for v in values)
            return {"wins": wins, "losses": losses, "ties": len(values)-wins-losses,
                "mean_excess_return": float(np.mean(values)) if values else None,
                "median_excess_return": float(np.median(values)) if values else None,
                "positive_period_rate": wins/len(values) if values else None}
        security_predictions = sum(len(r["constituent_returns"]["selected"]) for r in rows)
        requirements_met = len(rows) >= 12 and security_predictions >= 500
        intervals = None
        if requirements_met:
            intervals = {key: {"lower": float(np.quantile([r[key] for r in rows], .025)),
                "upper": float(np.quantile([r[key] for r in rows], .975)),
                "method": "bounded empirical vintage interval"}
                for key in ("excess_vs_price_only", "excess_vs_equal_weight")}
        horizons[str(h)] = {"mature_count": len(rows), "security_level_predictions": security_predictions,
            "against_price_only": stats("excess_vs_price_only"),
            "against_equal_weight": stats("excess_vs_equal_weight"), "confidence_intervals": intervals,
            "pre_registered_gates_evaluated": requirements_met}
    official = len(mtm["vintages"]); mature126=len(outcomes[126]); mature252=len(outcomes[252])
    counts = {symbol: all_selected.count(symbol) for symbol in set(all_selected)}
    concentration = (max(counts.values()) / len(all_selected)) if all_selected else None
    requirements_met = all(x["pre_registered_gates_evaluated"] for x in horizons.values())
    return {"command": "prospective-validation-ledger", "mode": "strictly_read_only", "labels": LABELS,
        "model_version": STRATEGY_VERSION, "configuration_hash": CONFIGURATION_HASH,
        "official_vintage_count": official, "immature_vintage_count": official-mature126,
        "mature_126_session_count": mature126, "mature_252_session_count": mature252,
        "horizons": horizons, "concentration": {"maximum_selected_symbol_share": concentration,
            "selection_observations": len(all_selected)},
        "gate_state": ("PRE_REGISTERED GATES AVAILABLE FOR REVIEW" if requirements_met
                       else "INSUFFICIENT PROSPECTIVE EVIDENCE"),
        "minimum_completed_vintages": SPECIFICATION["promotion_requirements"]["completed_monthly_vintages"]}
