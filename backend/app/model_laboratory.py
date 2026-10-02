"""Milestone 34 read-only model laboratory.

The laboratory explains the already-frozen prospective model.  It deliberately
has no persistence or publication capability.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pandas as pd

from .active_catalogue import select_active_catalogue
from .model_readiness import ReadinessError, fingerprint
from .price_segments import detect_price_segments
from .prospective_us_shadow import (CONFIGURATION_HASH, REGISTRATION_AT,
    SPECIFICATION, STRATEGY_VERSION, _dilution_from_facts, _paths, score_inputs)
from .research_observations import ObservationPolicy, build_model_ready_observations
from .research_scoring import prepare_cross_section

PREVIEW_LABELS = ["INDICATIVE PRE-VINTAGE PREVIEW", "NOT VALIDATION",
                  "NOT A PAPER SELECTION", "NOT INVESTMENT ADVICE"]
RECONSTRUCTION_LABELS = ["RETROSPECTIVE RECONSTRUCTION",
                         "NOT PROSPECTIVE VALIDATION", "ZERO VALIDATION CREDIT"]
FORMULA = SPECIFICATION["score_formula"]
MAX_CITATIONS = 3


class ModelLaboratoryError(ReadinessError):
    reason_code = "MODEL_LAB_NOT_READY"


def public_error_code(exc: Exception) -> str:
    if isinstance(exc, ModelLaboratoryError): return exc.reason_code
    if isinstance(exc, ReadinessError): return "MODEL_LAB_NOT_READY"
    return "MODEL_LAB_INTERNAL_ERROR"


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ModelLaboratoryError("timezone-aware decision timestamp required")
    return value.astimezone(timezone.utc)


def _tables(db: duckdb.DuckDBPyConnection) -> set[str]:
    return {str(row[0]) for row in db.execute("SHOW TABLES").fetchall()}


def _inputs(research_db: Path, production_db: Path, decision_at: datetime
            ) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    """Read authoritative inputs without applying prospective-vintage gates."""
    before = _paths(research_db, production_db)
    with duckdb.connect(str(production_db), read_only=True) as db: db.execute("SELECT 1")
    required = {"security_master_retrievals", "security_listings", "global_price_observations",
        "global_fx_observations", "global_corporate_actions", "eodhd_ingestion_checkpoints",
        "sec_issuers", "sec_checkpoints", "sec_facts"}
    with duckdb.connect(str(research_db), read_only=True) as db:
        if required - _tables(db): raise ModelLaboratoryError("model laboratory schema unavailable")
        active = select_active_catalogue(db, as_of=decision_at.replace(tzinfo=None))
        if active is None: raise ModelLaboratoryError("point-in-time catalogue unavailable")
        catalogue = active.listings
        identities = db.execute("SELECT security_id,company_name FROM security_listings WHERE retrieval_id=?", [active.retrieval_id]).fetchdf()
        catalogue = catalogue.merge(identities, on="security_id", how="left")
        prices = db.execute("SELECT * FROM global_price_observations").fetchdf()
        fx = db.execute("SELECT * FROM global_fx_observations").fetchdf()
        actions = db.execute("SELECT * FROM global_corporate_actions").fetchdf()
        failures = db.execute("SELECT qualified_symbol,error_code FROM eodhd_ingestion_checkpoints WHERE status='failed'").fetchdf()
        facts = db.execute("SELECT * FROM sec_facts").fetchdf()
        issuers = db.execute("SELECT security_id,mapped_at FROM sec_issuers").fetchdf()
        checkpoints = db.execute("SELECT security_id,status,updated_at FROM sec_checkpoints").fetchdf()
    cutoff = pd.Timestamp(decision_at)
    # build_model_ready_observations and prepare_cross_section are the exact frozen price path.
    us = catalogue.loc[catalogue.region.eq("US") & catalogue.eligible].copy()
    dataset = build_model_ready_observations(catalogue=catalogue, prices=prices, fx=fx,
        actions=actions, failures=failures, decision_at=decision_at, policy=ObservationPolicy())
    observations = dataset.observations
    us_obs = observations.loc[observations.security_id.isin(us.security_id)].copy()
    visible = prices.loc[pd.to_datetime(prices.retrieved_at, utc=True, errors="coerce").le(cutoff)].copy()
    visible_actions = actions.loc[pd.to_datetime(actions.ex_date, errors="coerce").dt.date.le(decision_at.date())]
    scores = prepare_cross_section(us_obs, prices, decision_at=decision_at,
        segment_boundaries=detect_price_segments(visible, visible_actions))
    if scores.empty: raise ModelLaboratoryError("frozen price score unavailable")
    scores["price_percentile"] = scores.composite_score.rank(method="average", pct=True)
    latest = visible.loc[visible.status.eq("available")].sort_values(
        ["qualified_symbol", "trading_date", "retrieved_at"]).drop_duplicates("qualified_symbol", keep="last")
    price_rows = scores.merge(latest[["qualified_symbol", "adjusted_close", "trading_date"]], on="qualified_symbol")
    price_rows = price_rows.rename(columns={"adjusted_close":"decision_price", "trading_date":"price_date"})
    price_rows["model_ready"] = True
    cp = checkpoints.copy(); cp["updated_at"] = pd.to_datetime(cp.updated_at, utc=True, errors="coerce")
    completed = set(cp.loc[cp.updated_at.le(cutoff) & cp.status.eq("completed"), "security_id"])
    mapped = set(issuers.loc[pd.to_datetime(issuers.mapped_at, utc=True, errors="coerce").le(cutoff), "security_id"])
    dilution = _dilution_from_facts(facts, us.loc[us.security_id.isin(mapped & completed)], decision_at)
    after = _paths(research_db, production_db)
    if before != after: raise ModelLaboratoryError("database fingerprint changed")
    fps = {"research":{"before":asdict(before[0]),"after":asdict(after[0]),"unchanged":True},
           "production":{"before":asdict(before[1]),"after":asdict(after[1]),"unchanged":True}}
    return price_rows, dilution, us, {"fingerprints":fps, "prices":visible, "fx":fx,
                                      "facts":facts, "catalogue_retrieved_at":getattr(active, "retrieved_at", None)}


def _render(prices: pd.DataFrame, dilution: pd.DataFrame, catalogue: pd.DataFrame,
            meta: dict[str, Any], decision_at: datetime, labels: list[str]) -> dict[str, Any]:
    eligible, selected = score_inputs(prices, dilution, decision_at=decision_at)
    merged = prices.merge(dilution, on="security_id", how="left")
    missing_dilution = max(0, len(prices) - len(eligible))
    missing_price = max(0, len(catalogue) - len(prices))
    reason_counts = Counter()
    if missing_dilution: reason_counts["dilution_unavailable"] = missing_dilution
    if missing_price: reason_counts["price_not_model_ready"] = missing_price
    names = catalogue.set_index("security_id").get("company_name", pd.Series(dtype=object)).to_dict()
    rows = []
    for rank, row in enumerate(selected.itertuples(), 1):
        pp, dp = float(row.price_percentile), float(row.dilution_percentile)
        pc, dc = .90 * pp, .10 * dp
        combined = pc + dc
        if not np.isclose(float(row.prospective_score), combined, rtol=0, atol=1e-12):
            raise ModelLaboratoryError("frozen score failed exact reconciliation")
        accession = str(row.provenance).removeprefix("sec:")[:32]
        rows.append({"preview_rank":rank, "security_id":str(row.security_id),
            "qualified_symbol":str(row.qualified_symbol), "company_name":str(names.get(row.security_id, "Unavailable"))[:120],
            "price_percentile":pp, "price_contribution":pc, "dilution_percentile":dp,
            "dilution_contribution":dc, "combined_score":combined,
            "decision_price":float(row.decision_price), "price_date":str(row.price_date),
            "diluted_share_growth":float(row.diluted_share_growth), "dilution_period_end":str(row.period_end),
            "dilution_public_at":str(row.filed_at), "dilution_retrieved_at":str(row.retrieved_at),
            "tie_break_fields":{"combined_score":combined,"qualified_symbol":str(row.qualified_symbol)},
            "risk_flags":["current_membership_not_survivorship_free"], "missing_contextual_information":["future returns", "investment suitability"],
            "official_sec_citations":([{"source":"SEC EDGAR", "accession_number":accession}] if accession else [])[:MAX_CITATIONS],
            "context_score_effect":"Context does not affect the frozen score."})
    dates = pd.to_datetime(meta["prices"].get("trading_date"), errors="coerce")
    return {"labels":labels, "decision_at":decision_at.isoformat(), "model_version":STRATEGY_VERSION,
        "configuration_hash":CONFIGURATION_HASH, "registration_timestamp":SPECIFICATION["registration_timestamp"],
        "formula":FORMULA, "weights":{"price":.90,"dilution":.10},
        "eligible_universe_count":int(len(catalogue)), "scored_count":int(len(eligible)),
        "withheld_count":int(len(catalogue)-len(eligible)), "withheld_reason_counts":dict(sorted(reason_counts.items())),
        "input_availability_range":{"first_price_date":str(dates.min().date()) if dates.notna().any() else None,
                                    "last_price_date":str(dates.max().date()) if dates.notna().any() else None},
        "database_fingerprints":meta["fingerprints"], "preview_entries":rows[:3],
        "prospective_vintages_created":0, "validation_observations_added":0,
        "recommendations_generated":0, "production_publication_available":False,
        "scheduler_enabled":False, "validation_credit":0}


def top3_preview(*, research_db: Path, production_db: Path, decision_at: datetime,
                 now: datetime | None = None) -> dict[str, Any]:
    decision_at = _utc(decision_at); now = _utc(now or datetime.now(timezone.utc))
    if decision_at > now: raise ModelLaboratoryError("future evidence prohibited")
    inputs = _inputs(research_db, production_db, decision_at)
    return _render(*inputs[:3], inputs[3], decision_at, PREVIEW_LABELS)


def assess_september_reconstruction(*, research_db: Path, production_db: Path,
                                    decision_at: datetime) -> dict[str, Any]:
    decision_at = _utc(decision_at)
    _paths(research_db, production_db)
    reasons: list[str] = []
    if decision_at.date() != date(2026, 9, 30):
        reasons.append("incomplete_month_end_prices")
    try:
        prices, dilution, catalogue, meta = _inputs(research_db, production_db, decision_at)
        latest = pd.to_datetime(meta["prices"].trading_date, errors="coerce").dt.date.max()
        if latest != date(2026, 9, 30): reasons.append("incomplete_month_end_prices")
        if pd.to_datetime(meta["prices"].retrieved_at, utc=True, errors="coerce").gt(decision_at).any(): reasons.append("evidence_retrieved_after_decision")
        if dilution.empty: reasons.append("unavailable_dilution_evidence")
        fx = meta["fx"]
        if fx.empty or not pd.to_datetime(fx.available_at, utc=True, errors="coerce").le(decision_at).any(): reasons.append("incomplete_fx")
    except Exception:
        reasons.append("unavailable_point_in_time_catalogue")
        prices = dilution = catalogue = pd.DataFrame(); meta = {}
    if reasons:
        return {"labels":RECONSTRUCTION_LABELS, "decision_at":decision_at.isoformat(),
            "reconstruction_status":"unavailable", "reason_codes":sorted(set(reasons)),
            "preview_entries":[], "validation_credit":0, "prospective_vintages_created":0,
            "validation_observations_added":0, "recommendations_generated":0}
    result = _render(prices, dilution, catalogue, meta, decision_at, RECONSTRUCTION_LABELS)
    result["reconstruction_status"] = "available"
    return result
