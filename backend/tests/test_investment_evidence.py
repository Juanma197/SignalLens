from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

import duckdb
import pytest

from app.canonical_units import normalize_unit
from app.investment_research import family_readiness
import app.investment_research as investment_research


def test_eps_unit_normalization_is_exact_and_preserves_source_semantics():
    rule = normalize_unit(canonical_field="basic_eps", source_unit="USD/shares",
        concept="EarningsPerShareBasic", currency="USD", scale_factor=1,
        period_nature="duration")
    assert rule is not None
    assert rule.source_unit == "USD/shares"
    assert rule.canonical_unit == "USD/share"
    assert rule.scale_factor == 1
    assert rule.rule_version == "1.0.0"
    for change in (
        {"canonical_field":"revenue"}, {"source_unit":"EUR/shares"},
        {"source_unit":"USD/units"}, {"scale_factor":1000},
        {"currency":None},
        {"concept":"EarningsPerShareDiluted"}, {"period_nature":"instant"},
    ):
        args={"canonical_field":"basic_eps","source_unit":"USD/shares",
              "concept":"EarningsPerShareBasic","currency":"USD",
              "scale_factor":1,"period_nature":"duration"}
        args.update(change)
        assert normalize_unit(**args) is None


def test_family_readiness_distinguishes_partial_minimum_and_full():
    partial=family_readiness({"assets":"available"},"financial_strength")
    assert partial["any_input_available"]
    assert not partial["minimum_calculable"]
    assert not partial["full_family_ready"]
    assert "shareholders_equity" in partial["missing_required_inputs"]
    full=family_readiness({x:"available" for x in partial["required_factors"]},"financial_strength")
    assert full["minimum_calculable"] and full["full_family_ready"]

import app.investment_evidence as investment_evidence
from app.investment_evidence import (MATERIALIZE_AUTHORIZATION, SCHEMA, availability,
    UNIT_REPAIR_AUTHORIZATION, apply_canonical_unit_repair,
    canonical_unit_repair_status, enrichment_plan, initialize_schema,
    materialize_stored, plan_canonical_unit_repair, status)
from app.investment_research import InvestmentResearchError
from app.model_readiness import fingerprint


DECISION=datetime(2026,10,1,tzinfo=timezone.utc)

def databases(tmp_path):
    research=tmp_path/"research.duckdb"; production=tmp_path/"production.duckdb"
    with duckdb.connect(str(research)) as db: db.execute("CREATE TABLE marker(value INTEGER)")
    with duckdb.connect(str(production)) as db: db.execute("CREATE TABLE marker(value INTEGER)")
    return research,production

def test_empty_schema_and_zero_model_outputs(tmp_path):
    research,production=databases(tmp_path)
    result=materialize_stored(research_db=research,production_db=production,
      decision_at=DECISION,authorization=MATERIALIZE_AUTHORIZATION)
    assert result["inserted"]==0 and result["rankings"]==[] and result["validation_credit"]==0
    with duckdb.connect(str(research),read_only=True) as db:
        tables={x[0] for x in db.execute("SHOW TABLES").fetchall()}
    assert {"investment_evidence_runs","security_classification_evidence","canonical_factor_evidence","corporate_action_coverage_evidence"}<=tables
    report=status(research_db=research,production_db=production,decision_at=DECISION)
    assert report["production_unchanged"] and report["candidates"]==[]

