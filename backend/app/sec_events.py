"""Research-only ingestion of SEC submission *metadata* for material events.

The module intentionally has no dependency on scoring or portfolio code.  Network
I/O is injected and tests use SEC-shaped fixtures; filing bodies and exhibits are
never requested or persisted.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import duckdb

from .sec_capability import SEC_SUBMISSIONS, fingerprint
from .sec_ingestion import BudgetClient, IngestionLimits, _catalogue, validate_paths

AUTHORIZATION_PHRASE = "I AUTHORIZE RESEARCH-ONLY SEC EVENT INGESTION"
FORMS = {"8-K", "8-K/A", "6-K", "6-K/A"}
MAX_SYMBOL_SAMPLES = 10

SCHEMA = """
CREATE TABLE IF NOT EXISTS sec_event_ingestion_runs(
 run_id VARCHAR PRIMARY KEY, started_at TIMESTAMPTZ NOT NULL, finished_at TIMESTAMPTZ,
 status VARCHAR NOT NULL, request_budget INTEGER NOT NULL, runtime_budget_seconds DOUBLE NOT NULL,
 request_count INTEGER NOT NULL DEFAULT 0, inserted_count INTEGER NOT NULL DEFAULT 0,
 unchanged_count INTEGER NOT NULL DEFAULT 0, amendment_count INTEGER NOT NULL DEFAULT 0,
 include_historical BOOLEAN NOT NULL, stop_reason VARCHAR);
CREATE TABLE IF NOT EXISTS sec_event_checkpoints(
 security_id VARCHAR PRIMARY KEY, qualified_symbol VARCHAR NOT NULL, cik VARCHAR NOT NULL,
 status VARCHAR NOT NULL, recent_completed BOOLEAN NOT NULL DEFAULT FALSE,
 historical_files_completed INTEGER NOT NULL DEFAULT 0, historical_files_total INTEGER NOT NULL DEFAULT 0,
 last_run_id VARCHAR, attempts INTEGER NOT NULL DEFAULT 0, updated_at TIMESTAMPTZ NOT NULL);
CREATE TABLE IF NOT EXISTS sec_event_metadata(
 event_id VARCHAR PRIMARY KEY, content_hash VARCHAR NOT NULL, security_id VARCHAR NOT NULL,
 qualified_symbol VARCHAR NOT NULL, cik VARCHAR NOT NULL, accession_number VARCHAR NOT NULL,
 form VARCHAR NOT NULL, filing_date DATE NOT NULL, public_at TIMESTAMPTZ NOT NULL,
 report_date DATE, item_codes VARCHAR NOT NULL, primary_document VARCHAR,
 canonical_metadata_reference VARCHAR NOT NULL, amends_accession VARCHAR,
 source_endpoint VARCHAR NOT NULL, retrieval_at TIMESTAMPTZ NOT NULL,
 issuer_match_evidence VARCHAR NOT NULL, classification_confidence DOUBLE NOT NULL,
 event_category VARCHAR NOT NULL, provenance VARCHAR NOT NULL,
 license_classification VARCHAR NOT NULL, scope VARCHAR NOT NULL,
 explanation VARCHAR NOT NULL, is_amendment BOOLEAN NOT NULL,
 UNIQUE(cik, accession_number, content_hash));
CREATE TABLE IF NOT EXISTS sec_event_failures(
 failure_id VARCHAR PRIMARY KEY, run_id VARCHAR NOT NULL, security_id VARCHAR,
 qualified_symbol VARCHAR, cik VARCHAR, stage VARCHAR NOT NULL, reason_code VARCHAR NOT NULL,
 retryable BOOLEAN NOT NULL, attempt INTEGER NOT NULL, occurred_at TIMESTAMPTZ NOT NULL,
 resolved_at TIMESTAMPTZ);
