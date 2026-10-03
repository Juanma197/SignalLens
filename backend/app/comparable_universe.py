"""Milestone 37 point-in-time comparable-universe research primitives.

This module is deliberately calculation-only.  It contains no ranking, scoring,
provider, or persistence path; callers must supply evidence already known at the
decision timestamp.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
import math
from typing import Any, Iterable

ORDINARY = "us_operating_company"
SECURITY_TYPES = frozenset({ORDINARY, "bank", "insurance", "reit", "bdc",
    "investment_fund", "spac", "spac_unit", "foreign_issuer_or_adr",
    "pre_revenue_development_stage", "other_special_structure",
    "classification_unavailable"})
EXCLUDED_TYPES = SECURITY_TYPES - {ORDINARY}

CLASSIFICATION_REASONS = {
    "bank":"financial_sector_separate_model_required",
    "insurance":"financial_sector_separate_model_required", "reit":"reit_model_required",
    "bdc":"bdc_model_required", "investment_fund":"fund_not_operating_company",
    "spac":"spac_not_operating_company", "spac_unit":"bundled_security_not_comparable",
    "foreign_issuer_or_adr":"international_evidence_not_assessed",
    "pre_revenue_development_stage":"development_stage_not_comparable",
    "other_special_structure":"special_structure_model_required",
    "classification_unavailable":"classification_evidence_unavailable"}

@dataclass(frozen=True)
class Classification:
    security_type: str
    included: bool
    reason_code: str | None
    evidence_source: str
    public_at: str
    retrieved_at: str
    available_at: str = ""
    confidence: str = "unavailable"

    @property
    def withholding_reason(self) -> str | None:
        return self.reason_code

def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timezone_aware_timestamp_required")
    return value.astimezone(timezone.utc)

def classify_security(evidence: Iterable[dict[str, Any]], decision_at: datetime,
                      *, security_id: str | None = None) -> Classification:
    """Read the canonical, current classification visible at ``decision_at``.

    Materialized rows take their availability and source names from the Milestone
    38 schema.  The older names remain accepted solely for pre-materialization
    in-memory evidence; they never override a materialized row.
    """
    decision = _aware(decision_at); valid=[]
    for item in evidence:
        kind=item.get("security_type")
        durable=item.get("durable_identifier")
        source=item.get("evidence_source_family") or item.get("source")
        if (kind not in SECURITY_TYPES or not durable or not source
                or (security_id is not None and str(item.get("security_id")) != security_id)
                or item.get("is_current", True) is not True):
            continue
        try:
            public=_aware(item["public_at"]); retrieved=_aware(item["retrieved_at"])
            available=_aware(item.get("available_at") or max(public,retrieved))
        except (KeyError, TypeError, ValueError): continue
        effective_from=item.get("effective_from"); effective_to=item.get("effective_to")
        try:
            if effective_from is not None and _aware(effective_from)>decision: continue
            if effective_to is not None and decision>=_aware(effective_to): continue
        except (TypeError,ValueError): continue
        if public <= decision and retrieved <= decision and available <= decision:
            valid.append((kind,item,public,retrieved,available))
    if any(x[1].get("conflict_details") not in (None,"",{},[]) for x in valid):
        return Classification("classification_unavailable",False,"classification_ambiguous",
                              "materialized_conflict","","","","unavailable")
    kinds={x[0] for x in valid}
    if len(kinds) != 1:
        reason="classification_ambiguous" if len(kinds)>1 else "classification_evidence_unavailable"
        return Classification("classification_unavailable",False,reason,"none","","","","unavailable")
    kind,item,public,retrieved,available=max(
        valid,key=lambda x:(x[4],x[3],x[2],str(x[1].get("evidence_key") or "")))
    stored_reason=item.get("classification_reason")
    reason=None if kind==ORDINARY else (stored_reason or CLASSIFICATION_REASONS[kind])
    return Classification(kind,kind==ORDINARY,reason,str(item.get("evidence_source_family") or item.get("source")),
        public.isoformat(),retrieved.isoformat(),available.isoformat(),
        str(item.get("confidence_category") or item.get("confidence") or "legacy"))

def repair_issuer_mapping(*, security_id: str, decision_at: datetime,
                          listing_identities: Iterable[dict[str,Any]],
                          stored_mappings: Iterable[dict[str,Any]],
                          retrieval_status: str="available") -> dict[str,Any]:
    """Produce a read-only mapping plan, protecting effective-dated ticker reuse."""
    decision=_aware(decision_at)
    identities=[x for x in listing_identities if x.get("security_id")==security_id
        and _aware(x["effective_from"])<=decision
        and (x.get("effective_to") is None or decision<_aware(x["effective_to"]))]
    if len(identities)>1: status="ambiguous_mapping"
    elif not identities: status="mapping_evidence_unavailable"
    else:
        identity=identities[0]; expected=str(identity.get("cik") or "")
        candidates=[m for m in stored_mappings if m.get("security_id")==security_id
            and _aware(m["known_at"])<=decision]
        ciks={str(m.get("cik")) for m in candidates if m.get("cik")}
        if len(ciks)>1: status="ambiguous_mapping"
        elif ciks=={expected} and expected: status="mapped"
        elif ciks and expected and expected not in ciks: status="stale_ticker_mapping"
        elif expected: status="mapping_available_not_persisted"
        elif retrieval_status=="failed": status="provider_or_ingestion_failure"
        else: status="genuinely_unmapped"
    return {"security_id":security_id,"status":status,"read_only":True,
        "mapping_mutated":False,"estimated_provider_requests":0 if status in {"mapped","mapping_available_not_persisted"} else 1}

@dataclass(frozen=True)
class AliasContract:
    canonical_meaning: str; concept: str; unit: str; period_nature: str
    sign: str; period_construction: str; currency_required: bool
    aggregation_allowed: bool; double_count_group: str | None
    sector_limitations: tuple[str,...]; known_failure_modes: tuple[str,...]

def _contract(meaning, concept, nature="instant", sign="positive", aggregate=False, group=None):
    return AliasContract(meaning,concept,"monetary",nature,sign,
        "point_in_time" if nature=="instant" else "non_overlapping_ttm",True,aggregate,group,
        (ORDINARY,), ("extension_semantics","unit_or_currency_mismatch","overlapping_periods"))

ALIAS_CONTRACTS={
 "cash":(_contract("cash and cash equivalents","CashAndCashEquivalentsAtCarryingValue"),
         _contract("cash including restricted cash","CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents")),
 "short_term_investments":(_contract("separately identified short-term investments","ShortTermInvestments"),),
 "current_debt":(_contract("current debt","LongTermDebtCurrent",group="current_debt"),
    _contract("current debt","ShortTermBorrowings",group="current_debt"),
    _contract("current debt","ShortTermDebtCurrent",group="current_debt"),
    _contract("commercial paper explicitly included in current debt","CommercialPaper",group="current_debt")),
 "non_current_debt":(_contract("non-current debt","LongTermDebtNoncurrent",group="non_current_debt"),
    _contract("non-current borrowings","LongTermBorrowings",group="non_current_debt")),
 "interest_expense":(_contract("interest expense","InterestExpenseNonOperating","duration","expense"),
    _contract("interest and debt expense","InterestAndDebtExpense","duration","expense")),
}

def accept_alias(field: str, fact: dict[str,Any]) -> bool:
    return any(fact.get("concept")==c.concept and fact.get("unit_kind")==c.unit
        and fact.get("period_nature")==c.period_nature and fact.get("sign")==c.sign
        and (not c.currency_required or bool(fact.get("currency"))) for c in ALIAS_CONTRACTS.get(field,()))

def _withheld(reason): return {"status":"withheld","reason_code":reason}
def _ratio(numerator, denominator):
    if not all(isinstance(x,(int,float)) and math.isfinite(x) for x in (numerator,denominator)):
        return _withheld("nonfinite_value")
    if denominator <= 1e-9: return _withheld("unreliable_denominator")
    return {"status":"available","value":numerator/denominator}

def earnings_yield(net_income, market_cap, *, currency_compatible=True):
    if not currency_compatible: return _withheld("currency_mismatch")
    result=_ratio(net_income,market_cap)
    if result["status"]=="available": result["negative_earnings"]=net_income<0
    return result

def fcf_yield(operating_cash_flow, capital_expenditure, market_cap, *, capex_sign="positive_outflow", periods_compatible=True):
    if not periods_compatible: return _withheld("incompatible_periods")
    if capex_sign not in {"positive_outflow","negative_cash_flow"}: return _withheld("incompatible_sign_convention")
    if not all(isinstance(x,(int,float)) and math.isfinite(x) for x in (operating_cash_flow,capital_expenditure)):
        return _withheld("nonfinite_value")
    fcf=operating_cash_flow-capital_expenditure if capex_sign=="positive_outflow" else operating_cash_flow+capital_expenditure
    result=_ratio(fcf,market_cap)
    if result["status"]=="available": result.update({"free_cash_flow":fcf,"negative_fcf":fcf<0})
    return result

def book_to_market(equity, market_cap, *, security_type=ORDINARY, reliable=True):
    if security_type != ORDINARY: return _withheld("security_type_not_comparable")
    if not reliable or not isinstance(equity,(int,float)) or equity<=0: return _withheld("negative_or_unreliable_equity")
    return _ratio(equity,market_cap)

def construct_enterprise_value(market_cap, debt_components, cash, *, currency_compatible=True, confirmed_debt_free=False):
    if not currency_compatible: return _withheld("currency_mismatch")
    components=list(debt_components or [])
    if not components and not confirmed_debt_free: return _withheld("debt_evidence_unavailable")
    groups=[x.get("double_count_group") for x in components]
    if None in groups or len(groups)!=len(set(groups)): return _withheld("debt_component_double_count")
    if cash is None: return _withheld("cash_evidence_unavailable")
    values=[x.get("value") for x in components]
    if not all(isinstance(x,(int,float)) and math.isfinite(x) and x>=0 for x in values+[market_cap,cash]):
        return _withheld("nonfinite_value")
    return {"status":"available","value":market_cap+sum(values)-cash,"confirmed_debt_free":confirmed_debt_free}

def financial_strength(*, current_debt=None, non_current_debt=None, confirmed_debt_free=False,
                       assets=None, equity=None, cash=None, operating_income=None,
                       interest_expense=None, security_type=ORDINARY):
    if security_type in {"bank","insurance"}: return {"status":"withheld","reason_code":"financial_sector_not_comparable"}
    if security_type != ORDINARY: return {"status":"withheld","reason_code":CLASSIFICATION_REASONS.get(security_type,"security_type_not_comparable")}
    if current_debt is None or non_current_debt is None:
        if not confirmed_debt_free: return {"status":"partial","reason_code":"debt_evidence_unavailable","debt_free_status":"unknown"}
        debt=0
    else: debt=current_debt+non_current_debt
    result={"status":"available","debt_free_status":"confirmed" if confirmed_debt_free and debt==0 else "not_debt_free",
      "debt_to_assets":_ratio(debt,assets),
      "debt_to_equity":_ratio(debt,equity) if isinstance(equity,(int,float)) and equity>0 else _withheld("negative_or_unreliable_equity"),
      "net_debt":debt-cash if isinstance(cash,(int,float)) and math.isfinite(cash) else _withheld("cash_evidence_unavailable")}
    result["interest_coverage"]=_ratio(operating_income,interest_expense) if isinstance(interest_expense,(int,float)) and interest_expense>0 else _withheld("interest_expense_unavailable_or_inapplicable")
    return result

def corporate_action_coverage(*, assessed: bool, valid_at_decision: bool, events: list[dict[str,Any]], unresolved=False):
    if not assessed or not valid_at_decision: return {"state":"missing_coverage","passes":False}
    if unresolved: return {"state":"unresolved_corporate_action","passes":False}
    if events: return {"state":"stored_corporate_action","passes":True,"event_count":len(events)}
    return {"state":"verified_no_corporate_action","passes":True,"event_count":0}

def aggregate_repair_plan(items: list[dict[str,Any]], limit=100) -> dict[str,Any]:
    limit=max(0,min(int(limit),100)); shown=items[:limit]
    count=lambda key:dict(sorted(Counter(str(x.get(key,"unknown")) for x in items).items()))
    return {"total_work_item_count":len(items),"counts_by_disposition":count("disposition"),
      "counts_by_field":count("field"),"counts_by_reason_code":count("reason_code"),
      "counts_by_security_type":count("security_type"),
      "total_estimated_requests":sum(int(x.get("estimated_provider_requests",0)) for x in items),
      "displayed_work_item_count":len(shown),"work_item_limit":limit,
      "truncated":len(items)>len(shown),"work_items":shown}
