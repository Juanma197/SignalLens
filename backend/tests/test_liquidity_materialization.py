from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import json
import subprocess
import sys
from pathlib import Path

import duckdb
import pytest

from app.liquidity_materialization import (
    AUTHORIZATION_PHRASE, ERRORS, LiquidityMaterializationError,
    apply, plan, status, validate_plan,
)
from app.model_readiness import fingerprint
from app.liquidity_canonical_contract import (
    canonical_evidence_key, date_value, identity_scale,
    redundant_currency, same_number, timestamp_text,
)
from app.liquidity_resolution import CanonicalLiquidityResolutionError
from tests.test_liquidity_evidence import DECISION, fixture
from app.liquidity_compatibility import compatibility_audit
from app.liquidity_evidence import evidence_discovery, contract_assessment, company_preview
from app.liquidity_inventory import raw_canonical_inventory, evidence_gap_assessment
from app.financial_strength import evidence_audit as financial_strength_audit

LEGACY_SCHEMA="""CREATE TABLE canonical_factor_evidence(
 evidence_key VARCHAR PRIMARY KEY, security_id VARCHAR NOT NULL, qualified_symbol VARCHAR,
 canonical_field VARCHAR NOT NULL, value DOUBLE, unit VARCHAR, currency VARCHAR,
 period_start DATE, period_end DATE, instant_date DATE, fiscal_period VARCHAR, form VARCHAR,
 accession_or_source_identifier VARCHAR NOT NULL, public_at TIMESTAMPTZ NOT NULL,
 retrieved_at TIMESTAMPTZ NOT NULL, available_at TIMESTAMPTZ NOT NULL,
 materialized_at TIMESTAMPTZ NOT NULL, original_concept_or_field VARCHAR NOT NULL,
 alias_contract_version VARCHAR NOT NULL, sign_convention VARCHAR NOT NULL,
 reliability_state VARCHAR NOT NULL, withholding_reason VARCHAR, provenance JSON NOT NULL,
 source_fact_key VARCHAR, lineage JSON NOT NULL)"""

def legacy_fixture(tmp_path):
    research,production=fixture(tmp_path)
    stamp=datetime(2025,1,1,tzinfo=timezone.utc)
    with duckdb.connect(str(research)) as db:
        db.execute(LEGACY_SCHEMA)
        db.execute("""INSERT INTO canonical_factor_evidence
          (evidence_key,security_id,qualified_symbol,canonical_field,value,unit,currency,
           accession_or_source_identifier,public_at,retrieved_at,available_at,materialized_at,
           original_concept_or_field,alias_contract_version,sign_convention,reliability_state,
           provenance,source_fact_key,lineage)
          VALUES ('unrelated','z','ZZZ.US','book_value',7,'USD','USD','legacy',?,?,?,?,
          'StockholdersEquity','legacy-1','reported','usable','{\"legacy\":true}','legacy-fact','{\"legacy\":true}')""",
          [stamp,stamp,stamp,stamp])
    return research,production


