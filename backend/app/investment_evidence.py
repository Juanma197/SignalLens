"""Authorized Milestone 38 research evidence materialization.

This is an evidence normalizer, not an investment model.  It deliberately has no
score, percentile, rank, candidate, selection, publication, or validation path.
Network transport is injected so tests remain offline and live SEC access can only
occur through the separately authorized, bounded entry point.
"""
from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import time
from typing import Any, Callable
import uuid

import duckdb

from .active_catalogue import select_active_catalogue
from .model_readiness import fingerprint
from .sec_ingestion import validate_paths, valid_user_agent
from .prospective_us_shadow import CONFIGURATION_HASH, STRATEGY_VERSION
from .investment_research import CONCEPT_ALIASES, InvestmentResearchError
from .canonical_units import (EPS_UNIT_RULE_ID, EPS_UNIT_RULE_VERSION,
    normalize_unit)

MATERIALIZE_AUTHORIZATION = "I AUTHORIZE RESEARCH-ONLY INVESTMENT EVIDENCE MATERIALIZATION"
ENRICH_AUTHORIZATION = "I AUTHORIZE RESEARCH-ONLY SEC INVESTMENT EVIDENCE ENRICHMENT"
ALIAS_VERSION = "milestone-37-audited-alias-contracts-1"
MAX_SAMPLES = 10
UNIT_REPAIR_AUTHORIZATION = "I AUTHORIZE RESEARCH-ONLY CANONICAL EPS UNIT REPAIR 1.0.0"

SCHEMA = """
CREATE TABLE IF NOT EXISTS investment_evidence_runs(
 run_id VARCHAR PRIMARY KEY, command VARCHAR NOT NULL, mode VARCHAR NOT NULL,
 started_at TIMESTAMPTZ NOT NULL, finished_at TIMESTAMPTZ, decision_at TIMESTAMPTZ NOT NULL,
 request_budget INTEGER NOT NULL, request_count INTEGER NOT NULL DEFAULT 0,
 selected_security_count INTEGER NOT NULL DEFAULT 0, inserted_count INTEGER NOT NULL DEFAULT 0,
 updated_count INTEGER NOT NULL DEFAULT 0, unchanged_count INTEGER NOT NULL DEFAULT 0,
 withheld_count INTEGER NOT NULL DEFAULT 0, failure_count INTEGER NOT NULL DEFAULT 0,
 stop_reason VARCHAR, research_database_identity VARCHAR NOT NULL,
 production_database_identity VARCHAR NOT NULL, configuration_version VARCHAR NOT NULL,
 configuration_hash VARCHAR NOT NULL, ranking_count INTEGER NOT NULL DEFAULT 0 CHECK(ranking_count=0),
 candidate_count INTEGER NOT NULL DEFAULT 0 CHECK(candidate_count=0),
 selection_count INTEGER NOT NULL DEFAULT 0 CHECK(selection_count=0),
 vintage_count INTEGER NOT NULL DEFAULT 0 CHECK(vintage_count=0),
 validation_observation_count INTEGER NOT NULL DEFAULT 0 CHECK(validation_observation_count=0));
CREATE TABLE IF NOT EXISTS security_classification_evidence(
 evidence_key VARCHAR PRIMARY KEY, security_id VARCHAR NOT NULL, qualified_symbol VARCHAR,
 security_type VARCHAR NOT NULL, classification_reason VARCHAR NOT NULL,
 evidence_source_family VARCHAR NOT NULL, source_record_identifier VARCHAR NOT NULL,
 cik VARCHAR, durable_identifier VARCHAR NOT NULL, effective_from TIMESTAMPTZ,
 effective_to TIMESTAMPTZ, public_at TIMESTAMPTZ NOT NULL, retrieved_at TIMESTAMPTZ NOT NULL,
 available_at TIMESTAMPTZ NOT NULL, materialized_at TIMESTAMPTZ NOT NULL,
 confidence_category VARCHAR NOT NULL, review_required BOOLEAN NOT NULL,
 provenance JSON NOT NULL, supersedes_evidence_key VARCHAR, is_current BOOLEAN NOT NULL,
 conflict_details JSON);
CREATE TABLE IF NOT EXISTS canonical_factor_evidence(
 evidence_key VARCHAR PRIMARY KEY, security_id VARCHAR NOT NULL, qualified_symbol VARCHAR,
 canonical_field VARCHAR NOT NULL, value DOUBLE, unit VARCHAR, currency VARCHAR,
 period_start DATE, period_end DATE, instant_date DATE, fiscal_period VARCHAR, form VARCHAR,
 accession_or_source_identifier VARCHAR NOT NULL, public_at TIMESTAMPTZ NOT NULL,
 retrieved_at TIMESTAMPTZ NOT NULL, available_at TIMESTAMPTZ NOT NULL,
 materialized_at TIMESTAMPTZ NOT NULL, original_concept_or_field VARCHAR NOT NULL,
 alias_contract_version VARCHAR NOT NULL, sign_convention VARCHAR NOT NULL,
 reliability_state VARCHAR NOT NULL, withholding_reason VARCHAR, provenance JSON NOT NULL,
 source_fact_key VARCHAR, lineage JSON NOT NULL);
CREATE TABLE IF NOT EXISTS corporate_action_coverage_evidence(
 evidence_key VARCHAR PRIMARY KEY, security_id VARCHAR NOT NULL, qualified_symbol VARCHAR,
 coverage_state VARCHAR NOT NULL, assessed_from DATE NOT NULL, assessed_to DATE NOT NULL,
 source_identifier VARCHAR NOT NULL, public_at TIMESTAMPTZ NOT NULL,
 retrieved_at TIMESTAMPTZ NOT NULL, available_at TIMESTAMPTZ NOT NULL,
 materialized_at TIMESTAMPTZ NOT NULL, provenance JSON NOT NULL);
CREATE TABLE IF NOT EXISTS investment_evidence_checkpoints(
 security_id VARCHAR NOT NULL, workflow VARCHAR NOT NULL, endpoint VARCHAR NOT NULL,
 status VARCHAR NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, last_run_id VARCHAR,
 updated_at TIMESTAMPTZ NOT NULL, retryable BOOLEAN NOT NULL,
 PRIMARY KEY(security_id,workflow,endpoint));
CREATE TABLE IF NOT EXISTS investment_evidence_failures(
 failure_id VARCHAR PRIMARY KEY, run_id VARCHAR NOT NULL, security_id VARCHAR,
 workflow VARCHAR NOT NULL, endpoint VARCHAR, reason_code VARCHAR NOT NULL,
 retryable BOOLEAN NOT NULL, attempt INTEGER NOT NULL, sanitized_message VARCHAR NOT NULL,
 occurred_at TIMESTAMPTZ NOT NULL, resolved_at TIMESTAMPTZ);
CREATE TABLE IF NOT EXISTS issuer_mapping_candidates(
 candidate_key VARCHAR PRIMARY KEY, security_id VARCHAR NOT NULL, qualified_symbol VARCHAR,
 cik VARCHAR NOT NULL, evidence_source VARCHAR NOT NULL, source_identifier VARCHAR NOT NULL,
 effective_from TIMESTAMPTZ NOT NULL, effective_to TIMESTAMPTZ,
 confidence_category VARCHAR NOT NULL, review_status VARCHAR NOT NULL,
 conflict_state VARCHAR NOT NULL, ticker_reuse_protected BOOLEAN NOT NULL,
 observed_at TIMESTAMPTZ NOT NULL);
"""

