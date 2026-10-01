import json
import os
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import httpx
import pytest

from app.international_fundamentals import capability_report
from app.sec_capability import fingerprint
from app.sec_ingestion import (AUTHORIZATION_PHRASE, IngestionLimits, ingest, plan,
                               status)

FIXTURE=Path(__file__).parent/"fixtures/sec_capability.json"


def databases(tmp_path):
    research=tmp_path/"research.duckdb"; production=tmp_path/"production.duckdb"
    with duckdb.connect(str(production)) as db: db.execute("CREATE TABLE operator_data(secret VARCHAR)")
    with duckdb.connect(str(research)) as db:
        db.execute("CREATE TABLE security_master_retrievals(retrieval_id VARCHAR,retrieved_at TIMESTAMP,status VARCHAR)")
        db.execute("""CREATE TABLE security_listings(retrieval_id VARCHAR,security_id VARCHAR,ticker VARCHAR,
            qualified_symbol VARCHAR,primary_exchange VARCHAR,currency VARCHAR,instrument_type VARCHAR,
            active BOOLEAN,cik VARCHAR)""")
        db.execute("INSERT INTO security_master_retrievals VALUES ('r','2026-01-01','completed')")
        db.execute("INSERT INTO security_listings VALUES ('r','aaa','AAA','AAA.US','US','USD','common_stock',true,NULL),('r','intl','ZZZ','ZZZ.LSE','LSE','GBP','common_stock',true,NULL)")
    return research,production


def run(research,production,**kwargs):
    return ingest(research=research,production=production,authorization=AUTHORIZATION_PHRASE,
        dry_run=False,limits=IngestionLimits(),fixture=json.loads(FIXTURE.read_text()),
        now=datetime(2026,6,1,tzinfo=timezone.utc),**kwargs)


def test_plan_and_status_are_read_only_and_select_authoritative_catalogue(tmp_path):
    research,production=databases(tmp_path); before=(fingerprint(research),fingerprint(production))
    report=plan(research,production)
    assert report["selected_us_securities"]==1 and report["expected_requests"]==3
    assert status(research,production)["pending"]==1
    assert before==(fingerprint(research),fingerprint(production))


def test_ingestion_is_point_in_time_idempotent_resumable_and_production_isolated(tmp_path):
    research,production=databases(tmp_path); production_before=fingerprint(production)
    first=run(research,production)
    assert first["inserted"]>0 and first["revisions"]==1 and first["generated_candidates"]==0
    # A completed security is checkpointed and therefore skipped by a normal resume.
    second=run(research,production)
    assert second["selected"]==0 and second["inserted"]==0
    report=status(research,production)
    assert report["completed"]==1 and report["observations"]==first["inserted"]
    assert report["feature_families"]["debt_interest_coverage"]=="unavailable"
    with duckdb.connect(str(research),read_only=True) as db:
        assert db.execute("SELECT count(*) FROM sec_facts WHERE public_at=period_end::TIMESTAMP").fetchone()[0]==0
        assert db.execute("SELECT count(*) FROM sec_facts WHERE is_amendment").fetchone()[0]==1
    assert fingerprint(production)==production_before


def test_authorization_path_and_link_refusals(tmp_path):
    research,production=databases(tmp_path)
    with pytest.raises(PermissionError):
        ingest(research=research,production=production,authorization=None,dry_run=True,
            limits=IngestionLimits(),fixture={})
    hardlink=tmp_path/"hard.duckdb"; os.link(production,hardlink)
    with pytest.raises(ValueError,match="hard-linked"):
        plan(production,hardlink)


def test_failure_classification_and_retry_selection(tmp_path):
    research,production=databases(tmp_path); fixture=json.loads(FIXTURE.read_text())
    fixture["ticker_mapping"]={}
    result=ingest(research=research,production=production,authorization=AUTHORIZATION_PHRASE,
        dry_run=False,limits=IngestionLimits(),fixture=fixture)
    assert result["inserted"]==0
    assert status(research,production)["permanently_failed"]==1


def test_runtime_and_request_limits_validate_and_international_report_is_offline():
    with pytest.raises(ValueError): IngestionLimits(max_requests=0)
    report=capability_report()
    assert report["live_confirmation"] is False and report["storage_writes"]==0
    assert {r["region"] for r in report["regions"]}=={"LSE","TO","XETRA","PA"}
    assert report["yahoo_finance"]["point_in_time_backtesting"] is False


def test_non_us_report_can_select_actual_catalogue_representatives(tmp_path):
    research,_=databases(tmp_path)
    with duckdb.connect(str(research),read_only=True) as db:
        report=capability_report(db)
    assert next(row for row in report["regions"] if row["region"]=="LSE")["representative"]=="ZZZ.LSE"


def test_request_budget_stop_is_checkpointed_and_retryable(tmp_path,monkeypatch):
    research,production=databases(tmp_path)
    monkeypatch.setenv("SIGNALLENS_SEC_USER_AGENT","SignalLens Research test@example.com")
    transport=httpx.MockTransport(lambda _request:httpx.Response(200,json={"0":{"ticker":"AAA","cik_str":1001}}))
    result=ingest(research=research,production=production,authorization=AUTHORIZATION_PHRASE,
        dry_run=False,limits=IngestionLimits(max_requests=1),transport=transport)
    assert result["status"]=="stopped" and result["stop_reason"]=="request_budget_exhausted"
    assert status(research,production)["retryable"]==1