def test_plan_validation_authorization_apply_retry_and_provenance(tmp_path):
    research,production=fixture(tmp_path); original_production=production.read_bytes()
    issued=DECISION+timedelta(minutes=1)
    planned=plan(research_db=research,production_db=production,decision_at=DECISION,now=issued)
    token=planned["plan_identifier"]
    assert planned["proposed_observation_count"]==5 and planned["capacity"]["capacity_sufficient"]
    assert validate_plan(research_db=research,production_db=production,decision_at=DECISION,
      plan_identifier=token,now=issued+timedelta(hours=23))["valid"]
    assert validate_plan(research_db=research,production_db=production,decision_at=DECISION,
      plan_identifier=token,now=issued+timedelta(hours=24))["reason_code"]==ERRORS["expired"]
    assert validate_plan(research_db=research,production_db=production,decision_at=DECISION,
      plan_identifier=token+"x",now=issued)["reason_code"]==ERRORS["invalid"]
    with pytest.raises(LiquidityMaterializationError) as refused:
      apply(research_db=research,production_db=production,decision_at=DECISION,
        plan_identifier=token,authorization=AUTHORIZATION_PHRASE.lower(),now=issued)
    assert refused.value.code==ERRORS["authorization"]
    first=apply(research_db=research,production_db=production,decision_at=DECISION,
      plan_identifier=token,authorization=AUTHORIZATION_PHRASE,now=issued)
    assert first["inserted_count"]==5 and first["unchanged_count"]==0
    with duckdb.connect(str(research),read_only=True) as db:
      rows=db.execute("SELECT canonical_unit,canonical_currency,operation_contract_version,normalization_rationale FROM liquidity_canonical_materialization_revisions").fetchall()
      assert len(rows)==5 and all(x[:3]==("USD","USD","1.0.0") for x in rows)
      assert all("original_value" in json.loads(x[3]) for x in rows)
    second=apply(research_db=research,production_db=production,decision_at=DECISION,
      plan_identifier=token,authorization=AUTHORIZATION_PHRASE,now=issued)
    assert second["inserted_count"]==0 and second["unchanged_count"]==5
    assert second["idempotent_retry"] is True
    assert production.read_bytes()==original_production
    assert status(research_db=research,production_db=production)["state"]=="completed"


def test_capacity_source_change_and_transactional_schema_rollback(tmp_path):
    research,production=fixture(tmp_path); issued=DECISION+timedelta(minutes=1)
    planned=plan(research_db=research,production_db=production,decision_at=DECISION,now=issued)
    with pytest.raises(LiquidityMaterializationError) as capacity:
      apply(research_db=research,production_db=production,decision_at=DECISION,
        plan_identifier=planned["plan_identifier"],authorization=AUTHORIZATION_PHRASE,
        now=issued,capacity_available_bytes=0)
    assert capacity.value.code==ERRORS["capacity"]
    before=fingerprint(research)
    with pytest.raises(LiquidityMaterializationError) as rollback:
      apply(research_db=research,production_db=production,decision_at=DECISION,
        plan_identifier=planned["plan_identifier"],authorization=AUTHORIZATION_PHRASE,
        now=issued,fail_after_schema=True)
    assert rollback.value.code==ERRORS["internal"] and fingerprint(research)==before
    with duckdb.connect(str(research)) as db:
      db.execute("UPDATE sec_facts SET value=value+1 WHERE fact_key=(SELECT min(fact_key) FROM sec_facts)")
    invalid=validate_plan(research_db=research,production_db=production,decision_at=DECISION,
      plan_identifier=planned["plan_identifier"],now=issued)
    assert invalid["reason_code"]==ERRORS["fingerprint"]


def test_exact_legacy_schema_first_apply_retry_provenance_and_immutability(tmp_path):
    research,production=legacy_fixture(tmp_path); production_before=production.read_bytes()
    with duckdb.connect(str(research),read_only=True) as db:
      schema_before=db.execute("PRAGMA table_info('canonical_factor_evidence')").fetchall()
      unrelated_before=db.execute("SELECT * FROM canonical_factor_evidence WHERE evidence_key='unrelated'").fetchone()
    assert len(schema_before)==25
    issued=DECISION+timedelta(minutes=1)
    planned=plan(research_db=research,production_db=production,decision_at=DECISION,now=issued)
    assert planned["status"]=="ready" and planned["schema_compatibility"]["compatible"]
    first=apply(research_db=research,production_db=production,decision_at=DECISION,
      plan_identifier=planned["plan_identifier"],authorization=AUTHORIZATION_PHRASE,now=issued)
    assert (first["inserted_count"],first["unchanged_count"])==(5,0)
    with duckdb.connect(str(research),read_only=True) as db:
      assert db.execute("PRAGMA table_info('canonical_factor_evidence')").fetchall()==schema_before
      assert db.execute("SELECT * FROM canonical_factor_evidence WHERE evidence_key='unrelated'").fetchone()==unrelated_before
      inserted=db.execute("""SELECT evidence_key,security_id,accession_or_source_identifier,
        materialized_at,original_concept_or_field,alias_contract_version,sign_convention,
        reliability_state,provenance,lineage,available_at FROM canonical_factor_evidence
        WHERE evidence_key <> 'unrelated'""").fetchall()
      assert len(inserted)==5 and all(all(row[:8]) for row in inserted)
      for row in inserted:
        provenance=json.loads(row[8]); lineage=json.loads(row[9])
        for key in ("source_evidence_key","operation_type","operation_contract_hash","validator_version",
                    "plan_identity","materialization_run_id","decision_at"):
          assert key in provenance and key in lineage
        assert "normalization_rationale" in provenance and "original_timestamps" in provenance
        assert row[10]>DECISION
    retry_before=research.read_bytes()
    retry=apply(research_db=research,production_db=production,decision_at=DECISION,
      plan_identifier=planned["plan_identifier"],authorization=AUTHORIZATION_PHRASE,now=issued)
    assert (retry["inserted_count"],retry["unchanged_count"])==(0,5)
    assert retry["idempotent_retry"] is True
    assert research.read_bytes()==retry_before
    report=status(research_db=research,production_db=production)
    assert report["state"]=="completed" and report["latest_run"]["inserted_count"]==5
    assert report["schema_compatibility"]["compatible"] and production.read_bytes()==production_before
    for key in ("rankings","candidates","recommendations","selections","vintages","validation_observations"):
      assert first[key]==[]
    assert first["provider_request_count"]==0 and first["validation_credit"]==0