ZERO = {"recommendations":[], "candidates":[], "rankings":[], "paper_selections":[],
        "prospective_vintages":[], "validation_observations":[], "validation_credit":0}

def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise InvestmentResearchError("timezone-aware decision timestamp required")
    return value.astimezone(timezone.utc)

def _iso(value: Any) -> str:
    if isinstance(value, (datetime, date)): return value.isoformat()
    return str(value or "")

def _key(*values: Any) -> str:
    return hashlib.sha256("\x1f".join(_iso(v) for v in values).encode()).hexdigest()

def _tables(db): return {x[0] for x in db.execute("SHOW TABLES").fetchall()}
def _rows(db, table):
    if table not in _tables(db): return []
    cur=db.execute(f'SELECT * FROM "{table}"'); names=[x[0] for x in cur.description]
    return [dict(zip(names,row)) for row in cur.fetchall()]

def _aware(value: Any) -> datetime | None:
    if value is None: return None
    if isinstance(value,str): value=datetime.fromisoformat(value.replace("Z","+00:00"))
    if value.tzinfo is None: value=value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)

def availability(public_at: Any, retrieved_at: Any) -> datetime:
    public, retrieved = _aware(public_at), _aware(retrieved_at)
    if public is None or retrieved is None:
        raise ValueError("public_and_retrieval_timestamps_required")
    return max(public,retrieved)

def initialize_schema(db: duckdb.DuckDBPyConnection) -> None: db.execute(SCHEMA)

def _initialize_repair_schema(db: duckdb.DuckDBPyConnection) -> None:
    initialize_schema(db)
    db.execute("""CREATE TABLE IF NOT EXISTS canonical_unit_repair_runs(
      repair_run_id VARCHAR PRIMARY KEY, decision_at TIMESTAMPTZ NOT NULL,
      started_at TIMESTAMPTZ NOT NULL, finished_at TIMESTAMPTZ,
      research_database_identity VARCHAR NOT NULL, production_fingerprint VARCHAR NOT NULL,
      rule_version VARCHAR NOT NULL, planned_count INTEGER NOT NULL,
      inserted_count INTEGER NOT NULL DEFAULT 0, unchanged_count INTEGER NOT NULL DEFAULT 0,
      status VARCHAR NOT NULL, failure_reason VARCHAR)""")

def _catalogue(db, decision):
    if not {"security_master_retrievals","security_listings"} <= _tables(db): return []
    active=select_active_catalogue(db,as_of=decision.replace(tzinfo=None))
    if active is None: return []
    frame=active.listings
    return [{"security_id":str(x.security_id),"qualified_symbol":str(x.qualified_symbol)}
            for x in frame.loc[frame.region.eq("US") & frame.eligible].sort_values("qualified_symbol").itertuples(index=False)]

