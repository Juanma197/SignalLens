from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

import duckdb
import pytest

from app.financial_strength import (ALIAS_CONTRACT_VERSION, FINANCIAL_FIELD_ALIASES,
    construct_debt, contract_assessment, evidence_audit, company_preview)
from app.model_readiness import fingerprint

DECISION=datetime(2026,10,2,18,15,tzinfo=timezone.utc)

def fixture(tmp_path):
    root=tmp_path/"operator files with spaces"; root.mkdir()
    research=root/"research.duckdb"; production=root/"production.duckdb"
    with duckdb.connect(str(production)) as db: db.execute("CREATE TABLE marker(value INT)")
    with duckdb.connect(str(research)) as db:
        db.execute("""CREATE TABLE security_classification_evidence(
          security_id VARCHAR, qualified_symbol VARCHAR, security_type VARCHAR,
          public_at TIMESTAMPTZ,retrieved_at TIMESTAMPTZ,available_at TIMESTAMPTZ)""")
        db.execute("""CREATE TABLE canonical_factor_evidence(
          security_id VARCHAR,qualified_symbol VARCHAR,canonical_field VARCHAR,value DOUBLE,
          unit VARCHAR,currency VARCHAR,period_start DATE,period_end DATE,instant_date DATE,
          accession_or_source_identifier VARCHAR,public_at TIMESTAMPTZ,retrieved_at TIMESTAMPTZ,
          available_at TIMESTAMPTZ,original_concept_or_field VARCHAR,reliability_state VARCHAR,
          withholding_reason VARCHAR,provenance JSON)""")
        stamp=datetime(2026,9,1,tzinfo=timezone.utc)
        db.execute("INSERT INTO security_classification_evidence VALUES ('one','ONE.US','us_operating_company',?,?,?)",[stamp]*3)
        instant=[("assets",100,"Assets"),("shareholders_equity",-5,"StockholdersEquity"),
          ("current_assets",30,"AssetsCurrent"),("current_liabilities",20,"LiabilitiesCurrent"),
          ("total_debt",0,"DebtCurrentAndNoncurrent"),("cash_and_cash_equivalents",10,"CashAndCashEquivalentsAtCarryingValue"),
          ("finance_lease_liabilities",2,"FinanceLeaseLiability"),("operating_lease_liabilities",3,"OperatingLeaseLiability")]
        for field,value,concept in instant:
            db.execute("INSERT INTO canonical_factor_evidence VALUES (?,?,?,?,?,?,NULL,DATE '2025-12-31',DATE '2025-12-31','acc',?,?,?,?, 'usable',NULL,?)",
              ["one","ONE.US",field,value,"USD","USD",stamp,stamp,stamp,concept,json.dumps({"scale":1})])
        for field,value,concept in [("interest_expense",0,"InterestExpense"),("operating_income",8,"OperatingIncomeLoss"),("operating_cash_flow",6,"NetCashProvidedByUsedInOperatingActivities")]:
            db.execute("INSERT INTO canonical_factor_evidence VALUES (?,?,?,?,?,?,DATE '2025-01-01',DATE '2025-12-31',NULL,'acc',?,?,?,?, 'usable',NULL,?)",
              ["one","ONE.US",field,value,"USD","USD",stamp,stamp,stamp,concept,json.dumps({"scale":1})])
    return research,production

def test_alias_contract_is_exact_and_accounting_aware():
    assert "InterestExpense" in FINANCIAL_FIELD_ALIASES
    assert "interestexpense" not in FINANCIAL_FIELD_ALIASES
    gross=FINANCIAL_FIELD_ALIASES["InterestExpense"][0]
    assert gross["alias_contract_version"]==ALIAS_CONTRACT_VERSION
    assert gross["period_nature"]=="duration" and "net interest" in gross["excluded_meanings"]
    assert FINANCIAL_FIELD_ALIASES["InterestIncomeExpenseNonoperatingNet"][0]["canonical_field"]=="net_interest_expense"

