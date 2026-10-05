"""Milestone 36: read-only evidence coverage and Track B research foundation.

This module intentionally has no persistence, ranking, selection, or publication
entry point.  Missing evidence is represented by a reason code, never a value.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
from typing import Any

import duckdb

from .active_catalogue import select_active_catalogue
from .model_readiness import fingerprint
from .sec_ingestion import validate_paths
from .canonical_units import EPS_UNIT_RULE_ID, EPS_UNIT_RULE_VERSION, normalize_unit
from .prospective_us_shadow import CONFIGURATION_HASH, REGISTRATION_AT, STRATEGY_VERSION
from .comparable_universe import (ORDINARY, Classification, aggregate_repair_plan,
    book_to_market, classify_security, construct_enterprise_value, earnings_yield,
    fcf_yield, financial_strength)

MAX_SAMPLES = 10
TRACK_B_LABELS = ["TRACK B RESEARCH FOUNDATION — NOT A MODEL",
                  "NO PURCHASE RECOMMENDATION", "NO CANDIDATES GENERATED",
                  "ZERO VALIDATION CREDIT"]

REASON_CODES = frozenset({
    "evidence_unavailable", "stale_evidence", "issuer_unmapped", "issuer_mapping_ambiguous",
    "unsupported_taxonomy", "incompatible_units", "currency_mismatch", "nonfinite_value",
    "missing_public_availability_timestamp", "evidence_retrieved_after_decision",
    "insufficient_history", "unreliable_denominator", "no_model_ready_price",
    "provider_or_ingestion_failure", "missing_liquidity", "unresolved_corporate_action",
    "incomplete_horizon", "security_delisted", "financial_sector_not_comparable",
    "current_membership_survivorship_limitation", "stale_filing", "extreme_dilution",
    "going_concern_filing_risk", "excessive_estimated_trading_cost",
})

FIELDS = (
 "identity", "active_catalogue_membership", "model_ready_price_history", "decision_price",
 "historical_total_return_inputs", "corporate_actions_and_dividends", "sec_issuer_mapping",
 "sec_ingestion_checkpoint", "diluted_shares", "revenue", "operating_income", "net_income",
 "operating_cash_flow", "capital_expenditure", "free_cash_flow", "assets",
 "shareholders_equity", "current_debt", "non_current_debt", "interest_expense", "basic_eps",
 "diluted_eps", "market_capitalisation_inputs", "enterprise_value_inputs",
 "trading_liquidity_inputs", "official_sec_event_context")

FAMILIES = {
 "value": ("decision_price", "market_capitalisation_inputs", "enterprise_value_inputs", "free_cash_flow"),
 "business_quality": ("revenue", "operating_income", "net_income", "operating_cash_flow", "assets"),
 "financial_strength": ("assets", "shareholders_equity", "current_debt", "non_current_debt", "interest_expense"),
 "growth": ("revenue", "operating_income", "basic_eps", "diluted_eps"),
 "shareholder_treatment": ("diluted_shares", "corporate_actions_and_dividends"),
 "price_and_risk": ("model_ready_price_history", "historical_total_return_inputs", "trading_liquidity_inputs"),
}
FAMILY_OPTIONAL = {
 "value": ("net_income", "shareholders_equity"),
 "business_quality": (), "financial_strength": ("cash",),
 "growth": (), "shareholder_treatment": (), "price_and_risk": (),
}

def family_readiness(field_states: dict[str,str], family: str) -> dict[str,Any]:
    """The single family-readiness definition used by aggregates and previews."""
    required=FAMILIES[family]; optional=FAMILY_OPTIONAL[family]
    available=lambda field: field_states.get(field)=="available"
    any_input=any(available(x) for x in required+optional)
    if family=="value":
        minimum=available("decision_price") and available("market_capitalisation_inputs") and any(available(x) for x in ("free_cash_flow","net_income","shareholders_equity"))
    elif family=="financial_strength":
        minimum=available("assets") and (available("shareholders_equity") or (available("current_debt") and available("non_current_debt")))
    elif family=="shareholder_treatment": minimum=available("diluted_shares")
    else: minimum=any_input
    missing=[x for x in required if not available(x)]
    reasons=sorted({field_states.get(x,"evidence_unavailable") for x in missing})
    return {"any_input_available":any_input,"minimum_calculable":minimum,
      "full_family_ready":not missing,"required_factors":list(required),
      "optional_factors":list(optional),"missing_required_inputs":missing,
      "withholding_reasons":reasons}

def _family_aggregate(evidence: dict[str,dict[str,str]], symbols) -> dict[str,Any]:
    result={}
    for family in FAMILIES:
        rows=[family_readiness(evidence.get(s,{}),family) for s in symbols]
        missing=Counter(x for row in rows for x in row["missing_required_inputs"])
        reasons=Counter(x for row in rows for x in row["withholding_reasons"])
        result[family]={"any_input_available":sum(x["any_input_available"] for x in rows),
          "minimum_calculable":sum(x["minimum_calculable"] for x in rows),
          "full_family_ready":sum(x["full_family_ready"] for x in rows),"total":len(rows),
          "required_factors":list(FAMILIES[family]),"optional_factors":list(FAMILY_OPTIONAL[family]),
          "withholding_reasons":dict(sorted(reasons.items())),
          "missing_required_inputs":dict(sorted(missing.items()))}
    return result

def _json_dict(value: Any) -> dict[str,Any]:
    if isinstance(value,str):
        try: value=json.loads(value)
        except (TypeError,ValueError): return {}
    return dict(value) if isinstance(value,dict) else {}

def _canonical_repair_visible(row: dict[str,Any], decision: datetime,
                              completed_repairs: list[datetime]) -> bool:
    provenance=_json_dict(row.get("provenance")); lineage=_json_dict(row.get("lineage"))
    if lineage.get("revision_type")!="canonical_unit_normalization": return True
    normalization=_json_dict(provenance.get("unit_normalization"))
    exact=normalize_unit(canonical_field=str(row.get("canonical_field") or ""),
      source_unit=str(normalization.get("source_unit") or ""),
      concept=str(row.get("original_concept_or_field") or ""),currency=row.get("currency"),
      scale_factor=normalization.get("scale_factor"),
      period_nature="duration" if row.get("period_start") is not None else "instant")
    if not (lineage.get("rule_version")==EPS_UNIT_RULE_VERSION
            and row.get("unit")=="USD/share" and exact is not None
            and normalization.get("normalization_rule_identifier")==EPS_UNIT_RULE_ID
            and normalization.get("rule_version")==EPS_UNIT_RULE_VERSION): return False
    stamp=lineage.get("repair_decision_at") or provenance.get("repair_decision_at")
    if stamp:
        if isinstance(stamp,str): stamp=datetime.fromisoformat(stamp.replace("Z","+00:00"))
        return _aware_for_preview(stamp)<=decision
    return any(stamp<=decision for stamp in completed_repairs)

def _visible_canonical_rows(db, decision: datetime) -> list[dict[str,Any]]:
    """Return usable point-in-time rows, with an exact repair winning its source."""
    completed=[]
    if "canonical_unit_repair_runs" in _tables(db):
        completed=[_aware_for_preview(x[0]) for x in db.execute(
          "SELECT decision_at FROM canonical_unit_repair_runs WHERE status='completed' AND rule_version=?",
          [EPS_UNIT_RULE_VERSION]).fetchall()]
    canonical=_rows(db,"canonical_factor_evidence")
    from .liquidity_materialization import CONTRACT_HASH
    from .liquidity_resolution import resolve_canonical_liquidity
    effective,_resolution=resolve_canonical_liquidity(raw_rows=_rows(db,"sec_facts"),
      canonical_rows=canonical,revision_rows=_rows(db,"liquidity_canonical_materialization_revisions"),
      run_rows=_rows(db,"liquidity_canonical_materialization_runs"),decision_at=decision,
      contract_hash=CONTRACT_HASH)
    controlled={str(r.get("evidence_key")) for r in canonical
      if _json_dict(r.get("provenance")).get("operation_type")=="liquidity_canonical_materialization"}
    canonical=[r for r in canonical if str(r.get("evidence_key")) not in controlled]
    canonical.extend(r for r in effective if r.get("_canonical_revision"))
    rows=[r for r in canonical
          if r.get("reliability_state")=="usable" and _visible(r,decision) is None
          and r.get("available_at") is not None
          and _aware_for_preview(r["available_at"])<=decision
          and _canonical_repair_visible(r,decision,completed)]
    # Revisions sort ahead of source rows at equal source timestamps. This avoids
    # dependence on DuckDB row order or JSON serialization.
    def revision_rank(row):
        lineage=_json_dict(row.get("lineage"))
        return (row.get("available_at"),row.get("retrieved_at"),
          lineage.get("revision_type")=="canonical_unit_normalization",row.get("evidence_key"))
    return sorted(rows,key=revision_rank)

# Aliases are admitted only with an auditable, exact semantic contract.  They do
# not mean that a fact is repaired; point-in-time and issuer checks still apply.
CONCEPT_ALIASES = {
 "revenue": ({"concept":"RevenueFromContractWithCustomerExcludingAssessedTax", "unit":"USD",
              "duration":"duration", "sign":"credit", "meaning":"revenue"},
             {"concept":"Revenues", "unit":"USD", "duration":"duration", "sign":"credit", "meaning":"revenue"}),
 "operating_income": ({"concept":"OperatingIncomeLoss", "unit":"USD", "duration":"duration", "sign":"signed", "meaning":"operating income"},),
 "net_income": ({"concept":"NetIncomeLoss", "unit":"USD", "duration":"duration", "sign":"signed", "meaning":"net income attributable under US GAAP"},),
 "operating_cash_flow": ({"concept":"NetCashProvidedByUsedInOperatingActivities", "unit":"USD", "duration":"duration", "sign":"signed", "meaning":"operating cash flow"},),
 "capital_expenditure": ({"concept":"PaymentsToAcquirePropertyPlantAndEquipment", "unit":"USD", "duration":"duration", "sign":"debit", "meaning":"capital expenditure"},),
 "assets": ({"concept":"Assets", "unit":"USD", "duration":"instant", "sign":"debit", "meaning":"total assets"},),
 "shareholders_equity": ({"concept":"StockholdersEquity", "unit":"USD", "duration":"instant", "sign":"signed", "meaning":"stockholders equity"},),
 "current_debt": ({"concept":"ShortTermBorrowings", "unit":"USD", "duration":"instant", "sign":"credit", "meaning":"current debt"},),
 "non_current_debt": ({"concept":"LongTermDebtNoncurrent", "unit":"USD", "duration":"instant", "sign":"credit", "meaning":"non-current debt"},),
 "interest_expense": ({"concept":"InterestExpenseNonOperating", "unit":"USD", "duration":"duration", "sign":"debit", "meaning":"non-operating interest expense"},),
 "basic_eps": ({"concept":"EarningsPerShareBasic", "unit":"USD/shares", "duration":"duration", "sign":"signed", "meaning":"basic EPS"},),
 "diluted_eps": ({"concept":"EarningsPerShareDiluted", "unit":"USD/shares", "duration":"duration", "sign":"signed", "meaning":"diluted EPS"},),
 "diluted_shares": ({"concept":"WeightedAverageNumberOfDilutedSharesOutstanding", "unit":"shares", "duration":"duration", "sign":"debit", "meaning":"weighted-average diluted shares"},),
}

class InvestmentResearchError(RuntimeError):
    reason_code = "INVESTMENT_RESEARCH_NOT_READY"

def public_error_code(exc: Exception) -> str:
    # Domain readers may expose a stable, non-sensitive code without making
    # their bounded internal diagnostics part of the public CLI contract.
    code=getattr(exc,"reason_code",None)
    return str(code) if code else "INVESTMENT_RESEARCH_INTERNAL_ERROR"

def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise InvestmentResearchError("timezone-aware decision timestamp required")
    return value.astimezone(timezone.utc)

def _aware_for_preview(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)

def _json_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

def validate_alias(field: str, fact: dict[str, Any]) -> bool:
    """Require concept, unit, duration, sign, and meaning to match exactly."""
    return any(all(fact.get(k) == alias[k] for k in ("concept","unit","duration","sign","meaning"))
               for alias in CONCEPT_ALIASES.get(field, ()))

def _tables(db: duckdb.DuckDBPyConnection) -> set[str]:
    return {str(x[0]) for x in db.execute("SHOW TABLES").fetchall()}

def _rows(db: duckdb.DuckDBPyConnection, table: str) -> list[dict[str, Any]]:
    if table not in _tables(db): return []
    cur = db.execute(f'SELECT * FROM "{table}"')
    names = [d[0] for d in cur.description]
    return [dict(zip(names, row)) for row in cur.fetchall()]

def _visible(row: dict[str, Any], decision: datetime) -> str | None:
    public = row.get("public_at")
    retrieved = row.get("retrieved_at")
    if public is None: return "missing_public_availability_timestamp"
    for value in (public, retrieved):
        if value is not None:
            aware = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
            if aware.astimezone(timezone.utc) > decision: return "evidence_retrieved_after_decision"
    return None

def _immutable(research: Path, production: Path, operation):
    validate_paths(research, production)
    before = {"research": asdict(fingerprint(research)), "production": asdict(fingerprint(production))}
    with duckdb.connect(str(production), read_only=True) as db: db.execute("SELECT 1")
    with duckdb.connect(str(research), read_only=True) as db: result = operation(db)
    after = {"research": asdict(fingerprint(research)), "production": asdict(fingerprint(production))}
    if before != after: raise InvestmentResearchError("database changed during read-only operation")
    result["database_fingerprints"] = {k:{"before":before[k], "after":after[k], "unchanged":True} for k in before}
    return result

def _classification_payload(value: Classification) -> dict[str, Any]:
    payload=asdict(value)
    payload.update({"inclusion_status":"included" if value.included else "withheld",
                    "withholding_reason":value.reason_code})
    return payload

def _classification_for(db, security_id: str, decision: datetime,
                        legacy_rows: list[dict[str,Any]] | None=None) -> Classification:
    """Apply one classification precedence rule to every Track B reader.

    Once the materialized table exists it is authoritative, including an empty
    or withheld result.  Before Milestone 38 only an explicit legacy
    ``security_type`` or catalogue instrument type is defensible; names and
    ticker shapes are deliberately ignored.
    """
    tables=_tables(db)
    if "security_classification_evidence" in tables:
        rows=[r for r in _rows(db,"security_classification_evidence")
              if str(r.get("security_id"))==security_id]
        return classify_security(rows,decision,security_id=security_id)
    rows=list(legacy_rows or [])
    mapping={"common_stock":ORDINARY,"common stock":ORDINARY,"bank":"bank",
             "insurance":"insurance","reit":"reit","bdc":"bdc",
             "fund":"investment_fund","etf":"investment_fund","spac":"spac",
             "spac_unit":"spac_unit","spac unit":"spac_unit","adr":"foreign_issuer_or_adr"}
    for item in _rows(db,"security_listings"):
        if str(item.get("security_id"))!=security_id: continue
        kind=mapping.get(str(item.get("instrument_type") or "").lower())
        if kind:
            stamp=item.get("retrieved_at") or decision
            rows.append({"security_id":security_id,"security_type":kind,
              "durable_identifier":security_id,"source":"legacy_catalogue_instrument_type",
              "public_at":stamp,"retrieved_at":stamp,"available_at":stamp})
    return classify_security(rows,decision,security_id=security_id)

def coverage_audit(*, research_db: Path, production_db: Path, decision_at: datetime,
                   max_samples: int = MAX_SAMPLES) -> dict[str, Any]:
    decision = _utc(decision_at); max_samples = max(0, min(MAX_SAMPLES, int(max_samples)))
    def audit(db):
        active = select_active_catalogue(db, as_of=decision.replace(tzinfo=None))
        if active is None: raise InvestmentResearchError("catalogue unavailable")
        securities = active.listings
        securities = securities.loc[securities.region.eq("US") & securities.eligible].sort_values("qualified_symbol")
        classifications={str(sec.qualified_symbol):_classification_for(db,str(sec.security_id),decision)
                         for sec in securities.itertuples(index=False)}
        securities=securities.loc[securities.qualified_symbol.map(lambda s: classifications[str(s)].included)]
        prices, facts = _rows(db,"global_price_observations"), _rows(db,"sec_facts")
        canonical=_visible_canonical_rows(db,decision)
        actions, issuers = _rows(db,"global_corporate_actions"), _rows(db,"sec_issuers")
        checkpoints, filings = _rows(db,"sec_checkpoints"), _rows(db,"sec_filings")
        events = _rows(db,"sec_events")
        by_symbol={}; affected={f:[] for f in FIELDS}; counts={f:0 for f in FIELDS}; year=Counter()
        eps_diagnostics=Counter()
        for sec in securities.itertuples(index=False):
            sid, sym = str(sec.security_id), str(sec.qualified_symbol)
            status={f:"evidence_unavailable" for f in FIELDS}; status["identity"]=None; status["active_catalogue_membership"]=None
            sp=[p for p in prices if str(p.get("qualified_symbol"))==sym and p.get("status")=="available"]
            sp=[p for p in sp if p.get("retrieved_at") is not None and (p["retrieved_at"].replace(tzinfo=timezone.utc) if p["retrieved_at"].tzinfo is None else p["retrieved_at"]) <= decision]
            sp.sort(key=lambda p:(p.get("trading_date") or date.min,p.get("retrieved_at")))
            if sp:
                last=sp[-1]; age=(decision.date()-last["trading_date"]).days
                status["decision_price"] = None if age <= 7 and last.get("adjusted_close") is not None else ("stale_evidence" if age>7 else "nonfinite_value")
                status["model_ready_price_history"] = None if len(sp)>=252 else "insufficient_history"
                status["historical_total_return_inputs"] = None if len(sp)>=253 else "insufficient_history"
                status["trading_liquidity_inputs"] = None if len(sp)>=20 and all(p.get("volume") is not None for p in sp[-20:]) else "missing_liquidity"
            else:
                status["decision_price"]=status["model_ready_price_history"]="no_model_ready_price"
                status["historical_total_return_inputs"]="insufficient_history"; status["trading_liquidity_inputs"]="missing_liquidity"
            sa=[a for a in actions if str(a.get("qualified_symbol"))==sym]
            status["corporate_actions_and_dividends"] = None if sa else "evidence_unavailable"
            maps=[i for i in issuers if str(i.get("security_id"))==sid and i.get("mapped_at") is not None]
            status["sec_issuer_mapping"] = "issuer_unmapped" if not maps else ("issuer_mapping_ambiguous" if len({m.get('cik') for m in maps})>1 else None)
            cp=[c for c in checkpoints if str(c.get("security_id"))==sid]
            status["sec_ingestion_checkpoint"] = None if cp and cp[-1].get("status")=="completed" else "provider_or_ingestion_failure"
            sf=[f for f in facts if str(f.get("security_id"))==sid]
            for field, aliases in CONCEPT_ALIASES.items():
                candidates=[f for f in sf if f.get("concept") in {a["concept"] for a in aliases}]
                reasons=[]
                for fact in candidates:
                    reason=_visible(fact,decision)
                    if reason: reasons.append(reason); continue
                    value=fact.get("value")
                    if not isinstance(value,(int,float)) or not math.isfinite(float(value)): reasons.append("nonfinite_value"); continue
                    allowed={a["unit"] for a in aliases if a["concept"]==fact.get("concept")}
                    normalized=normalize_unit(canonical_field=field,source_unit=str(fact.get("unit") or ""),
                      concept=str(fact.get("concept") or ""),currency=fact.get("currency"),scale_factor=1,
                      period_nature="duration" if fact.get("period_start") is not None else "instant")
                    if fact.get("unit") not in allowed and normalized is None: reasons.append("incompatible_units"); continue
                    status[field]=None; year[int(fact.get("fiscal_year") or fact.get("period_end").year)] += 1; break
                else:
                    status[field] = reasons[0] if reasons else ("unsupported_taxonomy" if sf else "evidence_unavailable")
            # Materialized canonical evidence has precedence over the legacy raw
            # fact audit.  It is still point-in-time filtered and only explicitly
            # usable rows can make a field available.
            cf=[f for f in canonical if str(f.get("security_id"))==sid
                and f.get("reliability_state")=="usable"]
            for field in {str(f.get("canonical_field")) for f in cf}:
                if field in status: status[field]=None
            repaired_eps=[f for f in cf if f.get("canonical_field") in {"basic_eps","diluted_eps"}
              and _json_dict(f.get("lineage")).get("revision_type")=="canonical_unit_normalization"]
            raw_usable_eps=any(status[x] is None for x in ("basic_eps","diluted_eps")) and not repaired_eps
            qualifying_source=any(f.get("concept") in {"EarningsPerShareBasic","EarningsPerShareDiluted"}
              and f.get("unit")=="USD/shares" for f in sf)
            other_withheld=any(f.get("canonical_field") in {"basic_eps","diluted_eps"}
              and f.get("reliability_state")=="withheld" and f.get("withholding_reason")!="incompatible_units"
              for f in _rows(db,"canonical_factor_evidence") if str(f.get("security_id"))==sid)
            if repaired_eps: eps_diagnostics["repaired_historical_observations_now_usable"]+=len(repaired_eps)
            if raw_usable_eps: eps_diagnostics["companies_eps_already_satisfied_before_repair"]+=1
            if not qualifying_source and not any(status[x] is None for x in ("basic_eps","diluted_eps")):
                eps_diagnostics["companies_missing_eps_no_qualifying_source"]+=1
            if other_withheld: eps_diagnostics["companies_eps_withheld_other_reason"]+=1
            status["free_cash_flow"] = None if status["operating_cash_flow"] is None and status["capital_expenditure"] is None else "evidence_unavailable"
            status["market_capitalisation_inputs"] = None if status["decision_price"] is None and status["diluted_shares"] is None else "unreliable_denominator"
            status["enterprise_value_inputs"] = None if status["market_capitalisation_inputs"] is None and status["current_debt"] is None and status["non_current_debt"] is None else "unreliable_denominator"
            status["official_sec_event_context"] = None if any(str(e.get("security_id"))==sid for e in events) or any(str(f.get("cik")) in {str(m.get("cik")) for m in maps} for f in filings) else "evidence_unavailable"
            by_symbol[sym]={f:("available" if r is None else r) for f,r in status.items()}
            for f,r in status.items():
                if r is None: counts[f]+=1
                elif len(affected[f])<max_samples: affected[f].append(sym)
        total=len(securities)
        field_coverage={f:{"available":counts[f],"total":total,"percent":round(100*counts[f]/total,2) if total else 0,
                           "affected_symbol_samples":affected[f]} for f in FIELDS}
        family=_family_aggregate(by_symbol,by_symbol)
        incompatible=Counter((str(x.get("canonical_field") or "unknown"),str(x.get("unit") or "missing"))
          for x in canonical if x.get("withholding_reason")=="incompatible_units")
        unit_audit={"total":sum(incompatible.values()),"by_canonical_field_and_source_unit":[
          {"canonical_field":field,"source_unit":unit,"count":count}
          for (field,unit),count in sorted(incompatible.items())[:100]],
          "combination_limit":100,"truncated":len(incompatible)>100,"unit_validation_changed":False}
        return {"command":"investment-grade-coverage-audit","decision_at":decision.isoformat(),
                "active_us_securities":len(classifications),"comparable_universe_count":total,
                "classification_counts":dict(sorted(Counter(x.security_type for x in classifications.values()).items())),
                "classification_samples":{s:_classification_payload(classifications[s]) for s in list(classifications)[:max_samples]},
                "incompatible_unit_audit":unit_audit,
                "canonical_eps_repair_diagnostics":{k:eps_diagnostics.get(k,0) for k in (
                  "repaired_historical_observations_now_usable","companies_eps_already_satisfied_before_repair",
                  "companies_missing_eps_no_qualifying_source","companies_eps_withheld_other_reason")},
                "field_coverage":field_coverage,"factor_family_coverage":family,
                "coverage_by_year":dict(sorted(year.items())),"security_evidence":by_symbol,
                "bounded_sample_limit":max_samples,"read_only":True}
    return _immutable(research_db,production_db,audit)

def repair_plan(**kwargs) -> dict[str, Any]:
    report=coverage_audit(**kwargs); work=[]
    routing={"issuer_unmapped":"review_required","issuer_mapping_ambiguous":"review_required",
             "provider_or_ingestion_failure":"automatic","insufficient_history":"automatic",
             "missing_liquidity":"automatic","unsupported_taxonomy":"review_required",
             "incompatible_units":"review_required","currency_mismatch":"review_required",
             "evidence_unavailable":"unavailable_with_current_evidence"}
    # Aggregate the complete defect population. Samples are bounded only after
    # every item has contributed to the counters.
    for symbol,evidence in report["security_evidence"].items():
        for field,reason in evidence.items():
            if reason == "available": continue
            work.append({"symbol":symbol,"field":field,"reason_code":reason,
                         "disposition":routing.get(reason,"review_required"),
                         "estimated_provider_requests":1 if routing.get(reason)=="automatic" else 0,
                         "security_type":"classification_unavailable","status":"unrepaired"})
    report.update({"command":"plan-investment-data-repair",**aggregate_repair_plan(work,100),
                   "estimates_are_planning_only":True,"ingestion_performed":False,"mappings_mutated":False})
    return report

def comparable_universe_readiness(*, research_db: Path, production_db: Path,
                                  decision_at: datetime, max_samples: int=MAX_SAMPLES) -> dict[str,Any]:
    decision=_utc(decision_at); max_samples=max(0,min(MAX_SAMPLES,int(max_samples)))
    def build(db):
        active=select_active_catalogue(db,as_of=decision.replace(tzinfo=None))
        if active is None: raise InvestmentResearchError("catalogue unavailable")
        securities=active.listings
        securities=securities.loc[securities.region.eq("US") & securities.eligible].sort_values("qualified_symbol")
        types=Counter(); exclusions:dict[str,list[str]]={}; included=[]
        classification_samples={}
        for sec in securities.itertuples(index=False):
            classification=_classification_for(db,str(sec.security_id),decision)
            if len(classification_samples)<max_samples:
                classification_samples[str(sec.qualified_symbol)]=_classification_payload(classification)
            types[classification.security_type]+=1
            if classification.included: included.append(str(sec.qualified_symbol))
            else: exclusions.setdefault(classification.reason_code or "excluded",[]).append(str(sec.qualified_symbol))
        audit=coverage_audit(research_db=research_db,production_db=production_db,decision_at=decision,max_samples=max_samples)
        comparable=set(included); evidence=audit["security_evidence"]
        fields={f:{"available":sum(evidence.get(s,{}).get(f)=="available" for s in comparable),"total":len(comparable)} for f in FIELDS}
        families=_family_aggregate(evidence,sorted(comparable))
        return {"command":"comparable-universe-research-readiness","decision_at":decision.isoformat(),
          "labels":TRACK_B_LABELS,"active_us_labelled_catalogue_count":len(securities),
          "counts_by_security_type":dict(sorted(types.items())),"ordinary_operating_company_universe_count":len(included),
          "classification_samples":classification_samples,
          "ordinary_company_symbol_samples":included[:max_samples],
          "exclusions_by_reason":{k:{"count":len(v),"symbol_samples":v[:max_samples]} for k,v in sorted(exclusions.items())},
          "mapping_readiness":fields["sec_issuer_mapping"],"field_coverage":fields,"factor_capability":families,
          "companies_with_any_value_input":families["value"]["any_input_available"],
          "companies_with_minimum_calculable_value":families["value"]["minimum_calculable"],
          "companies_with_any_financial_strength_input":families["financial_strength"]["any_input_available"],
          "at_least_four_valid_factor_families":sum(sum(all(evidence.get(s,{}).get(f)=="available" for f in req) for req in FAMILIES.values())>=4 for s in comparable),
          "remaining_blockers":sorted({r for s in comparable for r in evidence.get(s,{}).values() if r!="available"}),
          "current_membership_survivorship_warning":True,"read_only":True,
          "recommendations":[],"candidates":[],"rankings":[],"paper_selections":[],"prospective_vintages":[],"validation_observations":[],"validation_credit":0}
    return _immutable(research_db,production_db,build)

def company_factor_preview(*, research_db: Path, production_db: Path, decision_at: datetime,
                           qualified_symbol: str) -> dict[str,Any]:
    decision=_utc(decision_at)
    if "." not in qualified_symbol or len(qualified_symbol)>40: raise InvestmentResearchError("exchange-qualified symbol required")
    def build(db):
        rows=[r for r in _rows(db,"company_factor_evidence") if r.get("qualified_symbol")==qualified_symbol and _visible(r,decision) is None]
        # Milestone 38 stores atomic canonical evidence.  Build the preview on
        # read so no derived metric can predate its latest component.
        if not rows and "canonical_factor_evidence" in _tables(db):
            evidence=[r for r in _visible_canonical_rows(db,decision)
                      if r.get("qualified_symbol")==qualified_symbol]
            latest={}
            for item in evidence:
                field=item.get("canonical_field")
                if field not in latest or (item["available_at"],item.get("retrieved_at"),item["evidence_key"])>(latest[field]["available_at"],latest[field].get("retrieved_at"),latest[field]["evidence_key"]): latest[field]=item
            if latest:
                get=lambda name: latest.get(name,{}).get("value")
                classification=_classification_for(db,str(next(iter(latest.values()))["security_id"]),decision)
                current=get("current_debt"); noncurrent=get("non_current_debt")
                rows=[{"qualified_symbol":qualified_symbol,"security_type":classification.security_type,
                  "classification":asdict(classification),"market_capitalisation":get("market_capitalisation"),
                  "net_income_ttm":get("net_income"),"operating_cash_flow_ttm":get("operating_cash_flow"),
                  "capital_expenditure_ttm":get("capital_expenditure"),"shareholders_equity":get("shareholders_equity"),
                  "current_debt":current,"non_current_debt":noncurrent,"eligible_cash":get("cash"),
                  "assets":get("assets"),"operating_income_ttm":get("operating_income"),"interest_expense_ttm":get("interest_expense"),
                  "confirmed_debt_free":current==0 and noncurrent==0,"debt_components":[x for x in
                    ({"value":current,"double_count_group":"current_debt"},{"value":noncurrent,"double_count_group":"non_current_debt"}) if x["value"] is not None],
                  "currency_compatible":len({x.get("currency") for x in latest.values() if x.get("currency")})<=1,
                  "periods_compatible":True,"capex_sign":"positive_outflow","equity_reliable":True,
                  "public_at":max(x["public_at"] for x in latest.values()),"retrieved_at":max(x["retrieved_at"] for x in latest.values()),
                  "available_at":max(x["available_at"] for x in latest.values()),"source":"canonical_factor_evidence",
                  "canonical_inputs":{k:{"value":v.get("value"),"unit":v.get("unit"),"currency":v.get("currency"),"available_at":str(v.get("available_at")),"provenance":v.get("provenance")} for k,v in latest.items()},
                  "missing_evidence":[x for x in ("decision_price","diluted_shares","net_income","operating_cash_flow","capital_expenditure","shareholders_equity","assets","current_debt","non_current_debt","interest_expense") if x not in latest]}]
        if not rows: raise InvestmentResearchError("factor evidence unavailable")
        r=max(rows,key=lambda x:x.get("retrieved_at")); sid=str(r.get("security_id") or "")
        if not sid:
            listings=[x for x in _rows(db,"security_listings") if x.get("qualified_symbol")==qualified_symbol]
            sid=str(listings[0].get("security_id")) if len(listings)==1 else ""
        legacy=[]
        if r.get("security_type") in {ORDINARY,"bank","insurance","reit","bdc","investment_fund","spac","spac_unit","foreign_issuer_or_adr","pre_revenue_development_stage","other_special_structure","classification_unavailable"}:
            legacy=[{"security_id":sid,"security_type":r.get("security_type"),"durable_identifier":sid,
              "source":"legacy_company_factor_evidence","public_at":r.get("public_at"),
              "retrieved_at":r.get("retrieved_at"),"available_at":r.get("available_at") or r.get("retrieved_at")}]
        classification=_classification_for(db,sid,decision,legacy); kind=classification.security_type
        r={**r,"security_type":kind,"classification":_classification_payload(classification)}
        market=r.get("market_capitalisation")
        values={"earnings_yield":earnings_yield(r.get("net_income_ttm"),market,currency_compatible=r.get("currency_compatible",True)),
          "fcf_yield":fcf_yield(r.get("operating_cash_flow_ttm"),r.get("capital_expenditure_ttm"),market,capex_sign=r.get("capex_sign","positive_outflow"),periods_compatible=r.get("periods_compatible",True)),
          "book_to_market":book_to_market(r.get("shareholders_equity"),market,security_type=kind,reliable=r.get("equity_reliable",True)),
          "enterprise_value":construct_enterprise_value(market,r.get("debt_components"),r.get("eligible_cash"),currency_compatible=r.get("currency_compatible",True),confirmed_debt_free=r.get("confirmed_debt_free",False)),
          "financial_strength":financial_strength(current_debt=r.get("current_debt"),non_current_debt=r.get("non_current_debt"),confirmed_debt_free=r.get("confirmed_debt_free",False),assets=r.get("assets"),equity=r.get("shareholders_equity"),cash=r.get("eligible_cash"),operating_income=r.get("operating_income_ttm"),interest_expense=r.get("interest_expense_ttm"),security_type=kind)}
        states={field:("available" if field in (r.get("canonical_inputs") or {}) else "evidence_unavailable") for field in FIELDS}
        # Derived readiness aliases use the same conditions as the coverage audit.
        states["free_cash_flow"]="available" if {"operating_cash_flow","capital_expenditure"}<=(r.get("canonical_inputs") or {}).keys() else "evidence_unavailable"
        states["market_capitalisation_inputs"]="available" if {"decision_price","diluted_shares"}<=(r.get("canonical_inputs") or {}).keys() else "unreliable_denominator"
        states["enterprise_value_inputs"]="available" if states["market_capitalisation_inputs"]=="available" and {"current_debt","non_current_debt"}<=(r.get("canonical_inputs") or {}).keys() else "unreliable_denominator"
        readiness={name:family_readiness(states,name) for name in FAMILIES}
        return {"command":"company-investment-factor-preview","qualified_symbol":qualified_symbol,
          "decision_at":decision.isoformat(),"inputs":{k:v for k,v in r.items() if k not in {"debt_components"}},
          "calculations":values,"provenance":{"public_at":str(r.get("public_at")),"retrieved_at":str(r.get("retrieved_at")),"source":r.get("source")},
          "classification":r.get("classification"),"comparable_universe_eligible":classification.included,
          "corporate_action_state":next((x.get("coverage_state") for x in _rows(db,"corporate_action_coverage_evidence") if x.get("qualified_symbol")==qualified_symbol and _aware_for_preview(x.get("available_at"))<=decision),"coverage_missing"),
          "missing_evidence":r.get("missing_evidence",[]),"factor_family_readiness":readiness,
          "percentiles":[],"composite_scores":[],"rankings":[],"candidates":[],"recommendations":[],"read_only":True}
    return _immutable(research_db,production_db,build)

def track_b_panel_feasibility(*,research_db:Path,production_db:Path,decision_at:datetime,max_samples:int=MAX_SAMPLES):
    report=comparable_universe_readiness(research_db=research_db,production_db=production_db,decision_at=decision_at,max_samples=max_samples)
    symbols=report.get("ordinary_company_symbol_samples",[])
    # Counts are derived from the complete aggregate; samples remain bounded.
    full={name:data["full_family_ready"] for name,data in report["factor_capability"].items()}
    audit=coverage_audit(research_db=research_db,production_db=production_db,decision_at=decision_at,max_samples=max_samples)
    distribution=Counter()
    patterns=Counter()
    for symbol,states in audit["security_evidence"].items():
        rows={name:family_readiness(states,name) for name in FAMILIES}
        distribution[sum(x["full_family_ready"] for x in rows.values())]+=1
        patterns["|".join(sorted(name for name,x in rows.items() if not x["full_family_ready"])) or "none"]+=1
    total=report["ordinary_operating_company_universe_count"]
    return {"command":"track-b-panel-feasibility","decision_at":report["decision_at"],"labels":TRACK_B_LABELS,
      "comparable_universe_size":total,"per_family_availability":report["factor_capability"],
      "companies_by_usable_family_count":{str(k):distribution.get(k,0) for k in (3,4,5,6)},
      "cross_sectional_sample_sizes":full,"classification_exclusions":report["exclusions_by_reason"],
      "missingness_patterns":[{"pattern":k,"count":v} for k,v in patterns.most_common(max(0,min(MAX_SAMPLES,max_samples)))],
      "future_panel_feasible":total>0 and distribution.get(4,0)+distribution.get(5,0)+distribution.get(6,0)>0,
      "feasibility_is_not_model_approval":True,"read_only":True,
      "recommendations":[],"candidates":[],"rankings":[],"paper_selections":[],"prospective_vintages":[],"validation_observations":[],"validation_credit":0,
      "database_fingerprints":report["database_fingerprints"]}

@dataclass(frozen=True)
class CostAssumptions:
    estimated_spread_bps: float = 25.0
    commission_bps: float = 5.0
    fx_bps: float = 10.0
    slippage_bps: float = 10.0
    turnover: float = 1.0
    position_value: float = 10_000.0
    def __post_init__(self):
        if not (1 <= self.estimated_spread_bps <= 500 and 0 <= self.commission_bps <= 100
                and 0 <= self.fx_bps <= 200 and 0 <= self.slippage_bps <= 200
                and 0 < self.turnover <= 2 and self.position_value > 0):
            raise ValueError("cost assumptions outside documented bounds")
    @property
    def configuration_hash(self): return _json_hash(asdict(self))

def total_return(*, start_price: float, end_price: float, cash_dividends: float,
                 assumptions: CostAssumptions, spread_bps: float | None = None) -> dict[str, Any]:
    if min(start_price,end_price)<=0 or not all(math.isfinite(x) for x in (start_price,end_price,cash_dividends)):
        return {"status":"withheld","reason_code":"unreliable_denominator"}
    spread=assumptions.estimated_spread_bps if spread_bps is None else spread_bps
    gross=(end_price+cash_dividends)/start_price-1
    costs=(spread+assumptions.commission_bps+assumptions.fx_bps+assumptions.slippage_bps)*assumptions.turnover/10000
    return {"status":"available","gross_total_return":gross,"net_total_return":gross-costs,
            "cost_fraction":costs,"spread_bps":spread,"spread_source":"estimated_conservative" if spread_bps is None else "observed",
            "configuration_hash":assumptions.configuration_hash}

def execution_cost_capability(*, research_db: Path, production_db: Path, decision_at: datetime,
                              assumptions: CostAssumptions | None=None) -> dict[str,Any]:
    assumptions=assumptions or CostAssumptions(); audit=coverage_audit(research_db=research_db,production_db=production_db,decision_at=decision_at)
    available=audit["field_coverage"]["trading_liquidity_inputs"]["available"]
    return {"command":"execution-cost-capability","decision_at":_utc(decision_at).isoformat(),"assumptions":asdict(assumptions),
            "configuration_hash":assumptions.configuration_hash,"liquidity_ready":available,
            "split_adjusted_prices":True,"cash_dividends":True,"corporate_actions":True,
            "exact_completed_session_horizons":[126,252],"missing_or_delisted_withheld":True,
            "spread_fallback":"estimated_conservative","database_fingerprints":audit["database_fingerprints"],"read_only":True}

def research_readiness(**kwargs) -> dict[str,Any]:
    audit=coverage_audit(**kwargs); total=audit["comparable_universe_count"]
    gate_map={"price_history":"model_ready_price_history","freshness":"decision_price","liquidity":"trading_liquidity_inputs",
              "identity":"sec_issuer_mapping","accounting":"revenue","valuation_denominator":"market_capitalisation_inputs",
              "corporate_action":"corporate_actions_and_dividends"}
    gates={g:{"passed":audit["field_coverage"][f]["available"],"failed":total-audit["field_coverage"][f]["available"],
              "evidence_timestamp":audit["decision_at"]} for g,f in gate_map.items()}
    return {"command":"undervalued-quality-research-readiness","labels":TRACK_B_LABELS,
      "track_a":{"version":STRATEGY_VERSION,"configuration_hash":CONFIGURATION_HASH,"registration_timestamp":REGISTRATION_AT.isoformat(),"status":"frozen_unchanged"},
      "track_b":{"status":"draft_research_foundation","factor_family_coverage":audit["factor_family_coverage"]},
      "investability_research_gates":gates,"execution_cost_capability":"available_with_explicit_bounded_assumptions",
      "limitations":["current_membership_survivorship_limitation","international_coverage_not_assessed","sector_comparability_requires_review"],
      "work_required":["repair point-in-time evidence gaps","pre-register a distinct version and hash","lock horizons, comparators, and costs","separate development and untouched holdout periods","apply multiple-testing controls","collect prospective evidence after registration"],
      "recommendations":[],"candidates":[],"rankings":[],"paper_selections":[],"prospective_vintages":[],"validation_observations":[],"validation_credit":0,
      "database_fingerprints":audit["database_fingerprints"],"read_only":True}