def _classification_candidates(db, security, decision):
    sid=security["security_id"]; out=[]
    # Explicit reviewed evidence has the highest semantic authority, while conflicts
    # are still refused rather than precedence-picked.
    for r in _rows(db,"reviewed_security_classifications"):
        if str(r.get("security_id"))==sid:
            out.append((str(r.get("security_type")),"explicit_review",str(r.get("review_id") or _key(r)),r))
    issuers=[r for r in _rows(db,"sec_issuers") if str(r.get("security_id"))==sid]
    ciks={str(r.get("cik")) for r in issuers}
    for r in _rows(db,"sec_entity_metadata"):
        if str(r.get("cik")) not in ciks: continue
        kind=r.get("security_type") or r.get("classification")
        if kind: out.append((str(kind),"sec_entity_metadata",str(r.get("source_identifier") or r.get("cik")),r))
    forms={str(r.get("form","")) for r in _rows(db,"sec_filings") if str(r.get("cik")) in ciks and _aware(r.get("public_at")) and availability(r.get("public_at"),r.get("retrieved_at"))<=decision}
    if forms & {"20-F","40-F","6-K"}: out.append(("foreign_issuer_or_adr","sec_filing_regime",",".join(sorted(forms)),issuers[-1] if issuers else {}))
    elif forms & {"10-K","10-Q"}: out.append(("us_operating_company","sec_filing_regime",",".join(sorted(forms)),issuers[-1] if issuers else {}))
    for r in _rows(db,"security_listings"):
        if str(r.get("security_id"))!=sid: continue
        raw=str(r.get("instrument_type") or r.get("security_type") or "").lower()
        mapping={"common stock":"us_operating_company","common_stock":"us_operating_company",
          "adr":"foreign_issuer_or_adr","fund":"investment_fund","etf":"investment_fund",
          "spac unit":"spac_unit","unit":"spac_unit","reit":"reit","bdc":"bdc"}
        if raw in mapping: out.append((mapping[raw],"catalogue_instrument_type",str(r.get("retrieval_id") or _key(r)),r))
    valid=[]
    for kind,family,source,r in out:
        pub=r.get("public_at") or r.get("mapped_at") or r.get("retrieved_at")
        ret=r.get("retrieved_at") or r.get("mapped_at")
        try: avail=availability(pub,ret)
        except ValueError: continue
        if avail<=decision: valid.append((kind,family,source,r,_aware(pub),_aware(ret),avail))
    return valid

_EXTRA_ALIASES={
 "cash":("CashAndCashEquivalentsAtCarryingValue","CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"),
 "short_term_investments":("ShortTermInvestments",), "current_debt":("LongTermDebtCurrent","ShortTermBorrowings","ShortTermDebtCurrent","CommercialPaper"),
 "non_current_debt":("LongTermDebtNoncurrent","LongTermBorrowings"),
 "interest_expense":("InterestExpenseNonOperating","InterestAndDebtExpense"),
 "net_income":("NetIncomeLossAvailableToCommonStockholdersBasic","NetIncomeLoss"),
 "basic_shares":("WeightedAverageNumberOfSharesOutstandingBasic",),
 "diluted_shares":("WeightedAverageNumberOfDilutedSharesOutstanding",),
}
def _aliases():
    result={k:{x["concept"] for x in v} for k,v in CONCEPT_ALIASES.items()}
    for k,v in _EXTRA_ALIASES.items(): result.setdefault(k,set()).update(v)
    return result

def _canonical_rows(db, security, decision, now):
    sid=security["security_id"]; aliases=_aliases(); facts=[r for r in _rows(db,"sec_facts") if str(r.get("security_id"))==sid]
    candidates=[]
    for field,concepts in aliases.items():
        for r in facts:
            if r.get("concept") not in concepts: continue
            try: avail=availability(r.get("public_at"),r.get("retrieved_at"))
            except ValueError: continue
            if avail>decision: continue
            unit=str(r.get("unit") or ""); currency=r.get("currency")
            expected="shares" if "shares" in field else "USD"
            period_start=r.get("period_start"); period_end=r.get("period_end")
            nature="duration" if period_start else "instant"
            normalization=normalize_unit(canonical_field=field,source_unit=unit,concept=str(r.get("concept")),
              currency=currency,scale_factor=1,period_nature=nature)
            reliable=(unit==expected or (expected=="USD" and unit in {"monetary",str(currency or "")}) or normalization is not None)
            canonical_unit=normalization.canonical_unit if normalization else unit
            sign="positive_outflow" if field=="capital_expenditure" else "reported_signed"
            key=_key(sid,field,r.get("fact_key") or r.get("accession_number"),period_start,period_end,r.get("public_at"),normalization.rule_version if normalization else "source-unit")
            candidates.append({"evidence_key":key,"security_id":sid,"qualified_symbol":security["qualified_symbol"],"canonical_field":field,
              "value":float(r["value"]) if reliable and r.get("value") is not None else None,"unit":canonical_unit,"source_unit":unit,"normalization":normalization.provenance() if normalization else None,"currency":currency,
              "period_start":period_start,"period_end":period_end,"instant_date":period_end if nature=="instant" else None,
              "fiscal_period":r.get("fiscal_period"),"form":r.get("form"),"source":str(r.get("accession_number") or r.get("fact_key")),
              "public_at":_aware(r.get("public_at")),"retrieved_at":_aware(r.get("retrieved_at")),"available_at":avail,"materialized_at":now,
              "concept":str(r.get("concept")),"sign":sign,"reliability":"usable" if reliable else "withheld",
              "withholding":None if reliable else "incompatible_units","source_fact_key":r.get("fact_key"),"period_nature":nature})
    # Latest visible revision per semantic period wins; distinct quarterly/YTD/annual
    # intervals remain distinct and are never summed here (preventing overlapping TTM).
    chosen={}
    for r in candidates:
        semantic=(r["canonical_field"],r["period_start"],r["period_end"],r["fiscal_period"],r["unit"],r["currency"])
        if semantic not in chosen or (r["public_at"],r["retrieved_at"])>(chosen[semantic]["public_at"],chosen[semantic]["retrieved_at"]): chosen[semantic]=r
    return list(chosen.values())

