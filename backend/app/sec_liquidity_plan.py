"""Strictly read-only plan for filling the SEC liquidity evidence gap."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
from typing import Any

import duckdb

from .financial_strength import AGGREGATE_MAXIMUM_BYTES, ZERO_OUTPUTS, compact_utf8_size
from .liquidity_inventory import FIELDS, STANDARD_CONCEPTS, _classify, _decision, _load
from .model_readiness import fingerprint

CONCEPTS = tuple(STANDARD_CONCEPTS)
DEFAULT_REQUEST_BUDGET = 205
PLAN_LIFETIME = timedelta(days=7)
SAMPLE_LIMIT = 10
ARTIFACT_TABLES = ("sec_companyfacts_payloads", "sec_provider_payloads")

def _tables(db: duckdb.DuckDBPyConnection) -> set[str]:
    return {str(row[0]) for row in db.execute("SHOW TABLES").fetchall()}

def _artifact_ciks(db: duckdb.DuckDBPyConnection) -> set[str]:
    """Recognize only durable payload stores carrying identity and original bytes."""
    result: set[str] = set()
    for table in ARTIFACT_TABLES:
        if table not in _tables(db): continue
        columns={str(row[1]) for row in db.execute(f"PRAGMA table_info('{table}')").fetchall()}
        payload=next((x for x in ("payload","payload_json","response_body") if x in columns),None)
        if "cik" not in columns or payload is None: continue
        digest_column="sha256" if "sha256" in columns else None
        endpoint_column=next((x for x in ("endpoint","source_endpoint") if x in columns),None)
        selected=["cik",f'"{payload}"']+[f'"{x}"' for x in (digest_column,endpoint_column) if x]
        for row in db.execute(f'SELECT {",".join(selected)} FROM "{table}" WHERE "{payload}" IS NOT NULL').fetchall():
            cik,body=row[:2]; extra=list(row[2:]); digest=extra.pop(0) if digest_column else None
            endpoint=extra.pop(0) if endpoint_column else None
            if not cik or body in (b"", "") or (endpoint is not None and "companyfacts" not in str(endpoint).lower()): continue
            raw=body if isinstance(body,bytes) else str(body).encode()
            if digest is not None and str(digest).lower()!=hashlib.sha256(raw).hexdigest(): continue
            try: embedded=json.loads(raw).get("cik")
            except (json.JSONDecodeError,UnicodeDecodeError,AttributeError): continue
            if embedded is not None and str(embedded).zfill(10)!=str(cik).zfill(10): continue
            result.add(str(cik).zfill(10))
    return result

def _bounded(values: list[str]) -> dict[str, Any]:
    values=sorted(values)
    return {"items":values[:SAMPLE_LIMIT],"returned_count":min(len(values),SAMPLE_LIMIT),
            "total_count":len(values),"truncated":len(values)>SAMPLE_LIMIT}

def plan_sec_liquidity_evidence_ingestion(*, research_db: Path, production_db: Path,
        decision_at: Any, max_request_budget: int=DEFAULT_REQUEST_BUDGET,
        generated_at: datetime | None=None) -> dict[str, Any]:
    if not 0 <= max_request_budget <= DEFAULT_REQUEST_BUDGET:
        raise ValueError("maximum request budget must be between 0 and 205")
    decision,population,raw,canonical,issuers,sources,immutability=_load(
        research_db,production_db,decision_at)
    security_ids={sid for sid,_ in population}; symbols=dict(population)
    mappings: dict[str,set[str]]={sid:set() for sid in security_ids}
    for row in issuers:
        sid=str(row.get("security_id")); cik=row.get("cik")
        if sid in mappings and cik: mappings[sid].add(str(cik).zfill(10))
    mapped={sid:next(iter(ciks)) for sid,ciks in mappings.items() if len(ciks)==1}
    unmapped=sorted(symbols[sid] for sid,ciks in mappings.items() if len(ciks)!=1)
    requiring=sorted(symbols[sid] for sid in security_ids if any(
        _classify(sid,field,raw,canonical,issuers,decision)[0]=="no_relevant_raw_or_canonical_fact"
        for field in FIELDS))
    requiring_ids={sid for sid in security_ids if symbols[sid] in requiring}
    artifacts:set[str]=set()
    with duckdb.connect(str(research_db),read_only=True) as r, duckdb.connect(str(production_db),read_only=True) as p:
        artifacts=_artifact_ciks(r)|_artifact_ciks(p)
    replay_ids={sid for sid,cik in mapped.items() if sid in requiring_ids and cik in artifacts}
    replay=sorted(symbols[sid] for sid in replay_ids)
    live=sorted(symbols[sid] for sid in mapped if sid in requiring_ids and sid not in replay_ids)
    estimate=2*len(live) # one submissions and one Company Facts request per mapped issuer
    blockers=[]
    if unmapped: blockers.append("UNMAPPED_ISSUER_IDENTITY")
    if estimate>max_request_budget: blockers.append("REQUEST_BUDGET_EXCEEDED")
    generated=(generated_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    expires=generated+PLAN_LIFETIME
    identity={"decision_at":decision.isoformat(),"fingerprints":immutability["before"],
              "companies":requiring,"mapped_ciks":sorted(mapped.values()),"concepts":CONCEPTS,
              "estimated_requests":estimate,"request_budget":max_request_budget}
    plan_id=hashlib.sha256(json.dumps(identity,sort_keys=True,separators=(",",":"),default=str).encode()).hexdigest()
    report={"command":"plan-sec-liquidity-evidence-ingestion","read_only":True,
      "decision_at":decision.isoformat(),"plan_identifier":plan_id,"plan_generated_at":generated.isoformat(),"plan_expires_at":expires.isoformat(),
      "status":"blocked" if blockers else "ready","blocker_codes":blockers,
      "comparable_company_count":len(population),"mapped_cik_count":len(mapped),
      "unmapped_identity_count":len(unmapped),"unmapped_identity_samples":_bounded(unmapped),
      "concepts_requested":list(CONCEPTS),
      "companies_requiring_ingestion":{"count":len(requiring),"samples":_bounded(requiring)},
      "offline_replay":{"available":bool(replay),"company_count":len(replay),"samples":_bounded(replay),
        "recognized_tables":list(ARTIFACT_TABLES),"identity_requirement":"exact CIK plus retained non-empty original payload"},
      "live_sec_retrieval":{"company_count":len(live),"samples":_bounded(live),
        "estimated_request_count":estimate,"estimate_formula":"2 x live companies (submissions + Company Facts)"},
      "request_budget_ceiling":max_request_budget,
      "checkpoint_strategy":"durable per-security_id/CIK state after each issuer; retry only incomplete issuers",
      "expected_destination_tables":["sec_issuers","sec_filings","sec_facts","sec_ingestion_runs","sec_checkpoints","sec_failures","sec_raw_response_provenance"],
      "anticipated_canonical_materialization":"separate post-ingestion, point-in-time raw-versus-canonical reconciliation; no automatic alias or extension authorization",
      "database_fingerprints":immutability,"provider_requests":0,"database_writes":0,
      "apply_contract":{"implemented":False,"authorization_phrase":"I AUTHORIZE RESEARCH-ONLY SEC LIQUIDITY EVIDENCE INGESTION",
        "requirements":["immediately preceding unexpired database-bound plan identifier","matching research and production fingerprints","explicit maximum-request budget","research-only writes","bounded rate and retries","durable issuer checkpoints","raw response provenance and filed/public/retrieved timestamps","restart-safe persistence","unchanged production verification","post-ingestion reconciliation"]},
      "bounds":{"sample_limit":SAMPLE_LIMIT,"maximum_compact_utf8_bytes":AGGREGATE_MAXIMUM_BYTES},**ZERO_OUTPUTS}
    report["compact_utf8_bytes"]=compact_utf8_size(report)
    if report["compact_utf8_bytes"]>AGGREGATE_MAXIMUM_BYTES: raise RuntimeError("plan output exceeds bound")
    return report

def validate_apply_preconditions(plan: dict[str, Any], *, research_db: Path,
        production_db: Path, now: datetime) -> list[str]:
    """Pure/read-only validation for the documented future apply boundary."""
    blockers=[]
    if now.astimezone(timezone.utc) >= datetime.fromisoformat(plan["plan_expires_at"]):
        blockers.append("PLAN_EXPIRED")
    expected=plan["database_fingerprints"]["before"]
    actual={"research":fingerprint(research_db),"production":fingerprint(production_db)}
    if actual != expected: blockers.append("DATABASE_FINGERPRINT_CHANGED")
    return blockers
