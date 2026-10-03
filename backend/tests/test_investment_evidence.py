from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

import duckdb
import pytest

import app.investment_evidence as investment_evidence
from app.investment_evidence import (MATERIALIZE_AUTHORIZATION, SCHEMA, availability,
    enrichment_plan, initialize_schema, materialize_stored, status)
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
