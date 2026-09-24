from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import pandas as pd
import pytest
from app.global_research import (GlobalResearchRepository, RawFundamentalFact,
    learn_constrained_weights, normalize_metric, parse_operator_facts, robust_peer_scores, score_candidates)

NOW=datetime(2026,9,24,tzinfo=timezone.utc)
def fact(**changes):
    values=dict(security_id="US:X",report_id="r1",source_concept="Revenues",value=Decimal("10"),currency="USD",unit="USD",
      period_start=NOW.date(),period_end=NOW.date(),published_at=NOW-timedelta(days=2),available_at=NOW-timedelta(days=1),retrieved_at=NOW,
      accounting_standard="US-GAAP",source_document="https://sec.example/r1",source_provider="SEC",provenance={"accession":"1"})
    values.update(changes); return RawFundamentalFact(**values)

def test_publication_and_retrieval_boundaries_and_revision(tmp_path):
    repo=GlobalResearchRepository(tmp_path/"r.duckdb")
    old=fact(); revised=fact(report_id="r2",value=Decimal("12"),available_at=NOW+timedelta(days=1),retrieved_at=NOW+timedelta(days=2),revision_of_fact_id=old.fact_id)
    repo.import_facts([old,revised])
    assert repo.facts_as_of(NOW)[0]["value"]==Decimal("10.0000000000")
    assert repo.facts_as_of(NOW+timedelta(days=3))[0]["value"]==Decimal("12.0000000000")

def test_missing_is_not_zero_and_ifrs_us_gaap_mapping():
    assert normalize_metric("us-gaap:Revenues")=="revenue"
    assert normalize_metric("ifrs-full:Revenue")=="revenue"
    missing=fact(value=None,state="missing",accounting_standard="IFRS")
    assert missing.value is None

def test_operator_validation_and_currency_units():
    text="security_id,report_id,source_concept,value,currency,unit,period_start,period_end,published_at,available_at,accounting_standard,source_document\nGB:X,r,Revenue,100,GBP,GBP,2025-01-01,2025-12-31,2026-01-01T00:00:00Z,2026-01-02T00:00:00Z,IFRS,file://report\n"
    row=parse_operator_facts(text,source_provider="approved",retrieved_at=NOW)[0]
    assert (row.currency,row.unit,row.accounting_standard)==("GBP","GBP","IFRS")

def test_sector_relative_outlier_handling_and_fallback():
    frame=pd.DataFrame({"industry":["a"]*5,"sector":["s"]*5,"region":["EU"]*5,"accounting_standard":["IFRS"]*5,"v":[1,2,3,4,100]})
    scores=robust_peer_scores(frame,"v",minimum_peers=5)
    assert scores.notna().all() and scores.iloc[-1]==1

def test_risk_gates_confidence_and_determinism():
    frame=pd.DataFrame([{"security_id":"A","momentum":.8,"value":.7,"quality":.6,"catalyst":.5,"risk_penalty":.1,"coverage":1,"freshness":1,"hard_exclusion":False},
      {"security_id":"B","momentum":1,"value":1,"quality":1,"catalyst":1,"risk_penalty":0,"coverage":.2,"freshness":.2,"hard_exclusion":False}])
    a=score_candidates(frame); b=score_candidates(frame)
    pd.testing.assert_frame_equal(a,b); assert a.loc[a.security_id.eq("B"),"eligible"].item()==False

def test_learned_weights_use_only_pre_cutoff_rows():
    dates=pd.date_range("2024-01-01",periods=15,freq="MS",tz="UTC")
    rows=pd.DataFrame({"as_of":dates,"momentum":range(15),"value":range(15),"quality":range(15),"catalyst":range(15),"forward_return":range(15)})
    first=learn_constrained_weights(rows,cutoff=NOW,minimum_periods=12)
    rows.loc[len(rows)] = [NOW+timedelta(days=1),999,999,999,999,-999]
    assert first==learn_constrained_weights(rows,cutoff=NOW,minimum_periods=12)

def test_immutable_vintage_and_production_isolation(tmp_path):
    path=tmp_path/"r.duckdb"; repo=GlobalResearchRepository(path)
    scored=score_candidates(pd.DataFrame([{"security_id":"A","momentum":.8,"value":.7,"quality":.6,"catalyst":.5,"risk_penalty":0,"coverage":1,"freshness":1,"hard_exclusion":False}]))
    first=repo.create_vintage(universe_snapshot_id="s1",evaluated_at=NOW,scored=scored)
    second=repo.create_vintage(universe_snapshot_id="s1",evaluated_at=NOW,scored=scored)
    assert second["status"]=="already_exists" and first["vintage_id"]==second["vintage_id"]
    import duckdb
    with duckdb.connect(str(path),read_only=True) as db: assert "prediction_vintages" not in {r[0] for r in db.execute("show tables").fetchall()}

def test_validation_dry_run_can_be_byte_for_byte_non_mutating(tmp_path,monkeypatch):
    from app import global_research_cli as cli
    db=tmp_path/"r.duckdb"; db.write_bytes(b"unchanged"); before=hashlib.sha256(db.read_bytes()).digest()
    csv=tmp_path/"f.csv"; csv.write_text("security_id,report_id,source_concept,value,currency,unit,period_start,period_end,published_at,available_at,accounting_standard,source_document\nGB:X,r,Revenue,1,GBP,GBP,,2025-12-31,2026-01-01T00:00:00Z,2026-01-02T00:00:00Z,IFRS,file://x\n")
    args=cli.parser().parse_args(["fundamentals-validate","--file",str(csv),"--source","approved"])
    assert cli.execute(args,now=NOW)["mutated"] is False
    assert hashlib.sha256(db.read_bytes()).digest()==before