def test_overlapping_debt_is_not_summed_and_leases_stay_separate():
    def fact(value,concept): return {"value":value,"original_concept":concept}
    fields={"total_debt":fact(30,"DebtCurrentAndNoncurrent"),"current_debt":fact(10,"ShortTermDebtCurrent"),
      "non_current_debt":fact(20,"LongTermDebtNoncurrent"),"finance_lease_liabilities":fact(2,"FinanceLeaseLiability"),
      "operating_lease_liabilities":fact(3,"OperatingLeaseLiability")}
    result=construct_debt(fields)
    assert [x["value"] for x in result["alternatives"]]==[30,30]
    assert result["selected_method"] is None
    assert result["finance_leases_separate"] and result["operating_leases_separate"]

def test_read_only_point_in_time_audit_and_company_semantics(tmp_path):
    research,production=fixture(tmp_path); before=(research.read_bytes(),production.read_bytes())
    audit=evidence_audit(research_db=research,production_db=production,decision_at=DECISION)
    preview=company_preview(research_db=research,production_db=production,decision_at=DECISION,qualified_symbol="ONE.US")
    assert audit["companies"][0]==preview["company"]
    company=preview["company"]
    assert company["debt_state"]=="debt_free_explicit"
    assert company["denominator_warnings"]==["negative_equity"]
    assert audit["interest_gap_diagnosis"]["zero_values_explicitly_reported"]==1
    assert sum(audit["interest_gap_diagnosis"].values())==1
    assert company["components"]["coverage"]=="unavailable"  # explicit zero interest is not a valid divisor
    assert company["score_contribution"] is None and company["recommendation"] is None
    assert (research.read_bytes(),production.read_bytes())==before
    for key in ("rankings","candidates","recommendations","selections","vintages","validation_observations"):
        assert audit[key]==[]

def test_post_decision_unit_currency_duration_and_missing_are_withheld(tmp_path):
    research,production=fixture(tmp_path)
    with duckdb.connect(str(research)) as db:
        future=datetime(2026,10,3,tzinfo=timezone.utc)
        db.execute("INSERT INTO canonical_factor_evidence VALUES ('one','ONE.US','interest_expense',4,'shares','EUR',NULL,DATE '2026-09-30',DATE '2026-09-30','future',?,?,?,?, 'usable',NULL,'{}')",
          [future,future,future,"InterestExpense"])
    audit=evidence_audit(research_db=research,production_db=production,decision_at=DECISION)
    row=[x for x in audit["companies"][0]["observations"] if x["source_filing"]=="future"][0]
    assert not row["usable"] and row["withholding_reason"]=="post_decision_evidence"
    with pytest.raises(Exception): evidence_audit(research_db=research,production_db=production,decision_at=datetime(2026,1,1))

def test_contracts_are_counterfactual_not_model_selection(tmp_path):
    research,production=fixture(tmp_path)
    report=contract_assessment(research_db=research,production_db=production,decision_at=DECISION)
    assert set(report["candidate_contracts"])=={"A_all_components_mandatory","B_leverage_liquidity_mandatory_coverage_optional","C_two_independent_components","D_debt_free_or_leveraged_branch"}
    assert report["contract_selected"] is None and report["validation_credit"]==0
    assert report["metric_assessments"]["debt_to_equity"]["negative_or_zero_denominator_behavior"].startswith("negative_equity_flag")

def test_cli_errors_are_bounded_stable_and_redacted(tmp_path):
    research,production=fixture(tmp_path)
    command=[sys.executable,"-m","app.investment_research_cli","financial-strength-company-preview",
      "--research-db",str(research),"--production-db",str(production),"--decision-at",DECISION.isoformat(),"--qualified-symbol","MISSING.US"]
    result=subprocess.run(command,cwd=Path(__file__).parents[1],text=True,capture_output=True)
    assert result.returncode==1
    assert json.loads(result.stderr)=={"error":{"code":"INVESTMENT_RESEARCH_NOT_READY","message":"investment research request failed; details redacted"},"status":"failed"}
    assert str(research) not in result.stderr
