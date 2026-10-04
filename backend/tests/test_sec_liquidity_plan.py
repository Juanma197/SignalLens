from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess
import sys

import duckdb
import httpx

from app.financial_strength import AGGREGATE_MAXIMUM_BYTES, compact_utf8_size
from app.liquidity_inventory import raw_canonical_inventory
from app.sec_liquidity_plan import (CONCEPTS, plan_sec_liquidity_evidence_ingestion,
    validate_apply_preconditions)

DECISION=datetime(2026,10,2,18,15,tzinfo=timezone.utc)

def databases(tmp_path: Path, *, mapped=71, artifact=False):
    root=tmp_path/"SEC liquidity plan files with spaces"; root.mkdir()
    research=root/"research evidence.duckdb"; production=root/"production evidence.duckdb"
    stamp=datetime(2026,7,1,tzinfo=timezone.utc)
    with duckdb.connect(str(production)) as db: db.execute("CREATE TABLE marker(x INTEGER)")
    with duckdb.connect(str(research)) as db:
        db.execute("CREATE TABLE security_classification_evidence(security_id VARCHAR,qualified_symbol VARCHAR,security_type VARCHAR,public_at TIMESTAMPTZ,retrieved_at TIMESTAMPTZ,available_at TIMESTAMPTZ)")
        db.execute("CREATE TABLE sec_issuers(security_id VARCHAR,qualified_symbol VARCHAR,ticker VARCHAR,cik VARCHAR)")
        db.execute("CREATE TABLE sec_facts(fact_key VARCHAR,security_id VARCHAR,qualified_symbol VARCHAR,cik VARCHAR,taxonomy VARCHAR,concept VARCHAR,value DOUBLE,unit VARCHAR,currency VARCHAR,period_start DATE,period_end DATE,accession_number VARCHAR,public_at TIMESTAMPTZ,retrieved_at TIMESTAMPTZ)")
        for i in range(71):
            sid=f"s{i:02}"; symbol=f"S{i:02}.US"; cik=f"{i+1:010}"
            db.execute("INSERT INTO security_classification_evidence VALUES (?,?,'us_operating_company',?,?,?)",[sid,symbol,stamp,stamp,stamp])
            if i<mapped: db.execute("INSERT INTO sec_issuers VALUES (?,?,?,?)",[sid,symbol,f"S{i:02}",cik])
            db.execute("INSERT INTO sec_facts VALUES (?,?,?,?,?,?,?,?,?,NULL,DATE '2026-06-30',?,?,?)",
              [f"f{i}",sid,symbol,cik,"us-gaap","Assets",100+i,"USD","USD",f"acc{i}",stamp,stamp])
        if artifact:
            db.execute("CREATE TABLE sec_companyfacts_payloads(cik VARCHAR,payload_json VARCHAR,sha256 VARCHAR)")
            db.execute("INSERT INTO sec_companyfacts_payloads VALUES ('0000000001','{\"cik\":1}','524214b2511053d3f6427224160c8baa45cf1356b180f63734673be994f3e231')")
    return research,production

def test_operator_71_aggregation_overlap_zero_materialization(tmp_path):
    research,production=databases(tmp_path)
    report=raw_canonical_inventory(research_db=research,production_db=production,decision_at=DECISION)
    company=report["company_reconciliation"]
    assert report["comparable_company_count"]==71
    assert company["requiring_new_sec_ingestion"]["count"]==71
    assert company["requiring_accounting_review"]["count"]==71
    assert company["requiring_canonical_materialization"]["count"]==0
    assert company["identity_failures"]["count"]==0

def test_plan_mapped_replay_requests_bounds_network_and_immutability(tmp_path,monkeypatch):
    research,production=databases(tmp_path,artifact=True); before=(research.read_bytes(),production.read_bytes())
    monkeypatch.setattr(httpx.Client,"get",lambda *a,**k: (_ for _ in ()).throw(AssertionError("network used")))
    generated=datetime(2026,10,4,tzinfo=timezone.utc)
    report=plan_sec_liquidity_evidence_ingestion(research_db=research,production_db=production,
      decision_at=DECISION,generated_at=generated)
    assert report["mapped_cik_count"]==71 and report["unmapped_identity_count"]==0
    assert report["offline_replay"]["available"] and report["offline_replay"]["company_count"]==1
    assert report["live_sec_retrieval"]["company_count"]==70
    assert report["live_sec_retrieval"]["estimated_request_count"]==140
    assert report["concepts_requested"]==list(CONCEPTS) and "AssetsCurrent" in CONCEPTS and "Assets" in CONCEPTS
    assert report["provider_requests"]==report["database_writes"]==0
    assert report["status"]=="ready" and report["blocker_codes"]==[]
    assert compact_utf8_size(report)<=AGGREGATE_MAXIMUM_BYTES
    assert (research.read_bytes(),production.read_bytes())==before
    for key in ("rankings","candidates","recommendations","selections","vintages","validation_observations"):
        assert report[key]==[]
    assert report["validation_credit"]==0

def test_unmapped_budget_expiry_fingerprint_and_deterministic_id(tmp_path):
    research,production=databases(tmp_path,mapped=70)
    a=plan_sec_liquidity_evidence_ingestion(research_db=research,production_db=production,
      decision_at=DECISION,max_request_budget=10,generated_at=DECISION)
    b=plan_sec_liquidity_evidence_ingestion(research_db=research,production_db=production,
      decision_at=DECISION,max_request_budget=10,generated_at=DECISION+timedelta(hours=1))
    assert a["plan_identifier"]!=b["plan_identifier"]
    assert a["unmapped_identity_count"]==1
    assert a["offline_replay"]["available"] is False
    assert a["blocker_codes"]==["UNMAPPED_ISSUER_IDENTITY","REQUEST_BUDGET_EXCEEDED"]
    assert validate_apply_preconditions(a,research_db=research,production_db=production,
      now=DECISION+timedelta(days=8))==["PLAN_EXPIRED"]
    with duckdb.connect(str(research)) as db: db.execute("INSERT INTO sec_issuers VALUES ('extra','X.US','X','9999999999')")
    assert validate_apply_preconditions(a,research_db=research,production_db=production,
      now=DECISION+timedelta(minutes=5))==["DATABASE_FINGERPRINT_CHANGED"]

def test_cli_paths_with_spaces_and_stable_redacted_error(tmp_path):
    research,production=databases(tmp_path)
    command=[sys.executable,"-m","app.sec_ingestion_cli","plan-sec-liquidity-evidence-ingestion",
      "--research-db",str(research),"--production-db",str(production),"--decision-at",DECISION.isoformat()]
    result=subprocess.run(command,cwd=Path(__file__).parents[1],capture_output=True,text=True)
    assert result.returncode==0 and json.loads(result.stdout)["comparable_company_count"]==71
    command[-1]="not-a-date"
    result=subprocess.run(command,cwd=Path(__file__).parents[1],capture_output=True,text=True)
    assert result.returncode==1 and str(research) not in result.stderr
    assert json.loads(result.stderr)["error"]["message"]=="SEC ingestion command failed; details redacted"