def test_materialization_fingerprints_research_before_writable_open(monkeypatch,tmp_path):
    spaced=tmp_path/"Windows paths with spaces"; spaced.mkdir()
    research,production=databases(spaced)
    research_before=fingerprint(research); production_bytes=production.read_bytes()
    real_connect=duckdb.connect; writable_research_open=False; calls=[]

    class TrackedConnection:
        def __init__(self,connection): self.connection=connection
        def __getattr__(self,name): return getattr(self.connection,name)
        def __enter__(self):
            nonlocal writable_research_open
            writable_research_open=True
            return self
        def __exit__(self,*args):
            nonlocal writable_research_open
            try: return self.connection.__exit__(*args)
            finally: writable_research_open=False

    def tracked_connect(database,*args,**kwargs):
        connection=real_connect(database,*args,**kwargs)
        if Path(database)==research and not kwargs.get("read_only",False):
            return TrackedConnection(connection)
        return connection

    real_fingerprint=investment_evidence.fingerprint
    def windows_locking_fingerprint(path):
        path=Path(path); calls.append((path,writable_research_open))
        if path==research and writable_research_open:
            raise PermissionError(13,"Permission denied",str(path))
        return real_fingerprint(path)

    monkeypatch.setattr(investment_evidence.duckdb,"connect",tracked_connect)
    monkeypatch.setattr(investment_evidence,"fingerprint",windows_locking_fingerprint)
    result=materialize_stored(research_db=research,production_db=production,
      decision_at=DECISION,authorization=MATERIALIZE_AUTHORIZATION)

    assert result["production_unchanged"] and production.read_bytes()==production_bytes
    assert not any(path==research and is_open for path,is_open in calls)
    assert [path for path,_ in calls].count(research)==1
    assert fingerprint(research)!=research_before
    with real_connect(str(research),read_only=True) as db:
        stored=db.execute("SELECT research_database_identity FROM investment_evidence_runs").fetchone()[0]
    assert stored==json.dumps(research_before,default=str)

def test_mid_transaction_failure_rolls_back_schema_and_rows(monkeypatch,tmp_path):
    research,production=databases(tmp_path)
    research_before=research.read_bytes(); production_before=production.read_bytes()
    real_initialize=investment_evidence.initialize_schema
    def fail_after_schema(db):
        real_initialize(db)
        raise RuntimeError("forced mid-transaction failure")
    monkeypatch.setattr(investment_evidence,"initialize_schema",fail_after_schema)

    with pytest.raises(RuntimeError,match="forced mid-transaction failure"):
        materialize_stored(research_db=research,production_db=production,
          decision_at=DECISION,authorization=MATERIALIZE_AUTHORIZATION)

    assert research.read_bytes()==research_before
    assert production.read_bytes()==production_before
    with duckdb.connect(str(research),read_only=True) as db:
        assert {row[0] for row in db.execute("SHOW TABLES").fetchall()}=={"marker"}

def test_successful_retry_is_idempotent_and_status_is_read_only(tmp_path):
    research,production=databases(tmp_path)
    for _ in range(2):
        result=materialize_stored(research_db=research,production_db=production,
          decision_at=DECISION,authorization=MATERIALIZE_AUTHORIZATION)
        assert result["inserted"]==0
    before=(research.read_bytes(),production.read_bytes())
    report=status(research_db=research,production_db=production,decision_at=DECISION)
    assert report["read_only"] if "read_only" in report else report["database_immutability"]["verified"]
    assert (research.read_bytes(),production.read_bytes())==before
    with duckdb.connect(str(research),read_only=True) as db:
        assert db.execute("SELECT count(*) FROM investment_evidence_runs").fetchone()[0]==2
        for table in ("security_classification_evidence","canonical_factor_evidence",
                      "corporate_action_coverage_evidence","investment_evidence_failures",
                      "investment_evidence_checkpoints"):
            assert db.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]==0

def test_public_cli_failure_is_stable_and_redacted(tmp_path):
    research,production=databases(tmp_path)
    command=[sys.executable,"-m","app.investment_research_cli",
      "materialize-stored-investment-evidence","--research-db",str(research),
      "--production-db",str(production),"--decision-at",DECISION.isoformat(),
      "--authorization","not authorized"]
    completed=subprocess.run(command,cwd=Path(__file__).parents[1],text=True,
      capture_output=True,check=False)
    payload=json.loads(completed.stderr)
    assert completed.returncode==1
    assert payload=={"error":{"code":"INVESTMENT_RESEARCH_NOT_READY",
      "message":"investment research request failed; details redacted"},"status":"failed"}
    assert "exact materialization authorization required" not in completed.stderr

def test_availability_never_uses_materialization_time():
    public=datetime(2020,1,1,tzinfo=timezone.utc); retrieved=datetime(2022,1,1,tzinfo=timezone.utc)
    assert availability(public,retrieved)==retrieved
    with pytest.raises(ValueError): availability(public,None)

def test_authorization_and_timezone_fail_closed(tmp_path):
    research,production=databases(tmp_path)
    with pytest.raises(InvestmentResearchError): materialize_stored(research_db=research,production_db=production,decision_at=DECISION,authorization="yes")
    with pytest.raises(InvestmentResearchError): status(research_db=research,production_db=production,decision_at=datetime(2026,1,1))