@pytest.mark.parametrize("seam",["fail_after_schema","fail_after_insert_preparation"])
def test_legacy_schema_transaction_rollbacks_are_byte_exact(tmp_path,seam):
    research,production=legacy_fixture(tmp_path); issued=DECISION+timedelta(minutes=1)
    planned=plan(research_db=research,production_db=production,decision_at=DECISION,now=issued)
    before=research.read_bytes()
    with pytest.raises(LiquidityMaterializationError) as failure:
      apply(research_db=research,production_db=production,decision_at=DECISION,
        plan_identifier=planned["plan_identifier"],authorization=AUTHORIZATION_PHRASE,now=issued,**{seam:True})
    assert failure.value.code==ERRORS["internal"] and research.read_bytes()==before
    with duckdb.connect(str(research),read_only=True) as db:
      assert not any(name.startswith("liquidity_canonical_materialization") for name in (x[0] for x in db.execute("SHOW TABLES").fetchall()))


@pytest.mark.parametrize(("mutation","column"),[
  ("ALTER TABLE canonical_factor_evidence ALTER value TYPE VARCHAR","value"),
  ("ALTER TABLE canonical_factor_evidence ALTER lineage DROP NOT NULL","lineage"),
])
def test_incompatible_legacy_schema_refused_read_only_and_cli_redacted(tmp_path,mutation,column):
    research,production=legacy_fixture(tmp_path)
    with duckdb.connect(str(research)) as db:
      db.execute(mutation)
    before=(research.read_bytes(),production.read_bytes())
    planned=plan(research_db=research,production_db=production,decision_at=DECISION,now=DECISION)
    assert planned["status"]=="blocked" and planned["blockers"]==[ERRORS["schema"]]
    assert planned["schema_compatibility"]["issues"][0]["column"]==column
    command=[sys.executable,"-m","app.investment_research_cli","apply-liquidity-canonical-materialization",
      "--research-db",str(research),"--production-db",str(production),"--decision-at",DECISION.isoformat(),
      "--plan-identifier",planned["plan_identifier"],"--authorization",AUTHORIZATION_PHRASE]
    result=subprocess.run(command,cwd=Path(__file__).parents[1],capture_output=True,text=True)
    assert result.returncode==1 and str(research) not in result.stderr
    assert json.loads(result.stderr)["error"]["code"]==ERRORS["schema"]
    assert (research.read_bytes(),production.read_bytes())==before


