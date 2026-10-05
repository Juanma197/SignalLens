"""Milestone 40 financial-strength evidence and contract audit.

This module is deliberately a reader.  It has no write, network, scoring, ranking,
or selection entry point.  Accounting concepts are accepted only through the exact
versioned contract below; absence is never converted to zero.
"""
from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timezone
import json
import math
from pathlib import Path
from typing import Any

import duckdb

from .model_readiness import fingerprint
from .sec_ingestion import validate_paths
from .investment_research import InvestmentResearchError, TRACK_B_LABELS

ALIAS_CONTRACT_VERSION = "financial-strength-alias-contract-1.0.0"
COMPANY_SAMPLE_LIMIT = 10
SYMBOL_SAMPLE_LIMIT = 10
CONCEPT_SAMPLE_LIMIT = 10
OBSERVATIONS_PER_FIELD_LIMIT = 3
CITATION_LIMIT = 10
AGGREGATE_MAXIMUM_BYTES = 512 * 1024
CONTRACT_MAXIMUM_BYTES = 256 * 1024
PREVIEW_MAXIMUM_BYTES = 256 * 1024
INTEREST_GAP_REASONS = ("no_interest_related_facts_stored",
 "facts_exist_under_unsupported_concepts","usable_facts_exist_but_were_not_mapped",
 "facts_use_incompatible_units","facts_are_post_decision",
 "facts_lack_reliable_timestamps","zero_values_explicitly_reported",
 "appears_debt_free_but_interest_expense_absent",
 "interest_expense_embedded_in_net_interest_concept",
 "financial_sector_accounting_not_comparable","insufficient_evidence_to_determine_reason")
ZERO_OUTPUTS = {"rankings": [], "candidates": [], "recommendations": [],
                "selections": [], "vintages": [], "validation_observations": [],
                "validation_credit": 0}

def _alias(field: str, concept: str, nature: str, meaning: str,
           confidence: str = "high", sign: str = "reported_nonnegative",
           transformations: tuple[str, ...] = ("identity",),
           excluded: tuple[str, ...] = ()) -> dict[str, Any]:
    return {"canonical_field": field, "concept": concept, "expected_unit": "USD",
            "currency_requirement": "USD", "period_nature": nature,
            "sign_convention": sign, "accounting_meaning": meaning,
            "permitted_transformations": list(transformations),
            "excluded_meanings": list(excluded), "confidence": confidence,
            "alias_contract_version": ALIAS_CONTRACT_VERSION}