def test_enrichment_plan_is_read_only_and_empty(tmp_path):
    research,production=databases(tmp_path)
    before=research.read_bytes()
    result=enrichment_plan(research_db=research,production_db=production,decision_at=DECISION)
    assert result["read_only"] and result["estimated_request_count"]==0
    assert research.read_bytes()==before

def _repair_fixture(tmp_path):
    root=tmp_path/"operator data with spaces"; root.mkdir()
    research,production=databases(root)
    available=datetime(2026,9,1,tzinfo=timezone.utc)
    with duckdb.connect(str(research)) as db:
        initialize_schema(db)
        db.execute("""CREATE TABLE sec_facts(fact_key VARCHAR, value DOUBLE)""")
        db.execute("INSERT INTO sec_facts VALUES ('fact-1',2.5)")
        db.execute("""INSERT INTO canonical_factor_evidence(
          evidence_key,security_id,qualified_symbol,canonical_field,value,unit,currency,
          period_start,period_end,accession_or_source_identifier,public_at,retrieved_at,
          available_at,materialized_at,original_concept_or_field,alias_contract_version,
          sign_convention,reliability_state,withholding_reason,provenance,source_fact_key,lineage)
          VALUES ('source-1','sid-1','SPACE.US','basic_eps',NULL,'USD/shares','USD',
          DATE '2025-01-01',DATE '2025-12-31','accession-1',?,?,?,?,
          'EarningsPerShareBasic','alias-v1','reported_signed','withheld','incompatible_units',
          ?, 'fact-1', ?)""",[available,available,available,available,
          json.dumps({"source_unit":"USD/shares"}),json.dumps({"latest_visible_revision":True})])
    return research,production

def test_canonical_unit_repair_is_append_only_visible_and_idempotent(tmp_path):
    research,production=_repair_fixture(tmp_path); production_before=production.read_bytes()
    initial=plan_canonical_unit_repair(research_db=research,production_db=production,decision_at=DECISION)
    assert initial["eligible_revision_count"]==1
    assert initial["source_unit_counts"]=={"USD/shares":1}
    assert initial["evidence_key_samples"]==["source-1"]
    first=apply_canonical_unit_repair(research_db=research,production_db=production,
      decision_at=DECISION,authorization=UNIT_REPAIR_AUTHORIZATION)
    assert first["inserted"]==1 and first["unchanged"]==0 and not first["idempotent_retry"]
    assert production.read_bytes()==production_before

    post=plan_canonical_unit_repair(research_db=research,production_db=production,decision_at=DECISION)
    assert post["eligible_revision_count"]==0 and post["source_unit_counts"]=={}
    assert post["evidence_key_samples"]==[]
    report=canonical_unit_repair_status(research_db=research,production_db=production,decision_at=DECISION)
    assert report["pending_eligible_revisions"]==0
    assert report["recent_runs"][-1]["planned_count"]==1
    assert report["recent_runs"][-1]["inserted_count"]==1
    assert report["visible_repaired_revision_counts"]==[
      {"canonical_field":"basic_eps","rule_version":"1.0.0","count":1}]
    assert all(not report[key] for key in ("rankings","candidates","recommendations",
      "paper_selections","prospective_vintages","validation_observations"))
    assert report["validation_credit"]==0

    before_retry=research.read_bytes()
    retry=apply_canonical_unit_repair(research_db=research,production_db=production,
      decision_at=DECISION,authorization=UNIT_REPAIR_AUTHORIZATION)
    assert retry["inserted"]==0 and retry["unchanged"]==1 and retry["idempotent_retry"]
    assert research.read_bytes()==before_retry and production.read_bytes()==production_before
    with duckdb.connect(str(research),read_only=True) as db:
        rows=db.execute("SELECT evidence_key,unit,reliability_state,lineage FROM canonical_factor_evidence ORDER BY evidence_key").fetchall()
        assert len(rows)==2 and sum(r[2]=="usable" for r in rows)==1
        assert next(r for r in rows if r[0]=="source-1")[1:3]==("USD/shares","withheld")
        assert len({r[0] for r in rows})==2
        visible=investment_evidence._matching_repairs(db,DECISION)
        assert visible["source-1"]["unit"]=="USD/share"
        consumer=investment_research._visible_canonical_rows(db,DECISION)
        assert [(r["unit"],r["reliability_state"]) for r in consumer]==[("USD/share","usable")]
    # The source remains eligible before the repair decision, proving that a
    # post-boundary revision is not leaked backwards in time.
    earlier=plan_canonical_unit_repair(research_db=research,production_db=production,
      decision_at=datetime(2026,9,15,tzinfo=timezone.utc))
    assert earlier["eligible_revision_count"]==1

