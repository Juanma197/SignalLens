"""Research-only, point-in-time SEC fundamentals ingestion.

This module deliberately has no scoring, ranking, publishing, or production-write
dependency.  Network access is injected so the complete state machine can be tested
with deterministic fixtures.
"""
from __future__ import annotations

import json
import hashlib
import os
import time
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any, Callable

import duckdb
import httpx

from .active_catalogue import eodhd_ticker, select_active_catalogue
from .sec_capability import (CONCEPTS, SEC_FACTS, SEC_SUBMISSIONS, SEC_TICKERS,
                             accession_availability, fingerprint, normalize_facts,
                             ticker_ciks, valid_user_agent)

AUTHORIZATION_PHRASE = "I AUTHORIZE RESEARCH-ONLY SEC INGESTION"
REGIONS = ("US",)
MAX_SYMBOL_SAMPLES = 10
MAX_SAMPLE_LENGTH = 80

SCHEMA = """
CREATE TABLE IF NOT EXISTS sec_issuers(
 security_id VARCHAR NOT NULL, qualified_symbol VARCHAR NOT NULL, ticker VARCHAR NOT NULL,
 cik VARCHAR NOT NULL CHECK(length(cik)=10), issuer_name VARCHAR,
 mapping_source VARCHAR NOT NULL, mapped_at TIMESTAMPTZ NOT NULL,
 PRIMARY KEY(security_id,cik));
CREATE TABLE IF NOT EXISTS sec_filings(
 cik VARCHAR NOT NULL, accession_number VARCHAR NOT NULL, form VARCHAR NOT NULL,
 filed_date DATE, public_at TIMESTAMPTZ, is_amendment BOOLEAN NOT NULL,
 source_endpoint VARCHAR NOT NULL, retrieved_at TIMESTAMPTZ NOT NULL,
 PRIMARY KEY(cik,accession_number));
CREATE TABLE IF NOT EXISTS sec_facts(
 fact_key VARCHAR PRIMARY KEY, security_id VARCHAR NOT NULL, qualified_symbol VARCHAR NOT NULL, ticker VARCHAR NOT NULL,
 cik VARCHAR NOT NULL, taxonomy VARCHAR NOT NULL, concept VARCHAR NOT NULL,
 value DOUBLE NOT NULL, unit VARCHAR NOT NULL, currency VARCHAR,
 period_start DATE, period_end DATE NOT NULL, fiscal_year INTEGER, fiscal_period VARCHAR,
 frame VARCHAR, form VARCHAR NOT NULL, accession_number VARCHAR NOT NULL,
 filed_date DATE, public_at TIMESTAMPTZ NOT NULL, is_amendment BOOLEAN NOT NULL,
 is_revision BOOLEAN NOT NULL, source_endpoint VARCHAR NOT NULL,
 retrieved_at TIMESTAMPTZ NOT NULL);
CREATE TABLE IF NOT EXISTS sec_ingestion_runs(
 run_id VARCHAR PRIMARY KEY, started_at TIMESTAMPTZ NOT NULL, finished_at TIMESTAMPTZ,
 status VARCHAR NOT NULL, dry_run BOOLEAN NOT NULL, request_budget INTEGER NOT NULL,
 runtime_budget_seconds DOUBLE NOT NULL, request_count INTEGER NOT NULL DEFAULT 0,
 inserted_count INTEGER NOT NULL DEFAULT 0, unchanged_count INTEGER NOT NULL DEFAULT 0,
 revision_count INTEGER NOT NULL DEFAULT 0, stop_reason VARCHAR);
CREATE TABLE IF NOT EXISTS sec_checkpoints(
 security_id VARCHAR PRIMARY KEY, qualified_symbol VARCHAR NOT NULL, ticker VARCHAR NOT NULL,
 cik VARCHAR, status VARCHAR NOT NULL, last_run_id VARCHAR, updated_at TIMESTAMPTZ NOT NULL,
 attempts INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS sec_failures(
 failure_id VARCHAR PRIMARY KEY, run_id VARCHAR NOT NULL, security_id VARCHAR,
 qualified_symbol VARCHAR, stage VARCHAR NOT NULL, reason_code VARCHAR NOT NULL,
 retryable BOOLEAN NOT NULL, attempt INTEGER NOT NULL, occurred_at TIMESTAMPTZ NOT NULL,
 resolved_at TIMESTAMPTZ);
"""