# Exact keys are intentional.  There is no label, substring, case, or plural match.
_SPECS = (
 _alias("assets","Assets","instant","total assets"),
 _alias("current_assets","AssetsCurrent","instant","current assets"),
 _alias("cash_and_cash_equivalents","CashAndCashEquivalentsAtCarryingValue","instant","unrestricted cash and cash equivalents",excluded=("restricted cash",)),
 _alias("restricted_cash","RestrictedCashAndCashEquivalentsCurrent","instant","restricted cash",excluded=("unrestricted cash",)),
 _alias("shareholders_equity","StockholdersEquity","instant","stockholders' equity attributable to the reporting entity",sign="reported_signed",excluded=("temporary equity","noncontrolling interest")),
 _alias("shareholders_equity","StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest","instant","total stockholders' equity including NCI",confidence="medium",sign="reported_signed",excluded=("equity excluding NCI",)),
 _alias("total_liabilities","Liabilities","instant","total liabilities"),
 _alias("current_liabilities","LiabilitiesCurrent","instant","current liabilities"),
 _alias("current_debt","ShortTermBorrowings","instant","short-term borrowings",excluded=("current portion of long-term debt",)),
 _alias("current_debt","ShortTermDebtCurrent","instant","short-term debt",excluded=("current lease liabilities",)),
 _alias("current_debt","LongTermDebtCurrent","instant","current portion of long-term debt",excluded=("all current debt when separately reported",)),
 _alias("non_current_debt","LongTermDebtNoncurrent","instant","long-term debt due after one year",excluded=("finance leases","operating leases")),
 _alias("total_debt","LongTermDebtAndFinanceLeaseObligationsCurrentAndNoncurrent","instant","reported debt including finance leases",confidence="medium",excluded=("operating leases",)),
 _alias("total_debt","DebtCurrentAndNoncurrent","instant","explicit current and non-current debt",excluded=("operating leases",)),
 _alias("short_term_borrowings","ShortTermBorrowings","instant","short-term borrowings",excluded=("long-term debt current portion",)),
 _alias("long_term_debt_current","LongTermDebtCurrent","instant","current portion of long-term debt"),
 _alias("long_term_debt_non_current","LongTermDebtNoncurrent","instant","non-current portion of long-term debt"),
 _alias("finance_lease_liabilities","FinanceLeaseLiability","instant","finance-lease liabilities",excluded=("operating leases","funded debt")),
 _alias("operating_lease_liabilities","OperatingLeaseLiability","instant","operating-lease liabilities",excluded=("finance leases","funded debt")),
 _alias("interest_expense","InterestExpense","duration","gross interest expense",excluded=("interest income","net interest")),
 _alias("interest_expense_non_operating","InterestExpenseNonOperating","duration","non-operating interest expense",excluded=("net interest",)),
 _alias("interest_and_debt_expense","InterestAndDebtExpense","duration","interest and debt expense",confidence="medium",excluded=("interest income","net interest")),
 _alias("net_interest_expense","InterestIncomeExpenseNonoperatingNet","duration","net non-operating interest income/expense",sign="reported_signed",excluded=("gross interest expense",)),
 _alias("interest_income","InterestIncomeExpenseNonoperatingNet","duration","net interest concept; not gross interest income",confidence="low",sign="reported_signed",excluded=("gross interest income",)),
 _alias("interest_income","InvestmentIncomeInterestAndDividend","duration","interest and dividend income",confidence="medium",excluded=("interest expense",)),
 _alias("operating_income","OperatingIncomeLoss","duration","operating income or loss",sign="reported_signed"),
 _alias("ebit","IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest","duration","pre-tax continuing income; EBIT-compatible only with separately evidenced net interest",confidence="low",sign="reported_signed",transformations=("identity_for_audit_only",),excluded=("EBITDA",)),
 _alias("net_income","NetIncomeLoss","duration","net income or loss",sign="reported_signed"),
 _alias("operating_cash_flow","NetCashProvidedByUsedInOperatingActivities","duration","net operating cash flow",sign="reported_signed"),
)
FINANCIAL_FIELD_ALIASES: dict[str, tuple[dict[str, Any], ...]] = {}
for _spec in _SPECS:
    FINANCIAL_FIELD_ALIASES[_spec["concept"]] = (*FINANCIAL_FIELD_ALIASES.get(_spec["concept"], ()), _spec)
AUDIT_FIELDS = tuple(dict.fromkeys(x["canonical_field"] for x in _SPECS))

