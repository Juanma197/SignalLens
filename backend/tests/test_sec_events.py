from datetime import datetime, timezone
import json
import os

import duckdb
import httpx
import pytest

from app.sec_capability import fingerprint
from app.sec_events import (AUTHORIZATION_PHRASE, classify, ingest, normalize,
                            plan, status)
from app.sec_ingestion import IngestionLimits

NOW=datetime(2026,9,10,tzinfo=timezone.utc)


def databases(tmp_path):
    research,production=tmp_path/"research.duckdb",tmp_path/"production.duckdb"
    with duckdb.connect(str(production)) as db: db.execute("CREATE TABLE protected(value VARCHAR)")
    with duckdb.connect(str(research)) as db:
        db.execute("CREATE TABLE security_master_retrievals(retrieval_id VARCHAR,retrieved_at TIMESTAMP,status VARCHAR)")
        db.execute("""CREATE TABLE security_listings(retrieval_id VARCHAR,security_id VARCHAR,ticker VARCHAR,
          qualified_symbol VARCHAR,primary_exchange VARCHAR,currency VARCHAR,instrument_type VARCHAR,active BOOLEAN,cik VARCHAR)""")
        db.execute("INSERT INTO security_master_retrievals VALUES ('r','2026-01-01','completed')")
        db.execute("INSERT INTO security_listings VALUES ('r','s1','AAA','AAA.US','US','USD','common_stock',true,NULL),('r','s2','NOPE','NOPE.US','US','USD','common_stock',true,NULL)")
        db.execute("""CREATE TABLE sec_issuers(security_id VARCHAR,qualified_symbol VARCHAR,ticker VARCHAR,cik VARCHAR,
          issuer_name VARCHAR,mapping_source VARCHAR,mapped_at TIMESTAMPTZ,PRIMARY KEY(security_id,cik))""")
        db.execute("INSERT INTO sec_issuers VALUES ('s1','AAA.US','AAA','0000000001','Issuer','stored','2026-01-01T00:00:00Z')")
    return research,production


def payload(timestamp="2026-09-01T12:00:00Z"):
    return {"cik":"1","filings":{"recent":{
      "accessionNumber":["0001-26-000001","0001-26-000002","0001-26-000003","0001-26-000004"],
      "form":["8-K","8-K/A","6-K","10-Q"],"filingDate":["2026-09-01"]*4,
      "acceptanceDateTime":[timestamp,"2026-09-02T12:00:00Z","2026-09-03T12:00:00Z","2026-09-04T12:00:00Z"],
      "reportDate":["2026-08-31"]*4,"items":["2.02","","5.02",""] ,
      "primaryDocument":["a.htm","a2.htm","foreign.htm","q.htm"]},
      "files":[{"name":"old.json","filingCount":1000}]}}


def run(research,production,fixture=None,**kwargs):
    return ingest(research=research,production=production,authorization=AUTHORIZATION_PHRASE,
      limits=kwargs.pop("limits",IngestionLimits()),fixture=fixture or {"submissions":{"0000000001":payload()}},
      now=NOW,**kwargs)


def test_forms_categories_amendments_idempotence_and_no_bodies(tmp_path):
    research,production=databases(tmp_path); before=fingerprint(production)
    result=run(research,production)
    assert result["inserted"]==3 and result["amendments"]==1 and result["historical_downloaded"]==0
    report=status(research,production)
    assert report["forms"]=={"6-K":1,"8-K":1,"8-K/A":1}
    assert report["item_metadata_coverage"]==pytest.approx(2/3,abs=1e-4)
    with duckdb.connect(str(research),read_only=True) as db:
        columns={row[1] for row in db.execute("PRAGMA table_info('sec_event_metadata')").fetchall()}
        assert not {"body","html","text","exhibit"}&columns
        amended=db.execute("SELECT public_at,explanation,amends_accession FROM sec_event_metadata WHERE is_amendment").fetchone()
        assert amended[0].isoformat().startswith("2026-09-02") and amended[2] is None
    with duckdb.connect(str(research)) as db:
        db.execute("UPDATE sec_event_checkpoints SET status='retryable_failure'")
    again=run(research,production,retry_only=True)
    assert again["inserted"]==0 and again["unchanged"]==3
    assert fingerprint(production)==before


def test_point_in_time_rejections_and_issuer_match():
    issuer={"security_id":"s1","qualified_symbol":"AAA.US","cik":"0000000001"}
    for value,code in [(None,"missing"),("2026-09-01T12:00:00","naive"),("2027-01-01T00:00:00Z","future")]:
        rows,failures=normalize(payload(value),issuer,NOW,"https://data.sec.gov/submissions/CIK0000000001.json")
        assert len(rows)==2 and any(code in reason for reason,_ in failures)
    with pytest.raises(ValueError,match="issuer_identity_mismatch"):
        normalize(payload(),{**issuer,"cik":"0000000002"},NOW,"endpoint")


def test_plan_status_read_only_and_historical_cost_visible(tmp_path):
    research,production=databases(tmp_path); before=(research.read_bytes(),production.read_bytes())
    report=plan(research,production)
    assert report["mapped_issuers_eligible"]==1 and report["permanently_unmapped_issuers"]==1
    assert report["expected_recent_requests"]["exact"]==1 and not report["historical_expansion_requests"]["automatic"]
    assert before==(research.read_bytes(),production.read_bytes())


def test_authorization_path_protection_and_classification(tmp_path):
    research,production=databases(tmp_path)
    assert classify("1.01")[1:]==("material_contract",1.0)
    assert classify("")[1:]==("general_company_news",.35)
    with pytest.raises(PermissionError):
        ingest(research=research,production=production,authorization="wrong",limits=IngestionLimits(),fixture={})
    hard=tmp_path/"hard.duckdb"; os.link(production,hard)
    with pytest.raises(ValueError,match="hard-linked"): plan(production,hard)


def test_request_budget_stop_and_resume(tmp_path, monkeypatch):
    research,production=databases(tmp_path)
    with duckdb.connect(str(research)) as db:
        db.execute("INSERT INTO sec_issuers VALUES ('s2','NOPE.US','NOPE','0000000002','Second','stored','2026-01-01T00:00:00Z')")
    monkeypatch.setenv("SIGNALLENS_SEC_USER_AGENT","SignalLens tests test@example.com")
    transport=httpx.MockTransport(lambda request: httpx.Response(200,json=payload(),request=request))
    first=ingest(research=research,production=production,authorization=AUTHORIZATION_PHRASE,
        limits=IngestionLimits(max_requests=1,max_attempts=1),now=NOW,transport=transport)
    assert first["inserted"]==3 and first["status"]=="stopped" and first["stop_reason"]=="request_budget_exhausted"
    second_payload=payload(); second_payload["cik"]="2"
    resumed=run(research,production,fixture={"submissions":{"0000000002":second_payload}},retry_only=True)
    assert resumed["inserted"]==3
