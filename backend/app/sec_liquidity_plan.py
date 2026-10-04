"""Strictly read-only plan for filling the SEC liquidity evidence gap."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import base64
import hashlib
import json
from pathlib import Path
from typing import Any

import duckdb

from .financial_strength import AGGREGATE_MAXIMUM_BYTES, ZERO_OUTPUTS, compact_utf8_size
from .liquidity_inventory import (FIELDS, STANDARD_CONCEPTS, _classify, _decision,
    _load, completed_liquidity_retrieval_security_ids)
from .model_readiness import fingerprint
from .sec_liquidity_contract import operation_identity

CONCEPTS = tuple(STANDARD_CONCEPTS)
DEFAULT_REQUEST_BUDGET = 205
PLAN_LIFETIME = timedelta(minutes=15)
SAMPLE_LIMIT = 10
ARTIFACT_TABLES = ("sec_companyfacts_payloads", "sec_provider_payloads")
PLAN_IDENTIFIER_VERSION = "v1"


class LiquidityPlanError(Exception):
    """A bounded, public preflight failure (the message is the stable code)."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()


def _encode_identifier(issued_at: datetime, identity: dict[str, Any]) -> str:
    payload = base64.urlsafe_b64encode(_canonical(identity)).decode().rstrip("=")
    digest = hashlib.sha256(_canonical(identity)).hexdigest()
    return f"{PLAN_IDENTIFIER_VERSION}:{int(issued_at.timestamp())}:{payload}:{digest}"


