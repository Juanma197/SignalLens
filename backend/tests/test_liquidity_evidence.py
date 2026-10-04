from datetime import date, datetime, timezone
import json, subprocess, sys
from pathlib import Path

import duckdb
import pytest

from app.liquidity_evidence import (AGGREGATE_MAXIMUM_BYTES, CONTRACT_MAXIMUM_BYTES,
    PREVIEW_MAXIMUM_BYTES, compact_utf8_size, evidence_discovery,
    contract_assessment, company_preview)

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