METRIC_CONTRACTS = {
 "debt_to_assets": ("total debt under one documented construction", "assets", ["debt","assets"], [], "assets > 0", "withhold"),
 "debt_to_equity": ("total debt under one documented construction", "shareholders_equity", ["debt","shareholders_equity"], [], "equity > 0", "negative_equity_flag; zero withheld"),
 "net_debt_to_assets": ("debt minus unrestricted cash; restricted cash excluded", "assets", ["debt","assets","cash_and_cash_equivalents"], [], "assets > 0", "withhold"),
 "net_debt_to_ebit_or_ebitda": ("debt minus unrestricted cash", "explicitly defensible EBIT; EBITDA is never shortcut-derived", ["debt","cash_and_cash_equivalents","ebit"], [], "EBIT > 0", "distress flag; ratio withheld"),
 "interest_coverage": ("defensible EBIT", "gross interest expense", ["ebit","interest_expense"], [], "interest expense > 0", "zero is not-applicable only with explicit debt-free evidence; otherwise withheld"),
 "operating_cash_flow_to_debt": ("operating cash flow", "total debt", ["operating_cash_flow","debt"], [], "debt > 0", "explicit zero debt is not-applicable"),
 "current_ratio": ("current assets", "current liabilities", ["current_assets","current_liabilities"], [], "current liabilities > 0", "withhold"),
 "equity_ratio": ("shareholders equity", "assets", ["shareholders_equity","assets"], [], "assets > 0", "negative equity retained as distress flag, not ordinary ratio"),
 "change_in_leverage": ("current and prior comparable debt/assets", "prior debt/assets", ["debt","assets"], [], "both periods valid", "withhold"),
 "distress_or_negative_equity_flags": ("signed equity and denominator states", "not a ratio", ["shareholders_equity"], [], "signed value required", "explicit flag"),
}

def _aware(value: Any) -> datetime | None:
    if value is None: return None
    if isinstance(value, str): value=datetime.fromisoformat(value.replace("Z","+00:00"))
    if value.tzinfo is None or value.utcoffset() is None: return None
    return value.astimezone(timezone.utc)

def _decision(value: datetime) -> datetime:
    result=_aware(value)
    if result is None: raise InvestmentResearchError("timezone-aware decision timestamp required")
    return result

def _tables(db): return {x[0] for x in db.execute("SHOW TABLES").fetchall()}
def _rows(db, table):
    if table not in _tables(db): return []
    cur=db.execute(f'SELECT * FROM "{table}"'); names=[x[0] for x in cur.description]
    return [dict(zip(names,row)) for row in cur.fetchall()]
def _json(value):
    if isinstance(value,str):
        try: value=json.loads(value)
        except (ValueError,TypeError): return {}
    return value if isinstance(value,dict) else {}

def _visible(row, decision):
    public,retrieved,available=(_aware(row.get(x)) for x in ("public_at","retrieved_at","available_at"))
    if public is None or retrieved is None: return False,"missing_reliable_public_or_retrieval_timestamp"
    expected=max(public,retrieved)
    if available is None: return False,"missing_canonical_availability_timestamp"
    if available != expected: return False,"invalid_canonical_availability_timestamp"
    if any(x>decision for x in (public,retrieved,available)): return False,"post_decision_evidence"
    return True,None

def _unit_ok(row, spec):
    unit=str(row.get("unit") or ""); currency=str(row.get("currency") or "")
    if unit not in {"USD","monetary"}: return False,"incompatible_units"
    if currency != "USD": return False,"currency_mismatch"
    nature="duration" if row.get("period_start") is not None else "instant"
    if nature != spec["period_nature"]: return False,"period_nature_mismatch"
    value=row.get("value")
    if value is None or not math.isfinite(float(value)): return False,"nonfinite_value"
    if spec["sign_convention"]=="reported_nonnegative" and float(value)<0: return False,"sign_convention_violation"
    return True,None

def _companies(canonical, classifications, decision):
    allowed=set()
    for row in classifications:
        visible, _ = _visible(row,decision)
        if visible and row.get("security_type")=="us_operating_company":
            allowed.add((str(row.get("security_id")),str(row.get("qualified_symbol") or "")))
    # Old offline fixtures may predate classification materialization.  Canonical
    # evidence then defines the bounded audit population, never an inferred class.
    if not allowed and not classifications:
        allowed={(str(r.get("security_id")),str(r.get("qualified_symbol") or "")) for r in canonical}
    # This is the complete population.  Bounds belong on returned detail, never
    # on the population used for aggregate accounting.
    return sorted(allowed,key=lambda x:(x[1],x[0]))

def _bounded(items, limit):
    values=list(items); returned=values[:limit]
    return {"items":returned,"total_count":len(values),"returned_count":len(returned),
            "sample_limit":limit,"truncated":len(values)>len(returned)}

