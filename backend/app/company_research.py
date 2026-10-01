"""Deterministic, point-in-time company research briefs (Milestone 32).

This adapter is deliberately read-only.  It describes registered evidence; it
does not generate a ranking, create a vintage, or interpret filing prose.
"""
from __future__ import annotations

import html
import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pandas as pd

from .active_catalogue import select_active_catalogue
from .model_readiness import ReadinessError, fingerprint
from .prospective_us_shadow import (CONFIGURATION_HASH, SPECIFICATION,
                                    database_inputs, score_inputs)

MAX_EVENTS = 10
MAX_TEXT = 240
NOTICE = "PAPER RESEARCH ONLY — NOT INVESTMENT ADVICE."


def _utc(value: datetime, now: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ReadinessError("decision timestamp must be timezone-aware")
    value = value.astimezone(timezone.utc)
    if value > now.astimezone(timezone.utc):
        raise ReadinessError("future decision timestamps are not permitted")
    return value


def _safe(value: Any, limit: int = MAX_TEXT) -> str:
    """Render stored text as inert, bounded display text."""
    return html.escape(str(value or ""), quote=True)[:limit]


def _tables(db: duckdb.DuckDBPyConnection) -> set[str]:
    return {str(row[0]) for row in db.execute("SHOW TABLES").fetchall()}


def _quality(value: float, strong: float, weak: float, *, inverse: bool = False) -> str:
    if not np.isfinite(value): return "unavailable"
    if inverse: return "supportive" if value <= strong else "cautionary" if value >= weak else "mixed"
    return "supportive" if value >= strong else "cautionary" if value <= weak else "mixed"


def _fundamental_context(facts: pd.DataFrame) -> list[dict[str, Any]]:
    families = {
        "revenue_direction": (["Revenue", "Revenues", "SalesRevenueNet"], False),
        "profitability": (["NetIncomeLoss", "ProfitLoss"], False),
        "operating_cash_generation": (["NetCashProvidedByUsedInOperatingActivities"], False),
        "free_cash_flow": (["NetCashProvidedByUsedInOperatingActivities", "PaymentsToAcquirePropertyPlantAndEquipment"], False),
        "debt_context": (["LongTermDebt", "LongTermDebtCurrent", "DebtCurrent"], True),
        "eps": (["EarningsPerShareDiluted"], False),
        "shares_dilution": (["WeightedAverageNumberOfDilutedSharesOutstanding"], True),
        "valuation_context": ([], False),
    }
    output = []
    for label, (concepts, inverse) in families.items():
        subset = facts.loc[facts.concept.isin(concepts)].sort_values(["period_end", "public_at"])
        if label == "free_cash_flow" and not subset.empty:
            latest = subset.groupby("concept").tail(1)
            values = {r.concept: float(r.value) for r in latest.itertuples()}
            value = values.get("NetCashProvidedByUsedInOperatingActivities")
            capex = values.get("PaymentsToAcquirePropertyPlantAndEquipment")
            value = None if value is None or capex is None else value - abs(capex)
        else:
            value = None if subset.empty else float(subset.iloc[-1].value)
        if value is None:
            classification, direction = "unavailable", "No defensible point-in-time value is available."
        elif label in {"revenue_direction", "shares_dilution"} and len(subset) >= 2:
            prior = float(subset.iloc[-2].value)
            change = None if prior == 0 else value / abs(prior) - 1
            classification = "unavailable" if change is None else _quality(change, .02, -.02, inverse=inverse)
            direction = "increased" if change and change > .01 else "decreased" if change and change < -.01 else "broadly stable"
        else:
            classification = _quality(value, 0, 0, inverse=inverse)
            direction = "positive" if value > 0 else "negative" if value < 0 else "neutral"
        output.append({"metric": label, "classification": classification, "direction": direction,
                       "value": value, "score_effect": "none_context_only"})
    return output


def _selection(db: duckdb.DuckDBPyConnection, security_id: str,
               decision_at: datetime) -> dict[str, Any]:
    if decision_at.date() < date(2026, 10, 31):
        return {"exists": False, "reason": "no paper selection exists yet",
                "first_permissible_vintage": "2026-10-31"}
    if "prospective_us_shadow_vintages" not in _tables(db):
        return {"exists": False, "reason": "no registered paper vintage exists at this decision time",
                "first_permissible_vintage": "2026-10-31"}
    rows = db.execute("""SELECT decision_at,selections_json,eligible_json FROM prospective_us_shadow_vintages
        WHERE decision_at<=? ORDER BY decision_at DESC LIMIT 24""", [decision_at]).fetchall()
    for vintage_at, raw, eligible_raw in rows:
        members = json.loads(raw)
        universe = json.loads(eligible_raw)
        for rank, member in enumerate(members, 1):
            if str(member.get("security_id")) == security_id:
                price = float(member["price_percentile"])
                dilution = float(member["dilution_percentile"])
                final = float(member["prospective_score"])
                return {"exists": True, "decision_at": vintage_at, "paper_group_rank": rank,
                    "final_frozen_score": final, "price_contribution": .9 * price,
                    "dilution_contribution": .1 * dilution,
                    "eligible_universe_percentile": (sum(float(x["prospective_score"]) <= final for x in universe)
                                                     / len(universe) if universe else None),
                    "tie_break": "qualified_symbol ascending when scores tie",
                    "reason": "Its frozen score placed it within the bounded zero-to-three paper group.",
                    "validated_recommendation": False}
    return {"exists": False, "reason": "company was not in a registered zero-to-three paper group",
            "first_permissible_vintage": "2026-10-31"}


def company_research_brief(*, research_db: Path, production_db: Path,
                           qualified_symbol: str, decision_at: datetime,
                           now: datetime | None = None, max_events: int = 6) -> dict[str, Any]:
    """Return one bounded brief from evidence known at ``decision_at``."""
    now = now or datetime.now(timezone.utc); decision_at = _utc(decision_at, now)
    if not qualified_symbol or "." not in qualified_symbol or qualified_symbol != qualified_symbol.upper():
        raise ReadinessError("a normalized exchange-qualified symbol is required")
    if not 1 <= max_events <= MAX_EVENTS: raise ReadinessError("event limit must be between 1 and 10")
    before = (fingerprint(research_db), fingerprint(production_db))
    prices, dilution, _ = database_inputs(research_db=research_db, production_db=production_db,
        decision_at=decision_at, session_date=decision_at.date())
    matches = prices.loc[prices.qualified_symbol.eq(qualified_symbol)]
    if len(matches) != 1: raise ReadinessError("unknown, ambiguous, or non-model-ready qualified symbol")
    eligible, _ = score_inputs(prices, dilution, decision_at=decision_at)
    scored = eligible.loc[eligible.security_id.eq(matches.iloc[0].security_id)]
    if len(scored) != 1: raise ReadinessError("observation is not model-ready with point-in-time dilution")
    row = scored.iloc[0]; security_id = str(row.security_id)
    with duckdb.connect(str(research_db), read_only=True) as db:
        active = select_active_catalogue(db, as_of=decision_at.replace(tzinfo=None))
        identities = db.execute("""SELECT company_name,ticker,primary_exchange,listing_country,currency
          FROM security_listings WHERE retrieval_id=? AND security_id=?""",
          [active.retrieval_id, security_id]).fetchall()
        if len(identities) != 1: raise ReadinessError("ticker reuse or identity ambiguity requires effective-date resolution")
        company, ticker, exchange, country, currency = identities[0]
        facts = db.execute("""SELECT * FROM sec_facts WHERE security_id=? AND public_at<=?
          AND retrieved_at<=? ORDER BY period_end,public_at,retrieved_at""", [security_id, decision_at, decision_at]).fetchdf()
        events = []
        if "sec_event_metadata" in _tables(db):
            records = db.execute("""SELECT public_at,form,event_category,is_amendment,explanation,
              accession_number,item_codes FROM sec_event_metadata WHERE security_id=? AND public_at<=?
              AND retrieval_at<=? ORDER BY public_at DESC,event_id DESC LIMIT ?""",
              [security_id, decision_at, decision_at, max_events]).fetchall()
            events = [{"publication_timestamp": r[0], "form": _safe(r[1], 16),
                "category": _safe(r[2], 64) if r[6] != "[]" else "detailed classification unavailable",
                "amendment": bool(r[3]), "explanation": _safe(r[4]),
                "citation": {"accession": _safe(r[5], 32), "form": _safe(r[1], 16)}} for r in records]
        selection = _selection(db, security_id, decision_at)
        permanent = ("sec_checkpoints" in _tables(db) and bool(db.execute(
            "SELECT count(*) FROM sec_checkpoints WHERE security_id=? AND status='permanent_failure' AND updated_at<=?",
            [security_id, decision_at]).fetchone()[0]))
    price_percentile = float(row.price_percentile)
    strength = "strong" if price_percentile >= .67 else "weak" if price_percentile <= .33 else "moderate"
    growth = float(row.diluted_share_growth)
    ownership = "diluted" if growth > .01 else "reduced" if growth < -.01 else "broadly stable"
    risk_flags = ["current_membership_not_survivorship_free"]
    if (decision_at.date() - pd.Timestamp(row.price_date).date()).days > 7:
        risk_flags.append("stale_or_missing_data")
    volatility = float(row.get("annualized_volatility_63d", np.nan))
    drawdown = float(row.get("max_drawdown_126d", np.nan))
    if (np.isfinite(volatility) and volatility >= .80) or (np.isfinite(drawdown) and drawdown <= -.50):
        risk_flags.append("extreme_volatility_or_drawdown")
    if permanent: risk_flags.append("permanent_SEC_mapping_failure")
    categories = {e["category"] for e in events}
    for category, flag in (("capital_raise", "material_capital_raise"),
                           ("delisting_compliance", "delisting_or_compliance_filing"),
                           ("bankruptcy_distress", "bankruptcy_or_distress_filing")):
        if category in categories: risk_flags.append(flag)
    if any(e["amendment"] for e in events): risk_flags.append("recent_amendment")
    missing = [x["metric"] for x in _fundamental_context(facts) if x["classification"] == "unavailable"]
    result = {"notice": NOTICE, "known_at": decision_at, "company_identity": {
        "security_id": security_id, "qualified_symbol": qualified_symbol, "ticker": _safe(ticker, 24),
        "company_name": _safe(company, 120), "exchange": _safe(exchange, 32),
        "country": _safe(country, 32), "currency": _safe(currency, 8)},
      "why_it_is_being_viewed": selection,
      "price_behaviour": {"momentum": "positive" if row.get("momentum_126d", 0) > 0 else "non-positive",
        "trend": "positive" if row.get("trend_21d", 0) > 0 else "non-positive",
        "volatility": volatility, "drawdown": drawdown,
        "history_quality": "model-ready", "peer_strength": strength, "price_percentile": price_percentile,
        "price_contribution": .9 * price_percentile},
      "dilution_share_count_evidence": {"year_over_year_change": growth, "ownership_effect": ownership,
        "public_availability_date": row.filed_at, "period_end": row.period_end,
        "source_filing_provenance": _safe(row.provenance, 80), "staleness_or_withholding": None,
        "dilution_percentile": float(row.dilution_percentile),
        "frozen_score_contribution": .1 * float(row.dilution_percentile), "weight": .10},
      "financial_context": _fundamental_context(facts), "recent_official_filings_events": events,
      "risks_and_warnings": risk_flags, "missing_information": missing,
      "model_status": {"validated": False, "recommendation": False, "price_weight": .90,
        "dilution_weight": .10, "failed_fundamentals_composite_used": False,
        "configuration_matches_frozen_strategy": bool(CONFIGURATION_HASH)},
      "source_citations": {"price": "registered model-ready price observations",
        "dilution": _safe(row.provenance, 80), "event_accessions": [e["citation"] for e in events]},
      "bounds": {"event_limit": max_events, "text_character_limit": MAX_TEXT}}
    if before != (fingerprint(research_db), fingerprint(production_db)):
        raise ReadinessError("database changed during read-only brief")
    result["database_immutability"] = {"verified": True}
    return result


def prospective_selection_briefs(*, research_db: Path, production_db: Path,
                                  decision_at: datetime, now: datetime | None = None) -> dict[str, Any]:
    """Read at most three already-registered selections; never create a vintage."""
    now = now or datetime.now(timezone.utc); decision_at = _utc(decision_at, now)
    before = (fingerprint(research_db), fingerprint(production_db)); symbols: list[str] = []
    if decision_at.date() >= date(2026, 10, 31):
        with duckdb.connect(str(research_db), read_only=True) as db:
            if "prospective_us_shadow_vintages" in _tables(db):
                raw = db.execute("SELECT selections_json FROM prospective_us_shadow_vintages WHERE decision_at<=? ORDER BY decision_at DESC LIMIT 1", [decision_at]).fetchone()
                if raw: symbols = [str(x["qualified_symbol"]) for x in json.loads(raw[0])[:3]]
    briefs = [company_research_brief(research_db=research_db, production_db=production_db,
              qualified_symbol=s, decision_at=decision_at, now=now) for s in symbols]
    if before != (fingerprint(research_db), fingerprint(production_db)): raise ReadinessError("database changed during read-only briefs")
    return {"notice": NOTICE, "decision_at": decision_at, "paper_selection_count": len(briefs),
        "briefs": briefs, "reason": None if briefs else "no paper selection exists yet",
        "first_permissible_vintage": SPECIFICATION["first_permissible_vintage"]}
