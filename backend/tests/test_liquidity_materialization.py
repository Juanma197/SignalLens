from datetime import timedelta
import json

import duckdb
import pytest

from app.liquidity_materialization import (AUTHORIZATION_PHRASE, ERRORS,
    LiquidityMaterializationError, apply, plan, status, validate_plan)
from app.model_readiness import fingerprint
from tests.test_liquidity_evidence import DECISION, fixture


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