def _bounds(maximum):
    return {"company_sample_limit":COMPANY_SAMPLE_LIMIT,
            "symbol_sample_limit":SYMBOL_SAMPLE_LIMIT,
            "concept_sample_limit":CONCEPT_SAMPLE_LIMIT,
            "observations_per_field_limit":OBSERVATIONS_PER_FIELD_LIMIT,
            "citation_limit":CITATION_LIMIT,
            "maximum_compact_utf8_bytes":maximum}

def compact_utf8_size(payload):
    """Size of Python's deterministic compact JSON, independent of shell encoding."""
    return len(json.dumps(payload,sort_keys=True,separators=(",",":"),default=str).encode("utf-8"))

def _within_contract(payload, maximum):
    payload["compact_utf8_bytes"]=0
    for _ in range(3):
        payload["compact_utf8_bytes"]=compact_utf8_size(payload)
    if payload["compact_utf8_bytes"]>maximum:
        raise InvestmentResearchError("financial strength response exceeds size contract")
    return payload

def _observation(row, decision):
    concept=str(row.get("original_concept_or_field") or "")
    specs=FINANCIAL_FIELD_ALIASES.get(concept, ())
    spec=next((x for x in specs if x["canonical_field"]==row.get("canonical_field")),None)
    usable,reason=_visible(row,decision)
    if usable and not specs: usable,reason=False,"unsupported_exact_concept"
    elif usable and spec is None: usable,reason=False,"canonical_concept_mismatch"
    if usable: usable,reason=_unit_ok(row,spec)
    if usable and row.get("reliability_state") not in {None,"usable"}: usable,reason=False,str(row.get("withholding_reason") or "source_withheld")
    provenance=_json(row.get("provenance"))
    return {"canonical_field":row.get("canonical_field"),"original_concept":concept,
      "value":row.get("value"),"unit":row.get("unit"),"currency":row.get("currency"),
      "scale":provenance.get("scale",provenance.get("scale_factor",1)),
      "period_nature":"duration" if row.get("period_start") is not None else "instant",
      "period_start":str(row.get("period_start") or "") or None,"period_end":str(row.get("period_end") or row.get("instant_date") or "") or None,
      "public_at":str(row.get("public_at") or "") or None,"retrieved_at":str(row.get("retrieved_at") or "") or None,
      "available_at":str(row.get("available_at") or "") or None,
      "source_filing":row.get("accession_or_source_identifier"),"usable":usable,
      "withholding_reason":reason,"alias_contract_version":ALIAS_CONTRACT_VERSION}

def _latest(observations):
    result={}
    for x in observations:
        if not x["usable"]: continue
        key=x["canonical_field"]
        order=(x.get("period_end") or "",x.get("available_at") or "",x.get("source_filing") or "")
        if key not in result or order>result[key][0]: result[key]=(order,x)
    return {k:v[1] for k,v in result.items()}

def construct_debt(fields):
    """Return alternative non-overlapping debt constructions; never select by coverage."""
    alternatives=[]
    if "total_debt" in fields: alternatives.append({"method":"A","components":["total_debt"],"value":fields["total_debt"]["value"]})
    if {"current_debt","non_current_debt"}<=fields.keys():
        concepts={fields[x]["original_concept"] for x in ("current_debt","non_current_debt")}
        if len(concepts)==2: alternatives.append({"method":"B","components":["current_debt","non_current_debt"],"value":sum(fields[x]["value"] for x in ("current_debt","non_current_debt"))})
    parts=("short_term_borrowings","long_term_debt_current","long_term_debt_non_current")
    if set(parts)<=fields.keys() and len({fields[x]["original_concept"] for x in parts})==3:
        alternatives.append({"method":"C","components":list(parts),"value":sum(fields[x]["value"] for x in parts)})
    return {"alternatives":alternatives,"finance_leases_separate":"finance_lease_liabilities" in fields,
            "operating_leases_separate":"operating_lease_liabilities" in fields,
            "selected_method":None,"overlap_prevention":"one alternative at a time; never sum alternatives or lease classes"}

