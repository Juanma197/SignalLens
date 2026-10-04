from datetime import datetime, timezone
import json

import duckdb
import httpx
import pytest

from app.sec_liquidity_ingestion import (AUTHORIZATION_PHRASE, SECRequestClient,
    _initialize, apply, status)
from app.sec_liquidity_contract import (CONCEPT_CONTRACT_HASH,
    OPERATION_CONTRACT_VERSION, OPERATION_TYPE)
from app.sec_liquidity_plan import plan_sec_liquidity_evidence_ingestion
from test_sec_liquidity_plan import DECISION, databases


def fixture_for_all():
    submissions={}; facts={}
    for i in range(71):
        cik=f"{i+1:010}"; acc=f"0000000000-26-{i:06}"
        submissions[cik]={"cik":int(cik),"filings":{"recent":{"accessionNumber":[acc],"form":["10-Q"],"filingDate":["2026-08-01"],"acceptanceDateTime":["20260801120000"]}}}
        facts[cik]={"cik":int(cik),"facts":{"us-gaap":{
            "CashAndCashEquivalentsAtCarryingValue":{"units":{"USD":[{"val":100+i,"end":"2026-06-30","accn":acc,"fy":2026,"fp":"Q2"}]}},
            "MadeUpCashLabel":{"units":{"USD":[{"val":999,"end":"2026-06-30","accn":acc}]}},
        }}}
    return {"submissions":submissions,"companyfacts":facts}


def test_apply_exact_contract_raw_provenance_idempotency_and_status(tmp_path,monkeypatch):
    research,production=databases(tmp_path); production_bytes=production.read_bytes()
    plan=plan_sec_liquidity_evidence_ingestion(research_db=research,production_db=production,
        decision_at=DECISION,max_request_budget=205)
    monkeypatch.setattr(httpx.Client,"get",lambda *a,**k: (_ for _ in ()).throw(AssertionError("network used")))
    with pytest.raises(PermissionError):
        apply(research_db=research,production_db=production,decision_at=DECISION,
            plan_identifier=plan["plan_identifier"],max_request_budget=205,authorization="wrong",fixture=fixture_for_all())
    result=apply(research_db=research,production_db=production,decision_at=DECISION,
        plan_identifier=plan["plan_identifier"],max_request_budget=205,
        authorization=AUTHORIZATION_PHRASE,fixture=fixture_for_all())
    assert result["status"]=="completed" and result["completed_issuers"]==71
    assert result["provider_request_count"]==0 and production.read_bytes()==production_bytes
    with duckdb.connect(str(research),read_only=True) as db:
        assert db.execute("SELECT count(*) FROM sec_liquidity_raw_provenance").fetchone()[0]==142
        assert db.execute("SELECT count(*) FROM sec_facts WHERE concept='CashAndCashEquivalentsAtCarryingValue'").fetchone()[0]==71
        assert db.execute("SELECT count(*) FROM sec_facts WHERE concept='MadeUpCashLabel'").fetchone()[0]==0
    report=status(research_db=research,production_db=production)
    assert report["completed_issuer_count"]==71 and report["failure_samples"]["returned_count"]<=10
    assert all(report[key]==[] for key in ("rankings","recommendations","selections","vintages"))


def test_client_budget_host_content_and_transient_retry():
    attempts=0
    def handler(request):
        nonlocal attempts; attempts+=1
        if attempts==1: return httpx.Response(503,headers={"content-type":"application/json"},json={})
        return httpx.Response(200,headers={"content-type":"application/json"},json={"cik":1})
    client=SECRequestClient("SignalLens test sec-ops@signallens.invalid",2,transport=httpx.MockTransport(handler),sleeper=lambda _:None,jitter=lambda:0)
    payload,_,_=client.get("https://data.sec.gov/submissions/CIK0000000001.json")
    assert payload["cik"]==1 and client.count==2
    with pytest.raises(ValueError,match="UNAPPROVED"):
        client.get("https://example.com/secret")


def test_cli_rejects_undocumented_budget_alias():
    from app.sec_ingestion_cli import parser
    with pytest.raises(SystemExit):
        parser().parse_args(["apply-sec-liquidity-evidence-ingestion","--max-requests","205"])


def _seed_operator_legacy(db_path):
    with duckdb.connect(str(db_path)) as db:
        db.execute("CREATE TABLE sec_ingestion_runs(run_id VARCHAR,started_at TIMESTAMPTZ,status VARCHAR,request_budget INTEGER,request_count INTEGER,plan_id VARCHAR)")
        db.execute("INSERT INTO sec_ingestion_runs VALUES ('generic',now(),'completed',205,193,NULL)")
        db.execute("CREATE TABLE sec_checkpoints(security_id VARCHAR,status VARCHAR)")
        db.executemany("INSERT INTO sec_checkpoints VALUES (?,?)",[(f'old-{i}', 'completed' if i<89 else 'permanent_failure') for i in range(100)])
        db.execute("CREATE TABLE sec_failures(failure_id VARCHAR,reason_code VARCHAR)")
        db.executemany("INSERT INTO sec_failures VALUES (?,?)",[(str(i),'provider_not_found') for i in range(11)])


def test_operator_legacy_shape_is_ignored_and_status_is_byte_read_only(tmp_path):
    research,production=databases(tmp_path); _seed_operator_legacy(research)
    before=research.read_bytes()
    report=status(research_db=research,production_db=production,decision_at=DECISION)
    assert research.read_bytes()==before
    assert report["latest_run"] is None and report["completed_issuer_count"]==0
    assert report["failed_issuer_count"]==0 and report["actual_provider_request_count"]==0
    assert report["remaining_issuer_count"]==71 and report["checkpoint_states"]=={}
    assert report["failure_reason_counts"]=={} and report["production_unchanged_evidence"]["state"]=="not_applicable"