def test_later_rule_version_does_not_suppress_exact_repair(tmp_path):
    research,production=_repair_fixture(tmp_path)
    with duckdb.connect(str(research)) as db:
        source=db.execute("SELECT * FROM canonical_factor_evidence WHERE evidence_key='source-1'").fetchone()
        columns=[x[0] for x in db.description]; row=dict(zip(columns,source))
        row.update(evidence_key="future",unit="USD/share",reliability_state="usable",withholding_reason=None,
          provenance=json.dumps({"unit_normalization":{"source_unit":"USD/shares","canonical_unit":"USD/share",
            "normalization_rule_identifier":"eps-usd-per-share-lossless","rule_version":"2.0.0","scale_factor":1},
            "supersedes_evidence_key":"source-1"}),
          lineage=json.dumps({"revision_type":"canonical_unit_normalization","supersedes_evidence_key":"source-1","rule_version":"2.0.0","repair_decision_at":DECISION.isoformat()}))
        db.execute("INSERT INTO canonical_factor_evidence VALUES ("+",".join("?" for _ in columns)+")",[row[x] for x in columns])
    assert plan_canonical_unit_repair(research_db=research,production_db=production,
      decision_at=DECISION)["eligible_revision_count"]==1

def test_unit_repair_failure_rolls_back_and_cli_error_is_redacted(monkeypatch,tmp_path):
    research,production=_repair_fixture(tmp_path); before=(research.read_bytes(),production.read_bytes())
    real=investment_evidence._initialize_repair_schema
    def fail(db): real(db); raise RuntimeError("secret repair failure")
    monkeypatch.setattr(investment_evidence,"_initialize_repair_schema",fail)
    with pytest.raises(RuntimeError,match="secret repair failure"):
        apply_canonical_unit_repair(research_db=research,production_db=production,
          decision_at=DECISION,authorization=UNIT_REPAIR_AUTHORIZATION)
    assert (research.read_bytes(),production.read_bytes())==before
    command=[sys.executable,"-m","app.investment_research_cli","apply-canonical-unit-repair",
      "--research-db",str(research),"--production-db",str(production),
      "--decision-at",DECISION.isoformat(),"--authorization","wrong"]
    completed=subprocess.run(command,cwd=Path(__file__).parents[1],text=True,capture_output=True)
    assert completed.returncode==1 and "wrong" not in completed.stderr
    assert json.loads(completed.stderr)["error"]["message"]=="investment research request failed; details redacted"

import investment_evidence_fixture as evidence_fixture

def stored_evidence(path):
    """Every stored evidence row, without the per-run timestamps and ids."""
    skip={"materialized_at","last_run_id","updated_at"}; result={}
    with duckdb.connect(str(path),read_only=True) as db:
        for table in ("security_classification_evidence","canonical_factor_evidence",
                      "corporate_action_coverage_evidence","investment_evidence_checkpoints"):
            columns=[c[0] for c in db.execute(f'DESCRIBE "{table}"').fetchall() if c[0] not in skip]
            result[table]=sorted(map(repr,db.execute(f'SELECT {",".join(columns)} FROM "{table}"').fetchall()))
    return result

def materialize_fixture(tmp_path,name,monkeypatch,batch_size):
    research=tmp_path/f"{name}.duckdb"; production=tmp_path/"production.duckdb"
    evidence_fixture.create(research,companies=24)
    if not production.exists():
        with duckdb.connect(str(production)) as db: db.execute("CREATE TABLE marker(value INTEGER)")
    monkeypatch.setattr(investment_evidence,"BATCH_SIZE",batch_size)
    runs=[materialize_stored(research_db=research,production_db=production,
            decision_at=evidence_fixture.DECISION,authorization=MATERIALIZE_AUTHORIZATION) for _ in range(2)]
    return research,runs

