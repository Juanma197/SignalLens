from datetime import datetime, timezone
import json
from pathlib import Path

import duckdb
import pytest

from app.investment_research import (CONFIGURATION_HASH, CostAssumptions, REASON_CODES,
    TRACK_B_LABELS, comparable_universe_readiness, company_factor_preview, total_return,
    validate_alias)
from app.investment_evidence import initialize_schema
from model_readiness_fixture import create_research_fixture

def test_track_a_frozen_and_track_b_has_required_boundaries():
    assert CONFIGURATION_HASH == "7b11264778fd120c03c820275d9c048d002bdb8564510fcf989cb590ce1b7ebd"
    assert TRACK_B_LABELS == ["TRACK B RESEARCH FOUNDATION — NOT A MODEL",
        "NO PURCHASE RECOMMENDATION", "NO CANDIDATES GENERATED", "ZERO VALIDATION CREDIT"]

def test_alias_requires_complete_semantic_compatibility():
    good={"concept":"Revenues","unit":"USD","duration":"duration","sign":"credit","meaning":"revenue"}
    assert validate_alias("revenue",good)
    for key,bad in (("unit","shares"),("duration","instant"),("sign","debit"),("meaning","gross profit")):
        changed=good|{key:bad}; assert not validate_alias("revenue",changed)
    assert not validate_alias("revenue",good|{"concept":"SalesRevenueNet"})

def test_cost_hash_deterministic_and_costs_never_silently_zero():
    a=CostAssumptions(); b=CostAssumptions()
    assert a.configuration_hash == b.configuration_hash
    result=total_return(start_price=100,end_price=110,cash_dividends=2,assumptions=a)
    assert result["gross_total_return"] == pytest.approx(.12)
    assert result["net_total_return"] < result["gross_total_return"]
    assert result["cost_fraction"] == pytest.approx(.005)
    assert result["spread_source"] == "estimated_conservative"

def test_observed_spread_commission_fx_slippage_and_denominator_withholding():
    assumptions=CostAssumptions(commission_bps=4,fx_bps=6,slippage_bps=8,turnover=.5)
    result=total_return(start_price=10,end_price=11,cash_dividends=.1,assumptions=assumptions,spread_bps=12)
    assert result["cost_fraction"] == pytest.approx((12+4+6+8)*.5/10000)
    assert result["spread_source"] == "observed"
    assert total_return(start_price=0,end_price=1,cash_dividends=0,assumptions=assumptions) == {"status":"withheld","reason_code":"unreliable_denominator"}

def test_reason_codes_are_stable_bounded_and_spec_has_no_weights():
    assert len(REASON_CODES) == len(set(REASON_CODES)) and len(REASON_CODES) < 50
    spec=json.loads((Path(__file__).parents[1]/"app/undervalued_quality_draft_v0.1.0.json").read_text())
    assert spec["weights"] is None
    assert len(spec["factors"]) == 6
    assert "prospective evidence after registration" in spec["future_registration_requirements"]

def test_operator_shaped_materialized_classification_is_consumed_read_only(tmp_path):
    research=tmp_path/"research.duckdb"; production=tmp_path/"production.duckdb"
    create_research_fixture(research,per_region=100)
    with duckdb.connect(str(production)) as db: db.execute("CREATE TABLE marker(value INTEGER)")
    decision=datetime(2026,9,28,20,tzinfo=timezone.utc)
    with duckdb.connect(str(research)) as db:
        initialize_schema(db)
        for index in range(100):
            sid=f"security-us-{index}"; symbol=f"ALPHA{index}.US"
            kind="us_operating_company" if index<71 else "classification_unavailable"
            reason="concordant_authoritative_evidence" if index<71 else "classification_evidence_unavailable"
            db.execute("""INSERT INTO security_classification_evidence(
              evidence_key,security_id,qualified_symbol,security_type,classification_reason,
              evidence_source_family,source_record_identifier,durable_identifier,public_at,
              retrieved_at,available_at,materialized_at,confidence_category,review_required,
              provenance,is_current) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,true)""",
              [f"c{index}",sid,symbol,kind,reason,"stored_evidence_hierarchy",f"source-{index}",sid,
               decision,decision,decision,decision,"high" if index<71 else "unavailable",
               kind=="classification_unavailable",json.dumps({"fixture":True})])
        db.execute("""INSERT INTO canonical_factor_evidence(
          evidence_key,security_id,qualified_symbol,canonical_field,value,unit,currency,
          accession_or_source_identifier,public_at,retrieved_at,available_at,materialized_at,
          original_concept_or_field,alias_contract_version,sign_convention,reliability_state,
          provenance,lineage) VALUES ('f1','security-us-0','ALPHA0.US','revenue',100,'USD','USD',
          'fixture',?,?,?,?, 'Revenues','test','reported_signed','usable','{}','{}')""",
          [decision,decision,decision,decision])
    before=(research.read_bytes(),production.read_bytes())
    readiness=comparable_universe_readiness(research_db=research,production_db=production,
      decision_at=decision,max_samples=10)
    preview=company_factor_preview(research_db=research,production_db=production,
      decision_at=decision,qualified_symbol="ALPHA0.US")
    assert readiness["counts_by_security_type"]=={
      "classification_unavailable":29,"us_operating_company":71}
    assert readiness["ordinary_operating_company_universe_count"]==71
    assert readiness["field_coverage"]["revenue"]["total"]==71
    assert preview["inputs"]["canonical_inputs"]["revenue"]["value"]==100
    assert preview["classification"]==readiness["classification_samples"]["ALPHA0.US"]
    assert preview["comparable_universe_eligible"]
    assert preview["calculations"]["financial_strength"].get("reason_code")!="financial_sector_not_comparable"
    assert not any(preview[key] for key in ("recommendations","candidates","rankings"))
    assert readiness["validation_credit"]==0 and (research.read_bytes(),production.read_bytes())==before

def test_absent_materialization_uses_only_explicit_legacy_instrument_type(tmp_path):
    research=tmp_path/"research.duckdb"; production=tmp_path/"production.duckdb"
    create_research_fixture(research)
    with duckdb.connect(str(production)) as db: db.execute("CREATE TABLE marker(value INTEGER)")
    result=comparable_universe_readiness(research_db=research,production_db=production,
      decision_at=datetime(2026,9,28,20,tzinfo=timezone.utc))
    assert result["ordinary_operating_company_universe_count"]==1
    assert result["classification_samples"]["ALPHA.US"]["evidence_source"]=="legacy_catalogue_instrument_type"
