"""Versioned, lossless validation of persisted liquidity measurements.

SEC Company Facts identifies monetary denomination with the unit key (for
example ``USD``).  ``sec_facts.currency`` is a local, redundant projection and
older rows legitimately leave it null.  This module is the single place where
that persisted representation is interpreted.
"""
from __future__ import annotations

from datetime import date, datetime
import hashlib
import json
import math
from typing import Any, TypedDict

from .financial_strength import _aware

VALIDATOR_VERSION = "liquidity-measurement-validator-1.0.0"
STALE_DAYS = 550

CONCEPT_FIELDS = {
    "AssetsCurrent": "current_assets",
    "LiabilitiesCurrent": "current_liabilities",
    "CashAndCashEquivalentsAtCarryingValue": "unrestricted_cash",
    "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents": "cash_plus_restricted_cash",
    "RestrictedCashAndCashEquivalentsCurrent": "restricted_cash",
    "RestrictedCashAndCashEquivalents": "restricted_cash",
    "ShortTermInvestments": "short_term_investments",
    "MarketableSecuritiesCurrent": "marketable_securities",
    "InventoryNet": "inventory",
    "AccountsReceivableNetCurrent": "receivables",
    "AccountsPayableCurrent": "accounts_payable",
    "Assets": "assets",
    "Liabilities": "liabilities",
}

class ValidationResult(TypedDict):
    accepted: bool
    canonical_field: str | None
    source_taxonomy: str
    source_concept: str
    source_unit: str | None
    canonical_unit: str | None
    source_currency: str | None
    canonical_currency: str | None
    scale: int | float | None
    applied_scale_factor: int
    measurement_nature: str
    period_compatible: bool
    point_in_time_visible: bool
    stale: bool
    reason_code: str
    validator_version: str
    lossless_normalization_provenance: dict[str, Any]
    evidence_identity: str

def _concept(row: dict[str, Any]) -> str:
    return str(row.get("concept") or row.get("original_concept_or_field") or "")

def source_scale(row: dict[str, Any]) -> Any:
    if "scale" in row and row.get("scale") is not None:
        return row.get("scale")
    provenance=row.get("provenance")
    if isinstance(provenance,str):
        try: provenance=json.loads(provenance)
        except (TypeError,ValueError): provenance={}
    if isinstance(provenance,dict):
        return provenance.get("scale",provenance.get("scale_factor"))
    return None

def evidence_identity(row: dict[str, Any]) -> str:
    """Identity is independent of consumer and database-copy location."""
    supplied=row.get("fact_key") or row.get("evidence_key")
    if supplied: return str(supplied)
    values=(row.get("security_id"),row.get("cik"),row.get("taxonomy"),_concept(row),
        row.get("period_start"),row.get("period_end") or row.get("instant_date"),
        row.get("accession_number") or row.get("accession_or_source_identifier"),
        row.get("unit"),row.get("currency"),source_scale(row),row.get("value"))
    return hashlib.sha256(json.dumps(values,default=str,separators=(",", ":")).encode()).hexdigest()

def validate_measurement(row: dict[str, Any], decision_at: datetime) -> ValidationResult:
    decision=_aware(decision_at)
    if decision is None: raise ValueError("timezone-aware decision timestamp required")
    concept=_concept(row); field=CONCEPT_FIELDS.get(concept)
    taxonomy=str(row.get("taxonomy") or "")
    unit=row.get("unit"); currency=row.get("currency")
    normalized_currency="USD" if str(unit)=="USD" and (currency is None or str(currency).strip() in {"","USD"}) else None
    nature="duration" if row.get("period_start") is not None else "instant"
    public,retrieved,available=(_aware(row.get(k)) for k in ("public_at","retrieved_at","available_at"))
    derived_available=max(public,retrieved) if public and retrieved else None
    visible=bool(derived_available and derived_available<=decision and
        (row.get("available_at") is None or (available==derived_available and available<=decision)))
    end=row.get("period_end") or row.get("instant_date")
    stale=not isinstance(end,date) or (decision.date()-end).days>STALE_DAYS
    scale=source_scale(row)
    scale_supported=scale is None or (isinstance(scale,(int,float)) and math.isfinite(float(scale)) and float(scale) in (0.0,1.0))
    value=row.get("value")
    finite=isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(float(value))
    reason="accepted"
    if field is None: reason="concept_not_contractual"
    elif not taxonomy.lower().startswith("us-gaap"): reason="taxonomy_not_supported"
    elif not public or not retrieved: reason="visibility_timestamp_missing"
    elif not visible: reason="not_visible_at_decision"
    elif nature!="instant": reason="measurement_nature_duration"
    elif str(unit)!="USD": reason="source_unit_not_usd"
    elif currency is not None and str(currency).strip() not in {"","USD"}: reason="unit_currency_contradiction"
    elif normalized_currency is None: reason="currency_ambiguous"
    elif not scale_supported: reason="scale_not_lossless"
    elif not finite: reason="value_nonfinite_or_invalid"
    elif float(value)<0: reason="value_negative"
    elif stale: reason="period_stale"
    accepted=reason=="accepted"
    return {"accepted":accepted,"canonical_field":field,"source_taxonomy":taxonomy,
      "source_concept":concept,"source_unit":unit,"canonical_unit":"USD" if accepted else None,
      "source_currency":currency,"canonical_currency":"USD" if accepted else None,
      "scale":scale,"applied_scale_factor":1,"measurement_nature":nature,
      "period_compatible":nature=="instant","point_in_time_visible":visible,"stale":stale,
      "reason_code":reason,"validator_version":VALIDATOR_VERSION,
      "lossless_normalization_provenance":{"original_value":value,"original_unit":unit,
        "original_currency":currency,"original_scale":scale,"normalized_value":value if accepted else None,
        "currency_rule":"sec_usd_unit_redundant_currency_null_blank_or_usd" if normalized_currency else None,
        "scale_rule":"absent_zero_or_one_is_identity" if scale_supported else None,
        "source_database":row.get("_source_database"),"source_table":row.get("_source_table"),
        "accession":row.get("accession_number") or row.get("accession_or_source_identifier"),
        "fiscal_year":row.get("fiscal_year"),"fiscal_period":row.get("fiscal_period"),
        "filed_at":str(row.get("filed_at") or row.get("filed_date") or "") or None,
        "public_at":str(row.get("public_at") or "") or None,
        "retrieved_at":str(row.get("retrieved_at") or "") or None,
        "available_at":str(row.get("available_at") or "") or None,
        "controlled_ingestion":{"operation_type":row.get("operation_type"),
          "operation_contract_version":row.get("operation_contract_version"),
          "concept_contract_hash":row.get("concept_contract_hash"),"run_id":row.get("ingestion_run_id"),
          "plan_id":row.get("ingestion_plan_id")}},
      "evidence_identity":evidence_identity(row)}