@dataclass(frozen=True)
class IngestionLimits:
    max_requests: int = 205
    runtime_seconds: float = 900
    max_attempts: int = 2
    pacing_seconds: float = .12
    timeout_seconds: float = 20
    max_response_bytes: int = 5_000_000

    def __post_init__(self) -> None:
        if not 1 <= self.max_requests <= 205: raise ValueError("max_requests out of range")
        if not 1 <= self.runtime_seconds <= 3600: raise ValueError("runtime_seconds out of range")
        if not 1 <= self.max_attempts <= 3: raise ValueError("max_attempts out of range")
        if not .1 <= self.pacing_seconds <= 2: raise ValueError("pacing_seconds out of range")
        if not 1 <= self.timeout_seconds <= 60: raise ValueError("timeout_seconds out of range")
        if not 1 <= self.max_response_bytes <= 10_000_000: raise ValueError("max_response_bytes out of range")


def validate_paths(research: Path, production: Path) -> None:
    if not research.exists() or not production.exists(): raise ValueError("both database paths must exist")
    if research.is_symlink() or production.is_symlink(): raise ValueError("symlinked database paths prohibited")
    rs, ps = research.stat(), production.stat()
    if research.resolve() == production.resolve() or (rs.st_dev, rs.st_ino) == (ps.st_dev, ps.st_ino):
        raise ValueError("research and production databases must be distinct and not hard-linked")


def _catalogue(db: duckdb.DuckDBPyConnection) -> list[dict[str, str]]:
    active = select_active_catalogue(db)
    if active is None: return []
    chosen = active.listings.loc[active.listings["eligible"].astype(bool) & active.listings["region"].eq("US")]
    tickers = {str(row[0]): eodhd_ticker(row[1]) for row in db.execute(
        "SELECT security_id,ticker FROM security_listings WHERE retrieval_id=?", [active.retrieval_id]).fetchall()}
    return [{"security_id": str(row.security_id), "qualified_symbol": str(row.qualified_symbol),
             "ticker": tickers[str(row.security_id)]} for row in chosen.itertuples(index=False)]


def _readonly(operation: Callable[[duckdb.DuckDBPyConnection], dict[str, Any]], research: Path,
              production: Path) -> dict[str, Any]:
    validate_paths(research, production)
    before = {str(p): fingerprint(p) for p in (research, production)}
    with duckdb.connect(str(production), read_only=True) as db: db.execute("SELECT 1")
    with duckdb.connect(str(research), read_only=True) as db: result = operation(db)
    after = {str(p): fingerprint(p) for p in (research, production)}
    if before != after: raise RuntimeError("read-only operation changed a database")
    result["database_immutability"] = {"verified": True, "before": before, "after": after}
    result["constraints"] = ["Research only; no ranking or candidate was generated.",
        "US fundamentals must not penalize non-US securities."]
    return result


def plan(research: Path, production: Path) -> dict[str, Any]:
    def query(db: duckdb.DuckDBPyConnection) -> dict[str, Any]:
        selected = _catalogue(db)
        tables = {r[0] for r in db.execute("SHOW TABLES").fetchall()}
        completed: set[str] = set()
        if "sec_checkpoints" in tables:
            completed = {r[0] for r in db.execute("SELECT security_id FROM sec_checkpoints WHERE status='completed'").fetchall()}
        pending = [r for r in selected if r["security_id"] not in completed]
        return {"command":"plan-sec-ingestion", "selected_us_securities":len(selected),
                "completed":len(completed & {r['security_id'] for r in selected}), "pending":len(pending),
                "expected_requests": 1 + 2 * len(pending),
                "symbol_samples":[r["qualified_symbol"] for r in pending[:MAX_SYMBOL_SAMPLES]]}
    return _readonly(query, research, production)


