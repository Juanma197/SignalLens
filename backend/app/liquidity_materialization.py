"""Authorized, offline, append-only canonical liquidity materialization.

The operation intentionally has no HTTP client.  Every input is a persisted SEC
fact and the production database is opened read-only only.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
import base64
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import socket
import uuid
from typing import Any

import duckdb

from .financial_strength import ZERO_OUTPUTS
from .liquidity_compatibility import TARGET_FIELDS, _snapshot
from .liquidity_measurement import CONCEPT_FIELDS, VALIDATOR_VERSION
from .liquidity_canonical_contract import (OPERATION_TYPE,OPERATION_CONTRACT_VERSION,
    canonical_available_at,canonical_evidence_key,operation_identity as _operation_identity,
    timestamp_text)
from .model_readiness import fingerprint
from .sec_ingestion import validate_paths

PLAN_TOKEN_VERSION = "lcm1"
PLAN_LIFETIME = timedelta(hours=24)
AUTHORIZATION_PHRASE = "I AUTHORIZE RESEARCH-ONLY CANONICAL LIQUIDITY MATERIALIZATION"
LOCK_STALE_AFTER = timedelta(minutes=30)
LOCK_NAME = "canonical-liquidity"
ERRORS = {
 "authorization":"LIQUIDITY_MATERIALIZATION_AUTHORIZATION_INVALID",
 "invalid":"LIQUIDITY_MATERIALIZATION_PLAN_INVALID",
 "expired":"LIQUIDITY_MATERIALIZATION_PLAN_EXPIRED",
 "fingerprint":"LIQUIDITY_MATERIALIZATION_FINGERPRINT_CHANGED",
 "decision":"LIQUIDITY_MATERIALIZATION_DECISION_MISMATCH",
 "contract":"LIQUIDITY_MATERIALIZATION_CONTRACT_MISMATCH",
 "reconciliation":"LIQUIDITY_MATERIALIZATION_RECONCILIATION_FAILED",
 "source":"LIQUIDITY_MATERIALIZATION_SOURCE_CHANGED",
 "conflict":"LIQUIDITY_MATERIALIZATION_CONFLICT",
 "capacity":"LIQUIDITY_MATERIALIZATION_CAPACITY_INSUFFICIENT",
 "locked":"LIQUIDITY_MATERIALIZATION_LOCKED",
 "schema":"LIQUIDITY_MATERIALIZATION_SCHEMA_INCOMPATIBLE",
 "internal":"LIQUIDITY_MATERIALIZATION_INTERNAL_ERROR",
}


class LiquidityMaterializationError(Exception):
    def __init__(self, code: str): self.code=code; super().__init__(code)


CONTRACT_DOCUMENT = {
 "canonical_fields": list(TARGET_FIELDS),
 "source_concept_mappings": {k:v for k,v in sorted(CONCEPT_FIELDS.items()) if v in TARGET_FIELDS},
 "validation_rules": ["exact-us-gaap-concept","instant","visible-at-decision","maximum-source-boundary",
   "not-stale-550-days","finite-nonnegative","USD-unit-and-currency","identity-scale-only"],
 "unit_currency_normalization": "USD unit and null/blank/USD redundant currency normalize to USD",
 "scale_rules": "absent, zero, or one means lossless identity factor 1",
 "timestamp_rules": "available_at=max(public_at,retrieved_at); never backdate",
 "evidence_key_algorithm": "sha256(canonical JSON of operation, source identity, field and decision)",
 "write_schema": "canonical_factor_evidence append-only revision plus liquidity materialization manifest",
}
CONTRACT_HASH = hashlib.sha256(json.dumps(CONTRACT_DOCUMENT,sort_keys=True,separators=(",",":")).encode()).hexdigest()


def operation_identity() -> dict[str,str]:
    return _operation_identity(CONTRACT_HASH,VALIDATOR_VERSION)


def _canonical(value: Any) -> bytes:
    return json.dumps(value,sort_keys=True,separators=(",",":"),default=str).encode()


def _utc(value: datetime | None=None) -> datetime:
    return (value or datetime.now(timezone.utc)).astimezone(timezone.utc)


def _token(identity: dict[str,Any]) -> str:
    raw=_canonical(identity); payload=base64.urlsafe_b64encode(raw).decode().rstrip("=")
    return f"{PLAN_TOKEN_VERSION}.{payload}.{hashlib.sha256(raw).hexdigest()}"


def _decode(token: str) -> dict[str,Any]:
    import re
    match=re.fullmatch(r"lcm1\.([A-Za-z0-9_-]+)\.([0-9a-f]{64})",token)
    if not match: raise LiquidityMaterializationError(ERRORS["invalid"])
    try:
        raw=base64.b64decode(match.group(1)+"="*(-len(match.group(1))%4),altchars=b"-_",validate=True)
        value=json.loads(raw)
    except (ValueError,UnicodeError,json.JSONDecodeError):
        raise LiquidityMaterializationError(ERRORS["invalid"]) from None
    if (not isinstance(value,dict) or _canonical(value)!=raw or
        hashlib.sha256(raw).hexdigest()!=match.group(2) or _token(value)!=token):
        raise LiquidityMaterializationError(ERRORS["invalid"])
    return value


def _capacity(path: Path) -> dict[str,Any]:
    size=path.stat().st_size
    # DuckDB may retain the original file and WAL while checkpointing.  Two full
    # copies plus 512 MiB is deliberately conservative for this bounded append.
    required=2*size+512*1024*1024
    try: available=shutil.disk_usage(path.parent).free
    except OSError: available=None
    return {"research_size_bytes":size,"available_filesystem_bytes":available,
      "required_free_space_bytes":required,"formula":"2 * research_size_bytes + 512 MiB",
      "capacity_sufficient":available is not None and available>=required,
      "backup_recommendation":"Create and verify an operator-managed offline backup before apply; this command never creates or deletes backups."}


def _proposal(research_db: Path, production_db: Path, decision_at: Any):
    decision,companies,_observations,accepted,reconciliation,immutability=_snapshot(research_db,production_db,decision_at)
    rows=[]
    for field in TARGET_FIELDS:
        for source_key,(company,obs) in accepted[field].items():
            source=obs["validation"]; available=max(datetime.fromisoformat(str(obs["public_at"])),datetime.fromisoformat(str(obs["retrieval_at"])))
            key=canonical_evidence_key(source_key,field,decision)
            rows.append({"evidence_key":key,"source_evidence_key":source_key,"security_id":company["security_id"],
              "qualified_symbol":company["qualified_symbol"],"canonical_field":field,"value":source["lossless_normalization_provenance"]["normalized_value"],
              "unit":"USD","currency":"USD","applied_scale_factor":source["applied_scale_factor"],"measurement_nature":"instant",
              "period_end":obs["period_end"],"instant_date":obs["period_end"],"fiscal_year":source["lossless_normalization_provenance"]["fiscal_year"],
              "fiscal_period":source["lossless_normalization_provenance"]["fiscal_period"],"accession":obs["accession_or_filing_reference"],
              "taxonomy":obs["taxonomy"],"original_concept":obs["exact_concept"],"original_value":source["lossless_normalization_provenance"]["original_value"],
              "original_unit":source["source_unit"],"original_currency":source["source_currency"],"original_scale":source["scale"],
              "filed_at":source["lossless_normalization_provenance"]["filed_at"],"public_at":obs["public_at"],"retrieved_at":obs["retrieval_at"],
              "available_at":timestamp_text(available),"ingestion_provenance":source["lossless_normalization_provenance"]["controlled_ingestion"],
              "normalization":source["lossless_normalization_provenance"]})
    rows.sort(key=lambda x:(x["security_id"],x["canonical_field"],x["source_evidence_key"]))
    return decision,rows,reconciliation,immutability


def plan(*,research_db,production_db,decision_at,now: datetime|None=None):
    research_db=Path(research_db); production_db=Path(production_db); validate_paths(research_db,production_db)
    schema=schema_compatibility(research_db)
    decision,rows,reconciliation,immutability=_proposal(research_db,production_db,decision_at)
    issued=_utc(now).replace(microsecond=0); expires=issued+PLAN_LIFETIME
    counts=dict(sorted(Counter(x["canonical_field"] for x in rows).items()))
    identity={"issued_at":issued.isoformat(),"expires_at":expires.isoformat(),"decision_at":decision.isoformat(),
      "fingerprints":immutability["before"],**operation_identity(),"evidence_keys":[x["evidence_key"] for x in rows],
      "evidence_keys_digest":hashlib.sha256(_canonical([x["evidence_key"] for x in rows])).hexdigest(),
      "counts_by_canonical_field":counts,"proposed_company_count":len({x["security_id"] for x in rows}),
      "proposed_observation_count":len(rows),"zero_blocker_reconciliation":all(x["reconciled"] for x in reconciliation.values())}
    token=_token(identity); blockers=[] if identity["zero_blocker_reconciliation"] else [ERRORS["reconciliation"]]
    if not schema["compatible"]: blockers.append(ERRORS["schema"])
    return {"command":"plan-liquidity-canonical-materialization","read_only":True,"status":"ready" if not blockers else "blocked",
      "plan_identifier":token,"plan_identity_sha256":hashlib.sha256(token.encode()).hexdigest(),**identity,"blockers":blockers,
      "deterministic_evidence_keys":identity["evidence_keys"],
      "reconciliation":reconciliation,"schema_compatibility":schema,"capacity":_capacity(research_db),"provider_request_count":0,"database_write_count":0,
      "aliases_automatically_activated":0,**ZERO_OUTPUTS}


def validate_plan(*,research_db,production_db,decision_at,plan_identifier,now: datetime|None=None):
    checked=_utc(now); base={"command":"validate-liquidity-canonical-materialization-plan","read_only":True,
      "valid":False,"reason_code":None,"checked_at":checked.isoformat(),"provider_request_count":0,"database_write_count":0,**ZERO_OUTPUTS}
    schema=schema_compatibility(Path(research_db))
    if not schema["compatible"]: return {**base,"reason_code":ERRORS["schema"],"schema_compatibility":schema}
    try: bound=_decode(plan_identifier)
    except LiquidityMaterializationError as exc: return {**base,"reason_code":exc.code}
    try: issued=datetime.fromisoformat(bound["issued_at"]); expires=datetime.fromisoformat(bound["expires_at"])
    except (KeyError,TypeError,ValueError): return {**base,"reason_code":ERRORS["invalid"]}
    if issued.tzinfo is None or expires.tzinfo is None or issued>checked or expires!=issued+PLAN_LIFETIME: return {**base,"reason_code":ERRORS["invalid"]}
    if checked>=expires: return {**base,"reason_code":ERRORS["expired"]}
    if any(bound.get(k)!=v for k,v in operation_identity().items()): return {**base,"reason_code":ERRORS["contract"]}
    expected_fingerprints=bound.get("fingerprints",{})
    actual_fingerprints={"research":str(fingerprint(Path(research_db))),"production":str(fingerprint(Path(production_db)))}
    retry_fingerprint=False; retry_run_id=None
    if actual_fingerprints!=expected_fingerprints and actual_fingerprints.get("production")==expected_fingerprints.get("production"):
      # A completed application necessarily changes research's bytes.  Permit an
      # exact retry only at its recorded post-commit fingerprint and only when
      # every token-bound revision is present.
      try:
        with duckdb.connect(str(research_db),read_only=True) as db:
          if {"liquidity_canonical_materialization_runs","liquidity_canonical_materialization_revisions"} <= _tables(db):
            identity=hashlib.sha256(plan_identifier.encode()).hexdigest()
            run=db.execute("""SELECT run_id,operation_type,operation_contract_version,operation_contract_hash,
              validator_version,decision_at,inserted_count,unchanged_count,conflict_count,counts_by_field,
              production_sha256_before,production_sha256_after FROM liquidity_canonical_materialization_runs
              WHERE plan_identity=? AND status='completed' ORDER BY finished_at DESC LIMIT 1""",[identity]).fetchone()
            rows=db.execute("""SELECT evidence_key,operation_type,operation_contract_version,
              operation_contract_hash,validator_version,materialization_run_id FROM
              liquidity_canonical_materialization_revisions WHERE plan_identity=? ORDER BY evidence_key""",[identity]).fetchall()
            keys=sorted(str(x) for x in bound.get("evidence_keys",[]))
            canonical_count=db.execute("""SELECT count(*) FROM canonical_factor_evidence c JOIN
              liquidity_canonical_materialization_revisions r ON r.evidence_key=c.evidence_key
              WHERE r.plan_identity=? AND c.source_fact_key=r.source_evidence_key
              AND c.security_id=r.security_id AND c.canonical_field=r.canonical_field
              AND c.value=r.normalized_value AND c.unit=r.canonical_unit AND c.currency=r.canonical_currency
              AND c.original_concept_or_field=r.original_concept
              AND c.accession_or_source_identifier=r.accession
              AND c.public_at=r.public_at AND c.retrieved_at=r.retrieved_at
              AND c.materialized_at=r.materialized_at
              AND c.available_at=greatest(r.public_at,r.retrieved_at,r.materialized_at)""",[identity]).fetchone()[0]
            source_count=db.execute("""SELECT count(*) FROM liquidity_canonical_materialization_revisions r
              JOIN sec_facts s ON s.fact_key=r.source_evidence_key AND s.security_id=r.security_id
              AND s.concept=r.original_concept AND s.value=r.original_value
              AND s.unit=r.original_unit AND coalesce(nullif(trim(s.currency),''),'USD')=
                coalesce(nullif(trim(r.original_currency),''),'USD')
              AND s.period_end=r.period_end AND s.accession_number=r.accession
              AND s.public_at=r.public_at AND s.retrieved_at=r.retrieved_at
              WHERE r.plan_identity=?""",[identity]).fetchone()[0]
            expected=operation_identity(); count=bound.get("proposed_observation_count")
            retry_fingerprint=bool(run and
              tuple(run[1:5])==(expected["operation_type"],expected["operation_contract_version"],expected["operation_contract_hash"],expected["validator_version"]) and
              timestamp_text(run[5])==timestamp_text(bound.get("decision_at"))==timestamp_text(decision_at) and
              run[6]==count and run[7]==0 and run[8]==0 and
              json.loads(run[9])==bound.get("counts_by_canonical_field") and
              run[10]==expected_fingerprints.get("production") and run[11]==actual_fingerprints["production"] and
              len(rows)==count==canonical_count==source_count and [x[0] for x in rows]==keys and
              hashlib.sha256(_canonical(bound.get("evidence_keys"))).hexdigest()==bound.get("evidence_keys_digest") and
              all(tuple(x[1:5])==(expected["operation_type"],expected["operation_contract_version"],expected["operation_contract_hash"],expected["validator_version"]) and x[5]==run[0] for x in rows))
            if retry_fingerprint: retry_run_id=run[0]
      except duckdb.Error: retry_fingerprint=False
    if actual_fingerprints!=expected_fingerprints and not retry_fingerprint: return {**base,"reason_code":ERRORS["fingerprint"]}
    if retry_fingerprint:
      return {**base,"valid":True,"reason_code":None,"idempotent_retry":True,"completed_run_id":retry_run_id,
        "issued_at":issued.isoformat(),"expires_at":expires.isoformat(),
        "remaining_validity_seconds":max(0,int((expires-checked).total_seconds())),"capacity":_capacity(Path(research_db)),**operation_identity()}
    try: current=plan(research_db=research_db,production_db=production_db,decision_at=decision_at,now=issued)
    except Exception: return {**base,"reason_code":ERRORS["invalid"]}
    current_bound=_decode(current["plan_identifier"])
    if bound.get("decision_at")!=current_bound.get("decision_at"): return {**base,"reason_code":ERRORS["decision"]}
    if bound.get("fingerprints")!=current_bound.get("fingerprints") and not retry_fingerprint: return {**base,"reason_code":ERRORS["fingerprint"]}
    if not current_bound.get("zero_blocker_reconciliation"): return {**base,"reason_code":ERRORS["reconciliation"]}
    compare=dict(current_bound)
    if retry_fingerprint: compare["fingerprints"]=bound["fingerprints"]
    if bound!=compare: return {**base,"reason_code":ERRORS["source"]}
    return {**base,"valid":True,"reason_code":None,"idempotent_retry":False,"issued_at":issued.isoformat(),"expires_at":expires.isoformat(),
      "remaining_validity_seconds":max(0,int((expires-checked).total_seconds())),"capacity":_capacity(Path(research_db)),**operation_identity()}


SCHEMA="""
CREATE TABLE IF NOT EXISTS liquidity_canonical_materialization_runs(run_id VARCHAR PRIMARY KEY, plan_identity VARCHAR NOT NULL, operation_type VARCHAR NOT NULL, operation_contract_version VARCHAR NOT NULL, operation_contract_hash VARCHAR NOT NULL, validator_version VARCHAR NOT NULL, decision_at TIMESTAMPTZ NOT NULL, started_at TIMESTAMPTZ NOT NULL, finished_at TIMESTAMPTZ, status VARCHAR NOT NULL, inserted_count INTEGER NOT NULL, unchanged_count INTEGER NOT NULL, conflict_count INTEGER NOT NULL, counts_by_field JSON NOT NULL, counts_by_company JSON NOT NULL, production_sha256_before VARCHAR NOT NULL, production_sha256_after VARCHAR, reason_code VARCHAR);
CREATE TABLE IF NOT EXISTS liquidity_canonical_materialization_lock(lock_name VARCHAR PRIMARY KEY, run_id VARCHAR NOT NULL, plan_identity VARCHAR NOT NULL, operation_type VARCHAR NOT NULL, started_at TIMESTAMPTZ NOT NULL, last_progress_at TIMESTAMPTZ NOT NULL, process_id BIGINT, host_name VARCHAR);
CREATE TABLE IF NOT EXISTS liquidity_canonical_materialization_failures(failure_id VARCHAR PRIMARY KEY, run_id VARCHAR NOT NULL, plan_identity VARCHAR NOT NULL, operation_type VARCHAR NOT NULL, operation_contract_version VARCHAR NOT NULL, operation_contract_hash VARCHAR NOT NULL, validator_version VARCHAR NOT NULL, occurred_at TIMESTAMPTZ NOT NULL, reason_code VARCHAR NOT NULL);
CREATE TABLE IF NOT EXISTS liquidity_canonical_materialization_revisions(evidence_key VARCHAR PRIMARY KEY, source_evidence_key VARCHAR NOT NULL, security_id VARCHAR NOT NULL, qualified_symbol VARCHAR NOT NULL, canonical_field VARCHAR NOT NULL, normalized_value DOUBLE NOT NULL, canonical_unit VARCHAR NOT NULL, canonical_currency VARCHAR NOT NULL, applied_scale_factor DOUBLE NOT NULL, measurement_nature VARCHAR NOT NULL, period_end DATE, instant_date DATE, fiscal_year INTEGER, fiscal_period VARCHAR, accession VARCHAR, taxonomy VARCHAR NOT NULL, original_concept VARCHAR NOT NULL, original_value DOUBLE NOT NULL, original_unit VARCHAR, original_currency VARCHAR, original_scale DOUBLE, filed_at VARCHAR, public_at TIMESTAMPTZ NOT NULL, retrieved_at TIMESTAMPTZ NOT NULL, available_at TIMESTAMPTZ NOT NULL, ingestion_provenance JSON NOT NULL, normalization_rationale JSON NOT NULL, validator_version VARCHAR NOT NULL, operation_type VARCHAR NOT NULL, operation_contract_version VARCHAR NOT NULL, operation_contract_hash VARCHAR NOT NULL, materialization_run_id VARCHAR NOT NULL, materialized_at TIMESTAMPTZ NOT NULL, plan_identity VARCHAR NOT NULL);
"""

CANONICAL_COLUMNS={
 "evidence_key":("VARCHAR",True,True),"security_id":("VARCHAR",True,False),"qualified_symbol":("VARCHAR",False,False),
 "canonical_field":("VARCHAR",True,False),"value":("DOUBLE",False,False),"unit":("VARCHAR",False,False),
 "currency":("VARCHAR",False,False),"period_start":("DATE",False,False),"period_end":("DATE",False,False),
 "instant_date":("DATE",False,False),"fiscal_period":("VARCHAR",False,False),"form":("VARCHAR",False,False),
 "accession_or_source_identifier":("VARCHAR",True,False),"public_at":("TIMESTAMP WITH TIME ZONE",True,False),
 "retrieved_at":("TIMESTAMP WITH TIME ZONE",True,False),"available_at":("TIMESTAMP WITH TIME ZONE",True,False),
 "materialized_at":("TIMESTAMP WITH TIME ZONE",True,False),"original_concept_or_field":("VARCHAR",True,False),
 "alias_contract_version":("VARCHAR",True,False),"sign_convention":("VARCHAR",True,False),
 "reliability_state":("VARCHAR",True,False),"withholding_reason":("VARCHAR",False,False),
 "provenance":("JSON",True,False),"source_fact_key":("VARCHAR",False,False),"lineage":("JSON",True,False)}

def schema_compatibility(path: Path) -> dict[str,Any]:
    """Read-only, bounded validation of the legacy canonical write contract."""
    issues=[]
    try:
      with duckdb.connect(str(path),read_only=True) as db:
        if "canonical_factor_evidence" not in _tables(db):
          return {"compatible":True,"table_state":"absent_will_create","issue_count":0,"issues":[]}
        info=db.execute("PRAGMA table_info('canonical_factor_evidence')").fetchall()
        actual={r[1]:(str(r[2]).upper(),bool(r[3]),bool(r[5]),r[4]) for r in info}
        for name,(kind,required,pk) in CANONICAL_COLUMNS.items():
          got=actual.get(name)
          if not got: issues.append({"column":name,"reason":"missing"}); continue
          if got[0]!=kind: issues.append({"column":name,"reason":"type_mismatch","expected":kind,"actual":got[0]})
          if got[1]!=required: issues.append({"column":name,"reason":"nullability_mismatch"})
          if got[2]!=pk: issues.append({"column":name,"reason":"primary_key_mismatch"})
        for name,(kind,notnull,_pk,default) in actual.items():
          if name not in CANONICAL_COLUMNS and notnull and default is None:
            issues.append({"column":name,"reason":"unsupported_required_column"})
    except duckdb.Error:
      issues=[{"reason":"schema_inspection_failed"}]
    return {"compatible":not issues,"table_state":"existing","issue_count":len(issues),"issues":issues[:10],"issues_truncated":len(issues)>10}

def _ensure_canonical(db):
    definitions=[]
    for name,(kind,required,pk) in CANONICAL_COLUMNS.items():
      definitions.append(f'"{name}" {kind}'+(" PRIMARY KEY" if pk else " NOT NULL" if required else ""))
    db.execute("CREATE TABLE IF NOT EXISTS canonical_factor_evidence("+",".join(definitions)+")")


def _tables(db): return {x[0] for x in db.execute("SHOW TABLES").fetchall()}
def _row_payload(row): return json.dumps(row,sort_keys=True,default=str,separators=(",",":"))


def apply(*,research_db,production_db,decision_at,plan_identifier,authorization,now: datetime|None=None,
          capacity_available_bytes: int|None=None,fail_after_schema: bool=False,
          fail_after_insert_preparation: bool=False):
    if authorization!=AUTHORIZATION_PHRASE: raise LiquidityMaterializationError(ERRORS["authorization"])
    research_db=Path(research_db); production_db=Path(production_db); validate_paths(research_db,production_db)
    timestamp=_utc(now); validation=validate_plan(research_db=research_db,production_db=production_db,
      decision_at=decision_at,plan_identifier=plan_identifier,now=timestamp)
    if not validation["valid"]: raise LiquidityMaterializationError(validation["reason_code"])
    if validation.get("idempotent_retry"):
      bound=_decode(plan_identifier); identity=hashlib.sha256(plan_identifier.encode()).hexdigest()
      with duckdb.connect(str(research_db),read_only=True) as db:
        company_counts=dict(db.execute("SELECT security_id,count(*) FROM liquidity_canonical_materialization_revisions WHERE plan_identity=? GROUP BY security_id ORDER BY security_id",[identity]).fetchall())
      production_fingerprint=fingerprint(production_db)
      return {"command":"apply-liquidity-canonical-materialization","status":"completed",
        "run_id":validation["completed_run_id"],"plan_identity":identity,"idempotent_retry":True,
        "inserted_count":0,"unchanged_count":bound["proposed_observation_count"],"conflict_count":0,
        "counts_by_canonical_field":bound["counts_by_canonical_field"],"counts_by_company":company_counts,
        "production_fingerprint_before":production_fingerprint,"production_fingerprint_after":production_fingerprint,
        "provider_request_count":0,**operation_identity(),**ZERO_OUTPUTS}
    cap=_capacity(research_db); available=cap["available_filesystem_bytes"] if capacity_available_bytes is None else capacity_available_bytes
    if available is None or available<cap["required_free_space_bytes"]: raise LiquidityMaterializationError(ERRORS["capacity"])
    # Every preflight above occurs before this first writable open.
    prod_before=fingerprint(production_db)
    with duckdb.connect(str(production_db),read_only=True) as p: p.execute("SELECT 1")
    _decision,rows,reconciliation,_immutability=_proposal(research_db,production_db,decision_at)
    if not all(x["reconciled"] for x in reconciliation.values()): raise LiquidityMaterializationError(ERRORS["reconciliation"])
    run_id=str(uuid.uuid4()); plan_identity=hashlib.sha256(plan_identifier.encode()).hexdigest()
    counts_field=dict(sorted(Counter(x["canonical_field"] for x in rows).items())); counts_company=dict(sorted(Counter(x["security_id"] for x in rows).items()))
    inserted=unchanged=conflicts=0
    try:
      with duckdb.connect(str(research_db)) as db:
        db.begin()
        try:
          db.execute(SCHEMA)
          _ensure_canonical(db)
          if fail_after_schema: raise RuntimeError("test rollback seam")
          revision_count=db.execute("SELECT count(*) FROM liquidity_canonical_materialization_revisions WHERE plan_identity=?",[plan_identity]).fetchone()[0]
          if revision_count==len(rows):
            canonical_count=db.execute("SELECT count(*) FROM canonical_factor_evidence WHERE evidence_key IN (SELECT evidence_key FROM liquidity_canonical_materialization_revisions WHERE plan_identity=?)",[plan_identity]).fetchone()[0]
            if canonical_count!=len(rows): raise LiquidityMaterializationError(ERRORS["conflict"])
            retry_run_id=db.execute("SELECT run_id FROM liquidity_canonical_materialization_runs WHERE plan_identity=? AND status='completed' ORDER BY finished_at DESC LIMIT 1",[plan_identity]).fetchone()[0]
            unchanged=len(rows); db.rollback()
            # An identical retry is a genuinely mutation-free observation of the
            # already committed run, rather than a second manifest write.
            return {"command":"apply-liquidity-canonical-materialization","status":"completed","run_id":retry_run_id,
              "plan_identity":plan_identity,"inserted_count":0,"unchanged_count":unchanged,"conflict_count":0,
              "counts_by_canonical_field":counts_field,"counts_by_company":counts_company,"production_fingerprint_before":prod_before,
              "production_fingerprint_after":fingerprint(production_db),"provider_request_count":0,**operation_identity(),**ZERO_OUTPUTS}
          if revision_count: raise LiquidityMaterializationError(ERRORS["conflict"])
          if db.execute("SELECT count(*) FROM liquidity_canonical_materialization_lock").fetchone()[0]: raise LiquidityMaterializationError(ERRORS["locked"])
          db.execute("INSERT INTO liquidity_canonical_materialization_lock VALUES (?,?,?,?,?,?,?,?)",
            [LOCK_NAME,run_id,plan_identity,OPERATION_TYPE,timestamp,timestamp,os.getpid(),socket.gethostname()])
          columns=["evidence_key","source_evidence_key","security_id","qualified_symbol","canonical_field","normalized_value","canonical_unit","canonical_currency","applied_scale_factor","measurement_nature","period_end","instant_date","fiscal_year","fiscal_period","accession","taxonomy","original_concept","original_value","original_unit","original_currency","original_scale","filed_at","public_at","retrieved_at","available_at","ingestion_provenance","normalization_rationale","validator_version","operation_type","operation_contract_version","operation_contract_hash","materialization_run_id","materialized_at","plan_identity"]
          for row in rows:
            values=[row["evidence_key"],row["source_evidence_key"],row["security_id"],row["qualified_symbol"],row["canonical_field"],row["value"],row["unit"],row["currency"],row["applied_scale_factor"],row["measurement_nature"],row["period_end"],row["instant_date"],row["fiscal_year"],row["fiscal_period"],row["accession"],row["taxonomy"],row["original_concept"],row["original_value"],row["original_unit"],row["original_currency"],row["original_scale"],row["filed_at"],row["public_at"],row["retrieved_at"],row["available_at"],json.dumps(row["ingestion_provenance"]),json.dumps(row["normalization"]),VALIDATOR_VERSION,OPERATION_TYPE,OPERATION_CONTRACT_VERSION,CONTRACT_HASH,run_id,timestamp,plan_identity]
            old=db.execute("SELECT "+",".join(columns[1:])+" FROM liquidity_canonical_materialization_revisions WHERE evidence_key=?",[row["evidence_key"]]).fetchone()
            if old:
              compatible=db.execute("""SELECT count(*) FROM liquidity_canonical_materialization_revisions
                WHERE evidence_key=? AND source_evidence_key=? AND security_id=? AND canonical_field=?
                AND normalized_value=? AND canonical_unit='USD' AND canonical_currency='USD'
                AND original_concept=? AND validator_version=? AND operation_contract_hash=?""",
                [row["evidence_key"],row["source_evidence_key"],row["security_id"],row["canonical_field"],row["value"],row["original_concept"],VALIDATOR_VERSION,CONTRACT_HASH]).fetchone()[0]
              if not compatible: conflicts+=1; raise LiquidityMaterializationError(ERRORS["conflict"])
              unchanged+=1
            else:
              provenance={"source_evidence_key":row["source_evidence_key"],"taxonomy":row["taxonomy"],"original_value":row["original_value"],
                "original_unit":row["original_unit"],"original_currency":row["original_currency"],"original_scale":row["original_scale"],
                "applied_scale_factor":row["applied_scale_factor"],"measurement_nature":row["measurement_nature"],"fiscal_year":row["fiscal_year"],
                "fiscal_period":row["fiscal_period"],"filed_at":row["filed_at"],"ingestion":row["ingestion_provenance"],
                "normalization_rationale":row["normalization"],"operation_type":OPERATION_TYPE,
                "operation_contract_version":OPERATION_CONTRACT_VERSION,"operation_contract_hash":CONTRACT_HASH,
                "validator_version":VALIDATOR_VERSION,"plan_identity":plan_identity,"materialization_run_id":run_id,
                "decision_at":timestamp_text(decision_at),"original_timestamps":{"filed_at":row["filed_at"],"public_at":timestamp_text(row["public_at"]),
                "retrieved_at":timestamp_text(row["retrieved_at"]),"available_at":timestamp_text(row["available_at"])}}
              lineage={"source_evidence_key":row["source_evidence_key"],"source_fact_key":row["source_evidence_key"],
                "operation_type":OPERATION_TYPE,"operation_contract_hash":CONTRACT_HASH,"validator_version":VALIDATOR_VERSION,
                "plan_identity":plan_identity,"materialization_run_id":run_id,"decision_at":timestamp_text(decision_at)}
              canonical={"evidence_key":row["evidence_key"],"security_id":row["security_id"],
                "qualified_symbol":row["qualified_symbol"],"canonical_field":row["canonical_field"],"value":row["value"],"unit":"USD","currency":"USD",
                "period_start":None,"period_end":row["period_end"],"instant_date":row["instant_date"],"fiscal_period":row["fiscal_period"],
                "form":row["ingestion_provenance"].get("form"),"accession_or_source_identifier":row["accession"],
                "public_at":row["public_at"],"retrieved_at":row["retrieved_at"],"available_at":canonical_available_at(public_at=row["public_at"],retrieved_at=row["retrieved_at"],materialized_at=timestamp),
                "materialized_at":timestamp,"original_concept_or_field":row["original_concept"],
                "alias_contract_version":OPERATION_CONTRACT_VERSION,"sign_convention":"reported_nonnegative",
                "reliability_state":"usable","withholding_reason":None,"provenance":json.dumps(provenance),
                "source_fact_key":row["source_evidence_key"],"lineage":json.dumps(lineage)}
              existing=db.execute("SELECT security_id,canonical_field,value,source_fact_key FROM canonical_factor_evidence WHERE evidence_key=?",
                [row["evidence_key"]]).fetchone()
              if existing:
                conflicts+=1; raise LiquidityMaterializationError(ERRORS["conflict"])
              db.execute("INSERT INTO liquidity_canonical_materialization_revisions VALUES ("+",".join("?" for _ in values)+")",values)
              names=list(canonical); db.execute("INSERT INTO canonical_factor_evidence ("+",".join(f'\"{x}\"' for x in names)+") VALUES ("+",".join("?" for _ in names)+")",[canonical[x] for x in names]); inserted+=1
              if fail_after_insert_preparation: raise RuntimeError("test partial insert rollback seam")
          prod_after=fingerprint(production_db)
          if prod_after!=prod_before: raise LiquidityMaterializationError(ERRORS["fingerprint"])
          db.execute("INSERT INTO liquidity_canonical_materialization_runs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [run_id,plan_identity,OPERATION_TYPE,OPERATION_CONTRACT_VERSION,CONTRACT_HASH,VALIDATOR_VERSION,decision_at,timestamp,timestamp,"completed",inserted,unchanged,conflicts,json.dumps(counts_field),json.dumps(counts_company),str(prod_before),str(prod_after),None])
          db.execute("DELETE FROM liquidity_canonical_materialization_lock WHERE lock_name=?",[LOCK_NAME]); db.commit()
        except Exception: db.rollback(); raise
    except LiquidityMaterializationError: raise
    except Exception as exc: raise LiquidityMaterializationError(ERRORS["internal"]) from exc
    return {"command":"apply-liquidity-canonical-materialization","status":"completed","run_id":run_id,
      "plan_identity":plan_identity,"idempotent_retry":False,"inserted_count":inserted,"unchanged_count":unchanged,"conflict_count":conflicts,
      "counts_by_canonical_field":counts_field,"counts_by_company":counts_company,"production_fingerprint_before":prod_before,
      "production_fingerprint_after":fingerprint(production_db),"provider_request_count":0,**operation_identity(),**ZERO_OUTPUTS}


def status(*,research_db,production_db,decision_at=None):
    research_db=Path(research_db); production_db=Path(production_db); validate_paths(research_db,production_db)
    before=(fingerprint(research_db),fingerprint(production_db)); latest=None; lock=None
    with duckdb.connect(str(research_db),read_only=True) as db:
      tables=_tables(db)
      if "liquidity_canonical_materialization_runs" in tables:
        cols=[x[1] for x in db.execute("PRAGMA table_info('liquidity_canonical_materialization_runs')").fetchall()]
        row=db.execute("SELECT * FROM liquidity_canonical_materialization_runs ORDER BY started_at DESC,run_id DESC LIMIT 1").fetchone()
        if row: latest=dict(zip(cols,row))
      if "liquidity_canonical_materialization_lock" in tables:
        row=db.execute("SELECT run_id,plan_identity,started_at,last_progress_at FROM liquidity_canonical_materialization_lock WHERE lock_name=?",[LOCK_NAME]).fetchone()
        if row: lock={"state":"stale" if _utc()-row[3]>LOCK_STALE_AFTER else "active","run_id":row[0],"plan_identity":row[1],"started_at":row[2],"last_progress_at":row[3]}
    after=(fingerprint(research_db),fingerprint(production_db))
    if before!=after: raise LiquidityMaterializationError(ERRORS["internal"])
    state="running" if lock else (latest["status"] if latest else "never-run")
    schema=schema_compatibility(research_db)
    return {"command":"liquidity-canonical-materialization-status","read_only":True,"state":state,"latest_run":latest,
      "lock":lock or {"state":"unlocked"},"capacity":_capacity(research_db),"production_fingerprint_evidence":before[1],
      "schema_compatibility":schema,"reason_code":None if schema["compatible"] else ERRORS["schema"],
      "post_materialization_readiness":"run verification reports" if state=="completed" else "not materialized",
      "provider_request_count":0,"database_write_count":0,**operation_identity(),**ZERO_OUTPUTS}


def recover_stale_lock(*,research_db,production_db,run_id,authorization,decision_at=None,now: datetime|None=None):
    if authorization!=AUTHORIZATION_PHRASE: raise LiquidityMaterializationError(ERRORS["authorization"])
    validate_paths(Path(research_db),Path(production_db)); timestamp=_utc(now)
    with duckdb.connect(str(research_db)) as db:
      db.begin()
      try:
        if "liquidity_canonical_materialization_lock" not in _tables(db): raise LiquidityMaterializationError(ERRORS["locked"])
        row=db.execute("SELECT run_id,operation_type,last_progress_at FROM liquidity_canonical_materialization_lock WHERE lock_name=?",[LOCK_NAME]).fetchone()
        if not row or row[0]!=run_id or row[1]!=OPERATION_TYPE or timestamp-row[2]<=LOCK_STALE_AFTER: raise LiquidityMaterializationError(ERRORS["locked"])
        db.execute("DELETE FROM liquidity_canonical_materialization_lock WHERE lock_name=? AND run_id=?",[LOCK_NAME,run_id]); db.commit()
      except Exception: db.rollback(); raise
    return {"command":"recover-stale-liquidity-canonical-materialization-lock","status":"recovered","run_id":run_id,"provider_request_count":0,**ZERO_OUTPUTS}