def _decode_identifier(identifier: str) -> tuple[datetime, dict[str, Any], str]:
    """Strictly decode a v1 capability; legacy identifiers are intentionally invalid."""
    import re
    match = re.fullmatch(r"v1:(0|[1-9][0-9]{0,10}):([A-Za-z0-9_-]+):([0-9a-f]{64})", identifier)
    if not match:
        raise LiquidityPlanError("SEC_LIQUIDITY_PLAN_INVALID")
    try:
        epoch = int(match.group(1))
        issued = datetime.fromtimestamp(epoch, timezone.utc)
        encoded = match.group(2)
        raw = base64.b64decode(encoded + "=" * (-len(encoded) % 4), altchars=b"-_", validate=True)
        identity = json.loads(raw)
    except (ValueError, OverflowError, json.JSONDecodeError, UnicodeDecodeError):
        raise LiquidityPlanError("SEC_LIQUIDITY_PLAN_INVALID") from None
    if not isinstance(identity, dict) or identity.get("issued_at") != issued.isoformat():
        raise LiquidityPlanError("SEC_LIQUIDITY_PLAN_INVALID")
    if base64.urlsafe_b64encode(raw).decode().rstrip("=") != encoded:
        raise LiquidityPlanError("SEC_LIQUIDITY_PLAN_INVALID")
    actual = hashlib.sha256(_canonical(identity)).hexdigest()
    if actual != match.group(3):
        raise LiquidityPlanError("SEC_LIQUIDITY_PLAN_INVALID")
    return issued, identity, actual

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
    completed_ids=completed_liquidity_retrieval_security_ids(research_db) & requiring_ids
    completed=sorted(symbols[sid] for sid in completed_ids)
    live=sorted(symbols[sid] for sid in mapped if sid in requiring_ids and sid not in replay_ids
                and sid not in completed_ids)
    estimate=2*len(live) # one submissions and one Company Facts request per mapped issuer
    blockers=[]
    if unmapped: blockers.append("UNMAPPED_ISSUER_IDENTITY")
    if estimate>max_request_budget: blockers.append("REQUEST_BUDGET_EXCEEDED")
    # v1 uses exact whole-second issue time. Apply reconstructs from this immutable
    # instant rather than refreshing the plan with its own clock.
    generated=(generated_at or datetime.now(timezone.utc)).astimezone(timezone.utc).replace(microsecond=0)
    expires=generated+PLAN_LIFETIME
    identity={"issued_at":generated.isoformat(),"expires_at":expires.isoformat(),
              "decision_at":decision.isoformat(),"fingerprints":immutability["before"],
              "issuer_cohort":[{"security_id":sid,"qualified_symbol":symbols[sid],"cik":mapped.get(sid)} for sid in sorted(requiring_ids)],
              "concepts":list(CONCEPTS),"estimated_requests":estimate,
              "request_budget":max_request_budget,**operation_identity()}
    plan_id=_encode_identifier(generated,identity)
    report={"command":"plan-sec-liquidity-evidence-ingestion","read_only":True,
      **operation_identity(),
      "decision_at":decision.isoformat(),"plan_identifier":plan_id,"plan_generated_at":generated.isoformat(),"plan_expires_at":expires.isoformat(),
      "status":"blocked" if blockers else "ready","blocker_codes":blockers,
      "comparable_company_count":len(population),"mapped_cik_count":len(mapped),
      "unmapped_identity_count":len(unmapped),"unmapped_identity_samples":_bounded(unmapped),
      "concepts_requested":list(CONCEPTS),
      "companies_requiring_ingestion":{"count":len(requiring),"samples":_bounded(requiring)},
      "offline_replay":{"available":bool(replay),"company_count":len(replay),"samples":_bounded(replay),
        "recognized_tables":list(ARTIFACT_TABLES),"identity_requirement":"exact CIK plus retained non-empty original payload"},
      "previously_completed_retrieval":{"company_count":len(completed),"samples":_bounded(completed),
        "semantics":"matching isolated checkpoint and both retained endpoint payloads; no repeat provider request"},
      "live_sec_retrieval":{"company_count":len(live),"samples":_bounded(live),
        "estimated_request_count":estimate,"estimate_formula":"2 x live companies (submissions + Company Facts)"},
      "request_budget_ceiling":max_request_budget,
      "checkpoint_strategy":"durable per-security_id/CIK state after each issuer; retry only incomplete issuers",
      "expected_destination_tables":["sec_issuers","sec_filings","sec_facts","sec_liquidity_runs","sec_liquidity_checkpoints","sec_liquidity_failures","sec_liquidity_raw_provenance"],
      "anticipated_canonical_materialization":"separate post-ingestion, point-in-time raw-versus-canonical reconciliation; no automatic alias or extension authorization",
      "database_fingerprints":immutability,"provider_requests":0,"database_writes":0,
      "apply_contract":{"implemented":True,"authorization_phrase":"I AUTHORIZE RESEARCH-ONLY SEC LIQUIDITY EVIDENCE INGESTION",
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


def validate_sec_liquidity_plan(*, research_db: Path, production_db: Path,
        decision_at: Any, plan_identifier: str, max_request_budget: int,
        now: datetime | None=None) -> dict[str, Any]:
    """Perform the shared, strictly read-only apply/diagnostic preflight."""
    checked=(now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    try:
        issued, bound, _ = _decode_identifier(plan_identifier)
    except LiquidityPlanError as exc:
        return _validation_report(exc.code, checked)
    expires=issued+PLAN_LIFETIME
    common={"issued_at":issued.isoformat(),"expires_at":expires.isoformat(),
            "remaining_validity_seconds":max(0,int((expires-checked).total_seconds())),
            "contract_version":bound.get("operation_contract_version"),
            "contract_hash":bound.get("concept_contract_hash")}
    if issued > checked:
        return _validation_report("SEC_LIQUIDITY_PLAN_FUTURE_ISSUED",checked,**common)
    if checked >= expires:
        return _validation_report("SEC_LIQUIDITY_PLAN_EXPIRED",checked,**common)
    if bound.get("expires_at") != expires.isoformat():
        return _validation_report("SEC_LIQUIDITY_PLAN_INVALID",checked,**common)
    current_identity=operation_identity()
    if any(bound.get(key) != value for key,value in current_identity.items()):
        return _validation_report("SEC_LIQUIDITY_PLAN_CONTRACT_MISMATCH",checked,**common)
    try:
        rebuilt=plan_sec_liquidity_evidence_ingestion(research_db=research_db,
            production_db=production_db,decision_at=decision_at,
            max_request_budget=max_request_budget,generated_at=issued)
        _, actual, _=_decode_identifier(rebuilt["plan_identifier"])
    except (ValueError, OSError, duckdb.Error):
        return _validation_report("SEC_LIQUIDITY_PLAN_INVALID",checked,**common)
    if bound.get("decision_at") != actual.get("decision_at"):
        return _validation_report("SEC_LIQUIDITY_PLAN_DECISION_MISMATCH",checked,**common)
    expected_fp=bound.get("fingerprints",{})
    actual_fp=actual.get("fingerprints",{})
    fp_match={"research":expected_fp.get("research")==actual_fp.get("research"),
              "production":expected_fp.get("production")==actual_fp.get("production")}
    common["database_fingerprint_match"]=fp_match
    if not all(fp_match.values()):
        return _validation_report("SEC_LIQUIDITY_PLAN_FINGERPRINT_CHANGED",checked,**common)
    budget_ok=(bound.get("request_budget")==max_request_budget and
               isinstance(bound.get("estimated_requests"),int) and
               bound["estimated_requests"]<=max_request_budget)
    common["request_budget_sufficient"]=budget_ok
    if not budget_ok:
        return _validation_report("SEC_LIQUIDITY_REQUEST_BUDGET_INSUFFICIENT",checked,**common)
    # This catches cohort, exact CIK mapping, concepts, estimate, operation and all
    # other database-bound inputs without selectively accepting an altered token.
    if bound != actual or rebuilt["status"] != "ready":
        return _validation_report("SEC_LIQUIDITY_PLAN_INVALID",checked,**common)
    return _validation_report(None,checked,**common)


def _validation_report(code: str | None, checked: datetime, **values: Any) -> dict[str, Any]:
    return {"command":"validate-sec-liquidity-evidence-ingestion-plan","read_only":True,
            "valid":code is None,"reason_code":code,"checked_at":checked.isoformat(),
            "issued_at":None,"expires_at":None,"remaining_validity_seconds":0,
            "contract_version":None,"contract_hash":None,
            "database_fingerprint_match":{"research":False,"production":False},
            "request_budget_sufficient":False,"provider_requests":0,"database_writes":0,
            **values,**ZERO_OUTPUTS}