"""

ITEM_CATEGORIES = {
    "1.01": "material_contract", "1.02": "material_contract",
    "2.01": "acquisition_disposal", "2.02": "earnings_financial_results",
    "2.04": "bankruptcy_distress", "2.05": "bankruptcy_distress",
    "2.06": "bankruptcy_distress", "3.01": "delisting_compliance",
    "3.02": "capital_raise", "4.01": "other_material_event",
    "4.02": "other_material_event", "5.02": "management_director_change",
    "5.07": "shareholder_matters", "7.01": "other_material_event",
    "8.01": "other_material_event", "9.01": "other_material_event",
}
CATEGORY_PRIORITY = tuple(dict.fromkeys(ITEM_CATEGORIES.values()))


def initialize_schema(path: Path) -> None:
    with duckdb.connect(str(path)) as db:
        db.execute(SCHEMA)


def _strict_timestamp(value: Any, now: datetime) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("missing_publication_timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("invalid_publication_timestamp") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("naive_publication_timestamp")
    parsed = parsed.astimezone(timezone.utc)
    if parsed > now:
        raise ValueError("future_publication_timestamp")
    return parsed


def _columns(recent: dict[str, Any]) -> list[dict[str, Any]]:
    """Transpose SEC's column-oriented recent submissions, failing closed."""
    accessions = recent.get("accessionNumber", [])
    if not isinstance(accessions, list):
        raise ValueError("malformed_response")
    rows = []
    for index in range(len(accessions)):
        row = {}
        for key, values in recent.items():
            if isinstance(values, list):
                row[key] = values[index] if index < len(values) else None
        rows.append(row)
    return rows


def classify(items: Any) -> tuple[list[str], str, float]:
    codes = sorted({part.strip() for part in str(items or "").split(",") if part.strip()})
    categories = {ITEM_CATEGORIES[code] for code in codes if code in ITEM_CATEGORIES}
    if categories:
        category = next(name for name in CATEGORY_PRIORITY if name in categories)
        return codes, category, 1.0 if len(categories) == 1 else .85
    return codes, "material_event_unclassified" if codes else "general_company_news", .35


def _explanation(form: str, category: str, is_amendment: bool) -> str:
    if is_amendment:
        return "Filed an amendment after the original event filing."
    phrases = {
        "earnings_financial_results": "Filed an 8-K concerning financial results.",
        "material_event_unclassified": "Material-event filing present, but SEC item metadata was unavailable.",
        "general_company_news": "Material-event filing present, but SEC item metadata was unavailable.",
    }
    return phrases.get(category, f"Filed a {form} concerning {category.replace('_', ' ')}.")