def _company(sid,symbol,all_rows,decision):
    observations=sorted((_observation(r,decision) for r in all_rows if str(r.get("security_id"))==sid),
      key=lambda x:(str(x["canonical_field"]),str(x["period_end"]),str(x["original_concept"]),str(x["source_filing"])))
    fields=_latest(observations); debt=construct_debt(fields)
    debt_values=[x["value"] for x in debt["alternatives"]]
    debt_state="undetermined"
    if debt_values and all(x==0 for x in debt_values): debt_state="debt_free_explicit"
    elif debt_values and any(x>0 for x in debt_values): debt_state="leveraged"
    components={
      "leverage":"ready" if debt["alternatives"] and "assets" in fields and fields["assets"]["value"]>0 else "unavailable",
      "liquidity":"ready" if {"current_assets","current_liabilities"}<=fields.keys() and fields["current_liabilities"]["value"]>0 else "unavailable",
      "coverage":("not_applicable" if debt_state=="debt_free_explicit" and "interest_expense" not in fields else
        "ready" if "interest_expense" in fields and "ebit" in fields and fields["interest_expense"]["value"]>0 and fields["ebit"]["value"]>0 else "unavailable"),
      "cash_generation_debt_service":("not_applicable" if debt_state=="debt_free_explicit" else
        "ready" if debt["alternatives"] and "operating_cash_flow" in fields and max(debt_values)>0 else "unavailable")}
    independent=sum(v=="ready" for v in components.values())
    missing=sorted(set(AUDIT_FIELDS)-set(fields))
    reasons=Counter(x["withholding_reason"] for x in observations if not x["usable"])
    formulas={"leverage":"total_debt / assets","liquidity":"current_assets / current_liabilities",
      "coverage":"EBIT / gross_interest_expense","cash_generation_debt_service":"operating_cash_flow / total_debt"}
    return {"security_id":sid,"qualified_symbol":symbol,"classification":"financial_strength_evidence_assessed",
      "available_fields":sorted(fields),"observations":observations,"debt_construction":debt,"debt_state":debt_state,
      "components":components,"component_formulas":formulas,"missing_inputs":missing,
      "withholding_reasons":dict(sorted(reasons.items())),"denominator_warnings":sorted([
        *( ["negative_equity"] if fields.get("shareholders_equity",{}).get("value",1)<0 else []),
        *( ["nonpositive_assets"] if fields.get("assets",{}).get("value",1)<=0 else []),
        *( ["nonpositive_ebit"] if fields.get("ebit",{}).get("value",1)<=0 else [])]),
      "readiness":{"any_input_available":bool(fields),"minimum_calculable":independent>=1,
        "component_level":components,"full_family_ready":all(v in {"ready","not_applicable"} for v in components.values()),
        "not_applicable":[k for k,v in components.items() if v=="not_applicable"],
        "unavailable":[k for k,v in components.items() if v=="unavailable"]},
      "score_contribution":None,"recommendation":None}

def _interest_diagnosis(company, raw):
    fields=set(company["available_fields"]); obs=company["observations"]
    related=[r for r in raw if str(r.get("security_id"))==company["security_id"] and "interest" in str(r.get("concept","")).lower()]
    if "interest_expense" in fields and any(x["canonical_field"]=="interest_expense" and x["value"]==0 for x in obs if x["usable"]): return "zero_values_explicitly_reported"
    if "net_interest_expense" in fields: return "interest_expense_embedded_in_net_interest_concept"
    reasons={x["withholding_reason"] for x in obs if "interest" in str(x["canonical_field"])}
    if "incompatible_units" in reasons or "currency_mismatch" in reasons: return "facts_use_incompatible_units"
    if "post_decision_evidence" in reasons: return "facts_are_post_decision"
    if reasons & {"missing_reliable_public_or_retrieval_timestamp","missing_canonical_availability_timestamp"}: return "facts_lack_reliable_timestamps"
    if related:
        return "facts_exist_under_unsupported_concepts" if any(str(x.get("concept")) not in FINANCIAL_FIELD_ALIASES for x in related) else "usable_facts_exist_but_were_not_mapped"
    if company["debt_state"]=="debt_free_explicit": return "appears_debt_free_but_interest_expense_absent"
    return "no_interest_related_facts_stored" if not obs else "insufficient_evidence_to_determine_reason"

