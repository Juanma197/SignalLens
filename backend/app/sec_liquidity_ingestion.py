"""Controlled, research-only SEC liquidity evidence ingestion.

The entry points in this module are deliberately separate from the older broad
fundamentals ingester.  A caller must reconstruct the database-bound plan and
authorize this exact operation before any schema or lock write occurs.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import socket
import time
import uuid
from typing import Any, Callable
from urllib.parse import urlparse

import duckdb
import httpx

from .financial_strength import ZERO_OUTPUTS
from .liquidity_inventory import STANDARD_CONCEPTS, raw_canonical_inventory
from .model_readiness import fingerprint
from .sec_capability import SEC_FACTS, SEC_SUBMISSIONS, valid_user_agent
from .sec_ingestion import validate_paths
from .sec_liquidity_plan import plan_sec_liquidity_evidence_ingestion
from .sec_liquidity_contract import (CONCEPT_CONTRACT_HASH, OPERATION_CONTRACT_VERSION,
    OPERATION_TYPE, PARSER_VERSION, operation_identity)

AUTHORIZATION_PHRASE = "I AUTHORIZE RESEARCH-ONLY SEC LIQUIDITY EVIDENCE INGESTION"
PARSER_CONTRACT_VERSION = PARSER_VERSION
ALLOWED_HOST = "data.sec.gov"
MAX_BUDGET = 205
LOCK_STALE_AFTER = timedelta(minutes=30)
SAMPLE_LIMIT = 10


def _utc(value: datetime | None = None) -> datetime:
    return (value or datetime.now(timezone.utc)).astimezone(timezone.utc)


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _tables(db: duckdb.DuckDBPyConnection) -> set[str]:
    return {str(row[0]) for row in db.execute("SHOW TABLES").fetchall()}


def _columns(db: duckdb.DuckDBPyConnection, table: str) -> set[str]:
    return {str(row[1]) for row in db.execute(f"PRAGMA table_info('{table}')").fetchall()} if table in _tables(db) else set()


SCHEMA = """
CREATE TABLE IF NOT EXISTS sec_liquidity_runs(run_id VARCHAR PRIMARY KEY, operation_type VARCHAR NOT NULL, operation_contract_version VARCHAR NOT NULL, concept_contract_hash VARCHAR NOT NULL, lineage_id VARCHAR NOT NULL, plan_id VARCHAR NOT NULL, decision_at TIMESTAMPTZ NOT NULL, started_at TIMESTAMPTZ NOT NULL, finished_at TIMESTAMPTZ, status VARCHAR NOT NULL, request_budget INTEGER NOT NULL, request_count INTEGER NOT NULL DEFAULT 0, inserted_count INTEGER NOT NULL DEFAULT 0, unchanged_count INTEGER NOT NULL DEFAULT 0, stop_reason VARCHAR, production_sha256_before VARCHAR NOT NULL, production_sha256_after VARCHAR);
CREATE TABLE IF NOT EXISTS sec_liquidity_checkpoints(lineage_id VARCHAR NOT NULL, security_id VARCHAR NOT NULL, cik VARCHAR NOT NULL, operation_type VARCHAR NOT NULL, operation_contract_version VARCHAR NOT NULL, concept_contract_hash VARCHAR NOT NULL, run_id VARCHAR NOT NULL, plan_id VARCHAR NOT NULL, decision_at TIMESTAMPTZ NOT NULL, qualified_symbol VARCHAR NOT NULL, ticker VARCHAR NOT NULL, status VARCHAR NOT NULL, updated_at TIMESTAMPTZ NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, transaction_succeeded BOOLEAN NOT NULL DEFAULT FALSE, PRIMARY KEY(lineage_id,security_id,cik));
CREATE TABLE IF NOT EXISTS sec_liquidity_failures(failure_id VARCHAR PRIMARY KEY, operation_type VARCHAR NOT NULL, operation_contract_version VARCHAR NOT NULL, concept_contract_hash VARCHAR NOT NULL, lineage_id VARCHAR NOT NULL, run_id VARCHAR NOT NULL, plan_id VARCHAR NOT NULL, security_id VARCHAR, qualified_symbol VARCHAR, stage VARCHAR NOT NULL, reason_code VARCHAR NOT NULL, retryable BOOLEAN NOT NULL, attempt INTEGER NOT NULL, occurred_at TIMESTAMPTZ NOT NULL, resolved_at TIMESTAMPTZ);
CREATE TABLE IF NOT EXISTS sec_liquidity_raw_provenance(evidence_key VARCHAR PRIMARY KEY, operation_type VARCHAR NOT NULL, operation_contract_version VARCHAR NOT NULL, concept_contract_hash VARCHAR NOT NULL, lineage_id VARCHAR NOT NULL, run_id VARCHAR NOT NULL, plan_id VARCHAR NOT NULL, security_id VARCHAR NOT NULL, cik VARCHAR NOT NULL, endpoint_class VARCHAR NOT NULL, retrieved_at TIMESTAMPTZ NOT NULL, response_sha256 VARCHAR NOT NULL, byte_count BIGINT NOT NULL, content_type VARCHAR NOT NULL, payload_json VARCHAR NOT NULL, parser_version VARCHAR NOT NULL);
CREATE TABLE IF NOT EXISTS sec_liquidity_ingestion_lock(lock_name VARCHAR PRIMARY KEY, operation_type VARCHAR NOT NULL, operation_contract_version VARCHAR NOT NULL, concept_contract_hash VARCHAR NOT NULL, lineage_id VARCHAR NOT NULL, run_id VARCHAR NOT NULL, plan_id VARCHAR NOT NULL, process_id BIGINT, host_name VARCHAR, started_at TIMESTAMPTZ NOT NULL, last_progress_at TIMESTAMPTZ NOT NULL);
CREATE TABLE IF NOT EXISTS sec_issuers(security_id VARCHAR NOT NULL, qualified_symbol VARCHAR NOT NULL, ticker VARCHAR NOT NULL, cik VARCHAR NOT NULL, issuer_name VARCHAR, mapping_source VARCHAR, mapped_at TIMESTAMPTZ, PRIMARY KEY(security_id,cik));
CREATE TABLE IF NOT EXISTS sec_filings(cik VARCHAR NOT NULL, accession_number VARCHAR NOT NULL, form VARCHAR NOT NULL, filed_date DATE, public_at TIMESTAMPTZ, is_amendment BOOLEAN, source_endpoint VARCHAR, retrieved_at TIMESTAMPTZ, operation_type VARCHAR, operation_contract_version VARCHAR, concept_contract_hash VARCHAR, ingestion_run_id VARCHAR, ingestion_plan_id VARCHAR, PRIMARY KEY(cik,accession_number));
CREATE TABLE IF NOT EXISTS sec_facts(fact_key VARCHAR PRIMARY KEY, security_id VARCHAR NOT NULL, qualified_symbol VARCHAR, ticker VARCHAR, cik VARCHAR NOT NULL, taxonomy VARCHAR NOT NULL, concept VARCHAR NOT NULL, value DOUBLE NOT NULL, unit VARCHAR NOT NULL, currency VARCHAR, scale INTEGER, period_start DATE, period_end DATE, fiscal_year INTEGER, fiscal_period VARCHAR, frame VARCHAR, form VARCHAR, accession_number VARCHAR, filed_date DATE, public_at TIMESTAMPTZ, is_amendment BOOLEAN, is_revision BOOLEAN, source_endpoint VARCHAR, retrieved_at TIMESTAMPTZ, parser_contract_version VARCHAR, operation_type VARCHAR, operation_contract_version VARCHAR, concept_contract_hash VARCHAR, ingestion_run_id VARCHAR, ingestion_plan_id VARCHAR);
"""

EXTRA_COLUMNS = {
    "sec_facts": {"ticker":"VARCHAR", "scale":"INTEGER", "fiscal_year":"INTEGER",
        "fiscal_period":"VARCHAR", "frame":"VARCHAR", "form":"VARCHAR",
        "filed_date":"DATE", "is_amendment":"BOOLEAN", "is_revision":"BOOLEAN",
        "source_endpoint":"VARCHAR", "parser_contract_version":"VARCHAR", "operation_type":"VARCHAR", "operation_contract_version":"VARCHAR", "concept_contract_hash":"VARCHAR", "ingestion_run_id":"VARCHAR", "ingestion_plan_id":"VARCHAR"},
    "sec_filings": {"operation_type":"VARCHAR", "operation_contract_version":"VARCHAR", "concept_contract_hash":"VARCHAR", "ingestion_run_id":"VARCHAR", "ingestion_plan_id":"VARCHAR"},
    "sec_liquidity_ingestion_lock": {"operation_type":"VARCHAR", "operation_contract_version":"VARCHAR", "concept_contract_hash":"VARCHAR", "lineage_id":"VARCHAR"},
}


def _initialize(db: duckdb.DuckDBPyConnection) -> None:
    db.begin()
    try:
        db.execute(SCHEMA)
        for table, additions in EXTRA_COLUMNS.items():
            present = _columns(db, table)
            for name, kind in additions.items():
                if name not in present:
                    db.execute(f'ALTER TABLE "{table}" ADD COLUMN "{name}" {kind}')
        db.commit()
    except Exception:
        db.rollback()
        raise


class SECRequestClient:
    """Sequential, budget-counted SEC JSON transport with bounded retry."""
    def __init__(self, user_agent: str, budget: int, *, max_attempts: int = 3,
                 timeout_seconds: float = 20, max_response_bytes: int = 5_000_000,
                 pacing_seconds: float = .12, transport: httpx.BaseTransport | None = None,
                 sleeper: Callable[[float], None] = time.sleep,
                 jitter: Callable[[], float] = lambda: .5):
        if not _valid_user_agent(user_agent):
            raise ValueError("SEC_USER_AGENT_INVALID_REDACTED")
        if not 1 <= budget <= MAX_BUDGET or not 1 <= max_attempts <= 3:
            raise ValueError("invalid SEC request limits")
        self.budget, self.max_attempts, self.max_response_bytes = budget, max_attempts, max_response_bytes
        self.pacing_seconds, self.sleeper, self.jitter, self.count = pacing_seconds, sleeper, jitter, 0
        timeout = httpx.Timeout(timeout_seconds, connect=timeout_seconds, read=timeout_seconds)
        self.client = httpx.Client(transport=transport, headers={"User-Agent":user_agent,"Accept":"application/json"}, timeout=timeout, follow_redirects=False)

    def get(self, url: str) -> tuple[dict[str, Any], bytes, str]:
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.hostname != ALLOWED_HOST:
            raise ValueError("UNAPPROVED_PROVIDER_HOST")
        last = "TRANSIENT_PROVIDER_FAILURE"
        for attempt in range(self.max_attempts):
            if self.count >= self.budget:
                raise RuntimeError("REQUEST_BUDGET_EXHAUSTED")
            if self.count:
                self.sleeper(self.pacing_seconds + (self.pacing_seconds * (2 ** attempt) * self.jitter()))
            self.count += 1  # every attempted request, including transport failures
            try:
                response = self.client.get(url)
            except (httpx.TimeoutException, httpx.NetworkError):
                if attempt + 1 == self.max_attempts: raise RuntimeError(last) from None
                continue
            raw = response.content
            if len(raw) > self.max_response_bytes: raise ValueError("OVERSIZED_RESPONSE")
            if response.status_code in {408, 429, 500, 502, 503, 504}:
                if attempt + 1 == self.max_attempts: raise RuntimeError("TRANSIENT_HTTP_FAILURE")
                continue
            if response.status_code >= 400: raise ValueError("PERMANENT_HTTP_FAILURE")
            content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
            if content_type not in {"application/json", "application/*+json"} and not content_type.endswith("+json"):
                raise ValueError("INVALID_CONTENT_TYPE")
            try: payload = json.loads(raw)
            except (UnicodeDecodeError, json.JSONDecodeError): raise ValueError("INVALID_JSON") from None
            if not isinstance(payload, dict): raise ValueError("INVALID_JSON")
            return payload, raw, content_type
        raise RuntimeError(last)


def _workload(research: Path, production: Path, decision_at: Any) -> list[dict[str, str]]:
    from .liquidity_inventory import FIELDS, _classify, _load
    decision,population,raw,canonical,issuers,_,_ = _load(research,production,decision_at)
    mappings: dict[str, set[str]] = {sid:set() for sid,_ in population}
    symbols = dict(population)
    ticker_by_sid: dict[str,str] = {}
    for row in issuers:
        sid=str(row.get("security_id")); cik=row.get("cik")
        if sid in mappings and cik:
            mappings[sid].add(str(cik).zfill(10)); ticker_by_sid[sid]=str(row.get("ticker") or symbols[sid].split(".")[0])
    output=[]
    for sid,_ in population:
        if any(_classify(sid,f,raw,canonical,issuers,decision)[0]=="no_relevant_raw_or_canonical_fact" for f in FIELDS):
            ciks=mappings[sid]
            if len(ciks)!=1: raise ValueError("ISSUER_IDENTITY_NOT_DETERMINISTIC")
            output.append({"security_id":sid,"qualified_symbol":symbols[sid],"ticker":ticker_by_sid.get(sid,symbols[sid].split(".")[0]),"cik":next(iter(ciks))})
    return sorted(output,key=lambda x:(x["qualified_symbol"],x["security_id"]))


def _validate_cik(payload: dict[str,Any], cik: str) -> None:
    actual=payload.get("cik")
    if actual is None or str(actual).zfill(10)!=cik: raise ValueError("MISMATCHED_CIK")


def _availability(submissions: dict[str,Any]) -> dict[str,tuple[str,str,datetime]]:
    recent=submissions.get("filings",{}).get("recent",{})
    if not isinstance(recent,dict): return {}
    result={}
    for acc,form,filed,accepted in zip(*(recent.get(k,[]) for k in ("accessionNumber","form","filingDate","acceptanceDateTime"))):
        # Filing date alone is not an exact public boundary and is never promoted.
        if not (acc and accepted): continue
        text=str(accepted)
        try:
            public=(datetime.strptime(text,"%Y%m%d%H%M%S").replace(tzinfo=timezone.utc) if len(text)==14 and text.isdigit() else datetime.fromisoformat(text.replace("Z","+00:00")))
            if public.tzinfo is None: public=public.replace(tzinfo=timezone.utc)
        except ValueError: continue
        result[str(acc)]=(str(form or ""),str(filed or ""),public.astimezone(timezone.utc))
    return result


def _fact_rows(item: dict[str,str], payload: dict[str,Any], availability: dict[str,tuple[str,str,datetime]], retrieved: datetime) -> list[dict[str,Any]]:
    gaap=payload.get("facts",{}).get("us-gaap",{})
    if not isinstance(gaap,dict): return []
    rows=[]
    for concept in STANDARD_CONCEPTS:
        node=gaap.get(concept)
        if not isinstance(node,dict): continue
        units=node.get("units",{})
        if not isinstance(units,dict): continue
        for unit, observations in sorted(units.items()):
            if not isinstance(observations,list): continue
            for fact in observations:
                if not isinstance(fact,dict) or str(fact.get("accn")) not in availability: continue
                acc=str(fact["accn"]); form,filed,public=availability[acc]
                try: value=float(fact["val"])
                except (KeyError,TypeError,ValueError): continue
                end=fact.get("end")
                if not end: continue
                key=[item["security_id"],item["cik"],"us-gaap",concept,unit,fact.get("start"),end,acc,value]
                rows.append({"key":_sha(json.dumps(key,separators=(",",":"),default=str).encode()),"concept":concept,"unit":str(unit),"value":value,"start":fact.get("start"),"end":end,"fy":fact.get("fy"),"fp":fact.get("fp"),"frame":fact.get("frame"),"acc":acc,"form":form,"filed":filed or None,"public":public,"retrieved":retrieved})
    return rows


def _lineage_id(decision_at: str) -> str:
    return _sha(f"{OPERATION_TYPE}|{OPERATION_CONTRACT_VERSION}|{CONCEPT_CONTRACT_HASH}|{decision_at}".encode())


def _identity_sql(alias: str = "") -> tuple[str, list[str]]:
    prefix = f"{alias}." if alias else ""
    return (f"{prefix}operation_type=? AND {prefix}operation_contract_version=? AND {prefix}concept_contract_hash=?",
            [OPERATION_TYPE, OPERATION_CONTRACT_VERSION, CONCEPT_CONTRACT_HASH])


def _valid_user_agent(value: str) -> bool:
    lowered=value.strip().lower()
    if (not lowered or "your_real_email_address" in lowered or "example.com" in lowered
            or "placeholder" in lowered):
        return False
    # SEC asks for descriptive identification and monitored contact information.
    import re
    return bool(re.search(r"(?<![\w.-])[\w.+-]+@[\w-]+(?:\.[\w-]+)+", value)) and valid_user_agent(value)


def _raw_record(run_id: str, plan_id: str, lineage_id: str, item: dict[str,str], kind: str,
                raw: bytes, content_type: str, retrieved: datetime) -> list[Any]:
    digest=_sha(raw); key=_sha(f'{lineage_id}|{item["cik"]}|{kind}|{digest}'.encode())
    return [key,OPERATION_TYPE,OPERATION_CONTRACT_VERSION,CONCEPT_CONTRACT_HASH,lineage_id,
            run_id,plan_id,item["security_id"],item["cik"],kind,retrieved,digest,len(raw),
            content_type,raw.decode("utf-8"),PARSER_CONTRACT_VERSION]


def apply(*, research_db: Path, production_db: Path, decision_at: Any, plan_identifier: str,
          max_request_budget: int, authorization: str | None, fixture: dict[str,Any] | None=None,
          now: datetime | None=None, transport: httpx.BaseTransport | None=None,
          user_agent: str | None=None, interrupt_after: int | None=None) -> dict[str,Any]:
    if authorization != AUTHORIZATION_PHRASE: raise PermissionError("AUTHORIZATION_REQUIRED")
    validate_paths(research_db,production_db)
    if not 1 <= max_request_budget <= MAX_BUDGET: raise ValueError("INVALID_REQUEST_BUDGET")
    # Configuration is rejected before schema writes, locks, or transport construction.
    agent=user_agent if user_agent is not None else os.getenv("SIGNALLENS_SEC_USER_AGENT","")
    if fixture is None and not _valid_user_agent(agent): raise ValueError("SEC_USER_AGENT_INVALID_REDACTED")
    timestamp=_utc(now)
    try: planned_at=datetime.fromtimestamp(int(plan_identifier.split(":",1)[0]),timezone.utc)
    except (ValueError,IndexError): raise ValueError("PLAN_IDENTIFIER_MISMATCH") from None
    if timestamp >= planned_at + timedelta(minutes=15): raise ValueError("PLAN_EXPIRED")
    plan=plan_sec_liquidity_evidence_ingestion(research_db=research_db,production_db=production_db,
        decision_at=decision_at,max_request_budget=max_request_budget,generated_at=timestamp)
    if plan["plan_identifier"] != plan_identifier: raise ValueError("PLAN_IDENTIFIER_MISMATCH")
    if any(plan.get(k)!=v for k,v in operation_identity().items()): raise ValueError("OPERATION_CONTRACT_MISMATCH")
    if timestamp >= datetime.fromisoformat(plan["plan_expires_at"]): raise ValueError("PLAN_EXPIRED")
    if plan["status"]!="ready": raise ValueError("PLAN_NOT_READY")
    if plan["live_sec_retrieval"]["estimated_request_count"] > max_request_budget: raise ValueError("INSUFFICIENT_REQUEST_BUDGET")
    expected=plan["database_fingerprints"]["before"]
    if {"research":fingerprint(research_db),"production":fingerprint(production_db)} != expected: raise ValueError("DATABASE_FINGERPRINT_CHANGED")
    production_before=fingerprint(production_db)
    with duckdb.connect(str(production_db),read_only=True) as p: p.execute("SELECT 1")
    workload=_workload(research_db,production_db,decision_at)
    if len(workload)!=plan["companies_requiring_ingestion"]["count"]: raise ValueError("WORKLOAD_CHANGED")
    decision_iso=plan["decision_at"]; lineage=_lineage_id(decision_iso); run_id=str(uuid.uuid4())
    identity_where,identity_args=_identity_sql()
    with duckdb.connect(str(research_db)) as db:
        _initialize(db)
        lock=db.execute(f"SELECT run_id,last_progress_at FROM sec_liquidity_ingestion_lock WHERE lock_name='sec-liquidity' AND {identity_where}",identity_args).fetchone()
        if lock:
            raise RuntimeError("STALE_OPERATION_LOCK_RECOVERY_REQUIRED" if timestamp-lock[1]>LOCK_STALE_AFTER else "ACTIVE_OPERATION_LOCK")
        # A legacy/unidentified row is reported but never adopted or deleted.
        ambiguous=db.execute("SELECT count(*) FROM sec_liquidity_ingestion_lock WHERE lock_name='sec-liquidity'").fetchone()[0]
        if ambiguous: raise RuntimeError("OTHER_DATABASE_WRITER_LOCK")
        db.execute("INSERT INTO sec_liquidity_ingestion_lock(lock_name,operation_type,operation_contract_version,concept_contract_hash,lineage_id,run_id,plan_id,process_id,host_name,started_at,last_progress_at) VALUES ('sec-liquidity',?,?,?,?,?,?,?,?,?,?)",[OPERATION_TYPE,OPERATION_CONTRACT_VERSION,CONCEPT_CONTRACT_HASH,lineage,run_id,plan_identifier,os.getpid(),socket.gethostname()[:100],timestamp,timestamp])
        db.execute("INSERT INTO sec_liquidity_runs VALUES (?,?,?,?,?,?,?,?,NULL,'running',?,0,0,0,NULL,?,NULL)",[run_id,OPERATION_TYPE,OPERATION_CONTRACT_VERSION,CONCEPT_CONTRACT_HASH,lineage,plan_identifier,decision_iso,timestamp,max_request_budget,production_before.sha256])
        rows=db.execute(f"""SELECT c.security_id,c.cik FROM sec_liquidity_checkpoints c
          WHERE {identity_where} AND c.lineage_id=? AND c.decision_at=? AND c.status='completed'
          AND c.transaction_succeeded=true AND EXISTS (
            SELECT 1 FROM sec_liquidity_raw_provenance p WHERE p.lineage_id=c.lineage_id
            AND p.security_id=c.security_id AND p.cik=c.cik AND p.run_id=c.run_id
            AND p.plan_id=c.plan_id AND p.operation_type=c.operation_type
            AND p.operation_contract_version=c.operation_contract_version
            AND p.concept_contract_hash=c.concept_contract_hash GROUP BY p.security_id HAVING count(DISTINCT p.endpoint_class)=2)
          AND EXISTS (SELECT 1 FROM sec_liquidity_runs r WHERE r.run_id=c.run_id AND r.lineage_id=c.lineage_id
            AND r.plan_id=c.plan_id AND r.operation_type=c.operation_type
            AND r.operation_contract_version=c.operation_contract_version AND r.concept_contract_hash=c.concept_contract_hash)""",
            identity_args+[lineage,decision_iso]).fetchall()
        done={(r[0],r[1]) for r in rows}
    pending=[x for x in workload if (x["security_id"],x["cik"]) not in done]
    # Budget is per apply attempt; prior compatible runs only determine safe skipping.
    client=None if fixture is not None else SECRequestClient(agent,max_request_budget,transport=transport)
    completed=failed=inserted=unchanged=0; stop=None
    try:
        for item in pending:
            retrieved=_utc(now)
            try:
                if fixture is None:
                    sub,sub_raw,sub_type=client.get(SEC_SUBMISSIONS.format(cik=item["cik"]))
                    facts,facts_raw,facts_type=client.get(SEC_FACTS.format(cik=item["cik"]))
                else:
                    sub=fixture.get("submissions",{}).get(item["cik"]); facts=fixture.get("companyfacts",{}).get(item["cik"])
                    if not isinstance(sub,dict) or not isinstance(facts,dict): raise ValueError("FIXTURE_EVIDENCE_MISSING")
                    sub_raw=json.dumps(sub,sort_keys=True,separators=(",",":")).encode(); facts_raw=json.dumps(facts,sort_keys=True,separators=(",",":")).encode(); sub_type=facts_type="application/json"
                _validate_cik(sub,item["cik"]); _validate_cik(facts,item["cik"])
                availability=_availability(sub); rows=_fact_rows(item,facts,availability,retrieved)
                with duckdb.connect(str(research_db)) as db:
                    db.begin()
                    db.execute("INSERT OR IGNORE INTO sec_liquidity_raw_provenance VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",_raw_record(run_id,plan_identifier,lineage,item,"submissions",sub_raw,sub_type,retrieved))
                    db.execute("INSERT OR IGNORE INTO sec_liquidity_raw_provenance VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",_raw_record(run_id,plan_identifier,lineage,item,"companyfacts",facts_raw,facts_type,retrieved))
                    for row in rows:
                        exists=db.execute("SELECT count(*) FROM sec_facts WHERE fact_key=?",[row["key"]]).fetchone()[0]
                        if exists: unchanged+=1; continue
                        db.execute("""INSERT INTO sec_facts(fact_key,security_id,qualified_symbol,ticker,cik,taxonomy,concept,value,unit,currency,scale,period_start,period_end,fiscal_year,fiscal_period,frame,form,accession_number,filed_date,public_at,is_amendment,is_revision,source_endpoint,retrieved_at,parser_contract_version,operation_type,operation_contract_version,concept_contract_hash,ingestion_run_id,ingestion_plan_id) VALUES (?,?,?,?,?,'us-gaap',?,?,?,?,NULL,?,?,?,?,?,?,?,?,?,?,false,'companyfacts',?,?,?,?,?,?,?)""",
                          [row["key"],item["security_id"],item["qualified_symbol"],item["ticker"],item["cik"],row["concept"],row["value"],row["unit"],"USD" if row["unit"]=="USD" else None,row["start"],row["end"],row["fy"],row["fp"],row["frame"],row["form"],row["acc"],row["filed"],row["public"],row["form"].endswith("/A"),retrieved,PARSER_CONTRACT_VERSION,OPERATION_TYPE,OPERATION_CONTRACT_VERSION,CONCEPT_CONTRACT_HASH,run_id,plan_identifier]); inserted+=1
                        db.execute("""INSERT OR IGNORE INTO sec_filings(cik,accession_number,form,filed_date,public_at,is_amendment,source_endpoint,retrieved_at,operation_type,operation_contract_version,concept_contract_hash,ingestion_run_id,ingestion_plan_id) VALUES (?,?,?,?,?,?,'submissions',?,?,?,?,?,?)""",[item["cik"],row["acc"],row["form"],row["filed"],row["public"],row["form"].endswith("/A"),retrieved,OPERATION_TYPE,OPERATION_CONTRACT_VERSION,CONCEPT_CONTRACT_HASH,run_id,plan_identifier])
                    db.execute("""INSERT OR REPLACE INTO sec_liquidity_checkpoints VALUES (?,?,?,?,?,?,?,?,?,?,?,'completed',?,coalesce((SELECT attempts+1 FROM sec_liquidity_checkpoints WHERE lineage_id=? AND security_id=? AND cik=?),1),true)""",[lineage,item["security_id"],item["cik"],OPERATION_TYPE,OPERATION_CONTRACT_VERSION,CONCEPT_CONTRACT_HASH,run_id,plan_identifier,decision_iso,item["qualified_symbol"],item["ticker"],retrieved,lineage,item["security_id"],item["cik"]])
                    db.execute(f"UPDATE sec_liquidity_ingestion_lock SET last_progress_at=? WHERE lock_name='sec-liquidity' AND run_id=? AND {identity_where}",[retrieved,run_id]+identity_args); db.commit()
                completed+=1
                if interrupt_after is not None and completed>=interrupt_after: raise KeyboardInterrupt
            except (ValueError,RuntimeError) as exc:
                code=str(exc)[:64]; retryable=code in {"REQUEST_BUDGET_EXHAUSTED","TRANSIENT_PROVIDER_FAILURE","TRANSIENT_HTTP_FAILURE"}
                with duckdb.connect(str(research_db)) as db:
                    db.execute("INSERT INTO sec_liquidity_failures VALUES (?,?,?,?,?,?,?,?,?,?,?, ?,1,?,NULL)",[str(uuid.uuid4()),OPERATION_TYPE,OPERATION_CONTRACT_VERSION,CONCEPT_CONTRACT_HASH,lineage,run_id,plan_identifier,item["security_id"],item["qualified_symbol"],"issuer",code,retryable,retrieved])
                    db.execute("""INSERT OR REPLACE INTO sec_liquidity_checkpoints VALUES (?,?,?,?,?,?,?,?,?,?,?, ?,?,coalesce((SELECT attempts+1 FROM sec_liquidity_checkpoints WHERE lineage_id=? AND security_id=? AND cik=?),1),false)""",[lineage,item["security_id"],item["cik"],OPERATION_TYPE,OPERATION_CONTRACT_VERSION,CONCEPT_CONTRACT_HASH,run_id,plan_identifier,decision_iso,item["qualified_symbol"],item["ticker"],"retryable_failure" if retryable else "permanent_failure",retrieved,lineage,item["security_id"],item["cik"]])
                failed+=1
                if code=="REQUEST_BUDGET_EXHAUSTED": stop="REQUEST_BUDGET_EXHAUSTED"; break
    except KeyboardInterrupt: stop="INTERRUPTED"
    requests=client.count if client else 0
    final="exhausted" if stop=="REQUEST_BUDGET_EXHAUSTED" else ("partial" if stop else ("failed" if failed and not completed else ("partial" if failed else "completed")))
    production_after=fingerprint(production_db)
    if production_after != production_before: final="failed"; stop="PRODUCTION_FINGERPRINT_CHANGED"
    with duckdb.connect(str(research_db)) as db:
        db.execute(f"UPDATE sec_liquidity_runs SET finished_at=?,status=?,request_count=?,inserted_count=?,unchanged_count=?,stop_reason=?,production_sha256_after=? WHERE run_id=? AND {identity_where}",[timestamp,final,requests,inserted,unchanged,stop,production_after.sha256,run_id]+identity_args)
        db.execute(f"DELETE FROM sec_liquidity_ingestion_lock WHERE lock_name='sec-liquidity' AND run_id=? AND {identity_where}",[run_id]+identity_args)
    if production_after != production_before: raise RuntimeError("PRODUCTION_FINGERPRINT_CHANGED")
    return {"command":"apply-sec-liquidity-evidence-ingestion",**operation_identity(),"run_id":run_id,"plan_identifier":plan_identifier,"status":final,"completed_issuers":completed,"failed_issuers":failed,"skipped_completed_issuers":len(done),"provider_request_count":requests,"remaining_budget":max_request_budget-requests,"request_budget_semantics":"per_apply_attempt","inserted_facts":inserted,"unchanged_facts":unchanged,"production_unchanged":True,**ZERO_OUTPUTS}


def _never_run_report(remaining: int | None, other_lock: dict[str,Any]) -> dict[str,Any]:
    return {"command":"sec-liquidity-evidence-ingestion-status",**operation_identity(),"read_only":True,
        "state":"never_run" if remaining is None else "planned","latest_run":None,
        "completed_issuer_count":0,"remaining_issuer_count":remaining,"failed_issuer_count":0,
        "actual_provider_request_count":0,"remaining_budget":None,"normalized_fact_counts":{},
        "raw_payload_provenance_count":0,"checkpoint_states":{},"failure_reason_counts":{},
        "failure_samples":{"items":[],"returned_count":0,"limit":SAMPLE_LIMIT},
        "operation_lock":{"state":"none"},"other_writer_lock":other_lock,
        "production_unchanged_evidence":{"state":"not_applicable","unchanged":None,"changed":None},
        "post_ingestion_reconciliation_ready":False,"diagnostics":{"items":[],"returned_count":0,"limit":SAMPLE_LIMIT},**ZERO_OUTPUTS}


def status(*, research_db: Path, production_db: Path, decision_at: Any | None=None, now: datetime | None=None) -> dict[str,Any]:
    validate_paths(research_db,production_db); before={"research":fingerprint(research_db),"production":fingerprint(production_db)}
    remaining=None
    if decision_at is not None: remaining=len(_workload(research_db,production_db,decision_at))
    with duckdb.connect(str(production_db),read_only=True) as p: p.execute("SELECT 1")
    with duckdb.connect(str(research_db),read_only=True) as db:
        tables=_tables(db)
        other={"state":"none"}
        # Old-format liquidity locks and generic writer locks are visible, but never treated as ours.
        if "sec_liquidity_ingestion_lock" in tables:
            cols=_columns(db,"sec_liquidity_ingestion_lock")
            if not {"operation_type","operation_contract_version","concept_contract_hash"}<=cols:
                if db.execute("SELECT count(*) FROM sec_liquidity_ingestion_lock").fetchone()[0]: other={"state":"unknown","kind":"legacy_or_other_writer"}
        if "sec_liquidity_runs" not in tables or not {"operation_type","operation_contract_version","concept_contract_hash","plan_id"}<=_columns(db,"sec_liquidity_runs"):
            report=_never_run_report(remaining,other)
        else:
            iw,ia=_identity_sql()
            latest=db.execute(f"SELECT run_id,status,request_budget,request_count,plan_id,production_sha256_before,production_sha256_after,lineage_id FROM sec_liquidity_runs WHERE {iw} AND plan_id IS NOT NULL ORDER BY started_at DESC LIMIT 1",ia).fetchone()
            if not latest:
                report=_never_run_report(remaining,other)
            else:
                lineage=latest[7]
                checkpoints=dict(db.execute(f"SELECT status,count(*) FROM sec_liquidity_checkpoints WHERE {iw} AND lineage_id=? GROUP BY status ORDER BY status",ia+[lineage]).fetchall()) if "sec_liquidity_checkpoints" in tables else {}
                failures=dict(db.execute(f"SELECT reason_code,count(*) FROM sec_liquidity_failures WHERE {iw} AND lineage_id=? GROUP BY reason_code ORDER BY reason_code",ia+[lineage]).fetchall()) if "sec_liquidity_failures" in tables else {}
                samples=[{"reason_code":r[0],"qualified_symbol":str(r[1])[:80]} for r in db.execute(f"SELECT reason_code,qualified_symbol FROM sec_liquidity_failures WHERE {iw} AND lineage_id=? ORDER BY occurred_at DESC LIMIT ?",ia+[lineage,SAMPLE_LIMIT]).fetchall()] if "sec_liquidity_failures" in tables else []
                facts=dict(db.execute(f"SELECT concept,count(*) FROM sec_facts WHERE {iw} AND ingestion_run_id=? GROUP BY concept ORDER BY concept",ia+[latest[0]]).fetchall()) if "sec_facts" in tables and {"operation_type","operation_contract_version","concept_contract_hash"}<=_columns(db,"sec_facts") else {}
                raw=db.execute(f"SELECT count(*) FROM sec_liquidity_raw_provenance WHERE {iw} AND lineage_id=?",ia+[lineage]).fetchone()[0] if "sec_liquidity_raw_provenance" in tables else 0
                lock=db.execute(f"SELECT run_id,plan_id,started_at,last_progress_at FROM sec_liquidity_ingestion_lock WHERE lock_name='sec-liquidity' AND {iw}",ia).fetchone() if "sec_liquidity_ingestion_lock" in tables and {"operation_type","operation_contract_version","concept_contract_hash"}<=_columns(db,"sec_liquidity_ingestion_lock") else None
                lock_report={"state":"none"} if not lock else {"state":"stale" if _utc(now)-lock[3]>LOCK_STALE_AFTER else "active","run_id":lock[0],"plan_identifier":lock[1],"started_at":lock[2],"last_progress_at":lock[3]}
                completed=int(checkpoints.get("completed",0)); failed=sum(int(v) for k,v in checkpoints.items() if "failure" in str(k))
                fp_state="unavailable" if not latest[6] else "available"
                unchanged=None if fp_state=="unavailable" else latest[5]==latest[6]
                report={"command":"sec-liquidity-evidence-ingestion-status",**operation_identity(),"read_only":True,"state":latest[1],"latest_run":{"run_id":latest[0],"state":latest[1],"plan_identifier":latest[4]},"completed_issuer_count":completed,"remaining_issuer_count":max(0,(remaining if remaining is not None else completed+failed)-completed-failed),"failed_issuer_count":failed,"actual_provider_request_count":latest[3],"remaining_budget":max(0,latest[2]-latest[3]),"normalized_fact_counts":facts,"raw_payload_provenance_count":raw,"checkpoint_states":checkpoints,"failure_reason_counts":failures,"failure_samples":{"items":samples,"returned_count":len(samples),"limit":SAMPLE_LIMIT},"operation_lock":lock_report,"other_writer_lock":other,"production_unchanged_evidence":{"state":fp_state,"before_sha256":latest[5],"after_sha256":latest[6],"unchanged":unchanged,"changed":None if unchanged is None else not unchanged},"post_ingestion_reconciliation_ready":latest[1]=="completed" and lock is None, "diagnostics":{"items":[],"returned_count":0,"limit":SAMPLE_LIMIT},**ZERO_OUTPUTS}
    after={"research":fingerprint(research_db),"production":fingerprint(production_db)}
    if before!=after: raise RuntimeError("READ_ONLY_STATUS_CHANGED_DATABASE")
    return report


def recover_stale_lock(*, research_db: Path, production_db: Path, run_id: str,
                       authorization: str | None, now: datetime | None=None) -> dict[str,Any]:
    if authorization != AUTHORIZATION_PHRASE: raise PermissionError("AUTHORIZATION_REQUIRED")
    validate_paths(research_db,production_db); iw,ia=_identity_sql()
    with duckdb.connect(str(production_db),read_only=True) as p: p.execute("SELECT 1")
    with duckdb.connect(str(research_db)) as db:
        if "sec_liquidity_ingestion_lock" not in _tables(db) or not {"operation_type","operation_contract_version","concept_contract_hash"}<=_columns(db,"sec_liquidity_ingestion_lock"): raise ValueError("LOCK_NOT_FOUND")
        row=db.execute(f"SELECT last_progress_at FROM sec_liquidity_ingestion_lock WHERE lock_name='sec-liquidity' AND run_id=? AND {iw}",[run_id]+ia).fetchone()
        if not row: raise ValueError("LOCK_NOT_FOUND")
        if _utc(now)-row[0] <= LOCK_STALE_AFTER: raise ValueError("LOCK_NOT_STALE")
        db.execute(f"UPDATE sec_liquidity_runs SET status='partial',finished_at=?,stop_reason='STALE_LOCK_RECOVERED' WHERE run_id=? AND status='running' AND {iw}",[_utc(now),run_id]+ia)
        db.execute(f"DELETE FROM sec_liquidity_ingestion_lock WHERE lock_name='sec-liquidity' AND run_id=? AND {iw}",[run_id]+ia)
    return {"command":"recover-stale-sec-liquidity-ingestion-lock",**operation_identity(),"run_id":run_id,"status":"recovered","production_unchanged":True,**ZERO_OUTPUTS}
