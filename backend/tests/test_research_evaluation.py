from datetime import datetime, timedelta, timezone
import hashlib, json
import pandas as pd
import pytest
from app.research_evaluation import (PromotionPolicy, ResearchEvaluationRepository,
    bootstrap_mean_ci, create_manifest, evaluate_gates, leakage_findings, trading_cost)

NOW=datetime(2026,9,24,tzinfo=timezone.utc)
def passing_metrics():
    return {"monthly_vintages":120,"eligible_securities":500,"securities_by_region":{"US":200,"UK":100,"CA":50},
      "missingness":.1,"coefficient_drift":.1,"annual_turnover":4,"maximum_drawdown":-.2,
      "top_one_excess_ci":[.01,.03],"top_three_excess_ci":[.005,.02],"after_cost_excess_return":.04,
      "region_robust":True,"sector_robust":True,"regime_robust":True,"leakage_findings":0,
      "largest_security_contribution":.1,"untouched_forward_test":True,"historical_membership_complete":True,"delistings_included":True,
      "current_universe_coverage_pass":True,"prices_fresh":True,"fx_fresh":True,"fundamentals_fresh":True,
      "model_evaluation_complete":True,"hard_risk_gates_passed":True}

def test_future_information_and_next_session_rejected():
    rows=pd.DataFrame([{"security_id":"X","decision_at":NOW,"universe_available_at":NOW,
      "price_available_at":NOW+timedelta(seconds=1),"feature_available_at":NOW,"execution_at":NOW}])
    assert {x["reason"] for x in leakage_findings(rows)}=={"future_information","not_next_session"}

def test_historical_membership_delistings_and_gates_required():
    metrics=passing_metrics(); assert evaluate_gates(metrics)["passed"]
    metrics["historical_membership_complete"]=False; metrics["delistings_included"]=False
    assert set(evaluate_gates(metrics)["failed"]) >= {"historical_membership","delistings"}

def test_costs_stamp_duty_and_confidence_interval():
    assert trading_cost("UK","buy",1000)==pytest.approx(5.8)
    assert trading_cost("UK","sell",1000)==pytest.approx(.8)
    assert bootstrap_mean_ci([.01,.02,.03],seed=2)==bootstrap_mean_ci([.01,.02,.03],seed=2)

def test_manifest_is_deterministic_and_hashed(tmp_path):
    source=tmp_path/"rows.csv"; source.write_bytes(b"a,b\n1,2\n")
    kw=dict(dataset_version="v1",configuration={"monthly":True},code_commit="abc",retrieved_at=NOW,
            coverage={"date_range":["2016-01-01","2026-09-24"],"limitations":["incomplete"]})
    one=create_manifest([source],**kw); two=create_manifest([source],**kw)
    assert one==two and one["sources"][0]["sha256"]==hashlib.sha256(source.read_bytes()).hexdigest()

def test_separate_database_immutable_shadow_and_no_candidate_behavior(tmp_path):
    production=tmp_path/"prod.duckdb"
    with pytest.raises(ValueError): ResearchEvaluationRepository(production,production_path=production)
    repo=ResearchEvaluationRepository(tmp_path/"research.duckdb",production_path=production)
    failed=passing_metrics(); failed["missingness"]=1
    repo.store_evaluation("failed","v1",failed)
    assert repo.create_shadow("failed",NOW,[])["status"]=="withheld"
    repo.store_evaluation("passed","v1",passing_metrics())
    result=repo.create_shadow("passed",NOW,[])
    assert result["status"]=="completed" and result["candidates"]==0 and not production.exists()
    assert repo.create_shadow("passed",NOW,[])["status"]=="already_exists"

def test_shadow_rejects_stale_and_hard_risk_candidates(tmp_path):
    repo=ResearchEvaluationRepository(tmp_path/"research.duckdb")
    repo.store_evaluation("passed","v1",passing_metrics())
    candidates=[{"security_id":"STALE","eligible":True,"confidence":.9,"price_age_sessions":3,
                 "fx_age_sessions":1,"fundamental_age_days":10},
                {"security_id":"RISK","eligible":True,"confidence":.9,"hard_risk":True,
                 "price_age_sessions":1,"fx_age_sessions":1,"fundamental_age_days":10}]
    assert repo.create_shadow("passed",NOW,candidates)["candidates"]==0

def test_dry_run_is_byte_for_byte_non_mutating(tmp_path):
    from app import research_evaluation_cli as cli
    db=tmp_path/"research.duckdb"; db.write_bytes(b"unchanged")
    metrics=tmp_path/"metrics.json"; metrics.write_text(json.dumps(passing_metrics()))
    before=hashlib.sha256(db.read_bytes()).digest()
    args=cli.parser().parse_args(["--research-db",str(db),"walk-forward-evaluate","--metrics",str(metrics),"--evaluation-id","e","--model-version","v","--dry-run"])
    assert cli.execute(args)["mutated"] is False and hashlib.sha256(db.read_bytes()).digest()==before