def _report(*, research_db: Path, production_db: Path, decision_at: datetime):
    decision=_decision(decision_at); validate_paths(Path(research_db),Path(production_db))
    before=(fingerprint(Path(research_db)),fingerprint(Path(production_db)))
    with duckdb.connect(str(research_db),read_only=True) as research, duckdb.connect(str(production_db),read_only=True) as production:
        canonical=_rows(research,"canonical_factor_evidence"); classifications=_rows(research,"security_classification_evidence")
        raw=_rows(research,"sec_facts") + _rows(production,"sec_facts")
        raw=list({str(x.get("fact_key") or (x.get("security_id"),x.get("concept"),x.get("period_end"),x.get("accession_number"),x.get("unit"))):x for x in reversed(raw)}.values())
        from .liquidity_materialization import CONTRACT_HASH
        from .liquidity_resolution import resolve_canonical_liquidity
        effective,resolution=resolve_canonical_liquidity(raw_rows=raw,canonical_rows=canonical,
          revision_rows=_rows(research,"liquidity_canonical_materialization_revisions"),
          run_rows=_rows(research,"liquidity_canonical_materialization_runs"),decision_at=decision,
          contract_hash=CONTRACT_HASH)
        controlled={str(x.get("evidence_key")) for x in canonical
          if _json(x.get("provenance")).get("operation_type")=="liquidity_canonical_materialization"}
        canonical=[x for x in canonical if str(x.get("evidence_key")) not in controlled]
        canonical.extend(x for x in effective if x.get("_canonical_revision"))
        companies=[_company(sid,symbol,canonical,decision) for sid,symbol in _companies(canonical,classifications,decision)]
    after=(fingerprint(Path(research_db)),fingerprint(Path(production_db)))
    if before != after: raise InvestmentResearchError("database changed during read-only audit")
    return decision,companies,raw,{"research_unchanged":before[0]==after[0],"production_unchanged":before[1]==after[1],"verified":True}

def evidence_audit(*, research_db: Path, production_db: Path, decision_at: datetime) -> dict:
    decision,companies,raw,immutability=_report(research_db=research_db,production_db=production_db,decision_at=decision_at)
    interest=Counter(); samples={}
    for company in companies:
        reason=_interest_diagnosis(company,raw); interest[reason]+=1
        samples.setdefault(reason,[]).append(company["qualified_symbol"])
    coverage=Counter(f for c in companies for f in c["available_fields"])
    concept_counts=Counter(x["original_concept"] for c in companies for x in c["observations"])
    components=Counter(f"{name}:{state}" for c in companies for name,state in c["components"].items())
    company_summaries=[{"security_id":c["security_id"],"qualified_symbol":c["qualified_symbol"],
      "available_fields":c["available_fields"],"debt_state":c["debt_state"],
      "components":c["components"],"missing_inputs":c["missing_inputs"],
      "denominator_warnings":c["denominator_warnings"]} for c in companies]
    report={"command":"financial-strength-evidence-audit","decision_at":decision.isoformat(),"read_only":True,
      "alias_contract_version":ALIAS_CONTRACT_VERSION,"comparable_company_count":len(companies),
      "field_coverage":{f:coverage[f] for f in AUDIT_FIELDS},
      "concept_counts":dict(sorted(concept_counts.items())),
      "component_counts":dict(sorted(components.items())),
      "interest_gap_diagnosis":{k:interest[k] for k in INTEREST_GAP_REASONS},
      "interest_samples":{k:_bounded(sorted(samples.get(k,[])),SYMBOL_SAMPLE_LIMIT) for k in INTEREST_GAP_REASONS},
      "company_samples":_bounded(company_summaries,COMPANY_SAMPLE_LIMIT),
      "bounds":_bounds(AGGREGATE_MAXIMUM_BYTES),
      "database_immutability":immutability,"labels":TRACK_B_LABELS,**ZERO_OUTPUTS}
    return _within_contract(report,AGGREGATE_MAXIMUM_BYTES)