def test_legacy_rows_survive_migration_and_do_not_skip_full_fixture(tmp_path):
    research,production=databases(tmp_path); _seed_operator_legacy(research)
    with duckdb.connect(str(research)) as db:
        before=[db.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in ("sec_ingestion_runs","sec_checkpoints","sec_failures")]
        _initialize(db); _initialize(db)
        after=[db.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in ("sec_ingestion_runs","sec_checkpoints","sec_failures")]
    assert before==after==[1,100,11]
    plan=plan_sec_liquidity_evidence_ingestion(research_db=research,production_db=production,decision_at=DECISION,max_request_budget=205)
    result=apply(research_db=research,production_db=production,decision_at=DECISION,plan_identifier=plan["plan_identifier"],max_request_budget=205,authorization=AUTHORIZATION_PHRASE,fixture=fixture_for_all())
    assert result["completed_issuers"]==71 and result["skipped_completed_issuers"]==0


@pytest.mark.parametrize("agent",["", "SignalLens YOUR_REAL_EMAIL_ADDRESS", "SignalLens ops@example.com", "repository placeholder"])
def test_placeholder_user_agent_rejected_before_transport_or_schema(tmp_path,agent):
    research,production=databases(tmp_path)
    plan=plan_sec_liquidity_evidence_ingestion(research_db=research,production_db=production,decision_at=DECISION,max_request_budget=205)
    called=False
    def handler(request):
        nonlocal called; called=True
        return httpx.Response(500)
    with pytest.raises(ValueError,match="SEC_USER_AGENT_INVALID_REDACTED"):
        apply(research_db=research,production_db=production,decision_at=DECISION,plan_identifier=plan["plan_identifier"],max_request_budget=205,authorization=AUTHORIZATION_PHRASE,user_agent=agent,transport=httpx.MockTransport(handler))
    assert not called
    with duckdb.connect(str(research),read_only=True) as db:
        assert "sec_liquidity_runs" not in {r[0] for r in db.execute("SHOW TABLES").fetchall()}


def test_incompatible_checkpoint_and_other_lock_are_never_adopted_or_deleted(tmp_path):
    research,production=databases(tmp_path)
    with duckdb.connect(str(research)) as db:
        _initialize(db)
        db.execute("INSERT INTO sec_liquidity_ingestion_lock(lock_name,operation_type,operation_contract_version,concept_contract_hash,lineage_id,run_id,plan_id,started_at,last_progress_at) VALUES ('sec-liquidity','general_sec','0','other','x','other-run','other-plan',now(),now())")
    plan=plan_sec_liquidity_evidence_ingestion(research_db=research,production_db=production,decision_at=DECISION,max_request_budget=205)
    with pytest.raises(RuntimeError,match="OTHER_DATABASE_WRITER_LOCK"):
        apply(research_db=research,production_db=production,decision_at=DECISION,plan_identifier=plan["plan_identifier"],max_request_budget=205,authorization=AUTHORIZATION_PHRASE,fixture=fixture_for_all())
    with duckdb.connect(str(research),read_only=True) as db:
        assert db.execute("SELECT run_id FROM sec_liquidity_ingestion_lock").fetchone()[0]=="other-run"


def test_compatible_partial_retry_uses_fresh_attempt_budget_and_validated_skip(tmp_path):
    spaced=tmp_path/"database paths with spaces"; spaced.mkdir()
    research,production=databases(spaced)
    first=plan_sec_liquidity_evidence_ingestion(research_db=research,production_db=production,decision_at=DECISION,max_request_budget=205)
    partial=apply(research_db=research,production_db=production,decision_at=DECISION,plan_identifier=first["plan_identifier"],max_request_budget=205,authorization=AUTHORIZATION_PHRASE,fixture=fixture_for_all(),interrupt_after=1)
    assert partial["status"]=="partial" and partial["completed_issuers"]==1
    second=plan_sec_liquidity_evidence_ingestion(research_db=research,production_db=production,decision_at=DECISION,max_request_budget=205)
    resumed=apply(research_db=research,production_db=production,decision_at=DECISION,plan_identifier=second["plan_identifier"],max_request_budget=205,authorization=AUTHORIZATION_PHRASE,fixture=fixture_for_all())
    assert resumed["completed_issuers"]==70 and resumed["skipped_completed_issuers"]==1
    assert resumed["remaining_budget"]==205 and resumed["request_budget_semantics"]=="per_apply_attempt"


def test_incompatible_contract_checkpoint_does_not_skip(tmp_path):
    research,production=databases(tmp_path)
    with duckdb.connect(str(research)) as db:
        _initialize(db)
        db.execute("""INSERT INTO sec_liquidity_checkpoints VALUES
          ('foreign-lineage','security-000','0000000001',?,?,?,'foreign-run','foreign-plan',?,'US000','US000','completed',now(),1,true)""",
          [OPERATION_TYPE,"0.9.0","different-hash",DECISION])
    plan=plan_sec_liquidity_evidence_ingestion(research_db=research,production_db=production,decision_at=DECISION,max_request_budget=205)
    result=apply(research_db=research,production_db=production,decision_at=DECISION,plan_identifier=plan["plan_identifier"],max_request_budget=205,authorization=AUTHORIZATION_PHRASE,fixture=fixture_for_all())
    assert result["completed_issuers"]==71 and result["skipped_completed_issuers"]==0
