from datetime import date, datetime, timezone
import json, subprocess, sys
from pathlib import Path

import duckdb
import pytest

from app.liquidity_evidence import (AGGREGATE_MAXIMUM_BYTES, CONTRACT_MAXIMUM_BYTES,
    PREVIEW_MAXIMUM_BYTES, compact_utf8_size, evidence_discovery,
    contract_assessment, company_preview)
from app.liquidity_inventory import raw_canonical_inventory, evidence_gap_assessment
from app.liquidity_measurement import VALIDATOR_VERSION, validate_measurement
from app.liquidity_compatibility import (compatibility_audit,
    plan_canonical_materialization, reconcile_identity_sets)

DECISION=datetime(2026,10,2,18,15,tzinfo=timezone.utc)

def fixture(tmp_path):
    root=tmp_path/"liquidity files with spaces"; root.mkdir()
    research=root/"research.duckdb"; production=root/"production.duckdb"
    with duckdb.connect(str(production)) as db: db.execute("CREATE TABLE marker(x INT)")
    with duckdb.connect(str(research)) as db:
        db.execute("""CREATE TABLE security_classification_evidence(security_id VARCHAR,
          qualified_symbol VARCHAR,security_type VARCHAR,public_at TIMESTAMPTZ,
          retrieved_at TIMESTAMPTZ,available_at TIMESTAMPTZ)""")
        db.execute("""CREATE TABLE sec_facts(fact_key VARCHAR,security_id VARCHAR,
          qualified_symbol VARCHAR,taxonomy VARCHAR,taxonomy_version VARCHAR,concept VARCHAR,
          value DOUBLE,unit VARCHAR,currency VARCHAR,period_start DATE,period_end DATE,
          form VARCHAR,accession_number VARCHAR,public_at TIMESTAMPTZ,retrieved_at TIMESTAMPTZ)""")
        stamp=datetime(2026,7,1,tzinfo=timezone.utc)
        for sid,symbol in (("a","AAA.US"),("b","BBB.US")):
            db.execute("INSERT INTO security_classification_evidence VALUES (?,?,'us_operating_company',?,?,?)",[sid,symbol,stamp,stamp,stamp])
        def fact(sid,symbol,concept,value,end=date(2026,6,30),unit="USD",currency="USD",start=None,public=stamp):
            db.execute("INSERT INTO sec_facts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
              [f"{sid}-{concept}-{end}",sid,symbol,"us-gaap","2026",concept,value,unit,currency,start,end,"10-Q",f"acc-{sid}-{concept}",public,public])
        for concept,value in (("AssetsCurrent",100),("LiabilitiesCurrent",40),("Assets",250),
          ("InventoryNet",20),("CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",35),
          ("RestrictedCashAndCashEquivalentsCurrent",5)):
            fact("a","AAA.US",concept,value)
        fact("b","BBB.US","AssetsCurrent",90)
        fact("b","BBB.US","LiabilitiesCurrent",0)
        fact("b","BBB.US","CashAndCashEquivalentsAtCarryingValue",15)
        fact("b","BBB.US","CashAndCashEquivalentsAtCarryingValue",99,public=datetime(2026,10,3,tzinfo=timezone.utc))
        fact("b","BBB.US","RestrictedCashAndCashEquivalentsCurrent",2,end=date(2026,3,31))
        fact("b","BBB.US","AccountsReceivableNetCurrent",10,unit="shares")
        fact("b","BBB.US","WorkingCapital",20,start=date(2026,1,1))
    return research,production

def test_discovery_constructions_cash_separation_and_read_only(tmp_path):
    research,production=fixture(tmp_path); before=(research.read_bytes(),production.read_bytes())
    report=evidence_discovery(research_db=research,production_db=production,decision_at=DECISION)
    assert report["comparable_company_count"]==2
    assert report["field_diagnosis_counts"]["unrestricted_cash"]["qualifying component-based construction may be possible"]==1
    assert report["field_diagnosis_counts"]["unrestricted_cash"]["qualifying direct fact exists but alias is unsupported"]==1
    assert all(x["status"]=="proposed_not_authorized" for x in report["alias_proposals"]["items"])
    assert (research.read_bytes(),production.read_bytes())==before
    assert compact_utf8_size(report)<=AGGREGATE_MAXIMUM_BYTES