def test_conflicting_canonical_key_fails_closed(tmp_path):
    research,production=legacy_fixture(tmp_path); issued=DECISION+timedelta(minutes=1)
    planned=plan(research_db=research,production_db=production,decision_at=DECISION,now=issued)
    key=planned["evidence_keys"][0]; stamp=datetime(2025,1,1,tzinfo=timezone.utc)
    with duckdb.connect(str(research)) as db:
      db.execute("""INSERT INTO canonical_factor_evidence(evidence_key,security_id,canonical_field,
       accession_or_source_identifier,public_at,retrieved_at,available_at,materialized_at,
       original_concept_or_field,alias_contract_version,sign_convention,reliability_state,provenance,lineage)
       VALUES (?,'wrong','current_assets','wrong',?,?,?,?,'wrong','legacy','reported','usable','{}','{}')""",
       [key,stamp,stamp,stamp,stamp])
    planned=plan(research_db=research,production_db=production,decision_at=DECISION,now=issued)
    with pytest.raises(LiquidityMaterializationError) as failure:
      apply(research_db=research,production_db=production,decision_at=DECISION,
        plan_identifier=planned["plan_identifier"],authorization=AUTHORIZATION_PHRASE,now=issued)
    assert failure.value.code==ERRORS["conflict"]


def test_offline_post_materialization_consumer_lifecycle_is_read_only(tmp_path):
    """Reproduces the apply/before/after lifecycle without external resources."""
    research,production=legacy_fixture(tmp_path); production_before=production.read_bytes()
    issued=DECISION+timedelta(minutes=1)
    planned=plan(research_db=research,production_db=production,decision_at=DECISION,now=issued)
    applied=apply(research_db=research,production_db=production,decision_at=DECISION,
      plan_identifier=planned["plan_identifier"],authorization=AUTHORIZATION_PHRASE,now=issued)
    assert applied["inserted_count"]==5 and applied["provider_request_count"]==0
    research_after_apply=research.read_bytes()

    before=compatibility_audit(research_db=research,production_db=production,decision_at=DECISION)
    assert before["database_immutability"]["canonical_liquidity_resolution"]["future_revision_count"]==5
    assert before["database_immutability"]["canonical_liquidity_resolution"]["visible_selected_count"]==0

    later=issued+timedelta(minutes=1)
    reports=[
      compatibility_audit(research_db=research,production_db=production,decision_at=later),
      evidence_discovery(research_db=research,production_db=production,decision_at=later),
      contract_assessment(research_db=research,production_db=production,decision_at=later),
      company_preview(research_db=research,production_db=production,decision_at=later,qualified_symbol="AAA.US"),
      raw_canonical_inventory(research_db=research,production_db=production,decision_at=later),
      evidence_gap_assessment(research_db=research,production_db=production,decision_at=later),
      financial_strength_audit(research_db=research,production_db=production,decision_at=later),
    ]
    resolution=reports[0]["database_immutability"]["canonical_liquidity_resolution"]
    assert resolution["visible_selected_count"]==5
    assert resolution["deduplicated_source_count"]==5
    assert all(report["read_only"] for report in reports)
    assert research.read_bytes()==research_after_apply and production.read_bytes()==production_before
    for report in reports:
      assert report.get("rankings",[])==[] and report.get("candidates",[])==[]
      assert report.get("recommendations",[])==[] and report.get("validation_credit",0)==0