def _insert_factor(db,r):
    exists=db.execute("SELECT count(*) FROM canonical_factor_evidence WHERE evidence_key=?",[r["evidence_key"]]).fetchone()[0]
    if exists: return False
    provenance=json.dumps({"source_table":"sec_facts","source_fact_key":r["source_fact_key"],
      "source_unit":r.get("source_unit",r.get("unit")),"canonical_unit":r.get("unit"),
      "unit_normalization":r.get("normalization")},sort_keys=True)
    db.execute("""INSERT INTO canonical_factor_evidence VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",[
      r["evidence_key"],r["security_id"],r["qualified_symbol"],r["canonical_field"],r["value"],r["unit"],r["currency"],r["period_start"],r["period_end"],r["instant_date"],r["fiscal_period"],r["form"],r["source"],r["public_at"],r["retrieved_at"],r["available_at"],r["materialized_at"],r["concept"],ALIAS_VERSION,r["sign"],r["reliability"],r["withholding"],provenance,r["source_fact_key"],json.dumps({"latest_visible_revision":True,"period_nature":r["period_nature"]}),])
    return True

def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        try: value=json.loads(value)
        except (TypeError, ValueError): return {}
    return dict(value) if isinstance(value, dict) else {}

def _completed_repair_decisions(db) -> list[datetime]:
    if "canonical_unit_repair_runs" not in _tables(db): return []
    return [_aware(r[0]) for r in db.execute(
        "SELECT decision_at FROM canonical_unit_repair_runs "
        "WHERE status='completed' AND rule_version=?",[EPS_UNIT_RULE_VERSION]).fetchall()]

def _matching_repairs(db, decision: datetime) -> dict[str, dict[str, Any]]:
    """Index exact, visible canonical revisions by the source they supersede.

    Early 1.0.0 revisions did not serialize ``repair_decision_at``.  A completed
    1.0.0 run at or before the requested boundary is therefore the authoritative
    visibility fallback for those rows; this makes the deployed append-only data
    discoverable without rewriting it.
    """
    completed=[x for x in _completed_repair_decisions(db) if x and x<=decision]
    result={}
    for row in _rows(db,"canonical_factor_evidence"):
        provenance=_json_object(row.get("provenance")); lineage=_json_object(row.get("lineage"))
        normalization=_json_object(provenance.get("unit_normalization"))
        source=str(lineage.get("supersedes_evidence_key") or provenance.get("supersedes_evidence_key") or "")
        repair_at=_aware(lineage.get("repair_decision_at") or provenance.get("repair_decision_at"))
        visible=(repair_at<=decision) if repair_at else bool(completed)
        exact_rule=normalize_unit(canonical_field=str(row.get("canonical_field")),
          source_unit=str(normalization.get("source_unit") or ""),
          concept=str(row.get("original_concept_or_field") or ""),currency=row.get("currency"),
          scale_factor=normalization.get("scale_factor"),
          period_nature="duration" if row.get("period_start") is not None else "instant")
        if (source and visible and row.get("reliability_state")=="usable"
                and row.get("unit")=="USD/share"
                and exact_rule is not None
                and normalization.get("source_unit")=="USD/shares"
                and normalization.get("canonical_unit")=="USD/share"
                and normalization.get("normalization_rule_identifier")==EPS_UNIT_RULE_ID
                and normalization.get("rule_version")==EPS_UNIT_RULE_VERSION
                and normalization.get("scale_factor")==1
                and lineage.get("revision_type")=="canonical_unit_normalization"
                and lineage.get("rule_version")==EPS_UNIT_RULE_VERSION):
            result[source]=row
    return result

def _repair_candidates(db, decision):
    if "canonical_factor_evidence" not in _tables(db): return []
    result=[]; repaired=_matching_repairs(db,decision)
    for row in _rows(db,"canonical_factor_evidence"):
        if row.get("reliability_state")!="withheld" or row.get("withholding_reason")!="incompatible_units": continue
        if _aware(row.get("available_at")) is None or _aware(row.get("available_at"))>decision: continue
        if row["evidence_key"] in repaired: continue
        source_unit=str(row.get("unit") or "")
        rule=normalize_unit(canonical_field=str(row.get("canonical_field")),source_unit=source_unit,
          concept=str(row.get("original_concept_or_field")),currency=row.get("currency"),scale_factor=1,
          period_nature="duration" if row.get("period_start") is not None else "instant")
        if rule:
            # Milestone 38 intentionally nulled withheld values. Recover only the
            # exact immutable source fact identified by its durable fact key.
            if row.get("value") is None and row.get("source_fact_key") and "sec_facts" in _tables(db):
                source=db.execute("SELECT value FROM sec_facts WHERE fact_key=?",[row["source_fact_key"]]).fetchall()
                if len(source)==1: row={**row,"value":source[0][0]}
            if row.get("value") is not None: result.append((row,rule))
    return sorted(result,key=lambda x:x[0]["evidence_key"])

def _database_binding(path: Path) -> str:
    """A non-secret stable binding prevents a plan being applied to another path."""
    return hashlib.sha256(str(path.resolve()).encode()).hexdigest()

def plan_canonical_unit_repair(*,research_db:Path,production_db:Path,decision_at:datetime,max_samples:int=MAX_SAMPLES):
    decision=_utc(decision_at); validate_paths(research_db,production_db)
    before={"research":fingerprint(research_db),"production":fingerprint(production_db)}
    with duckdb.connect(str(production_db),read_only=True):
      with duckdb.connect(str(research_db),read_only=True) as db: candidates=_repair_candidates(db,decision)
    after={"research":fingerprint(research_db),"production":fingerprint(production_db)}
    if before!=after: raise InvestmentResearchError("database changed during read-only operation")
    return {"command":"plan-canonical-unit-repair","decision_at":decision.isoformat(),"rule_version":"1.0.0",
      "eligible_revision_count":len(candidates),"source_unit_counts":dict(Counter(x[0]["unit"] for x in candidates)),
      "canonical_unit":"USD/share","evidence_key_samples":[x[0]["evidence_key"] for x in candidates[:max(0,min(MAX_SAMPLES,max_samples))]],
      "research_database_identity":_database_binding(research_db),"production_fingerprint":before["production"],
      "read_only":True,"database_immutability":{"verified":True,"before":before,"after":after},**ZERO}

def apply_canonical_unit_repair(*,research_db:Path,production_db:Path,decision_at:datetime,authorization:str):
    if authorization!=UNIT_REPAIR_AUTHORIZATION: raise InvestmentResearchError("exact canonical unit repair authorization required")
    decision=_utc(decision_at); validate_paths(research_db,production_db)
    prod_before=fingerprint(production_db); binding=_database_binding(research_db); now=datetime.now(timezone.utc)
    run_id=_key("canonical-unit-repair",binding,decision,"1.0.0")
    inserted=unchanged=0
    with duckdb.connect(str(production_db),read_only=True): pass
    with duckdb.connect(str(research_db)) as db:
      db.execute("BEGIN")
      try:
        _initialize_repair_schema(db); candidates=_repair_candidates(db,decision)
        existing=db.execute("SELECT status,planned_count,inserted_count FROM canonical_unit_repair_runs WHERE repair_run_id=?",[run_id]).fetchone()
        if existing and existing[0]=="completed":
            db.execute("ROLLBACK"); return {"command":"apply-canonical-unit-repair","repair_run_id":run_id,"status":"completed","inserted":0,"unchanged":existing[1],"idempotent_retry":True,"production_unchanged":prod_before==fingerprint(production_db),**ZERO}
        db.execute("INSERT OR REPLACE INTO canonical_unit_repair_runs VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
          [run_id,decision,now,None,binding,json.dumps(prod_before,default=str),"1.0.0",len(candidates),0,0,"running",None])
        for old,rule in candidates:
          new_key=_key("canonical-unit-revision",binding,old["evidence_key"],rule.rule_version)
          if db.execute("SELECT count(*) FROM canonical_factor_evidence WHERE evidence_key=?",[new_key]).fetchone()[0]: unchanged+=1; continue
          provenance=old.get("provenance"); provenance=json.loads(provenance) if isinstance(provenance,str) else dict(provenance or {})
          provenance.update({"unit_normalization":rule.provenance(),"supersedes_evidence_key":old["evidence_key"],"repair_decision_at":decision.isoformat(),"historical_source_preserved":True,"research_database_identity":binding})
          lineage=old.get("lineage"); lineage=json.loads(lineage) if isinstance(lineage,str) else dict(lineage or {})
          lineage.update({"revision_type":"canonical_unit_normalization","supersedes_evidence_key":old["evidence_key"],"rule_version":rule.rule_version,"repair_decision_at":decision.isoformat()})
          values=[new_key,old["security_id"],old["qualified_symbol"],old["canonical_field"],old["value"],rule.canonical_unit,old["currency"],old["period_start"],old["period_end"],old["instant_date"],old["fiscal_period"],old["form"],old["accession_or_source_identifier"],old["public_at"],old["retrieved_at"],old["available_at"],now,old["original_concept_or_field"],old["alias_contract_version"],old["sign_convention"],"usable",None,json.dumps(provenance,sort_keys=True),old["source_fact_key"],json.dumps(lineage,sort_keys=True)]
          db.execute("INSERT INTO canonical_factor_evidence VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",values); inserted+=1
        db.execute("UPDATE canonical_unit_repair_runs SET finished_at=?,inserted_count=?,unchanged_count=?,status='completed' WHERE repair_run_id=?",[datetime.now(timezone.utc),inserted,unchanged,run_id]); db.execute("COMMIT")
      except Exception: db.execute("ROLLBACK"); raise
    if fingerprint(production_db)!=prod_before: raise InvestmentResearchError("production database changed")
    return {"command":"apply-canonical-unit-repair","repair_run_id":run_id,"status":"completed","inserted":inserted,"unchanged":unchanged,"idempotent_retry":False,"production_unchanged":True,**ZERO}

def canonical_unit_repair_status(*,research_db:Path,production_db:Path,decision_at:datetime,max_samples:int=MAX_SAMPLES):
    decision=_utc(decision_at); validate_paths(research_db,production_db); before={"research":fingerprint(research_db),"production":fingerprint(production_db)}
    with duckdb.connect(str(production_db),read_only=True):
      with duckdb.connect(str(research_db),read_only=True) as db:
        runs=_rows(db,"canonical_unit_repair_runs") if "canonical_unit_repair_runs" in _tables(db) else []
        pending=len(_repair_candidates(db,decision)); repairs=list(_matching_repairs(db,decision).values())
        repaired_counts=Counter((str(r.get("canonical_field")),
          _json_object(_json_object(r.get("provenance")).get("unit_normalization")).get("rule_version")) for r in repairs)
    after={"research":fingerprint(research_db),"production":fingerprint(production_db)}
    if before!=after: raise InvestmentResearchError("database changed during read-only operation")
    return {"command":"canonical-unit-repair-status","decision_at":decision.isoformat(),"pending_eligible_revisions":pending,
      "recent_runs":runs[-max(0,min(MAX_SAMPLES,max_samples)):],
      "visible_repaired_revision_counts":[{"canonical_field":field,"rule_version":version,"count":count}
        for (field,version),count in sorted(repaired_counts.items())],
      "read_only":True,"database_immutability":{"verified":True,"before":before,"after":after},**ZERO}

def _market_and_action_rows(db, security, decision, now):
    """Normalize price and explicit action coverage without treating absence as proof."""
    symbol=security["qualified_symbol"]; sid=security["security_id"]; factors=[]
    prices=[r for r in _rows(db,"global_price_observations") if r.get("qualified_symbol")==symbol
            and r.get("status")=="available" and _aware(r.get("retrieved_at"))<=decision
            and r.get("trading_date")<=decision.date()]
    if prices:
        p=max(prices,key=lambda x:(x["trading_date"],x["retrieved_at"])); value=p.get("adjusted_close")
        avail=_aware(p["retrieved_at"]); key=_key(sid,"decision_price",p["trading_date"],p.get("source"))
        factors.append({"evidence_key":key,"security_id":sid,"qualified_symbol":symbol,"canonical_field":"decision_price",
          "value":float(value) if value is not None else None,"unit":str(p.get("currency")),"currency":p.get("currency"),"period_start":None,"period_end":None,"instant_date":p["trading_date"],"fiscal_period":None,"form":None,
          "source":f"{p.get('source')}:{p['trading_date']}","public_at":avail,"retrieved_at":avail,"available_at":avail,"materialized_at":now,"concept":"adjusted_close","sign":"positive_price","reliability":"usable" if value is not None else "withheld","withholding":None if value is not None else "no_model_ready_price","source_fact_key":None,"period_nature":"instant"})
    actions=[r for r in _rows(db,"global_corporate_actions") if r.get("qualified_symbol")==symbol and r.get("ex_date")<=decision.date() and _aware(r.get("retrieved_at"))<=decision]
    checkpoints=[r for r in _rows(db,"eodhd_ingestion_checkpoints") if r.get("qualified_symbol")==symbol and r.get("stage") in {"corporate_actions","actions"} and r.get("status")=="completed" and _aware(r.get("updated_at"))<=decision]
    if actions: state="action_present"; source=",".join(sorted({_iso(x.get("source")) for x in actions})); retrieved=max(_aware(x["retrieved_at"]) for x in actions); start=min(x["ex_date"] for x in actions)
    elif checkpoints: state="verified_no_action"; source="eodhd_ingestion_checkpoint"; retrieved=max(_aware(x["updated_at"]) for x in checkpoints); start=min((_aware(x["updated_at"]).date() for x in checkpoints),default=decision.date())
    else: state="coverage_missing"; source="none"; retrieved=decision; start=decision.date()
    key=_key(sid,state,start,decision.date(),source)
    coverage=[key,sid,symbol,state,start,decision.date(),source,retrieved,retrieved,retrieved,now,json.dumps({"event_count":len(actions),"absence_not_assumed":True})]
    return factors,coverage

def plan_materialization(*,research_db:Path,production_db:Path,decision_at:datetime,max_samples:int=MAX_SAMPLES):
    decision=_utc(decision_at); max_samples=max(0,min(MAX_SAMPLES,int(max_samples))); validate_paths(research_db,production_db)
    before={"research":fingerprint(research_db),"production":fingerprint(production_db)}
    with duckdb.connect(str(production_db),read_only=True):
      with duckdb.connect(str(research_db),read_only=True) as db:
        securities=_catalogue(db,decision); resolvable=[]; unavailable=[]; factors=0
        for sec in securities:
            if _classification_candidates(db,sec,decision): resolvable.append(sec["qualified_symbol"])
            else: unavailable.append(sec["qualified_symbol"])
            factors+=len(_canonical_rows(db,sec,decision,decision))
    after={"research":fingerprint(research_db),"production":fingerprint(production_db)}
    if before!=after: raise InvestmentResearchError("database changed during read-only operation")
    return {"command":"plan-investment-evidence-materialization","decision_at":decision.isoformat(),"selected_security_count":len(securities),
      "classification_resolvable_count":len(resolvable),"classification_unavailable_count":len(unavailable),"canonical_factor_row_count":factors,
      "resolvable_symbol_samples":resolvable[:max_samples],"unavailable_symbol_samples":unavailable[:max_samples],"read_only":True,
      "database_immutability":{"verified":True,"before":before,"after":after},**ZERO}

def materialize_stored(*,research_db:Path,production_db:Path,decision_at:datetime,authorization:str):
    if authorization!=MATERIALIZE_AUTHORIZATION: raise InvestmentResearchError("exact materialization authorization required")
    decision=_utc(decision_at); validate_paths(research_db,production_db)
    # Fingerprint database files only while no DuckDB connection to that file is
    # open.  Windows denies a second filesystem handle while DuckDB owns its
    # writable handle, even though POSIX hosts commonly allow it.
    prod_before=fingerprint(production_db)
    research_before=fingerprint(research_db)
    now=datetime.now(timezone.utc); run_id=str(uuid.uuid4())
    with duckdb.connect(str(production_db),read_only=True) as p: p.execute("SELECT 1")
    inserted=unchanged=withheld=failures=0
    with duckdb.connect(str(research_db)) as db:
      db.execute("BEGIN")
      try:
        initialize_schema(db); securities=_catalogue(db,decision)
        db.execute("""INSERT INTO investment_evidence_runs(run_id,command,mode,started_at,decision_at,request_budget,research_database_identity,production_database_identity,configuration_version,configuration_hash,selected_security_count) VALUES (?,?,?,?,?,0,?,?,?,?,?)""",
          [run_id,"materialize-stored-investment-evidence","stored-evidence",now,decision,json.dumps(research_before,default=str),json.dumps(prod_before,default=str),STRATEGY_VERSION,CONFIGURATION_HASH,len(securities)])
        for sec in securities:
          try:
            cs=_classification_candidates(db,sec,decision); kinds={x[0] for x in cs}
            if len(kinds)==1:
              kind=next(iter(kinds)); best=cs[0]; key=_key(sec["security_id"],kind,best[1],best[2],best[6]); conflict=None
              row=[key,sec["security_id"],sec["qualified_symbol"],kind,"concordant_authoritative_evidence",best[1],best[2],str(best[3].get("cik") or "") or None,str(best[3].get("cik") or sec["security_id"]),best[3].get("effective_from"),best[3].get("effective_to"),best[4],best[5],best[6],now,"high",False,json.dumps({"hierarchy":["sec_entity_metadata","sec_filing_regime","catalogue_instrument_type","explicit_review"],"source":best[1]}),None,True,conflict]
            else:
              reason="conflicting_sources" if kinds else "classification_evidence_unavailable"; key=_key(sec["security_id"],reason,decision)
              row=[key,sec["security_id"],sec["qualified_symbol"],"classification_unavailable",reason,"stored_evidence_hierarchy","none",None,sec["security_id"],None,None,decision,decision,decision,now,"unavailable",bool(kinds),json.dumps({"sources":[x[1] for x in cs]}),None,True,json.dumps({"classifications":sorted(kinds)}) if kinds else None]; withheld+=1
            if db.execute("SELECT count(*) FROM security_classification_evidence WHERE evidence_key=?",[key]).fetchone()[0]: unchanged+=1
            else: db.execute("INSERT INTO security_classification_evidence VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",row); inserted+=1
            normalized=_canonical_rows(db,sec,decision,now)
            market,coverage=_market_and_action_rows(db,sec,decision,now)
            normalized.extend(market)
            latest={}
            for factor in normalized:
              if factor["reliability"]=="usable" and (factor["canonical_field"] not in latest or factor["available_at"]>latest[factor["canonical_field"]]["available_at"]): latest[factor["canonical_field"]]=factor
            def derived(field,value,components,unit,sign="derived"):
              available=max(x["available_at"] for x in components); source="derived:"+",".join(x["evidence_key"] for x in components)
              return {"evidence_key":_key(sec["security_id"],field,source),"security_id":sec["security_id"],"qualified_symbol":sec["qualified_symbol"],"canonical_field":field,"value":value,"unit":unit,"currency":components[0].get("currency"),"period_start":None,"period_end":None,"instant_date":decision.date(),"fiscal_period":None,"form":None,"source":source,"public_at":max(x["public_at"] for x in components),"retrieved_at":max(x["retrieved_at"] for x in components),"available_at":available,"materialized_at":now,"concept":field,"sign":sign,"reliability":"usable","withholding":None,"source_fact_key":None,"period_nature":"derived"}
            if {"decision_price","diluted_shares"}<=latest.keys(): normalized.append(derived("market_capitalisation",latest["decision_price"]["value"]*latest["diluted_shares"]["value"],[latest["decision_price"],latest["diluted_shares"]],latest["decision_price"]["unit"]))
            if {"operating_cash_flow","capital_expenditure"}<=latest.keys(): normalized.append(derived("free_cash_flow",latest["operating_cash_flow"]["value"]-latest["capital_expenditure"]["value"],[latest["operating_cash_flow"],latest["capital_expenditure"]],latest["operating_cash_flow"]["unit"],"operating_cash_flow_minus_positive_capex_outflow"))
            for factor in normalized:
              if _insert_factor(db,factor): inserted+=1
              else: unchanged+=1
              withheld+=factor["reliability"]!="usable"
            if db.execute("SELECT count(*) FROM corporate_action_coverage_evidence WHERE evidence_key=?",[coverage[0]]).fetchone()[0]: unchanged+=1
            else: db.execute("INSERT INTO corporate_action_coverage_evidence VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",coverage); inserted+=1
            withheld+=coverage[3] in {"coverage_missing","unresolved_action"}
            db.execute("INSERT OR REPLACE INTO investment_evidence_checkpoints VALUES (?,?,?,?,?,?,?,?)",[sec["security_id"],"stored_evidence_materialization","stored","completed",1,run_id,now,False])
          except Exception:
            failures+=1
            db.execute("INSERT INTO investment_evidence_failures VALUES (?,?,?,?,?,?,?,?,?,?,?)",[str(uuid.uuid4()),run_id,sec["security_id"],"stored_evidence_materialization","stored","materialization_failed",True,1,"stored evidence could not be normalized; details redacted",now,None])
            db.execute("INSERT OR REPLACE INTO investment_evidence_checkpoints VALUES (?,?,?,?,?,?,?,?)",[sec["security_id"],"stored_evidence_materialization","stored","retryable",1,run_id,now,True])
        finished=datetime.now(timezone.utc)
        db.execute("UPDATE investment_evidence_runs SET finished_at=?,inserted_count=?,unchanged_count=?,withheld_count=?,failure_count=?,stop_reason=? WHERE run_id=?",[finished,inserted,unchanged,withheld,failures,"completed" if not failures else "completed_with_failures",run_id])
        db.execute("COMMIT")
      except Exception: db.execute("ROLLBACK"); raise
    prod_after=fingerprint(production_db)
    if prod_before!=prod_after: raise InvestmentResearchError("production database changed")
    return {"command":"materialize-stored-investment-evidence","run_id":run_id,"inserted":inserted,"unchanged":unchanged,"withheld":withheld,"failures":failures,"production_unchanged":True,**ZERO}

def status(*,research_db:Path,production_db:Path,decision_at:datetime,max_samples:int=MAX_SAMPLES):
    decision=_utc(decision_at); validate_paths(research_db,production_db); before={"research":fingerprint(research_db),"production":fingerprint(production_db)}
    with duckdb.connect(str(production_db),read_only=True):
      with duckdb.connect(str(research_db),read_only=True) as db:
       tables=_tables(db)
       classifications=Counter(); withheld=Counter(); checkpoints=Counter(); runs=[]
       if "security_classification_evidence" in tables:
        for r in _rows(db,"security_classification_evidence"):
         if _aware(r.get("available_at"))<=decision and r.get("is_current"): classifications[str(r.get("security_type"))]+=1
       factors=Counter();
       if "canonical_factor_evidence" in tables:
        for r in _rows(db,"canonical_factor_evidence"):
         if _aware(r.get("available_at"))<=decision:
          factors[str(r.get("canonical_field"))]+=1
          if r.get("withholding_reason"): withheld[str(r["withholding_reason"])]+=1
       if "investment_evidence_checkpoints" in tables: checkpoints.update(str(r.get("status")) for r in _rows(db,"investment_evidence_checkpoints"))
       if "investment_evidence_runs" in tables: runs=_rows(db,"investment_evidence_runs")[-max(0,min(MAX_SAMPLES,int(max_samples))):]
    after={"research":fingerprint(research_db),"production":fingerprint(production_db)}
    if before!=after: raise InvestmentResearchError("database changed during read-only operation")
    return {"command":"investment-evidence-materialization-status","classification_counts":dict(sorted(classifications.items())),"comparable_universe_count":classifications.get("us_operating_company",0),"classification_conflicts":classifications.get("classification_unavailable",0),"canonical_factor_counts":dict(sorted(factors.items())),"withheld_counts_by_reason":dict(sorted(withheld.items())),"checkpoint_states":dict(sorted(checkpoints.items())),"recent_runs":runs,"production_unchanged":True,"database_immutability":{"verified":True,"before":before,"after":after},**ZERO}

def enrichment_plan(*,research_db:Path,production_db:Path,decision_at:datetime,max_samples:int=MAX_SAMPLES,refresh:bool=False):
    decision=_utc(decision_at); validate_paths(research_db,production_db); before={"research":fingerprint(research_db),"production":fingerprint(production_db)}
    with duckdb.connect(str(production_db),read_only=True):
      with duckdb.connect(str(research_db),read_only=True) as db:
       securities=_catalogue(db,decision); completed={str(r.get("security_id")) for r in _rows(db,"investment_evidence_checkpoints") if r.get("workflow")=="sec_enrichment" and r.get("status")=="completed"} if "investment_evidence_checkpoints" in _tables(db) else set()
       mapped={str(r.get("security_id")) for r in _rows(db,"sec_issuers")}; result={"resolvable_from_stored_evidence":[],"requires_sec_submissions_metadata":[],"requires_sec_company_facts_refresh":[],"requires_mapping_review":[],"requires_catalogue_instrument_type_review":[],"structurally_excluded":[]}; requests=Counter()
       for sec in securities:
        if sec["security_id"] in completed and not refresh: continue
        cs=_classification_candidates(db,sec,decision)
        if cs: result["resolvable_from_stored_evidence"].append(sec["qualified_symbol"])
        elif sec["security_id"] in mapped: result["requires_sec_submissions_metadata"].append(sec["qualified_symbol"]); requests["sec_submissions"]+=1
        else: result["requires_mapping_review"].append(sec["qualified_symbol"])
        if not _canonical_rows(db,sec,decision,decision) and sec["security_id"] in mapped: result["requires_sec_company_facts_refresh"].append(sec["qualified_symbol"]); requests["sec_companyfacts"]+=1
       bounded={k:{"count":len(v),"symbol_samples":v[:max(0,min(MAX_SAMPLES,int(max_samples)))]} for k,v in result.items()}
    after={"research":fingerprint(research_db),"production":fingerprint(production_db)}
    if before!=after: raise InvestmentResearchError("database changed during read-only operation")
    return {"command":"plan-investment-evidence-enrichment",**bounded,"estimated_requests_by_endpoint":dict(requests),"estimated_request_count":sum(requests.values()),"request_adds":{"sec_submissions":"classification evidence","sec_companyfacts":"factor evidence"},"read_only":True,"database_immutability":{"verified":True,"before":before,"after":after},**ZERO}

def enrich_from_sec(*,research_db:Path,production_db:Path,decision_at:datetime,authorization:str,user_agent:str,
 request_budget:int,runtime_budget_seconds:float=60,attempt_limit:int=2,pacing_seconds:float=.12,timeout_seconds:float=20,max_response_bytes:int=5_000_000,max_issuers:int|None=None,refresh:bool=False,transport:Callable|None=None):
    """Bounded SEC capability.  A transport must be explicit; development never calls it."""
    if authorization!=ENRICH_AUTHORIZATION: raise InvestmentResearchError("exact SEC enrichment authorization required")
    if not valid_user_agent(user_agent): raise InvestmentResearchError("compliant SEC user-agent required")
    if not 1<=request_budget<=205 or not 1<=runtime_budget_seconds<=3600 or not 1<=attempt_limit<=3 or not .1<=pacing_seconds<=2 or not 1<=timeout_seconds<=60 or not 1<=max_response_bytes<=10_000_000: raise ValueError("enrichment limits out of bounds")
    if transport is None: raise InvestmentResearchError("SEC transport must be explicitly configured")
    # Transport integration intentionally accepts only parsed submissions/companyfacts
    # metadata and never persists response bodies or filing text.
    plan=enrichment_plan(research_db=research_db,production_db=production_db,decision_at=decision_at,refresh=refresh)
    issuers=sorted(set(plan["requires_sec_submissions_metadata"]["symbol_samples"]+plan["requires_sec_company_facts_refresh"]["symbol_samples"]))
    if max_issuers is not None: issuers=issuers[:max(0,max_issuers)]
    return {"command":"enrich-investment-evidence-from-sec","status":"capability_ready","planned_issuers":issuers,"request_budget":request_budget,"request_count":0,"stop_reason":"transport_execution_not_started","raw_bodies_stored":False,"production_unchanged":True,**ZERO}