def test_preview_exact_semantics_and_incompatible_decomposition(tmp_path):
    research,production=fixture(tmp_path)
    a=company_preview(research_db=research,production_db=production,decision_at=DECISION,qualified_symbol="AAA.US")["company"]
    cash=[x for x in a["possible_constructions"] if x["field"]=="unrestricted_cash"]
    assert cash[0]["value"]==30
    assert any(x["field"]=="working_capital" and x["value"]==60 for x in a["possible_constructions"])
    b=company_preview(research_db=research,production_db=production,decision_at=DECISION,qualified_symbol="BBB.US")["company"]
    assert not [x for x in b["possible_constructions"] if x["field"]=="unrestricted_cash"]
    assert b["denominator_warnings"]==["zero_current_liabilities"]
    text=json.dumps(b,sort_keys=True)
    assert "99" not in text and "post-decision evidence only" not in text

def test_contract_denominators_quick_ratio_and_negative_working_capital(tmp_path):
    research,production=fixture(tmp_path)
    report=contract_assessment(research_db=research,production_db=production,decision_at=DECISION)
    assert report["construction_assessments"]["current_ratio"]["company_coverage"]==1
    assert report["construction_assessments"]["current_ratio"]["withholding_counts"]["zero_denominator"]==1
    assert report["construction_assessments"]["quick_ratio"]["company_coverage"]==1
    assert report["construction_assessments"]["working_capital"]["company_coverage"]==2
    assert report["contract_selected"] is None and report["validation_credit"]==0
    assert compact_utf8_size(report)<=CONTRACT_MAXIMUM_BYTES

def test_timezone_future_cli_errors_and_bounds(tmp_path):
    research,production=fixture(tmp_path)
    with pytest.raises(Exception): evidence_discovery(research_db=research,production_db=production,decision_at=datetime(2026,1,1))
    command=[sys.executable,"-m","app.investment_research_cli","liquidity-company-preview",
      "--research-db",str(research),"--production-db",str(production),"--decision-at",DECISION.isoformat(),"--qualified-symbol","NO.US"]
    result=subprocess.run(command,cwd=Path(__file__).parents[1],capture_output=True,text=True)
    assert result.returncode==1 and str(research) not in result.stderr
    assert json.loads(result.stderr)["error"]["message"]=="investment research request failed; details redacted"
    preview=company_preview(research_db=research,production_db=production,decision_at=DECISION,qualified_symbol="AAA.US")
    assert compact_utf8_size(preview)<=PREVIEW_MAXIMUM_BYTES
    for key in ("rankings","candidates","recommendations","selections","vintages","validation_observations"):
        assert preview[key]==[]