def test_exact_187_revision_historical_invisibility_and_post_visibility(tmp_path):
    research,production=legacy_fixture(tmp_path)
    stamp=datetime(2026,7,1,tzinfo=timezone.utc)
    with duckdb.connect(str(research)) as db:
      for index in range(3,70):
        sid=f"company-{index}"; symbol=f"C{index:02}.US"
        db.execute("INSERT INTO security_classification_evidence VALUES (?,?,'us_operating_company',?,?,?)",
          [sid,symbol,stamp,stamp,stamp])
        concepts=[]
        if index<=61: concepts.extend((("AssetsCurrent",100+index),("LiabilitiesCurrent",40+index)))
        if index>=6: concepts.append(("CashAndCashEquivalentsAtCarryingValue",20+index))
        for concept,value in concepts:
          db.execute("INSERT INTO sec_facts VALUES (?,?,?,?,?,?,?,?,NULL,NULL,DATE '2026-06-30','10-Q',?,?,?)",
            [f"{sid}-{concept}",sid,symbol,"us-gaap","2026",concept,value,"USD",
             f"acc-{sid}-{concept}",stamp,stamp])
    issued=DECISION+timedelta(minutes=1)
    planned=plan(research_db=research,production_db=production,decision_at=DECISION,now=issued)
    assert planned["proposed_observation_count"]==187
    assert planned["proposed_company_count"]==69
    assert planned["counts_by_canonical_field"]=={
      "current_assets":61,"current_liabilities":61,"unrestricted_cash":65}
    applied=apply(research_db=research,production_db=production,decision_at=DECISION,
      plan_identifier=planned["plan_identifier"],authorization=AUTHORIZATION_PHRASE,now=issued)
    assert applied["inserted_count"]==187 and applied["provider_request_count"]==0
    historical=compatibility_audit(research_db=research,production_db=production,decision_at=DECISION)
    post=compatibility_audit(research_db=research,production_db=production,
      decision_at=issued+timedelta(minutes=1))
    old=historical["database_immutability"]["canonical_liquidity_resolution"]
    new=post["database_immutability"]["canonical_liquidity_resolution"]
    assert (old["future_revision_count"],old["visible_selected_count"])==(187,0)
    assert (new["future_revision_count"],new["visible_selected_count"],new["deduplicated_source_count"])==(0,187,187)


def test_resolver_json_order_independence_and_stable_redacted_failure(tmp_path):
    research,production=legacy_fixture(tmp_path); issued=DECISION+timedelta(minutes=1)
    planned=plan(research_db=research,production_db=production,decision_at=DECISION,now=issued)
    apply(research_db=research,production_db=production,decision_at=DECISION,
      plan_identifier=planned["plan_identifier"],authorization=AUTHORIZATION_PHRASE,now=issued)
    with duckdb.connect(str(research)) as db:
      key,lineage=db.execute("SELECT evidence_key,lineage FROM canonical_factor_evidence WHERE evidence_key<>'unrelated' ORDER BY evidence_key LIMIT 1").fetchone()
      reordered=dict(reversed(list(json.loads(lineage).items())))
      db.execute("UPDATE canonical_factor_evidence SET lineage=? WHERE evidence_key=?",[json.dumps(reordered),key])
    later=issued+timedelta(minutes=1)
    assert compatibility_audit(research_db=research,production_db=production,decision_at=later)["reconciled"]
    with duckdb.connect(str(research)) as db:
      db.execute("UPDATE canonical_factor_evidence SET lineage='{}' WHERE evidence_key=?",[key])
    command=[sys.executable,"-m","app.investment_research_cli","liquidity-measurement-compatibility-audit",
      "--research-db",str(research),"--production-db",str(production),"--decision-at",later.isoformat()]
    result=subprocess.run(command,cwd=Path(__file__).parents[1],capture_output=True,text=True)
    error=json.loads(result.stderr)
    assert result.returncode==1
    assert result.stdout==""
    assert error=={"error":{"code":"LIQUIDITY_CANONICAL_RESOLUTION_FAILED",
      "message":"investment research request failed; details redacted"},"status":"failed"}
    assert str(research) not in result.stderr and "source_evidence_key" not in result.stderr


def test_valid_liquidity_consumer_clis_emit_nonempty_json_not_null(tmp_path):
    research,production=legacy_fixture(tmp_path); issued=DECISION+timedelta(minutes=1)
    planned=plan(research_db=research,production_db=production,decision_at=DECISION,now=issued)
    apply(research_db=research,production_db=production,decision_at=DECISION,
      plan_identifier=planned["plan_identifier"],authorization=AUTHORIZATION_PHRASE,now=issued)
    later=issued+timedelta(minutes=1)
    commands=("liquidity-measurement-compatibility-audit","liquidity-evidence-discovery",
      "liquidity-contract-assessment","liquidity-raw-canonical-inventory","liquidity-evidence-gap-assessment")
    for name in commands:
      command=[sys.executable,"-m","app.investment_research_cli",name,"--research-db",str(research),
        "--production-db",str(production),"--decision-at",later.isoformat()]
      result=subprocess.run(command,cwd=Path(__file__).parents[1],capture_output=True,text=True)
      assert result.returncode==0 and result.stderr=="" and result.stdout.strip()
      report=json.loads(result.stdout)
      assert isinstance(report,dict) and report and report["command"]==name
      assert report.get("rankings",[])==[] and report.get("validation_credit",0)==0


