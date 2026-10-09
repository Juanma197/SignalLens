import json
import os
from decimal import Decimal
from enum import Enum
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import httpx
import pytest

from app.international_fundamentals import capability_report
from app.sec_capability import fingerprint
from app.sec_ingestion import (AUTHORIZATION_PHRASE, IngestionLimits, ingest, plan,
                               _aggregate_map, _json_value, initialize_schema,
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


def post_pilot_databases(tmp_path):
    """Offline reproduction of the 100-name, three-request operator pilot."""
    research, production = databases(tmp_path)
    with duckdb.connect(str(research)) as db:
        db.execute("DELETE FROM security_listings")
        db.executemany(
            "INSERT INTO security_listings VALUES ('r',?,?,?,?,?,?,true,NULL)",
            [(f"sec{i:03}", f"T{i:03}", f"T{i:03}.US", "US", "USD", "common_stock")
             for i in range(100)],
        )
    initialize_schema(research)
    now = datetime(2026, 9, 30, 12, tzinfo=timezone.utc)
    with duckdb.connect(str(research)) as db:
        db.execute("INSERT INTO sec_issuers VALUES ('sec000','T000.US','T000','0000000001','Issuer','fixture',?)", [now])
        db.execute("""INSERT INTO sec_ingestion_runs VALUES
            ('pilot',?,?,'stopped',false,3,900,3,714,0,337,'request_budget_exhausted')""", [now, now])
        db.execute("INSERT INTO sec_checkpoints VALUES ('sec000','T000.US','T000','0000000001','completed','pilot',?,1)", [now])
        db.execute("INSERT INTO sec_checkpoints VALUES ('sec001','T001.US','T001',NULL,'retryable_failure','pilot',?,1)", [now])
        db.execute("""INSERT INTO sec_facts
            SELECT sha256(i::VARCHAR),'sec000','T000.US','T000','0000000001','us-gaap',
              CASE WHEN i%2=0 THEN 'Assets' ELSE 'NetIncomeLoss' END,i::DOUBLE,'USD',
              CASE WHEN i%3=0 THEN NULL ELSE 'USD' END,
              CASE WHEN i%5=0 THEN NULL ELSE DATE '2025-01-01' END,DATE '2025-12-31',2025,'FY',NULL,'10-K',
              'accession-'||i,DATE '2026-02-01',TIMESTAMPTZ '2026-02-01 12:00:00+00',false,i<337,
              'offline-fixture',?
            FROM range(714) AS facts(i)""", [now])
        db.execute("""INSERT INTO sec_failures VALUES
            ('failure','pilot','sec001','T001.US','ingestion','request_budget_exhausted',true,1,?,NULL)""", [now])
    return research, production


def test_empty_initialized_status_is_json_safe_and_immutable(tmp_path):
    research, production = databases(tmp_path)
    initialize_schema(research)
    before = (fingerprint(research), fingerprint(production))
    report = status(research, production)
    assert report["observations"] == report["revisions"] == 0
    assert report["public_availability_range"] == {"earliest": None, "latest": None}
    json.dumps(report, sort_keys=True)
    assert before == (fingerprint(research), fingerprint(production))


def test_exact_post_pilot_status_is_safe_consistent_and_read_only(tmp_path):
    research, production = post_pilot_databases(tmp_path)
    before = (fingerprint(research), fingerprint(production))
    with duckdb.connect(str(research), read_only=True) as db:
        legacy_currencies = dict(db.execute(
            "SELECT currency,count(*) FROM sec_facts GROUP BY currency ORDER BY currency"
        ).fetchall())
    with pytest.raises(TypeError, match="not supported between instances"):
        json.dumps({"currencies": legacy_currencies}, sort_keys=True)
    report = status(research, production)
    assert (report["selected_us_securities"], report["completed"], report["retryable"],
            report["permanently_failed"], report["pending"]) == (100, 1, 1, 0, 98)
    assert report["observations"] == 714 and report["revisions"] == 337
    assert report["currencies"] == {"(null)": 238, "USD": 476}
    assert report["checkpoint_consistency"]["status"] == "consistent"
    assert report["checkpoint_consistency"]["completed_in_latest_run"] == 1
    assert report["latest_run"]["stop_reason"] == "request_budget_exhausted"
    assert report["generated_rankings"] == report["generated_candidates"] == 0
    # This was the operator-visible crash: sort_keys compared None with "USD".
    json.dumps(report, sort_keys=True)
    assert before == (fingerprint(research), fingerprint(production))

    resume = plan(research, production)
    assert resume["completed"] == 1 and resume["pending"] == 99
    assert resume["expected_requests"] == 199
    assert "T000.US" not in resume["symbol_samples"]
    assert before == (fingerprint(research), fingerprint(production))


def test_status_counts_completed_retryable_permanent_and_pending_states(tmp_path):
    research, production = post_pilot_databases(tmp_path)
    now = datetime(2026, 9, 30, 12, tzinfo=timezone.utc)
    with duckdb.connect(str(research)) as db:
        db.execute("INSERT INTO sec_checkpoints VALUES ('sec002','T002.US','T002',NULL,'permanent_failure','pilot',?,1)", [now])
        # A checkpoint outside the active catalogue must not distort its counts.
        db.execute("INSERT INTO sec_checkpoints VALUES ('stale','OLD.US','OLD',NULL,'completed','pilot',?,1)", [now])
    report = status(research, production)
    assert (report["completed"], report["retryable"], report["permanently_failed"], report["pending"]) == (1, 1, 1, 97)


def test_duckdb_scalar_and_mixed_aggregate_values_are_json_safe():
    class State(Enum):
        COMPLETE = "complete"

    assert _aggregate_map([(None, 2), ("USD", Decimal("3")), (State.COMPLETE, 1)]) == {
        "(null)": 2, "USD": 3, "complete": 1,
    }
    assert _json_value(datetime(2026, 1, 2, tzinfo=timezone.utc)) == "2026-01-02T00:00:00+00:00"


def test_reingesting_a_company_recognises_its_stored_facts(tmp_path):
    """Stored DATE periods and parsed ISO-text periods must compare equal, so a
    re-run (e.g. after its checkpoint is cleared) inserts nothing new."""
    research,production=databases(tmp_path)
    first=run(research,production)
    with duckdb.connect(str(research)) as db:
        before=db.execute("SELECT count(*) FROM sec_facts").fetchone()[0]
        db.execute("DELETE FROM sec_checkpoints")
    again=run(research,production)
    assert again["selected"]==1 and again["inserted"]==0 and again["unchanged"]==first["inserted"]
    with duckdb.connect(str(research),read_only=True) as db:
        assert db.execute("SELECT count(*) FROM sec_facts").fetchone()[0]==before


def test_ingestion_keeps_the_two_documents_it_downloads(tmp_path):
    """Submissions (industry code) and companyfacts (cover-page shares) are retained
    with a verifiable digest, so no second download is needed."""
    import hashlib
    research,production=databases(tmp_path)
    run(research,production)
    with duckdb.connect(str(research),read_only=True) as db:
        rows=db.execute("SELECT endpoint_class,cik,payload_sha256,byte_count,payload_json FROM sec_raw_payloads ORDER BY endpoint_class").fetchall()
    assert [r[0] for r in rows]==["companyfacts","submissions"] and all(len(r[1])==10 for r in rows)
    assert all(hashlib.sha256(r[4].encode()).hexdigest()==r[2] and len(r[4].encode())==r[3] for r in rows)
    # Only the fields read later are kept: no filing lists, no financial statements.
    import json
    kept={r[0]:json.loads(r[4]) for r in rows}
    assert "filings" not in kept["submissions"] and set(kept["companyfacts"]["facts"])<={"dei"}
    run(research,production)  # a resumed run keeps one copy
    with duckdb.connect(str(research),read_only=True) as db:
        assert db.execute("SELECT count(*) FROM sec_raw_payloads").fetchone()[0]==2
