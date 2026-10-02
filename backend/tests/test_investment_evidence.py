from datetime import datetime, timezone

import duckdb
import pytest

from app.investment_evidence import (MATERIALIZE_AUTHORIZATION, SCHEMA, availability,
    enrichment_plan, initialize_schema, materialize_stored, status)
from app.investment_research import InvestmentResearchError


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