def test_shared_contract_normalizes_timezone_duckdb_types_currency_and_numbers(tmp_path):
    london="2026-07-01T13:00:00+01:00"; utc="2026-07-01T12:00:00+00:00"
    assert timestamp_text(london)==timestamp_text(utc)=="2026-07-01T12:00:00+00:00"
    assert canonical_evidence_key("fact","current_assets",london)==canonical_evidence_key("fact","current_assets",utc)
    assert date_value(date(2026,6,30))==date_value("2026-06-30")
    assert redundant_currency(None,"USD")==redundant_currency(" ","USD")==redundant_currency("usd","USD")=="USD"
    assert identity_scale(None)==identity_scale(0)==identity_scale(1.0)==identity_scale(Decimal("1.00"))
    assert same_number(1,1.0) and same_number(Decimal("1.00"),"1")
    path=tmp_path/"timestamp types.duckdb"
    with duckdb.connect(str(path)) as db:
      db.execute("SET TimeZone='Europe/London'")
      value=db.execute("SELECT TIMESTAMPTZ '2026-07-01 12:00:00+00', DATE '2026-06-30'").fetchone()
    assert timestamp_text(value[0])==timestamp_text(utc) and date_value(value[1])==date_value("2026-06-30")


@pytest.mark.parametrize(("reason","mutation"),[
  ("duplicate_or_missing_revision","DELETE FROM liquidity_canonical_materialization_revisions WHERE evidence_key=(SELECT min(evidence_key) FROM liquidity_canonical_materialization_revisions)"),
  ("incompatible_operation_identity","UPDATE liquidity_canonical_materialization_revisions SET validator_version='wrong' WHERE evidence_key=(SELECT min(evidence_key) FROM liquidity_canonical_materialization_revisions)"),
  ("canonical_field_mismatch","UPDATE canonical_factor_evidence SET canonical_field='wrong' WHERE evidence_key=(SELECT min(evidence_key) FROM liquidity_canonical_materialization_revisions)"),
  ("security_id_mismatch","UPDATE canonical_factor_evidence SET security_id='wrong' WHERE evidence_key=(SELECT min(evidence_key) FROM liquidity_canonical_materialization_revisions)"),
  ("conflicting_canonical_value","UPDATE canonical_factor_evidence SET value=value+1 WHERE evidence_key=(SELECT min(evidence_key) FROM liquidity_canonical_materialization_revisions)"),
  ("accounting_meaning_mismatch","UPDATE liquidity_canonical_materialization_revisions SET original_concept='wrong' WHERE evidence_key=(SELECT min(evidence_key) FROM liquidity_canonical_materialization_revisions)"),
  ("source_unit_or_currency_mismatch","UPDATE liquidity_canonical_materialization_revisions SET original_unit='shares' WHERE evidence_key=(SELECT min(evidence_key) FROM liquidity_canonical_materialization_revisions)"),
  ("unsupported_scale","UPDATE liquidity_canonical_materialization_revisions SET original_scale=1000 WHERE evidence_key=(SELECT min(evidence_key) FROM liquidity_canonical_materialization_revisions)"),
  ("unit_or_currency_mismatch","UPDATE canonical_factor_evidence SET currency='EUR' WHERE evidence_key=(SELECT min(evidence_key) FROM liquidity_canonical_materialization_revisions)"),
  ("period_mismatch","UPDATE canonical_factor_evidence SET period_end=DATE '2026-06-29' WHERE evidence_key=(SELECT min(evidence_key) FROM liquidity_canonical_materialization_revisions)"),
  ("source_identity_mismatch","UPDATE canonical_factor_evidence SET accession_or_source_identifier='wrong' WHERE evidence_key=(SELECT min(evidence_key) FROM liquidity_canonical_materialization_revisions)"),
  ("source_timestamp_mismatch","UPDATE canonical_factor_evidence SET public_at=public_at+INTERVAL 1 SECOND WHERE evidence_key=(SELECT min(evidence_key) FROM liquidity_canonical_materialization_revisions)"),
  ("revision_timestamp_mismatch","UPDATE liquidity_canonical_materialization_revisions SET retrieved_at=retrieved_at+INTERVAL 1 SECOND WHERE evidence_key=(SELECT min(evidence_key) FROM liquidity_canonical_materialization_revisions)"),
  ("availability_timestamp_mismatch","UPDATE canonical_factor_evidence SET available_at=available_at+INTERVAL 1 SECOND WHERE evidence_key=(SELECT min(evidence_key) FROM liquidity_canonical_materialization_revisions)"),
])
def test_each_resolver_corruption_reports_exact_bounded_reason(tmp_path,reason,mutation):
    research,production=legacy_fixture(tmp_path); issued=DECISION+timedelta(minutes=1)
    planned=plan(research_db=research,production_db=production,decision_at=DECISION,now=issued)
    apply(research_db=research,production_db=production,decision_at=DECISION,
      plan_identifier=planned["plan_identifier"],authorization=AUTHORIZATION_PHRASE,now=issued)
    with duckdb.connect(str(research)) as db: db.execute(mutation)
    with pytest.raises(CanonicalLiquidityResolutionError) as caught:
      compatibility_audit(research_db=research,production_db=production,decision_at=issued+timedelta(minutes=1))
    assert caught.value.diagnostics["issue_counts"]=={reason:1}
    assert caught.value.diagnostics["sample_limit"]==10
    assert len(caught.value.diagnostics["samples"])==1


