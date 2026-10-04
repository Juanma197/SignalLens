"""Read-only reconciliation of raw SEC facts and canonical liquidity evidence.

This module deliberately reports evidence; it never promotes a raw fact, changes
an alias contract, or writes either database.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

import duckdb

from .financial_strength import (_aware, _bounded, _bounds, _companies, _rows,
    AGGREGATE_MAXIMUM_BYTES, ZERO_OUTPUTS, compact_utf8_size)
from .investment_research import InvestmentResearchError, TRACK_B_LABELS
from .model_readiness import fingerprint
from .sec_ingestion import validate_paths
from .liquidity_measurement import VALIDATOR_VERSION, evidence_identity, validate_measurement

FIELDS = {
    "current_assets": ("AssetsCurrent",),
    "current_liabilities": ("LiabilitiesCurrent",),
    "unrestricted_cash": ("CashAndCashEquivalentsAtCarryingValue",),
}
STANDARD_CONCEPTS = (
    "AccountsPayableCurrent", "AccountsReceivableNetCurrent", "Assets", "AssetsCurrent",
    "CashAndCashEquivalentsAtCarryingValue",
    "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents", "InventoryNet",
    "Liabilities", "LiabilitiesCurrent", "MarketableSecuritiesCurrent",
    "RestrictedCashAndCashEquivalents", "RestrictedCashAndCashEquivalentsCurrent",
    "ShortTermInvestments",
)
STATES = (
    "compatible_canonical_fact_visible", "compatible_raw_fact_not_materialized",
    "compatible_raw_fact_post_decision", "raw_fact_incompatible_unit",
    "raw_fact_incompatible_duration", "raw_fact_stale",
    "issuer_extension_review_required", "only_broader_aggregate_exists",
    "no_relevant_raw_or_canonical_fact", "issuer_identity_unresolved",
    "conflicting_visible_facts",
)
SAMPLE_LIMIT = 10
STALE_DAYS = 550

def _decision(value: Any) -> datetime:
    result = _aware(value)
    if result is None: raise InvestmentResearchError("timezone-aware decision timestamp required")
    return result

def _tables(db): return {row[0] for row in db.execute("SHOW TABLES").fetchall()}

def _columns(db, table):
    if table not in _tables(db): return []
    return [row[1] for row in db.execute(f"PRAGMA table_info('{table}')").fetchall()]

def completed_liquidity_retrieval_security_ids(research_db: Path) -> set[str]:
    """Find exact controlled checkpoints backed by both retained endpoint payloads."""
    with duckdb.connect(str(research_db),read_only=True) as db:
        required={"sec_liquidity_runs","sec_liquidity_checkpoints","sec_liquidity_raw_provenance"}
        if not required <= _tables(db): return set()
        identity={"run_id","lineage_id","plan_id","operation_type",
                  "operation_contract_version","concept_contract_hash"}
        if not identity <= set(_columns(db,"sec_liquidity_runs")): return set()
        if not identity|{"security_id","cik","status","transaction_succeeded"} <= set(_columns(db,"sec_liquidity_checkpoints")): return set()
        if not identity|{"security_id","cik","endpoint_class"} <= set(_columns(db,"sec_liquidity_raw_provenance")): return set()
        rows=db.execute("""SELECT c.security_id
          FROM sec_liquidity_checkpoints c
          JOIN sec_liquidity_runs r ON r.run_id=c.run_id AND r.lineage_id=c.lineage_id
            AND r.plan_id=c.plan_id AND r.operation_type=c.operation_type
            AND r.operation_contract_version=c.operation_contract_version
            AND r.concept_contract_hash=c.concept_contract_hash
          JOIN sec_liquidity_raw_provenance p ON p.run_id=c.run_id
            AND p.lineage_id=c.lineage_id AND p.plan_id=c.plan_id
            AND p.security_id=c.security_id AND p.cik=c.cik
            AND p.operation_type=c.operation_type
            AND p.operation_contract_version=c.operation_contract_version
            AND p.concept_contract_hash=c.concept_contract_hash
          WHERE c.operation_type='sec_liquidity_evidence_ingestion'
            AND c.status='completed' AND c.transaction_succeeded=true
          GROUP BY c.security_id HAVING count(DISTINCT p.endpoint_class)=2""").fetchall()
    return {str(row[0]) for row in rows}

def _source_catalog(r, p):
    definitions = (
      ("research", "sec_facts", "raw", "SEC companyfacts observations"),
      ("production", "sec_facts", "raw", "SEC companyfacts observations"),
      ("research", "canonical_factor_evidence", "canonical", "materialized investment evidence"),
      ("research", "sec_issuers", "normalized", "SEC issuer identity bridge"),
      ("production", "sec_issuers", "normalized", "SEC issuer identity bridge"),
      ("research", "sec_filings", "normalized", "SEC filing availability metadata"),
      ("production", "sec_filings", "normalized", "SEC filing availability metadata"),
    )
    conns={"research":r,"production":p}; out=[]
    groups={
      "issuer_security_identity":("security_id","qualified_symbol","cik","fact_key","evidence_key"),
      "taxonomy_concept":("taxonomy","taxonomy_version","concept","canonical_field","original_concept_or_field"),
      "measurement":("value","unit","currency","scale","period_start","period_end","instant_date","fiscal_period"),
      "availability":("filed_date","public_at","retrieved_at","available_at","materialized_at"),
    }
    for database,table,layer,purpose in definitions:
        cols=_columns(conns[database],table)
        if not cols: continue
        out.append({"database":database,"table_name":table,"evidence_layer":layer,"purpose":purpose,
          **{name:[x for x in candidates if x in cols] for name,candidates in groups.items()},
          "universe_join":"security_id = security_classification_evidence.security_id; no ticker or company-name inference" if "security_id" in cols else "CIK/accession metadata only; joins through sec_issuers CIK and security_id"})
    return out

def _raw_status(row, decision):
    result=validate_measurement(row,decision)
    if result["accepted"]: return "compatible"
    return {"visibility_timestamp_missing":"compatible_raw_fact_post_decision",
      "not_visible_at_decision":"compatible_raw_fact_post_decision",
      "measurement_nature_duration":"raw_fact_incompatible_duration",
      "period_stale":"raw_fact_stale"}.get(result["reason_code"],"raw_fact_incompatible_unit")

def _canonical_ok(row, field, decision):
    if row.get("canonical_field") != field: return False
    if row.get("reliability_state") not in (None,"usable"): return False
    public,retrieved,available=(_aware(row.get(k)) for k in ("public_at","retrieved_at","available_at"))
    if not public or not retrieved or not available or available != max(public,retrieved) or available>decision: return False
    interpreted=dict(row)
    interpreted.setdefault("taxonomy","us-gaap")
    interpreted.setdefault("concept",row.get("original_concept_or_field"))
    return _raw_status(interpreted,decision)=="compatible"

def _identity_unresolved(sid, raw, issuers):
    ciks={str(x.get("cik")) for x in raw if str(x.get("security_id"))==sid and x.get("cik")}
    mapped={str(x.get("cik")) for x in issuers if str(x.get("security_id"))==sid and x.get("cik")}
    owners=defaultdict(set)
    for x in issuers:
        if x.get("cik"): owners[str(x["cik"])].add(str(x.get("security_id")))
    return bool(len(ciks)>1 or (ciks and mapped and ciks!=mapped) or any(len(owners[c])>1 for c in ciks|mapped))

def _classify(sid, field, raw, canonical, issuers, decision):
    if _identity_unresolved(sid,raw,issuers): return "issuer_identity_unresolved",[]
    can=[x for x in canonical if str(x.get("security_id"))==sid and _canonical_ok(x,field,decision)]
    if can:
        vals={(x.get("period_end") or x.get("instant_date"),x.get("value"),x.get("unit"),x.get("currency")) for x in can}
        if len({v[1:] for v in vals if v[0]==max(z[0] for z in vals)})>1: return "conflicting_visible_facts",can
        return "compatible_canonical_fact_visible",can
    concepts=set(FIELDS[field]); rows=[x for x in raw if str(x.get("security_id"))==sid and x.get("concept") in concepts]
    by_state=defaultdict(list)
    for x in rows: by_state[_raw_status(x,decision)].append(x)
    compatible=by_state["compatible"]
    if compatible:
        latest=max(x.get("period_end") or x.get("instant_date") for x in compatible)
        values={float(x["value"]) for x in compatible if (x.get("period_end") or x.get("instant_date"))==latest}
        if len(values)>1:return "conflicting_visible_facts",compatible
        return "compatible_raw_fact_not_materialized",compatible
    for state in ("compatible_raw_fact_post_decision","raw_fact_incompatible_unit","raw_fact_incompatible_duration","raw_fact_stale"):
        if by_state[state]: return state,by_state[state]
    extensions=[x for x in raw if str(x.get("security_id"))==sid and str(x.get("taxonomy") or "").lower() not in {"us-gaap","us-gaap-2024","us-gaap-2025","us-gaap-2026"} and any(t in str(x.get("concept") or "").lower() for t in ("current","cash","liquid"))]
    if extensions:return "issuer_extension_review_required",extensions
    broader={"current_assets":{"Assets"},"current_liabilities":{"Liabilities"},"unrestricted_cash":{"CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"}}[field]
    broad=[x for x in raw if str(x.get("security_id"))==sid and x.get("concept") in broader]
    if any(_raw_status(x,decision)=="compatible" for x in broad):return "only_broader_aggregate_exists",broad
    return "no_relevant_raw_or_canonical_fact",[]

def _load(research_db,production_db,decision_at):
    decision=_decision(decision_at); research_db=Path(research_db); production_db=Path(production_db)
    validate_paths(research_db,production_db)
    before={"research":fingerprint(research_db),"production":fingerprint(production_db)}
    with duckdb.connect(str(research_db),read_only=True) as r, duckdb.connect(str(production_db),read_only=True) as p:
        canonical=_rows(r,"canonical_factor_evidence"); classifications=_rows(r,"security_classification_evidence")
        population=_companies(canonical,classifications,decision)
        raw=[]; issuers=[]
        for db in (r,p): raw+=_rows(db,"sec_facts"); issuers+=_rows(db,"sec_issuers")
        # Deduplicate research-sync copies by stable fact identity.
        unique={}
        for x in raw:
            key=x.get("fact_key") or (x.get("security_id"),x.get("cik"),x.get("taxonomy"),x.get("concept"),x.get("period_start"),x.get("period_end"),x.get("accession_number"),x.get("unit"))
            unique[str(key)]=x
        raw=list(unique.values()); sources=_source_catalog(r,p)
    after={"research":fingerprint(research_db),"production":fingerprint(production_db)}
    if before!=after: raise InvestmentResearchError("database changed during read-only audit")
    return decision,population,raw,canonical,issuers,sources,{"verified":True,"before":before,"after":after,"research_unchanged":True,"production_unchanged":True}

def _report(command,research_db,production_db,decision_at):
    decision,population,raw,canonical,issuers,sources,immutability=_load(research_db,production_db,decision_at)
    completed_retrieval=completed_liquidity_retrieval_security_ids(Path(research_db))
    counts={f:{s:0 for s in STATES} for f in FIELDS}; samples={f:{s:[] for s in STATES} for f in FIELDS}; details=[]
    for sid,symbol in population:
        states={}
        for field in FIELDS:
            state,evidence=_classify(sid,field,raw,canonical,issuers,decision); states[field]=state
            counts[field][state]+=1; samples[field][state].append(symbol)
        details.append({"security_id":sid,"qualified_symbol":symbol,"fields":states})
    pair_counts=Counter(state for d in details for state in d["fields"].values())
    # These are independent company-level issue flags, not a partition.  In
    # particular, one absent field is enough to require ingestion even when a
    # different field has broader evidence that requires accounting review.
    absent=[d for d in details if "no_relevant_raw_or_canonical_fact" in d["fields"].values()]
    ingestion=sorted(d["qualified_symbol"] for d in absent if d["security_id"] not in completed_retrieval)
    exhausted=sorted(d["qualified_symbol"] for d in absent if d["security_id"] in completed_retrieval)
    review_states={"issuer_extension_review_required","conflicting_visible_facts",
                   "raw_fact_incompatible_unit","raw_fact_incompatible_duration",
                   "raw_fact_stale","only_broader_aggregate_exists"}
    review=sorted(d["qualified_symbol"] for d in details if any(x in review_states for x in d["fields"].values()))
    identity=sorted(d["qualified_symbol"] for d in details if "issuer_identity_unresolved" in d["fields"].values())
    materialization=sorted(d["qualified_symbol"] for d in details if "compatible_raw_fact_not_materialized" in d["fields"].values())
    standard=Counter(str(x.get("concept")) for x in raw if x.get("concept") in STANDARD_CONCEPTS)
    extensions=Counter(str(x.get("concept")) for x in raw if str(x.get("taxonomy") or "").lower() not in {"us-gaap","us-gaap-2024","us-gaap-2025","us-gaap-2026"} and any(t in str(x.get("concept") or "").lower() for t in ("current","cash","liquid")))
    report={"command":command,"decision_at":decision.isoformat(),"read_only":True,"comparable_company_count":len(population),
      "liquidity_measurement_validator_version":VALIDATOR_VERSION,
      "consumer_semantics":"all unit, currency, scale, period and visibility decisions come from validate_measurement",
      "identity_join_rule":"exact security_id only; CIK consistency checked through sec_issuers; ticker and company name prohibited",
      "source_table_inventory":sources,"standard_concept_observation_counts":{x:standard[x] for x in STANDARD_CONCEPTS},
      "issuer_extension_concepts":_bounded([{"concept":k,"observation_count":v,"status":"review_required_not_activated"} for k,v in sorted(extensions.items())],SAMPLE_LIMIT),
      "field_state_counts":counts,"field_state_samples":{f:{s:_bounded(sorted(v),SAMPLE_LIMIT) for s,v in by.items()} for f,by in samples.items()},
      "company_field_reconciliation":{"raw_compatible_facts_already_materialized":pair_counts["compatible_canonical_fact_visible"],"raw_compatible_facts_omitted_from_materialization":pair_counts["compatible_raw_fact_not_materialized"],"raw_facts_withheld_correctly":sum(pair_counts[x] for x in ("compatible_raw_fact_post_decision","raw_fact_incompatible_unit","raw_fact_incompatible_duration","raw_fact_stale")),"concepts_absent_from_raw_storage":pair_counts["no_relevant_raw_or_canonical_fact"]},
      "company_reconciliation":{"semantics":"independent issue flags; counts may overlap",
        "requiring_new_sec_ingestion":{"count":len(ingestion),"samples":_bounded(ingestion,SAMPLE_LIMIT)},
        "completed_retrieval_concept_absent":{"count":len(exhausted),"samples":_bounded(exhausted,SAMPLE_LIMIT),
          "semantics":"both controlled SEC endpoints were retained successfully; another identical retrieval is not indicated"},
        "requiring_accounting_review":{"count":len(review),"samples":_bounded(review,SAMPLE_LIMIT)},
        "requiring_canonical_materialization":{"count":len(materialization),"samples":_bounded(materialization,SAMPLE_LIMIT)},
        "identity_failures":{"count":len(identity),"samples":_bounded(identity,SAMPLE_LIMIT)}},
      "materialization_defect":"compatible exact standard raw facts exist but no usable canonical row for the same security and canonical field" if pair_counts["compatible_raw_fact_not_materialized"] else None,
      "ingestion_gap":"exact required standard concept is absent and no completed controlled retrieval exists" if ingestion else None,
      "company_samples":_bounded(details,SAMPLE_LIMIT),"database_immutability":immutability,"bounds":_bounds(AGGREGATE_MAXIMUM_BYTES),"labels":TRACK_B_LABELS,**ZERO_OUTPUTS}
    report["compact_utf8_bytes"]=0
    for _ in range(3):report["compact_utf8_bytes"]=compact_utf8_size(report)
    if report["compact_utf8_bytes"]>AGGREGATE_MAXIMUM_BYTES:raise InvestmentResearchError("liquidity inventory exceeds size contract")
    return report

def raw_canonical_inventory(**kwargs): return _report("liquidity-raw-canonical-inventory",**kwargs)
def evidence_gap_assessment(**kwargs): return _report("liquidity-evidence-gap-assessment",**kwargs)