def inventory_fixture(tmp_path):
    root=tmp_path/"raw canonical files with spaces"; root.mkdir()
    research=root/"research evidence.duckdb"; production=root/"production evidence.duckdb"
    stamp=datetime(2026,7,1,tzinfo=timezone.utc)
    with duckdb.connect(str(production)) as db:
        db.execute("CREATE TABLE marker(x INT)")
    with duckdb.connect(str(research)) as db:
        db.execute("""CREATE TABLE security_classification_evidence(security_id VARCHAR,
          qualified_symbol VARCHAR,security_type VARCHAR,public_at TIMESTAMPTZ,
          retrieved_at TIMESTAMPTZ,available_at TIMESTAMPTZ)""")
        db.execute("""CREATE TABLE sec_issuers(security_id VARCHAR,qualified_symbol VARCHAR,
          ticker VARCHAR,cik VARCHAR,issuer_name VARCHAR,mapping_source VARCHAR,mapped_at TIMESTAMPTZ)""")
        db.execute("""CREATE TABLE sec_facts(fact_key VARCHAR,security_id VARCHAR,
          qualified_symbol VARCHAR,ticker VARCHAR,cik VARCHAR,taxonomy VARCHAR,concept VARCHAR,
          value DOUBLE,unit VARCHAR,currency VARCHAR,period_start DATE,period_end DATE,
          form VARCHAR,accession_number VARCHAR,filed_date DATE,public_at TIMESTAMPTZ,
          retrieved_at TIMESTAMPTZ)""")
        db.execute("""CREATE TABLE canonical_factor_evidence(security_id VARCHAR,
          qualified_symbol VARCHAR,canonical_field VARCHAR,value DOUBLE,unit VARCHAR,currency VARCHAR,
          period_start DATE,period_end DATE,instant_date DATE,accession_or_source_identifier VARCHAR,
          public_at TIMESTAMPTZ,retrieved_at TIMESTAMPTZ,available_at TIMESTAMPTZ,
          reliability_state VARCHAR,original_concept_or_field VARCHAR)""")
        for i in range(11):
            sid=f"s{i}"; symbol=f"S{i:02}.US"; cik=f"{i+1:010}"
            db.execute("INSERT INTO security_classification_evidence VALUES (?,?,'us_operating_company',?,?,?)",[sid,symbol,stamp,stamp,stamp])
            db.execute("INSERT INTO sec_issuers VALUES (?,?,?,?,'Issuer','sec_ticker_map',?)",[sid,symbol,f"S{i:02}",cik,stamp])
        def raw(i,concept,value=10,unit="USD",start=None,end=date(2026,6,30),public=stamp,taxonomy="us-gaap"):
            db.execute("INSERT INTO sec_facts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
              [f"f{i}-{concept}-{value}",f"s{i}",f"S{i:02}.US",f"S{i:02}",f"{i+1:010}",taxonomy,concept,value,unit,"USD",start,end,"10-Q",f"acc{i}-{concept}",end,public,public])
        raw(0,"AssetsCurrent",100) # compatible raw, omitted
        raw(1,"Assets",200) # broader does not satisfy current assets
        raw(2,"LiabilitiesCurrent",20,public=datetime(2026,10,3,tzinfo=timezone.utc))
        raw(3,"CashAndCashEquivalentsAtCarryingValue",30,end=date(2024,1,1))
        raw(4,"AssetsCurrent",40,unit="shares")
        raw(5,"LiabilitiesCurrent",50,start=date(2026,1,1))
        raw(6,"CustomCurrentAssets",60,taxonomy="issuer-2026")
        raw(7,"AssetsCurrent",70); raw(7,"AssetsCurrent",71)
        raw(8,"AssetsCurrent",80)
        db.execute("INSERT INTO canonical_factor_evidence VALUES ('s8','S08.US','current_assets',80,'USD','USD',NULL,DATE '2026-06-30',DATE '2026-06-30','acc8',?,?,?, 'usable','AssetsCurrent')",[stamp,stamp,stamp])
        raw(9,"Assets",900)
    return research,production

def test_raw_canonical_inventory_exact_states_counts_bounds_and_immutability(tmp_path):
    research,production=inventory_fixture(tmp_path); before=(research.read_bytes(),production.read_bytes())
    report=raw_canonical_inventory(research_db=research,production_db=production,decision_at=DECISION)
    ca=report["field_state_counts"]["current_assets"]
    assert ca["compatible_raw_fact_not_materialized"]==1
    assert ca["only_broader_aggregate_exists"]==2
    assert ca["raw_fact_incompatible_unit"]==1
    assert ca["issuer_extension_review_required"]==1
    assert ca["conflicting_visible_facts"]==1
    assert ca["compatible_canonical_fact_visible"]==1
    assert ca["no_relevant_raw_or_canonical_fact"]==4
    assert report["field_state_counts"]["current_liabilities"]["compatible_raw_fact_post_decision"]==1
    assert report["field_state_counts"]["current_liabilities"]["raw_fact_incompatible_duration"]==1
    assert report["field_state_counts"]["unrestricted_cash"]["raw_fact_stale"]==1
    assert report["field_state_samples"]["current_assets"]["no_relevant_raw_or_canonical_fact"]["returned_count"]==4
    assert report["standard_concept_observation_counts"]["AssetsCurrent"]==5
    assert report["company_reconciliation"]["requiring_new_sec_ingestion"]["count"]==10
    assert report["company_reconciliation"]["requiring_accounting_review"]["count"]>=2
    assert report["company_reconciliation"]["requiring_canonical_materialization"]["count"]==1
    assert report["company_samples"]["returned_count"]==10 and report["company_samples"]["truncated"]
    assert report["database_immutability"]["before"]==report["database_immutability"]["after"]
    assert (research.read_bytes(),production.read_bytes())==before
    for key in ("rankings","candidates","recommendations","selections","vintages","validation_observations"):
        assert report[key]==[]
    assert report["validation_credit"]==0