@pytest.mark.parametrize("reason",[
  "ambiguous_or_unmatched_source","source_lineage_mismatch",
  "materialization_decision_mismatch","canonical_evidence_key_mismatch","malformed_lineage",
])
def test_structural_resolver_corruptions_report_exact_reason(tmp_path,reason):
    research,production=legacy_fixture(tmp_path); issued=DECISION+timedelta(minutes=1)
    planned=plan(research_db=research,production_db=production,decision_at=DECISION,now=issued)
    apply(research_db=research,production_db=production,decision_at=DECISION,
      plan_identifier=planned["plan_identifier"],authorization=AUTHORIZATION_PHRASE,now=issued)
    with duckdb.connect(str(research)) as db:
      key,source,provenance,lineage=db.execute("""SELECT evidence_key,source_fact_key,provenance,lineage
        FROM canonical_factor_evidence WHERE evidence_key<>'unrelated' ORDER BY evidence_key LIMIT 1""").fetchone()
      if reason=="ambiguous_or_unmatched_source":
        db.execute("DELETE FROM sec_facts WHERE fact_key=?",[source])
      elif reason=="source_lineage_mismatch":
        value=json.loads(lineage); value["source_fact_key"]="wrong"
        db.execute("UPDATE canonical_factor_evidence SET lineage=? WHERE evidence_key=?",[json.dumps(value),key])
      elif reason=="materialization_decision_mismatch":
        value=json.loads(provenance); value["decision_at"]="2026-01-01T00:00:00+00:00"
        db.execute("UPDATE canonical_factor_evidence SET provenance=? WHERE evidence_key=?",[json.dumps(value),key])
      elif reason=="canonical_evidence_key_mismatch":
        wrong="f"*64 if key!="f"*64 else "e"*64
        db.execute("UPDATE liquidity_canonical_materialization_revisions SET evidence_key=? WHERE evidence_key=?",[wrong,key])
        db.execute("UPDATE canonical_factor_evidence SET evidence_key=? WHERE evidence_key=?",[wrong,key])
      else:
        db.execute("UPDATE canonical_factor_evidence SET lineage='[]' WHERE evidence_key=?",[key])
    with pytest.raises(CanonicalLiquidityResolutionError) as caught:
      compatibility_audit(research_db=research,production_db=production,decision_at=issued+timedelta(minutes=1))
    assert caught.value.diagnostics["issue_counts"]=={reason:1}