def normalize(payload: dict[str, Any], issuer: dict[str, str], retrieved_at: datetime,
              source_endpoint: str, scope: str = "recent") -> tuple[list[dict[str, Any]], list[tuple[str, bool]]]:
    cik = str(payload.get("cik", "")).zfill(10)
    if cik != issuer["cik"]:
        raise ValueError("issuer_identity_mismatch")
    recent = payload.get("filings", {}).get("recent", {})
    records, failures = [], []
    for row in _columns(recent if isinstance(recent, dict) else {}):
        form = str(row.get("form") or "")
        if form not in FORMS:
            continue
        accession = str(row.get("accessionNumber") or "")
        if not accession:
            failures.append(("missing_accession", False)); continue
        try:
            public_at = _strict_timestamp(row.get("acceptanceDateTime"), retrieved_at)
            filing_date = str(row.get("filingDate") or "")
            datetime.fromisoformat(filing_date)
        except ValueError as exc:
            failures.append((str(exc), False)); continue
        codes, category, confidence = classify(row.get("items"))
        amendment = form.endswith("/A")
        reference = f"{source_endpoint}#{accession}"
        stable = {"cik": cik, "accession": accession, "form": form,
                  "public_at": public_at.isoformat(), "items": codes,
                  "primary_document": row.get("primaryDocument") or None}
        digest = hashlib.sha256(json.dumps(stable, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        records.append({**stable, "event_id": f"sec-event:{cik}:{accession}:{digest[:16]}", "content_hash": digest,
            "security_id": issuer["security_id"], "qualified_symbol": issuer["qualified_symbol"],
            "filing_date": filing_date, "report_date": row.get("reportDate") or None,
            "item_codes": json.dumps(codes, separators=(",", ":")), "canonical_reference": reference,
            # SEC submissions do not identify the accession amended; preserve the version without guessing.
            "amends_accession": row.get("amendsAccessionNumber") or None,
            "source_endpoint": source_endpoint, "retrieval_at": retrieved_at,
            "issuer_match_evidence": f"stored_sec_issuer_cik:{cik}",
            "confidence": confidence, "category": category, "scope": scope,
            "explanation": _explanation(form, category, amendment), "is_amendment": amendment})
    return sorted(records, key=lambda r: (r["public_at"], r["accession"])), failures


def _mapped(db: duckdb.DuckDBPyConnection) -> tuple[list[dict[str, str]], int]:
    catalogue = _catalogue(db)
    if "sec_issuers" not in {row[0] for row in db.execute("SHOW TABLES").fetchall()}:
        return [], len(catalogue)
    mappings = {str(r[0]): (str(r[1]), str(r[2])) for r in db.execute(
        "SELECT security_id,qualified_symbol,cik FROM sec_issuers ORDER BY security_id").fetchall()}
    eligible = [{**row, "qualified_symbol": mappings[row["security_id"]][0],
                 "cik": mappings[row["security_id"]][1]}
                for row in catalogue if row["security_id"] in mappings]
    return sorted(eligible, key=lambda r: (r["qualified_symbol"], r["security_id"])), len(catalogue)-len(eligible)


def _readonly(research: Path, production: Path, fn: Callable[[duckdb.DuckDBPyConnection], dict[str, Any]]) -> dict[str, Any]:
    validate_paths(research, production)
    before = {str(p): fingerprint(p) for p in (research, production)}
    with duckdb.connect(str(production), read_only=True) as db: db.execute("SELECT 1")
    with duckdb.connect(str(research), read_only=True) as db: result = fn(db)
    after = {str(p): fingerprint(p) for p in (research, production)}
    if before != after: raise RuntimeError("read-only command changed a database")
    return {**result, "database_immutability": {"verified": True, "before": before, "after": after},
            "generated_rankings": 0, "generated_candidates": 0, "generated_shadow_selections": 0,
            "notice": "OFFICIAL FILING CONTEXT — NOT USED IN SCORE", "stored_full_articles": 0}


def plan(research: Path, production: Path) -> dict[str, Any]:
    def query(db):
        issuers, unmapped = _mapped(db); tables = {r[0] for r in db.execute("SHOW TABLES").fetchall()}
        states = {}
        if "sec_event_checkpoints" in tables:
            states = {str(a): str(b) for a,b in db.execute("SELECT security_id,status FROM sec_event_checkpoints").fetchall()}
        counts = {name: sum(states.get(i["security_id"]) == name for i in issuers)
                  for name in ("completed", "retryable_failure", "permanent_failure")}
        pending = len(issuers)-sum(counts.values())
        historical = 0
        if "sec_event_checkpoints" in tables:
            historical = int(db.execute("SELECT coalesce(sum(historical_files_total-historical_files_completed),0) FROM sec_event_checkpoints").fetchone()[0])
        return {"command":"plan-sec-event-ingestion", "mapped_issuers_eligible":len(issuers),
            "permanently_unmapped_issuers":unmapped, **counts, "pending":pending,
            "expected_recent_requests":{"exact":pending+counts["retryable_failure"],"basis":"one submissions request per pending mapped issuer"},
            "historical_expansion_requests":{"known_pending":historical,"automatic":False},
            "symbol_samples":[i["qualified_symbol"][:80] for i in issuers if states.get(i["security_id"]) != "completed"][:MAX_SYMBOL_SAMPLES]}
    return _readonly(research, production, query)


def status(research: Path, production: Path) -> dict[str, Any]:
    def query(db):
        issuers, unmapped = _mapped(db); tables={r[0] for r in db.execute("SHOW TABLES").fetchall()}
        if "sec_event_metadata" not in tables:
            return {"command":"sec-event-ingestion-status","issuers_assessed":0,"mapped_issuers_eligible":len(issuers),
                "permanently_unmapped_issuers":unmapped,"event_filings":0,"forms":{},"item_metadata_coverage":0.0,
                "event_categories":{},"amendments":0,"publication_range":{"earliest":None,"latest":None},
                "coverage":{"recent":0,"historical_file":0},"affected_symbol_samples":[]}
        scalar=db.execute("SELECT count(*),min(public_at),max(public_at),sum(is_amendment),sum(item_codes<>'[]') FROM sec_event_metadata").fetchone()
        total=int(scalar[0]); assessed=int(db.execute("SELECT count(*) FROM sec_event_checkpoints").fetchone()[0])
        amap=lambda sql: {str(k):int(v) for k,v in db.execute(sql).fetchall()}
        return {"command":"sec-event-ingestion-status","issuers_assessed":assessed,"mapped_issuers_eligible":len(issuers),
            "permanently_unmapped_issuers":unmapped,"event_filings":total,
            "forms":amap("SELECT form,count(*) FROM sec_event_metadata GROUP BY form ORDER BY form"),
            "item_metadata_coverage":round(int(scalar[4] or 0)/total,4) if total else 0.0,
            "event_categories":amap("SELECT event_category,count(*) FROM sec_event_metadata GROUP BY event_category ORDER BY 1"),
            "amendments":int(scalar[3] or 0),"publication_range":{"earliest":scalar[1],"latest":scalar[2]},
            "coverage":amap("SELECT scope,count(*) FROM sec_event_metadata GROUP BY scope ORDER BY 1"),
            "affected_symbol_samples":[r[0][:80] for r in db.execute("SELECT DISTINCT qualified_symbol FROM sec_event_metadata ORDER BY 1 LIMIT 10").fetchall()],
            "recent_events":[{"qualified_symbol":r[0][:80],"form":r[1],"category":r[2],
                "publication_time":r[3],"amendment":bool(r[4]),"explanation":r[5][:240],
                "citation":{"accession":r[6],"form":r[1],"publication_time":r[3]}}
                for r in db.execute("""SELECT qualified_symbol,form,event_category,public_at,is_amendment,
                    explanation,accession_number FROM sec_event_metadata ORDER BY public_at DESC,event_id DESC LIMIT 10""").fetchall()]}
    return _readonly(research, production, query)


def _failure(db, run_id: str, issuer: dict[str,str], code: str, retryable: bool, now: datetime) -> None:
    db.execute("INSERT INTO sec_event_failures VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        [str(uuid.uuid4()),run_id,issuer["security_id"],issuer["qualified_symbol"],issuer.get("cik"),"submissions",code,retryable,1,now,None])
    db.execute("INSERT OR REPLACE INTO sec_event_checkpoints VALUES (?,?,?,?,?,?,?,?,?,?)",
        [issuer["security_id"],issuer["qualified_symbol"],issuer["cik"],"retryable_failure" if retryable else "permanent_failure",False,0,0,run_id,1,now])


def ingest(*, research: Path, production: Path, authorization: str | None,
           limits: IngestionLimits, fixture: dict[str, Any] | None = None,
           retry_only: bool = False, include_historical: bool = False,
           now: datetime | None = None, transport=None, clock=time.monotonic) -> dict[str, Any]:
    if authorization != AUTHORIZATION_PHRASE: raise PermissionError("exact SEC event authorization phrase required")
    validate_paths(research, production); production_before=fingerprint(production)
    with duckdb.connect(str(production), read_only=True) as db: db.execute("SELECT 1")
    initialize_schema(research); current=(now or datetime.now(timezone.utc)).astimezone(timezone.utc); run_id=str(uuid.uuid4())
    with duckdb.connect(str(research)) as db:
        issuers,_=_mapped(db); states={str(a):str(b) for a,b in db.execute("SELECT security_id,status FROM sec_event_checkpoints").fetchall()}
        issuers=[i for i in issuers if (states.get(i["security_id"])=="retryable_failure" if retry_only else states.get(i["security_id"])!="completed")]
        db.execute("INSERT INTO sec_event_ingestion_runs VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            [run_id,current,None,"running",limits.max_requests,limits.runtime_seconds,0,0,0,0,include_historical,None])
    client=None if fixture is not None else BudgetClient(os.getenv("SIGNALLENS_SEC_USER_AGENT",""),limits,clock=clock,transport=transport)
    inserted=unchanged=amendments=0; stop=None
    for issuer in issuers:
        endpoint=SEC_SUBMISSIONS.format(cik=issuer["cik"])
        try:
            payload=(fixture.get("submissions",{}).get(issuer["cik"]) if fixture else client.get(endpoint))
            if not isinstance(payload,dict): raise ValueError("malformed_response")
            records, failures=normalize(payload,issuer,current,endpoint)
            files=payload.get("filings",{}).get("files",[]) or []
            with duckdb.connect(str(research)) as db:
                db.begin()
                for record in records:
                    exists=db.execute("SELECT 1 FROM sec_event_metadata WHERE cik=? AND accession_number=? AND content_hash=?",[issuer["cik"],record["accession"],record["content_hash"]]).fetchone()
                    if exists:
                        unchanged+=1
                        continue
                    db.execute("INSERT INTO sec_event_metadata VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",[
                        record["event_id"],record["content_hash"],record["security_id"],record["qualified_symbol"],issuer["cik"],record["accession"],record["form"],record["filing_date"],record["public_at"],record["report_date"],record["item_codes"],record["primary_document"],record["canonical_reference"],record["amends_accession"],record["source_endpoint"],current,record["issuer_match_evidence"],record["confidence"],record["category"],"official_sec_submissions_metadata","public_record_metadata",record["scope"],record["explanation"],record["is_amendment"],])
                    inserted+=1; amendments+=int(record["is_amendment"])
                for code,retryable in failures: _failure(db,run_id,issuer,code,retryable,current)
                state="permanent_failure" if failures and not records else "completed"
                db.execute("INSERT OR REPLACE INTO sec_event_checkpoints VALUES (?,?,?,?,?,?,?,?,?,?)",[issuer["security_id"],issuer["qualified_symbol"],issuer["cik"],state,True,0,len(files),run_id,1,current])
                db.commit()
        except (RuntimeError,ValueError) as exc:
            code=str(exc); retryable=code in {"request_budget_exhausted","runtime_budget_exhausted","retryable_http","transport_failure"}
            with duckdb.connect(str(research)) as db: _failure(db,run_id,issuer,code,retryable,current)
            if code in {"request_budget_exhausted","runtime_budget_exhausted"}: stop=code; break
    requests=client.count if client else 0; final="stopped" if stop else "completed"
    with duckdb.connect(str(research)) as db: db.execute("UPDATE sec_event_ingestion_runs SET finished_at=?,status=?,request_count=?,inserted_count=?,unchanged_count=?,amendment_count=?,stop_reason=? WHERE run_id=?",[current,final,requests,inserted,unchanged,amendments,stop,run_id])
    if fingerprint(production)!=production_before: raise RuntimeError("production database changed")
    return {"command":"retry-sec-event-failures" if retry_only else "ingest-sec-events","run_id":run_id,
        "status":final,"selected":len(issuers),"requests":requests,"inserted":inserted,"unchanged":unchanged,
        "amendments":amendments,"stop_reason":stop,"historical_requested":include_historical,
        "historical_downloaded":0,"production_unchanged":True,"generated_rankings":0,"generated_candidates":0,"generated_shadow_selections":0}