def _contract_results(companies):
    result=Counter()
    for c in companies:
        ready=c["components"]; ready_count=sum(x=="ready" for x in ready.values())
        result["A_all_components_mandatory"]+=all(x=="ready" for x in ready.values())
        result["B_leverage_liquidity_mandatory_coverage_optional"]+=ready["leverage"]==ready["liquidity"]=="ready"
        result["C_two_independent_components"]+=ready_count>=2
        result["D_debt_free_or_leveraged_branch"]+=(c["debt_state"]=="debt_free_explicit" or
          (c["debt_state"]=="leveraged" and ready["leverage"]=="ready" and ready["coverage"]=="ready"))
    return dict(sorted(result.items()))

def contract_assessment(*, research_db: Path, production_db: Path, decision_at: datetime) -> dict:
    decision,companies,_,immutability=_report(research_db=research_db,production_db=production_db,decision_at=decision_at)
    metrics={}
    for name,(numerator,denominator,required,optional,valid,behavior) in METRIC_CONTRACTS.items():
        withholding=Counter(); count=0
        for c in companies:
            values={x["canonical_field"]:x["value"] for x in c["observations"] if x["usable"]}
            debt=bool(c["debt_construction"]["alternatives"])
            if name=="debt_to_assets": ok=debt and values.get("assets",0)>0
            elif name=="debt_to_equity": ok=debt and values.get("shareholders_equity",0)>0
            elif name=="net_debt_to_assets": ok=debt and "cash_and_cash_equivalents" in values and values.get("assets",0)>0
            elif name=="net_debt_to_ebit_or_ebitda": ok=debt and "cash_and_cash_equivalents" in values and values.get("ebit",0)>0
            elif name=="interest_coverage": ok=values.get("interest_expense",0)>0 and values.get("ebit",0)>0
            elif name=="operating_cash_flow_to_debt": ok=(debt and c["debt_state"]=="leveraged" and "operating_cash_flow" in values)
            elif name=="current_ratio": ok="current_assets" in values and values.get("current_liabilities",0)>0
            elif name=="equity_ratio": ok=values.get("shareholders_equity",0)>0 and values.get("assets",0)>0
            elif name=="change_in_leverage": ok=False  # one latest observation cannot establish comparable history
            else: ok="shareholders_equity" in values
            if ok: count+=1
            elif name in {"debt_to_equity","equity_ratio"} and values.get("shareholders_equity",1)<0: withholding["negative_equity"]+=1
            elif name in {"interest_coverage","net_debt_to_ebit_or_ebitda"} and values.get("ebit",1)<=0: withholding["nonpositive_ebit"]+=1
            elif name=="change_in_leverage": withholding["insufficient_comparable_history"]+=1
            elif c["debt_state"]=="debt_free_explicit" and name in {"interest_coverage","operating_cash_flow_to_debt"}: withholding["not_applicable_explicit_zero_debt"]+=1
            else: withholding["missing_or_invalid_inputs"]+=1
        metrics[name]={"numerator_contract":numerator,"denominator_contract":denominator,
          "required_inputs":required,"optional_inputs":optional,"valid_denominator_rules":valid,
          "negative_or_zero_denominator_behavior":behavior,"sector_limitations":"banks, insurers, and other financial-sector accounting require a separate contract",
          "minimum_history":2 if name=="change_in_leverage" else 1,
          "point_in_time_constraints":"public_at, retrieved_at, and canonical available_at must be no later than decision_at",
          "comparable_company_count":count,"withholding_counts":dict(sorted(withholding.items()))}
    aggregate=Counter()
    for c in companies:
        for k,v in c["components"].items(): aggregate[f"{k}:{v}"]+=1
    report={"command":"financial-strength-contract-assessment","decision_at":decision.isoformat(),"read_only":True,
      "comparable_company_count":len(companies),"readiness_semantics":{"any_input_available":"at least one usable contracted field",
       "minimum_calculable":"at least one component is ready","component_level":"ready, not_applicable, or unavailable",
       "full_family_ready":"every component is ready or explicitly not_applicable; interest absence alone is never not-applicable"},
      "component_counts":dict(sorted(aggregate.items())),"metric_assessments":metrics,
      "candidate_contracts":_contract_results(companies),"contract_selected":None,
      "selection_prohibition":"No contract is selected by returns or sample size; accounting judgment remains required.",
      "bounds":_bounds(CONTRACT_MAXIMUM_BYTES),
      "database_immutability":immutability,"labels":TRACK_B_LABELS,**ZERO_OUTPUTS}
    return _within_contract(report,CONTRACT_MAXIMUM_BYTES)

