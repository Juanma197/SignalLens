"""Milestone 41 read-only liquidity evidence discovery.

Nothing in this module materializes an alias.  Exact-name candidates are assessed
against an intentionally small accounting contract and remain proposals.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
import json, math, re
from pathlib import Path
from typing import Any

import duckdb

from .financial_strength import (_aware, _bounded, _bounds, _companies, _rows,
    _report as _financial_strength_report,
    compact_utf8_size, AGGREGATE_MAXIMUM_BYTES, CONTRACT_MAXIMUM_BYTES,
    PREVIEW_MAXIMUM_BYTES, ZERO_OUTPUTS)
from .investment_research import InvestmentResearchError, TRACK_B_LABELS
from .model_readiness import fingerprint
from .sec_ingestion import validate_paths
from .liquidity_measurement import (VALIDATOR_VERSION, evidence_identity,
    source_scale, validate_measurement)

PROPOSAL_VERSION="liquidity-alias-proposal-1.0.0"
DISCOVERY_RULE_VERSION="liquidity-exact-name-rule-1.0.0"
STALE_DAYS=550
DIAGNOSES=("qualifying direct fact exists but alias is unsupported",
 "qualifying component-based construction may be possible","only broader aggregate exists",
 "only narrower components exist","incompatible unit or currency","invalid duration/instant nature",
 "post-decision evidence only","timestamps missing or unreliable","stale period","conflicting facts",
 "taxonomy unsupported","no relevant fact stored","insufficient evidence")

def _spec(field, concept, meaning, relation="direct", confidence="high", exclusions=()):
    return {"canonical_field":field,"exact_concept":concept,"expected_unit":"USD",
      "period_nature":"instant","currency_requirement":"USD","scale_rule":"finite positive source scale; compare only equal scales",
      "sign_convention":"reported_nonnegative","accounting_meaning":meaning,"exclusions":list(exclusions),
      "relationship":relation,"confidence":confidence,"proposal_version":PROPOSAL_VERSION,
      "status":"proposed_not_authorized"}

_SPECS=(
 _spec("current_assets","AssetsCurrent","assets expected to be realized within the operating cycle"),
 _spec("current_liabilities","LiabilitiesCurrent","obligations due within the operating cycle"),
 _spec("unrestricted_cash","CashAndCashEquivalentsAtCarryingValue","unrestricted cash and cash equivalents",exclusions=("restricted cash","marketable securities")),
 _spec("cash_plus_restricted_cash","CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents","cash plus restricted cash","broader","medium",("unrestricted cash unless compatible restricted cash is separately reported",)),
 _spec("restricted_cash","RestrictedCashAndCashEquivalentsCurrent","current restricted cash","narrower"),
 _spec("short_term_investments","ShortTermInvestments","short-term investments","narrower","medium"),
 _spec("marketable_securities","MarketableSecuritiesCurrent","current marketable securities","narrower","medium"),
 _spec("inventory","InventoryNet","net inventory","narrower"),
 _spec("receivables","AccountsReceivableNetCurrent","net current trade receivables","narrower"),
 _spec("accounts_payable","AccountsPayableCurrent","current trade accounts payable","narrower"),
 _spec("assets","Assets","total assets"),
 _spec("liabilities","Liabilities","total liabilities","broader"),
)
SPECS={x["exact_concept"]:x for x in _SPECS}
TOKENS=re.compile(r"(AssetsCurrent|LiabilitiesCurrent|Cash|Restricted|MarketableSecurit|ShortTermInvestment|Inventory|Receivable|WorkingCapital|AccountsPayable|Assets|Liabilities)",re.I)

def _decision(value):
    result=_aware(value)
    if result is None: raise InvestmentResearchError("timezone-aware decision timestamp required")
    if result>datetime.now(timezone.utc): raise InvestmentResearchError("future decision timestamp prohibited")
    return result

def _nature(row): return "duration" if row.get("period_start") is not None else "instant"
def _scale(row): return source_scale(row)
def _concept(row): return str(row.get("concept") or row.get("original_concept_or_field") or "")
def _accession(row): return row.get("accession_number") or row.get("accession_or_source_identifier")

def _assess(row, decision):
    concept=_concept(row); spec=SPECS.get(concept); public=_aware(row.get("public_at")); retrieved=_aware(row.get("retrieved_at"))
    available=_aware(row.get("available_at")) or (max(public,retrieved) if public and retrieved else None)
    validation=validate_measurement(row,decision)
    labels={"visibility_timestamp_missing":"timestamps missing or unreliable",
      "not_visible_at_decision":"post-decision evidence only","measurement_nature_duration":"invalid duration/instant nature",
      "source_unit_not_usd":"incompatible unit or currency","unit_currency_contradiction":"incompatible unit or currency",
      "currency_ambiguous":"incompatible unit or currency","scale_not_lossless":"incompatible unit or currency",
      "taxonomy_not_supported":"taxonomy unsupported","concept_not_contractual":"taxonomy unsupported",
      "value_nonfinite_or_invalid":"insufficient evidence","value_negative":"insufficient evidence","period_stale":"stale period"}
    reason=None if validation["accepted"] else labels[validation["reason_code"]]
    end=row.get("period_end") or row.get("instant_date")
    taxonomy=str(row.get("taxonomy") or "unknown")
    version=row.get("taxonomy_version")
    if version is None and "-" in taxonomy: version=taxonomy.rsplit("-",1)[-1]
    return {"exact_concept":concept,"taxonomy":taxonomy,"taxonomy_version":version,
      "balance_type":row.get("balance_type") or "unknown_not_stored","period_nature":_nature(row),
      "expected_unit":"USD","observed_unit":row.get("unit"),"currency":validation["canonical_currency"],"source_currency":row.get("currency"),"scale":_scale(row),
      "sign":"negative" if isinstance(row.get("value"),(int,float)) and row["value"]<0 else "nonnegative",
      "value":row.get("value"),"period_end":str(end) if end else None,"form":row.get("form"),
      "accession_or_filing_reference":_accession(row),"public_at":str(row.get("public_at") or "") or None,
      "retrieval_at":str(row.get("retrieved_at") or "") or None,"canonical_availability_at":str(available) if available else None,
      "relationship":spec["relationship"] if spec else "incompatible","proposed_canonical_field":spec["canonical_field"] if spec else None,
      "confidence":spec["confidence"] if spec else "none","accepted":reason is None,
      "acceptance_or_withholding_reason":"passes exact semantic, point-in-time, unit, currency, scale, sign and instant checks" if reason is None else reason,
      "reason_code":validation["reason_code"],"validator_version":validation["validator_version"],
      "evidence_identity":validation["evidence_identity"],"validation":validation,
      "_source_database":row.get("_source_database"),"_source_table":row.get("_source_table")}

def _compatible(*items):
    return bool(items) and all(x and x["accepted"] for x in items) and len({(x["period_end"],x["currency"],str(x["scale"])) for x in items})==1

def _company(sid,symbol,rows,decision):
    assessed=[_assess(r,decision) for r in rows if str(r.get("security_id"))==sid and TOKENS.search(_concept(r))]
    assessed.sort(key=lambda x:(x["exact_concept"],x["period_end"] or "",x["canonical_availability_at"] or "",str(x["accession_or_filing_reference"])))
    accepted=[x for x in assessed if x["accepted"]]
    latest={}
    for x in accepted:
        f=x["proposed_canonical_field"]; key=(x["period_end"] or "",x["canonical_availability_at"] or "",str(x["accession_or_filing_reference"]))
        if f not in latest or key>latest[f][0]: latest[f]=(key,x)
    fields={k:v[1] for k,v in latest.items()}
    conflicts=[]
    for f, group in _group(accepted,"proposed_canonical_field").items():
        by_period=defaultdict(set)
        for x in group: by_period[(x["period_end"],x["currency"],str(x["scale"]))].add(x["value"])
        if any(len(v)>1 for v in by_period.values()): conflicts.append(f)
    constructions=[]
    if _compatible(fields.get("cash_plus_restricted_cash"),fields.get("restricted_cash")):
        value=fields["cash_plus_restricted_cash"]["value"]-fields["restricted_cash"]["value"]
        if value>=0: constructions.append({"field":"unrestricted_cash","method":"combined cash minus restricted cash","value":value,"status":"possible_not_authorized","accepted":True,"period_end":fields["cash_plus_restricted_cash"]["period_end"],"currency":"USD","scale":fields["cash_plus_restricted_cash"]["scale"]})
    if _compatible(fields.get("current_assets"),fields.get("current_liabilities")):
        constructions.append({"field":"working_capital","method":"current assets minus current liabilities","value":fields["current_assets"]["value"]-fields["current_liabilities"]["value"],"status":"possible_not_authorized","accepted":True,"period_end":fields["current_assets"]["period_end"],"currency":"USD","scale":fields["current_assets"]["scale"]})
    if _compatible(fields.get("current_assets"),fields.get("inventory")):
        constructions.append({"field":"quick_assets","method":"current assets minus inventory","value":fields["current_assets"]["value"]-fields["inventory"]["value"],"status":"possible_not_authorized","accepted":True,"period_end":fields["current_assets"]["period_end"],"currency":"USD","scale":fields["current_assets"]["scale"]})
    def diag(field):
        if field in conflicts:return "conflicting facts"
        if field in fields:return "qualifying direct fact exists but alias is unsupported"
        relevant=[x for x in assessed if x.get("proposed_canonical_field")==field]
        reasons=[x["acceptance_or_withholding_reason"] for x in relevant]
        for r in ("post-decision evidence only","timestamps missing or unreliable","incompatible unit or currency","invalid duration/instant nature","stale period","taxonomy unsupported"):
            if r in reasons:return r
        if field=="unrestricted_cash" and any(x["field"]==field for x in constructions):return "qualifying component-based construction may be possible"
        if field=="unrestricted_cash" and "cash_plus_restricted_cash" in fields:return "only broader aggregate exists"
        if field=="current_assets" and "assets" in fields:return "only broader aggregate exists"
        if field=="current_assets" and set(fields)&{"unrestricted_cash","inventory","receivables","short_term_investments","marketable_securities"}:return "only narrower components exist"
        if field=="current_liabilities" and "liabilities" in fields:return "only broader aggregate exists"
        if field=="current_liabilities" and "accounts_payable" in fields:return "only narrower components exist"
        return "no relevant fact stored" if not relevant else "insufficient evidence"
    diagnoses={f:diag(f) for f in ("current_assets","current_liabilities","unrestricted_cash")}
    warnings=[]
    if fields.get("current_liabilities",{}).get("value")==0:warnings.append("zero_current_liabilities")
    if fields.get("assets",{}).get("value",1)<=0:warnings.append("nonpositive_assets")
    return {"security_id":sid,"qualified_symbol":symbol,"selected":fields,"observations":assessed,
      "possible_constructions":constructions,"conflicts":conflicts,"primary_diagnosis":diagnoses,
      "missing_fields":sorted(set(("current_assets","current_liabilities","unrestricted_cash"))-set(fields)),
      "denominator_warnings":warnings,"score":None,"recommendation":None}

def _group(items,key):
    out=defaultdict(list)
    for x in items: out[x.get(key)].append(x)
    return out

def _load(research_db,production_db,decision_at):
    decision=_decision(decision_at); research_db=Path(research_db); production_db=Path(production_db)
    validate_paths(research_db,production_db); before=(fingerprint(research_db),fingerprint(production_db))
    with duckdb.connect(str(research_db),read_only=True) as r, duckdb.connect(str(production_db),read_only=True) as p:
        canonical=_rows(r,"canonical_factor_evidence"); classifications=_rows(r,"security_classification_evidence")
        raw=[]
        for database,db in (("research",r),("production",p)):
            for item in _rows(db,"sec_facts"):
                item={**item,"_source_database":database,"_source_table":"sec_facts"}; raw.append(item)
        population=_companies(canonical,classifications,decision)
        # Raw evidence is authoritative for discovery. Canonical rows supplement it
        # only when no raw row with the same stable observation identity exists.
        unique={}
        for item in raw: unique.setdefault(evidence_identity(item),item)
        raw=list(unique.values())
        seen={evidence_identity(x) for x in raw}
        canonical=[{**x,"_source_database":"research","_source_table":"canonical_factor_evidence",
          "taxonomy":x.get("taxonomy") or "us-gaap","concept":_concept(x)} for x in canonical]
        rows=raw+[x for x in canonical if evidence_identity(x) not in seen]
        companies=[_company(sid,sym,rows,decision) for sid,sym in population]
    after=(fingerprint(research_db),fingerprint(production_db))
    if before!=after:raise InvestmentResearchError("database changed during read-only audit")
    return decision,companies,{"research_unchanged":True,"production_unchanged":True,"verified":True,
      "before":{"research":before[0],"production":before[1]},
      "after":{"research":after[0],"production":after[1]}}

def _base(command,decision,immutability,maximum):
    return {"command":command,"decision_at":decision.isoformat(),"read_only":True,
      "liquidity_measurement_validator_version":VALIDATOR_VERSION,
      "consumer_semantics":"all unit, currency, scale, period and visibility decisions come from validate_measurement",
      "discovery_rule":{"version":DISCOVERY_RULE_VERSION,"rule":"exact case-sensitive allow-list plus exact names containing a bounded liquidity token; token discoveries remain withheld until semantic validation","tokens":TOKENS.pattern},"bounds":_bounds(maximum),"database_immutability":immutability,"labels":TRACK_B_LABELS,**ZERO_OUTPUTS}
def _finish(report,maximum):
    report["compact_utf8_bytes"]=0
    for _ in range(3):report["compact_utf8_bytes"]=compact_utf8_size(report)
    if report["compact_utf8_bytes"]>maximum:raise InvestmentResearchError("liquidity response exceeds size contract")
    return report

def evidence_discovery(*,research_db,production_db,decision_at):
    decision,companies,immutable=_load(research_db,production_db,decision_at)
    allobs=[x for c in companies for x in c["observations"]]
    concepts=[]
    for concept,obs in sorted(_group(allobs,"exact_concept").items()):
        visible=[x for x in obs if x["accepted"]]
        latest_periods=[]
        for company in companies:
            company_periods=[x["period_end"] for x in company["observations"] if x["exact_concept"]==concept and x["accepted"] and x["period_end"]]
            if company_periods: latest_periods.append(max(company_periods))
        periods=Counter(latest_periods)
        concepts.append({"exact_concept":concept,"taxonomy_versions":_bounded(sorted({f"{x['taxonomy']}:{x['taxonomy_version']}" for x in obs}),10),
          "balance_types":dict(sorted(Counter(x["balance_type"] for x in obs).items())),"period_natures":dict(sorted(Counter(x["period_nature"] for x in obs).items())),
          "observed_units":dict(sorted(Counter(str(x["observed_unit"]) for x in obs).items())),"currencies":dict(sorted(Counter(str(x["currency"]) for x in obs).items())),
          "scales":_bounded(sorted({str(x["scale"]) for x in obs}),10),"company_coverage_count":len({c["security_id"] for c in companies if any(y["exact_concept"]==concept and y["accepted"] for y in c["observations"])}),
          "observation_count":len(obs),"latest_visible_period_distribution":dict(sorted(periods.items())),
          "relationship":obs[0]["relationship"],"proposed_canonical_field":obs[0]["proposed_canonical_field"],"confidence":obs[0]["confidence"],
          "acceptance_or_withholding_reasons":dict(sorted(Counter(x["acceptance_or_withholding_reason"] for x in obs).items())),
          "representative_occurrences":_bounded(obs,3)})
    diagnoses={f:{r:0 for r in DIAGNOSES} for f in ("current_assets","current_liabilities","unrestricted_cash")}; samples={f:{r:[] for r in DIAGNOSES} for f in diagnoses}
    for c in companies:
        for f,r in c["primary_diagnosis"].items():diagnoses[f][r]+=1;samples[f][r].append(c["qualified_symbol"])
    proposals=[]
    for spec in _SPECS:
        count=len({c["security_id"] for c in companies if spec["canonical_field"] in c["selected"] and c["selected"][spec["canonical_field"]]["exact_concept"]==spec["exact_concept"]})
        proposals.append({**spec,"evidence_supported_company_count":count})
    report=_base("liquidity-evidence-discovery",decision,immutable,AGGREGATE_MAXIMUM_BYTES)
    report.update({"comparable_company_count":len(companies),"concept_assessments":_bounded(concepts,10),"alias_proposals":_bounded(proposals,10),"field_diagnosis_counts":diagnoses,
      "field_diagnosis_samples":{f:{r:_bounded(sorted(v),10) for r,v in by.items()} for f,by in samples.items()},
      "company_samples":_bounded([{"qualified_symbol":c["qualified_symbol"],"primary_diagnosis":c["primary_diagnosis"],"possible_constructions":c["possible_constructions"]} for c in companies],10)})
    return _finish(report,AGGREGATE_MAXIMUM_BYTES)

def _metric(c,name):
    f=c["selected"]; construction={x["field"]:x for x in c["possible_constructions"]}
    ca=f.get("current_assets"); cl=f.get("current_liabilities"); cash=f.get("unrestricted_cash") or construction.get("unrestricted_cash"); assets=f.get("assets")
    if name=="current_ratio": inputs=(ca,cl)
    elif name=="working_capital":
        return (True,None) if construction.get("working_capital") else (False,"missing_or_incompatible_inputs")
    elif name=="quick_ratio": inputs=(construction.get("quick_assets"),cl)
    elif name=="cash_ratio": inputs=(cash,cl)
    elif name=="working_capital_to_assets": inputs=(construction.get("working_capital"),assets)
    else:return False,"missing_or_invalid_debt_or_cash_inputs"
    if not all(inputs):return False,"missing_or_incompatible_inputs"
    if not _compatible(*[x for x in inputs if "accepted" in x]):return False,"incompatible_period_currency_or_scale"
    denominator=inputs[-1]["value"]
    if denominator==0:return False,"zero_denominator"
    if denominator<0:return False,"negative_denominator"
    return True,None

def contract_assessment(*,research_db,production_db,decision_at):
    decision,companies,immutable=_load(research_db,production_db,decision_at)
    definitions={"current_ratio":("current assets","current liabilities","direct/direct"),"quick_ratio":("compatible current assets minus inventory","current liabilities","constructed/direct"),"cash_ratio":("direct or compatibly decomposed unrestricted cash","current liabilities","direct-or-constructed/direct"),"working_capital":("current assets minus current liabilities","none","constructed"),"working_capital_to_assets":("compatible working capital","total assets","constructed/direct"),"net_debt_to_assets":("defensible debt minus unrestricted cash","total assets","constructed/direct")}
    metrics={}
    for name,(num,den,mode) in definitions.items():
        reasons=Counter(); ready=0
        for c in companies:
            ok,reason=_metric(c,name)
            if ok:ready+=1
            else:reasons[reason]+=1
        metrics[name]={"numerator_contract":num,"denominator_contract":den,"input_mode":mode,"overlap_and_double_counting_risk":"components must share company, period, currency, scale and accounting scope; never add aggregates to their components","denominator_rules":"strictly positive; zero and negative withhold","negative_and_zero_denominator_behavior":"withhold and report exact reason","minimum_history":1,"company_coverage":ready,"withholding_counts":dict(sorted(reasons.items())),"sector_limitations":"financial companies require a separate contract","point_in_time_constraints":"public and retrieval timestamps at or before decision; canonical availability equals their maximum"}
    direct=lambda c,f:f in c["selected"]
    hi=sum(direct(c,"current_assets") and direct(c,"current_liabilities") and c["selected"]["current_liabilities"]["value"]>0 for c in companies)
    cash_direct=sum(direct(c,"unrestricted_cash") for c in companies); cash_construct=sum(direct(c,"unrestricted_cash") or any(x["field"]=="unrestricted_cash" for x in c["possible_constructions"]) for c in companies)
    # Reuse Milestone 40's canonical implementation for exact baseline contract
    # effects.  The counterfactual changes only its liquidity component.
    _,baseline,_,_= _financial_strength_report(research_db=Path(research_db),production_db=Path(production_db),decision_at=decision_at)
    by_id={c["security_id"]:c for c in companies}
    def contract_counts(mode):
        counts=Counter()
        for old in baseline:
            ready=dict(old["components"]); discovered=by_id.get(old["security_id"])
            if mode!="existing" and discovered:
                liquid=direct(discovered,"current_assets") and direct(discovered,"current_liabilities") and discovered["selected"]["current_liabilities"]["value"]>0
                ready["liquidity"]="ready" if liquid else "unavailable"
            n=sum(v=="ready" for v in ready.values())
            counts["A"]+=all(v=="ready" for v in ready.values())
            counts["B"]+=ready["leverage"]==ready["liquidity"]=="ready"
            counts["C"]+=n>=2
            counts["D"]+=(old["debt_state"]=="debt_free_explicit" or (old["debt_state"]=="leveraged" and ready["leverage"]==ready["coverage"]=="ready"))
        return dict(counts)
    existing_contracts=contract_counts("existing"); alias_contracts=contract_counts("aliases")
    current=sum(c["components"]["liquidity"]=="ready" for c in baseline)
    report=_base("liquidity-contract-assessment",decision,immutable,CONTRACT_MAXIMUM_BYTES)
    report.update({"comparable_company_count":len(companies),"construction_assessments":metrics,"counterfactual_coverage":{"current_liquidity_readiness_under_existing_contract":current,"high_confidence_direct_aliases":hi,"documented_compatible_constructions":metrics["current_ratio"]["company_coverage"],"cash_direct_unrestricted_only":cash_direct,"cash_direct_or_compatible_construction":cash_construct,"contracts_A_to_D":{"existing":existing_contracts,"high_confidence_aliases":alias_contracts,"compatible_constructions":alias_contracts}},"contract_selected":None,"alias_activation":False})
    return _finish(report,CONTRACT_MAXIMUM_BYTES)

def company_preview(*,research_db,production_db,decision_at,qualified_symbol):
    decision,companies,immutable=_load(research_db,production_db,decision_at); match=[c for c in companies if c["qualified_symbol"]==qualified_symbol]
    if len(match)!=1:raise InvestmentResearchError("company unavailable or ambiguous")
    c=match[0]; observations=[x for x in c.pop("observations") if x["acceptance_or_withholding_reason"]!="post-decision evidence only"]
    grouped={str(k or "unsupported_concept"):_bounded(sorted(v,key=lambda x:(x["period_end"] or "",str(x["accession_or_filing_reference"])),reverse=True),3) for k,v in sorted(_group(observations,"proposed_canonical_field").items(),key=lambda z:str(z[0]))}
    citations=_bounded(sorted({str(x["accession_or_filing_reference"]) for x in observations if x["accession_or_filing_reference"]}),10)
    c["candidate_observations"]=grouped;c["citations"]=citations;c["observation_population"]={"total_count":len(observations),"returned_count":sum(x["returned_count"] for x in grouped.values()),"sample_limit":3,"truncated":len(observations)>sum(x["returned_count"] for x in grouped.values())}
    report=_base("liquidity-company-preview",decision,immutable,PREVIEW_MAXIMUM_BYTES);report["company"]=c
    return _finish(report,PREVIEW_MAXIMUM_BYTES)

aggregate_evidence_discovery=evidence_discovery