def test_batched_materialization_matches_one_company_at_a_time(tmp_path,monkeypatch):
    single,single_runs=materialize_fixture(tmp_path,"single",monkeypatch,1)
    batched,batched_runs=materialize_fixture(tmp_path,"batched",monkeypatch,7)
    counts=lambda runs:[{k:r[k] for k in ("inserted","unchanged","withheld","failures")} for r in runs]
    assert counts(single_runs)==counts(batched_runs)
    assert stored_evidence(single)==stored_evidence(batched)
    first,second=counts(batched_runs)
    assert first["inserted"]>500 and first["failures"]==0
    assert second["inserted"]==0 and second["unchanged"]==first["inserted"]

def test_materialization_uses_only_evidence_public_at_the_decision(tmp_path,monkeypatch):
    research,_=materialize_fixture(tmp_path,"research",monkeypatch,5)
    with duckdb.connect(str(research),read_only=True) as db:
        companies=db.execute("SELECT count(DISTINCT security_id) FROM security_classification_evidence").fetchone()[0]
        late=db.execute("SELECT count(*) FROM canonical_factor_evidence WHERE available_at>?",[evidence_fixture.DECISION]).fetchone()[0]
        after_decision_price=db.execute("SELECT count(*) FROM canonical_factor_evidence WHERE canonical_field='decision_price' AND value=99").fetchone()[0]
        conflicts=db.execute("SELECT count(*) FROM security_classification_evidence WHERE classification_reason='conflicting_sources'").fetchone()[0]
        derived=db.execute("SELECT count(*) FROM canonical_factor_evidence WHERE canonical_field IN ('market_capitalisation','free_cash_flow')").fetchone()[0]
        coverage=dict(db.execute("SELECT coverage_state,count(*) FROM corporate_action_coverage_evidence GROUP BY 1").fetchall())
    assert companies==21  # every eighth listing is an ADR, outside the catalogue
    assert late==0 and after_decision_price==0
    assert conflicts>0 and derived>0
    assert set(coverage)=={"action_present","verified_no_action","coverage_missing"}

def test_a_new_classification_retires_the_earlier_one(tmp_path):
    """Before the widening's SEC facts were stored, a company classified as
    'evidence unavailable'; once they were, the later run must replace that row,
    or every reader sees two types and withholds the company as ambiguous."""
    from app.comparable_universe import classify_security
    research=tmp_path/"research.duckdb"; production=tmp_path/"production.duckdb"
    evidence_fixture.create(research,companies=3)
    with duckdb.connect(str(production)) as db: db.execute("CREATE TABLE marker(value INTEGER)")
    with duckdb.connect(str(research)) as db:  # a foreign filing regime conflicts with the listing
        db.execute("INSERT INTO sec_filings VALUES ('0000000001','x-1','20-F',NULL,TIMESTAMPTZ '2026-09-01 00:00:00+00',false,'fixture',TIMESTAMPTZ '2026-09-01 00:00:00+00')")
    run=lambda: materialize_stored(research_db=research,production_db=production,
        decision_at=evidence_fixture.DECISION,authorization=MATERIALIZE_AUTHORIZATION)
    def rows():
        with duckdb.connect(str(research),read_only=True) as db:
            cur=db.execute("SELECT * FROM security_classification_evidence WHERE security_id='sec-000' ORDER BY materialized_at")
            names=[c[0] for c in cur.description]; return [dict(zip(names,r)) for r in cur.fetchall()]
    assert run()["superseded_classifications"]==0
    first=rows(); assert [r["security_type"] for r in first]==["classification_unavailable"]
    with duckdb.connect(str(research)) as db: db.execute("DELETE FROM sec_filings WHERE accession_number='x-1'")
    assert run()["superseded_classifications"]==1
    second=rows()
    assert [(r["security_type"],r["is_current"]) for r in second]==[("classification_unavailable",False),("us_operating_company",True)]
    later=datetime(2026,12,1,tzinfo=timezone.utc)
    assert classify_security(second,later,security_id="sec-000").included
    assert run()["superseded_classifications"]==0  # an unchanged rerun keeps it current
    assert [r["is_current"] for r in rows()]==[False,True]