def _preview_company(company):
    selected=_latest(company["observations"])
    alternatives={}
    for field in AUDIT_FIELDS:
        chosen=selected.get(field)
        candidates=[x for x in company["observations"] if x["canonical_field"]==field and x is not chosen]
        # Most recent evidence first, with a final stable tie break.  Post-decision
        # evidence is never exposed even as a conflict.
        candidates=[x for x in candidates if x["withholding_reason"]!="post_decision_evidence"]
        candidates.sort(key=lambda x:(str(x.get("period_end") or ""),str(x.get("available_at") or ""),
                                     str(x.get("original_concept") or ""),str(x.get("source_filing") or "")),reverse=True)
        if candidates: alternatives[field]=_bounded(candidates,OBSERVATIONS_PER_FIELD_LIMIT)
    citations=sorted({str(x["source_filing"]) for x in [*selected.values(),
      *(item for group in alternatives.values() for item in group["items"])] if x.get("source_filing")})
    result={k:v for k,v in company.items() if k!="observations"}
    result["selected_evidence"]={k:selected[k] for k in sorted(selected)}
    result["alternative_observations"]={k:alternatives[k] for k in sorted(alternatives)}
    result["citations"]=_bounded(citations,CITATION_LIMIT)
    result["observation_population"]={"total_count":len(company["observations"]),
      "returned_count":len(selected)+sum(x["returned_count"] for x in alternatives.values()),
      "sample_limit":None,"truncated":len(company["observations"])>
        len(selected)+sum(x["returned_count"] for x in alternatives.values())}
    return result

def company_preview(*, research_db: Path, production_db: Path, decision_at: datetime,
                    qualified_symbol: str) -> dict:
    decision,companies,_,immutability=_report(research_db=research_db,production_db=production_db,decision_at=decision_at)
    matches=[x for x in companies if x["qualified_symbol"]==qualified_symbol]
    if len(matches)!=1: raise InvestmentResearchError("company unavailable or ambiguous")
    report={"command":"financial-strength-company-preview","decision_at":decision.isoformat(),"read_only":True,
      "company":_preview_company(matches[0]),"bounds":_bounds(PREVIEW_MAXIMUM_BYTES),
      "database_immutability":immutability,"labels":TRACK_B_LABELS,**ZERO_OUTPUTS}
    return _within_contract(report,PREVIEW_MAXIMUM_BYTES)

def aggregate_evidence_audit(**kwargs):
    # evidence_audit is already aggregate-only.  Keep a distinct API entry point
    # so company preview semantics can never leak into the aggregate route.
    return evidence_audit(**kwargs)