def test_inventory_canonical_precedence_and_cli_redaction(tmp_path):
    research,production=inventory_fixture(tmp_path)
    assessment=evidence_gap_assessment(research_db=research,production_db=production,decision_at=DECISION)
    assert assessment["field_state_counts"]["current_assets"]["compatible_canonical_fact_visible"]==1
    command=[sys.executable,"-m","app.investment_research_cli","liquidity-raw-canonical-inventory",
      "--research-db",str(research),"--production-db",str(production),"--decision-at","2026-10-02T18:15:00"]
    result=subprocess.run(command,cwd=Path(__file__).parents[1],capture_output=True,text=True)
    assert result.returncode==1 and str(research) not in result.stderr
    assert json.loads(result.stderr)["error"]["message"]=="investment research request failed; details redacted"

def test_versioned_validator_sec_usd_currency_scale_nature_visibility_and_staleness():
    base={"security_id":"s","taxonomy":"us-gaap","concept":"AssetsCurrent","value":10.0,
      "unit":"USD","currency":None,"scale":None,"period_start":None,"period_end":date(2026,6,30),
      "public_at":datetime(2026,7,1,tzinfo=timezone.utc),"retrieved_at":datetime(2026,7,2,tzinfo=timezone.utc),
      "accession_number":"a"}
    accepted=validate_measurement(base,DECISION)
    assert accepted["accepted"] and accepted["canonical_currency"]=="USD"
    assert accepted["validator_version"]==VALIDATOR_VERSION and accepted["applied_scale_factor"]==1
    assert accepted["lossless_normalization_provenance"]["original_currency"] is None
    for scale in (0,1): assert validate_measurement({**base,"scale":scale},DECISION)["accepted"]
    assert validate_measurement({**base,"currency":"USD"},DECISION)["accepted"]
    cases=(({"currency":"EUR"},"unit_currency_contradiction"),({"unit":"EUR","currency":"EUR"},"source_unit_not_usd"),
      ({"scale":3},"scale_not_lossless"),({"value":float("inf")},"value_nonfinite_or_invalid"),
      ({"period_start":date(2026,1,1)},"measurement_nature_duration"),
      ({"retrieved_at":datetime(2026,10,4,tzinfo=timezone.utc)},"not_visible_at_decision"),
      ({"period_end":date(2024,1,1)},"period_stale"))
    for change,code in cases:
        result=validate_measurement({**base,**change},DECISION)
        assert not result["accepted"] and result["reason_code"]==code
    assert validate_measurement({**base,"concept":"Assets"},DECISION)["canonical_field"]=="assets"
    assert validate_measurement({**base,"concept":"Liabilities"},DECISION)["canonical_field"]=="liabilities"
    assert validate_measurement({**base,"concept":"CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"},DECISION)["canonical_field"]=="cash_plus_restricted_cash"

def test_compatibility_audit_and_materialization_plan_are_bounded_read_only_and_reconciled(tmp_path):
    research,production=fixture(tmp_path); before=(research.read_bytes(),production.read_bytes())
    audit=compatibility_audit(research_db=research,production_db=production,decision_at=DECISION)
    plan=plan_canonical_materialization(research_db=research,production_db=production,decision_at=DECISION)
    assert audit["reconciled"] and all(x["reconciled"] for x in audit["reconciliation"].values())
    assert plan["status"]=="ready" and plan["proposed_observation_count"]==5
    assert plan["provider_request_count"]==plan["database_write_count"]==plan["aliases_automatically_activated"]==0
    assert len(plan["deterministic_evidence_keys"])==len(set(plan["deterministic_evidence_keys"]))
    assert (research.read_bytes(),production.read_bytes())==before
    assert compact_utf8_size(audit)<=AGGREGATE_MAXIMUM_BYTES
    mismatch=reconcile_identity_sets({"current_assets":{"a"}}, {"current_assets":{"b"}})
    assert not mismatch["current_assets"]["reconciled"]

def test_new_cli_paths_with_spaces_and_stable_redaction(tmp_path):
    research,production=fixture(tmp_path)
    for name in ("liquidity-measurement-compatibility-audit","plan-liquidity-canonical-materialization"):
        command=[sys.executable,"-m","app.investment_research_cli",name,"--research-db",str(research),
          "--production-db",str(production),"--decision-at",DECISION.isoformat()]
        result=subprocess.run(command,cwd=Path(__file__).parents[1],capture_output=True,text=True,env={**__import__('os').environ,"PYTHONPATH":"."})
        assert result.returncode==0, result.stderr
        assert json.loads(result.stdout)["command"]==name