def _json_value(value: Any) -> Any:
    """Convert DuckDB/Python scalar values to deterministic JSON primitives."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, Enum):
        return _json_value(value.value)
    return str(value)


def _aggregate_map(rows: list[tuple[Any, Any]]) -> dict[str, int]:
    """Make GROUP BY results safe for ``json.dumps(sort_keys=True)``.

    DuckDB returns a Python ``None`` key for SQL NULL.  Mixing that key with text
    keys raises TypeError while JSON is sorting dictionary keys.
    """
    result: dict[str, int] = {}
    for raw_key, raw_count in rows:
        key = "(null)" if raw_key is None else str(_json_value(raw_key))
        result[key] = result.get(key, 0) + int(raw_count or 0)
    return result


def _sample(value: Any) -> str:
    text = "" if value is None else str(value)
    return "".join(character if character.isprintable() else "?" for character in text)[:MAX_SAMPLE_LENGTH]


def status(research: Path, production: Path) -> dict[str, Any]:
    def query(db: duckdb.DuckDBPyConnection) -> dict[str, Any]:
        selected = _catalogue(db); selected_ids = {row["security_id"] for row in selected}
        tables = {r[0] for r in db.execute("SHOW TABLES").fetchall()}
        if "sec_facts" not in tables:
            return {"command":"sec-ingestion-status", "selected_us_securities":len(selected), "mapped":0,
                    "completed":0,"pending":len(selected),"permanently_failed":0,"retryable":0,
                    "observations":0,"revisions":0,
                    "public_availability_range":{"earliest":None,"latest":None},
                    "feature_families":{k:"unavailable" for k in CONCEPTS},
                    "affected_symbol_samples":[],"generated_rankings":0,"generated_candidates":0,
                    "latest_run":None,"checkpoint_consistency":{"status":"not_applicable","checks":[]}}
        checkpoint_rows = db.execute("SELECT security_id,status FROM sec_checkpoints").fetchall()
        states = {str(security_id): str(state) for security_id, state in checkpoint_rows
                  if str(security_id) in selected_ids}
        completed = sum(state == "completed" for state in states.values())
        permanent = sum(state == "permanent_failure" for state in states.values())
        retryable = sum(state == "retryable_failure" for state in states.values())
        pending = len(selected_ids) - completed - permanent - retryable
        facts = db.execute("SELECT count(*),min(public_at),max(public_at) FROM sec_facts").fetchone()
        concepts = _aggregate_map(db.execute("SELECT concept,count(*) FROM sec_facts GROUP BY concept ORDER BY concept").fetchall())
        families = {}
        for family, expected in CONCEPTS.items():
            present = len(set(expected) & concepts.keys())
            families[family] = "unavailable" if present == 0 else ("available" if present == len(expected) else "partially_available")
        if not (set(CONCEPTS["debt_interest_coverage"]) & concepts.keys()) >= {"InterestExpenseNonOperating"}:
            families["debt_interest_coverage"] = "unavailable"
        conflicts = db.execute("""SELECT count(*) FROM (SELECT security_id,taxonomy,concept,period_start,period_end,
            accession_number FROM sec_facts GROUP BY ALL HAVING count(DISTINCT unit)>1)""").fetchone()[0]
        samples = [_sample(r[0]) for r in db.execute("SELECT DISTINCT qualified_symbol FROM sec_failures WHERE resolved_at IS NULL ORDER BY 1 LIMIT ?",[MAX_SYMBOL_SAMPLES]).fetchall()]
        run_row = db.execute("""SELECT run_id,started_at,finished_at,status,request_budget,request_count,
            inserted_count,unchanged_count,revision_count,stop_reason FROM sec_ingestion_runs
            ORDER BY started_at DESC,run_id DESC LIMIT 1""").fetchone()
        latest_run = None
        consistency = {"status":"not_applicable","checks":[]}
        if run_row:
            columns = ("run_id","started_at","finished_at","status","request_budget","request_count",
                       "inserted_count","unchanged_count","revision_count","stop_reason")
            latest_run = {key:_json_value(value) for key,value in zip(columns,run_row)}
            run_states = [str(row[0]) for row in db.execute(
                "SELECT status FROM sec_checkpoints WHERE last_run_id=?", [run_row[0]]).fetchall()]
            run_completed = run_states.count("completed")
            run_retryable = run_states.count("retryable_failure")
            checks = {
                "request_count_within_budget": int(run_row[5]) <= int(run_row[4]),
                "completed_issuers_have_request_capacity": int(run_row[5]) >= 1 + (2 * run_completed),
                "inserted_total_is_present": int(facts[0]) >= int(run_row[6]),
                "revision_total_is_present": int(db.execute("SELECT count(*) FROM sec_facts WHERE is_revision").fetchone()[0]) >= int(run_row[8]),
                "budget_stop_has_retryable_checkpoint": run_row[9] != "request_budget_exhausted" or run_retryable > 0,
                "budget_stop_exhausted_budget": run_row[9] != "request_budget_exhausted" or int(run_row[5]) == int(run_row[4]),
            }
            consistency = {"status":"consistent" if all(checks.values()) else "inconsistent",
                           "checks":[{"name":name,"passed":passed} for name,passed in checks.items()],
                           "completed_in_latest_run":run_completed,
                           "retryable_in_latest_run":run_retryable}
        return {"command":"sec-ingestion-status", "selected_us_securities":len(selected),
            "mapped":db.execute("SELECT count(DISTINCT security_id) FROM sec_issuers").fetchone()[0],
            "completed":completed, "pending":pending,
            "permanently_failed":permanent, "retryable":retryable,
            "observations":int(facts[0]), "public_availability_range":{"earliest":_json_value(facts[1]),"latest":_json_value(facts[2])},
            "coverage_by_concept":concepts, "feature_families":families,
            "forms":_aggregate_map(db.execute("SELECT form,count(*) FROM sec_facts GROUP BY form ORDER BY form").fetchall()),
            "units":_aggregate_map(db.execute("SELECT unit,count(*) FROM sec_facts GROUP BY unit ORDER BY unit").fetchall()),
            "currencies":_aggregate_map(db.execute("SELECT currency,count(*) FROM sec_facts GROUP BY currency ORDER BY currency").fetchall()),
            "amendments":db.execute("SELECT count(*) FROM sec_facts WHERE is_amendment").fetchone()[0],
            "revisions":db.execute("SELECT count(*) FROM sec_facts WHERE is_revision").fetchone()[0],
            "missing_availability":db.execute("SELECT count(*) FROM sec_failures WHERE reason_code='missing_availability_date'").fetchone()[0],
            "conflicting_units":conflicts,"affected_symbol_samples":samples,
            "latest_run":latest_run,"checkpoint_consistency":consistency,
            "generated_rankings":0,"generated_candidates":0}
    return _readonly(query, research, production)


class BudgetClient:
    def __init__(self, user_agent: str, limits: IngestionLimits, clock=time.monotonic,
                 sleeper=time.sleep, transport: httpx.BaseTransport | None = None):
        if not valid_user_agent(user_agent): raise ValueError("valid contact-bearing SIGNALLENS_SEC_USER_AGENT required")
        self.limits, self.count, self.clock, self.sleeper = limits, 0, clock, sleeper
        self.started = clock(); self.client = httpx.Client(transport=transport, headers={"User-Agent":user_agent}, timeout=limits.timeout_seconds)
    def get(self, url: str) -> dict[str, Any]:
        last = "transport_failure"
        for attempt in range(self.limits.max_attempts):
            if self.count >= self.limits.max_requests: raise RuntimeError("request_budget_exhausted")
            if self.clock()-self.started >= self.limits.runtime_seconds: raise RuntimeError("runtime_budget_exhausted")
            if self.count: self.sleeper(self.limits.pacing_seconds * (2 ** attempt))
            self.count += 1
            try:
                response=self.client.get(url)
                if len(response.content)>self.limits.max_response_bytes: raise RuntimeError("oversized_response")
                if response.status_code == 404: raise ValueError("provider_not_found")
                if response.status_code in {429,500,502,503,504}: last="retryable_http"; continue
                response.raise_for_status(); payload=response.json()
                if not isinstance(payload,dict): raise RuntimeError("malformed_response")
                return payload
            except httpx.HTTPError: last="transport_failure"
        raise RuntimeError(last)


def initialize_schema(research: Path) -> None:
    with duckdb.connect(str(research)) as db: db.execute(SCHEMA)


def ingest(*, research: Path, production: Path, authorization: str | None, dry_run: bool,
           limits: IngestionLimits, fixture: dict[str, Any] | None = None,
           retry_only: bool = False, now: datetime | None = None,
           transport: httpx.BaseTransport | None = None, clock=time.monotonic) -> dict[str, Any]:
    if authorization != AUTHORIZATION_PHRASE: raise PermissionError("exact SEC ingestion authorization phrase required")
    validate_paths(research, production); production_before=fingerprint(production)
    with duckdb.connect(str(production), read_only=True) as db: db.execute("SELECT 1")
    initialize_schema(research)
    timestamp=(now or datetime.now(timezone.utc)).astimezone(timezone.utc); run_id=str(uuid.uuid4())
    with duckdb.connect(str(research)) as db:
        selected=_catalogue(db)
        if retry_only:
            retry_ids={r[0] for r in db.execute("SELECT security_id FROM sec_checkpoints WHERE status='retryable_failure'").fetchall()}
            selected=[r for r in selected if r["security_id"] in retry_ids]
        else:
            done={r[0] for r in db.execute("SELECT security_id FROM sec_checkpoints WHERE status='completed'").fetchall()}
            selected=[r for r in selected if r["security_id"] not in done]
        db.execute("INSERT INTO sec_ingestion_runs VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            [run_id,timestamp,None,"running",dry_run,limits.max_requests,limits.runtime_seconds,0,0,0,0,None])
    user_agent=os.getenv("SIGNALLENS_SEC_USER_AGENT","")
    client=None if fixture is not None else BudgetClient(user_agent,limits,clock=clock,transport=transport)
    mapping_payload=fixture.get("ticker_mapping",{}) if fixture else client.get(SEC_TICKERS)
    mapping=ticker_ciks(mapping_payload,[r["ticker"] for r in selected])
    inserted=unchanged=revisions=0; stop_reason=None
    for item in selected:
        ticker=item["ticker"]; cik=mapping.get(ticker)
        if not cik:
            _failure(research,run_id,item,"ticker_mapping_missing",False,timestamp); continue
        try:
            submissions=fixture.get("submissions",{}).get(cik,{}) if fixture else client.get(SEC_SUBMISSIONS.format(cik=cik))
            facts=fixture.get("companyfacts",{}).get(cik,{}) if fixture else client.get(SEC_FACTS.format(cik=cik))
            rows,_,reasons=normalize_facts(ticker,cik,facts,accession_availability(submissions),timestamp)
            if dry_run: continue
            with duckdb.connect(str(research)) as db:
                db.begin()
                db.execute("INSERT OR IGNORE INTO sec_issuers VALUES (?,?,?,?,?,?,?)",[item["security_id"],item["qualified_symbol"],ticker,cik,None,SEC_TICKERS,timestamp])
                for row in rows:
                    existing=db.execute("""SELECT count(*) FROM sec_facts WHERE security_id=? AND taxonomy=? AND concept=? AND unit=?
                        AND period_start IS NOT DISTINCT FROM ? AND period_end=? AND accession_number=? AND value=?""",
                        [item["security_id"],row["taxonomy"],row["concept"],row["unit"],row["period_start"],row["period_end"],row["accession"],row["value"]]).fetchone()[0]
                    if existing: unchanged+=1; continue
                    revision=bool(db.execute("""SELECT count(*) FROM sec_facts WHERE security_id=? AND taxonomy=? AND concept=? AND unit=?
                        AND period_start IS NOT DISTINCT FROM ? AND period_end=?""",[item["security_id"],row["taxonomy"],row["concept"],row["unit"],row["period_start"],row["period_end"]]).fetchone()[0])
                    db.execute("INSERT OR IGNORE INTO sec_filings VALUES (?,?,?,?,?,?,?,?)",[cik,row["accession"],row["form"],row["filed_at"],row["public_at"],row["amendment"],SEC_SUBMISSIONS.format(cik=cik),timestamp])
                    key_parts=[item["security_id"],row["taxonomy"],row["concept"],row["unit"],
                        row["period_start"],row["period_end"],row["accession"],row["value"]]
                    fact_key=hashlib.sha256(json.dumps(key_parts,default=str,separators=(",",":"),ensure_ascii=True).encode()).hexdigest()
                    db.execute("INSERT INTO sec_facts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        [fact_key,item["security_id"],item["qualified_symbol"],ticker,cik,row["taxonomy"],row["concept"],row["value"],row["unit"],row["currency"],row["period_start"],row["period_end"],row["fiscal_year"],row["fiscal_period"],row.get("frame"),row["form"],row["accession"],row["filed_at"],row["public_at"],row["amendment"],revision,row["source_url"],timestamp])
                    inserted+=1; revisions+=int(revision)
                for reason in reasons:
                    if reason in {"missing_availability_date","conflicting_units"}: _failure_db(db,run_id,item,reason,False,timestamp)
                db.execute("INSERT OR REPLACE INTO sec_checkpoints VALUES (?,?,?,?,?,?,?,?)",[item["security_id"],item["qualified_symbol"],ticker,cik,"completed",run_id,timestamp,1])
                db.commit()
        except RuntimeError as exc:
            code=str(exc); retryable=code in {"request_budget_exhausted","runtime_budget_exhausted","retryable_http","transport_failure"}
            _failure(research,run_id,item,code,retryable,timestamp)
            if code in {"request_budget_exhausted","runtime_budget_exhausted"}: stop_reason=code; break
        except ValueError as exc:
            _failure(research,run_id,item,str(exc) if str(exc)=="provider_not_found" else "malformed_response",False,timestamp)
    requests=client.count if client else 0; final="stopped" if stop_reason else ("dry_run" if dry_run else "completed")
    with duckdb.connect(str(research)) as db: db.execute("""UPDATE sec_ingestion_runs SET finished_at=?,status=?,request_count=?,inserted_count=?,unchanged_count=?,revision_count=?,stop_reason=? WHERE run_id=?""",[timestamp,final,requests,inserted,unchanged,revisions,stop_reason,run_id])
    if fingerprint(production)!=production_before: raise RuntimeError("production database changed")
    return {"command":"retry-sec-failures" if retry_only else "ingest-sec-fundamentals","run_id":run_id,"status":final,
            "selected":len(selected),"requests":requests,"inserted":inserted,"unchanged":unchanged,"revisions":revisions,
            "stop_reason":stop_reason,"production_unchanged":True,"generated_rankings":0,"generated_candidates":0}


def _failure_db(db: duckdb.DuckDBPyConnection, run_id: str, item: dict[str,str], code: str,
                retryable: bool, now: datetime) -> None:
    db.execute("INSERT INTO sec_failures VALUES (?,?,?,?,?,?,?,?,?,?)",[str(uuid.uuid4()),run_id,item["security_id"],item["qualified_symbol"],"ingestion",code,retryable,1,now,None])


def _failure(research: Path, run_id: str, item: dict[str,str], code: str,
             retryable: bool, now: datetime) -> None:
    with duckdb.connect(str(research)) as db:
        _failure_db(db,run_id,item,code,retryable,now)
        db.execute("INSERT OR REPLACE INTO sec_checkpoints VALUES (?,?,?,?,?,?,?,?)",[item["security_id"],item["qualified_symbol"],item["ticker"],None,"retryable_failure" if retryable else "permanent_failure",run_id,now,1])
