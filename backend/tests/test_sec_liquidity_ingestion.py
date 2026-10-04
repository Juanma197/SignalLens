from datetime import datetime, timezone
import json

import duckdb
import httpx
import pytest

from app.sec_liquidity_ingestion import (AUTHORIZATION_PHRASE, SECRequestClient,
    apply, status)
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
        assert db.execute("SELECT count(*) FROM sec_raw_response_provenance").fetchone()[0]==142
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
    client=SECRequestClient("SignalLens test ops@example.com",2,transport=httpx.MockTransport(handler),sleeper=lambda _:None,jitter=lambda:0)
    payload,_,_=client.get("https://data.sec.gov/submissions/CIK0000000001.json")
    assert payload["cik"]==1 and client.count==2
    with pytest.raises(ValueError,match="UNAPPROVED"):
        client.get("https://example.com/secret")


def test_cli_rejects_undocumented_budget_alias():
    from app.sec_ingestion_cli import parser
    with pytest.raises(SystemExit):
        parser().parse_args(["apply-sec-liquidity-evidence-ingestion","--max-requests","205"])
