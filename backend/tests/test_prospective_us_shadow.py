from __future__ import annotations

import json
import warnings
from datetime import date, datetime, timedelta, timezone

import duckdb
import numpy as np
import pandas as pd
import pytest

from app.model_readiness import ReadinessError, fingerprint
from app.prospective_us_shadow import (AUTHORIZATION_PHRASE, CONFIGURATION_HASH,
    REGISTRATION_AT, SPECIFICATION, build_plan, configuration_hash,
    create_from_database_plan, create_from_plan,
    evaluate_maturity, plan_from_databases, score_inputs, status)
from app.prospective_us_shadow_cli import parser
from app.research_scheduler import SchedulerService
from app.global_market_data import PRICE_SCHEMA_SQL
from app.global_universe import SCHEMA_SQL
from app.sec_ingestion import SCHEMA as SEC_SCHEMA
from app.statistics import safe_correlation

UTC = timezone.utc


def inputs():
    prices = pd.DataFrame([
        {"security_id": "b", "qualified_symbol": "B.US", "price_percentile": .8, "decision_price": 20, "model_ready": True, "price_date": "2026-10-30"},
        {"security_id": "a", "qualified_symbol": "A.US", "price_percentile": .8, "decision_price": 10, "model_ready": True, "price_date": "2026-10-30"},
        {"security_id": "c", "qualified_symbol": "C.US", "price_percentile": .2, "decision_price": 30, "model_ready": True, "price_date": "2026-10-30"},
        {"security_id": "d", "qualified_symbol": "D.US", "price_percentile": .9, "decision_price": 40, "model_ready": True, "price_date": "2026-10-30"},
        {"security_id": "e", "qualified_symbol": "E.US", "price_percentile": .9, "decision_price": 50, "model_ready": False, "price_date": "2026-10-30"},
    ])
    dilution = pd.DataFrame([
        {"security_id": key, "diluted_share_growth": growth, "period_end": period,
         "filed_at": "2026-09-01T00:00:00Z", "retrieved_at": "2026-09-02T00:00:00Z",
         "reliable": reliable, "compatible": True, "provenance": f"fixture:{key}"}
        for key, growth, period, reliable in [
            ("a", -.1, "2025-12-31", True), ("b", -.1, "2025-12-31", True),
            ("c", .1, "2025-12-31", True), ("d", np.nan, "2025-12-31", True),
            ("e", 0, "2024-01-01", False)]] )
    return prices, dilution


def databases(tmp_path):
    research, production = tmp_path/"research.duckdb", tmp_path/"production.duckdb"
    for path in (research, production):
        duckdb.connect(str(path)).close()
    return research, production


def populated_databases(tmp_path):
    research, production = databases(tmp_path)
    decision = datetime(2026, 10, 30, 22, tzinfo=UTC)
    with duckdb.connect(str(research)) as db:
        db.execute(SCHEMA_SQL); db.execute(PRICE_SCHEMA_SQL); db.execute(SEC_SCHEMA)
        db.execute("""CREATE TABLE eodhd_ingestion_checkpoints(
            qualified_symbol VARCHAR, status VARCHAR, error_code VARCHAR)""")
        db.execute("INSERT INTO security_master_retrievals VALUES ('r','test','synthetic',?,NULL,'completed','hash',2,NULL)", [datetime(2026, 1, 1)])
        for index, symbol in enumerate(("A.US", "B.US"), 1):
            security_id = symbol[0].lower()
            db.execute("""INSERT INTO security_listings VALUES
                ('r',?,?,?, ?,?,'Synthetic','US','US','US','USD','common_stock',true,true,NULL,?,NULL,?,?, '{}')""",
                [security_id, f"co{index}", symbol, symbol[0], symbol,
                 str(index).zfill(10), datetime(2026, 1, 1), datetime(2026, 10, 30)])
            db.execute("INSERT INTO sec_issuers VALUES (?,?,?,?,?,?,?)",
                [security_id, symbol, symbol[0], str(index).zfill(10), "Synthetic", "synthetic", datetime(2026, 1, 1)])
            db.execute("INSERT INTO sec_checkpoints VALUES (?,?,?,?,'completed','run',?,1)",
                [security_id, symbol, symbol[0], str(index).zfill(10), datetime(2026, 10, 29, tzinfo=UTC)])
            for year, value in ((2024, 100 + index), (2025, 100 + index * 2)):
                db.execute("""INSERT INTO sec_facts(fact_key,security_id,qualified_symbol,ticker,cik,
                    taxonomy,concept,value,unit,currency,period_start,period_end,fiscal_year,fiscal_period,
                    frame,form,accession_number,filed_date,public_at,is_amendment,is_revision,source_endpoint,retrieved_at)
                    VALUES (?,?,?,?,?,'us-gaap','WeightedAverageNumberOfDilutedSharesOutstanding',?,'shares',NULL,?,?,?,?,NULL,'10-K',?,?,?,false,false,'synthetic',?)""",
                    [f"{security_id}-{year}", security_id, symbol, symbol[0], str(index).zfill(10), value,
                     date(year, 1, 1), date(year, 12, 31), year, "FY", f"acc-{security_id}-{year}",
                     date(year + 1, 2, 1), datetime(year + 1, 2, 1, tzinfo=UTC), datetime(year + 1, 2, 2, tzinfo=UTC)])
        days = pd.bdate_range(end="2026-10-30", periods=127)
        for index, (symbol, region, currency) in enumerate((
            ("C.LSE", "LSE", "GBP"), ("D.TO", "TO", "CAD"),
            ("E.XETRA", "XETRA", "EUR"), ("F.PA", "PA", "EUR")), 3):
            db.execute("""INSERT INTO security_listings VALUES
                ('r',?,?,?, ?,?,'Synthetic',?,?,?,?,'common_stock',true,true,NULL,NULL,NULL,?,?, '{}')""",
                [symbol.lower(), f"co{index}", symbol, symbol[0], symbol, region, region, region, currency,
                 datetime(2026, 1, 1), datetime(2026, 10, 30)])
        for index, day in enumerate(days):
            for symbol, offset, currency, region in (("A.US", 0, "USD", "US"),
                    ("B.US", 10, "USD", "US"), ("C.LSE", 20, "GBP", "LSE"),
                    ("D.TO", 30, "CAD", "TO"), ("E.XETRA", 40, "EUR", "XETRA"),
                    ("F.PA", 50, "EUR", "PA")):
                price = 100 + offset + index
                db.execute("INSERT INTO global_price_observations VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    [symbol, day.date(), region, currency, price, price, price, price, price, 1000,
                     "available", "synthetic", datetime.combine(day.date(), datetime.min.time(), tzinfo=UTC) + timedelta(hours=21)])
            for currency in ("USD", "CAD", "EUR"):
                db.execute("INSERT INTO global_fx_observations VALUES (?, 'GBP', ?, 1, 'synthetic', ?, ?)",
                    [currency, day.date(), decision, decision])
        for day in pd.bdate_range("2026-10-01", "2026-10-30"):
            db.execute("INSERT INTO global_exchange_sessions VALUES ('US',?,true,'synthetic',?)",
                [day.date(), datetime(2026, 10, 1, tzinfo=UTC)])
    return research, production, decision


def test_configuration_is_canonical_stable_and_locked():
    assert configuration_hash(SPECIFICATION) == CONFIGURATION_HASH
    assert len(CONFIGURATION_HASH) == 64
    changed = {**SPECIFICATION, "score_formula": "changed"}
    assert configuration_hash(changed) != CONFIGURATION_HASH


def test_boundary_and_dilution_withholding_and_deterministic_ties():
    prices, dilution = inputs()
    with pytest.raises(ReadinessError, match="strictly after"):
        build_plan(prices, dilution, decision_at=REGISTRATION_AT, generated_at=REGISTRATION_AT,
                   session_ready=True, fx_ready=True)
    with pytest.raises(ReadinessError, match="September"):
        build_plan(prices, dilution, decision_at=datetime(2026, 9, 30, tzinfo=UTC),
                   generated_at=REGISTRATION_AT, session_ready=True, fx_ready=True)
    eligible, selected = score_inputs(prices, dilution,
        decision_at=datetime(2026, 10, 30, 22, tzinfo=UTC))
    assert eligible.qualified_symbol.tolist() == ["A.US", "B.US", "C.US"]
    assert selected.qualified_symbol.tolist() == ["A.US", "B.US", "C.US"]
    assert "D.US" not in eligible.qualified_symbol.tolist()  # missing is not neutral/zero


def test_plan_expiry_creation_duplicate_cohorts_and_production_isolation(tmp_path):
    research, production = databases(tmp_path); prices, dilution = inputs()
    now = datetime(2026, 10, 30, 23, tzinfo=UTC)
    plan = build_plan(prices, dilution, decision_at=now-timedelta(hours=1), generated_at=now,
                      session_ready=True, fx_ready=True)
    before = fingerprint(production)
    result = create_from_plan(research_db=research, production_db=production, plan=plan,
        prices=prices, dilution=dilution, authorization=AUTHORIZATION_PHRASE, now=now)
    assert result["paper_selection_count"] == 3 and result["production_unchanged"]
    assert fingerprint(production) == before
    with duckdb.connect(str(research), read_only=True) as db:
        assert db.execute("select horizon_sessions from prospective_us_shadow_cohorts order by 1").fetchall() == [(126,), (252,)]
        assert db.execute("select count(*) from prospective_us_shadow_vintages").fetchone()[0] == 1
    duplicate = create_from_plan(research_db=research, production_db=production, plan=plan,
        prices=prices, dilution=dilution, authorization=AUTHORIZATION_PHRASE, now=now)
    assert duplicate == {"command": "create-prospective-us-shadow", "status": "already_exists", "mutated": False}
    expired = build_plan(prices, dilution, decision_at=now, generated_at=now-timedelta(minutes=11),
                         session_ready=True, fx_ready=True)
    with pytest.raises(ReadinessError, match="expired"):
        create_from_plan(research_db=research, production_db=production, plan=expired,
            prices=prices, dilution=dilution, authorization=AUTHORIZATION_PHRASE, now=now)


def test_modified_unready_and_unauthorized_plans_fail_transactionally(tmp_path):
    research, production = databases(tmp_path); prices, dilution = inputs()
    now = datetime(2026, 10, 31, tzinfo=UTC)
    plan = build_plan(prices, dilution, decision_at=now, generated_at=now,
                      session_ready=True, fx_ready=True)
    with pytest.raises(PermissionError):
        create_from_plan(research_db=research, production_db=production, plan=plan,
            prices=prices, dilution=dilution, authorization="no", now=now)
    plan["selected_ids"] = []
    with pytest.raises(ReadinessError, match="modified"):
        create_from_plan(research_db=research, production_db=production, plan=plan,
            prices=prices, dilution=dilution, authorization=AUTHORIZATION_PHRASE, now=now)
    with duckdb.connect(str(research), read_only=True) as db:
        assert "prospective_us_shadow_vintages" not in {r[0] for r in db.execute("show tables").fetchall()}


def test_maturity_exact_boundaries_baselines_and_incomplete_refusal():
    decision = datetime(2026, 10, 30, tzinfo=UTC)
    sessions = pd.date_range("2026-11-02", periods=252, freq="B")
    immature = evaluate_maturity(sessions[:125], decision, 126, {}, ["a"], ["b"], ["a", "b"])
    assert immature == {"matured": False, "reason": "exact_session_not_reached"}
    incomplete = evaluate_maturity(sessions[:126], decision, 126, {"a": .2}, ["a"], ["b"], ["a", "b"])
    assert incomplete["complete"] is False
    complete = evaluate_maturity(sessions, decision, 252, {"a": .2, "b": .1}, ["a"], ["b"], ["a", "b"])
    assert complete["incremental_vs_price_only"] == pytest.approx(.1)
    assert complete["excess_vs_equal_weight"] == pytest.approx(.05)


def test_safe_constant_correlation_has_reason_and_no_warning():
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        result = safe_correlation([1, 1, 1], [1, 2, 3])
    assert result == {"value": None, "reason": "zero_variance", "observations": 3}
    assert caught == []


def test_read_only_status_fingerprints_and_path_alias_rejection(tmp_path):
    research, production = databases(tmp_path)
    before = fingerprint(research), fingerprint(production)
    report = status(research_db=research, production_db=production)
    assert report["evidence_immature"] and report["paper_selection_count"] == 0
    assert before == (fingerprint(research), fingerprint(production))
    with pytest.raises(ReadinessError): status(research_db=research, production_db=research)
    hardlink = tmp_path/"hard.duckdb"; hardlink.hardlink_to(research)
    with pytest.raises(ReadinessError): status(research_db=research, production_db=hardlink)


def test_database_plan_refuses_future_before_reading_and_fixture_is_isolated(tmp_path):
    research, production = databases(tmp_path)
    now = datetime(2026, 10, 30, 20, tzinfo=UTC)
    with pytest.raises(ReadinessError, match="future"):
        plan_from_databases(research_db=research, production_db=production,
            decision_at=now + timedelta(hours=1), session_date=now.date(), now=now)
    commands = parser()._subparsers._group_actions[0].choices
    assert "plan-prospective-us-shadow-from-db" in commands
    assert "plan-prospective-us-shadow-offline-fixture" in commands
    assert "plan-prospective-us-shadow" not in commands


def test_database_plan_is_direct_deterministic_bounded_and_read_only(tmp_path):
    research, production, decision = populated_databases(tmp_path)
    before = fingerprint(research), fingerprint(production)
    first = plan_from_databases(research_db=research, production_db=production,
        decision_at=decision, session_date=decision.date(), now=decision)
    second = plan_from_databases(research_db=research, production_db=production,
        decision_at=decision, session_date=decision.date(), now=decision)
    assert first["source"] == "authoritative_databases"
    assert first["plan_identifier"] == second["plan_identifier"]
    assert 0 <= first["paper_selection_count"] <= 3
    assert all("price_percentile" in row and "dilution_percentile" in row
               for row in first["proposed_paper_selections"])
    assert before == (fingerprint(research), fingerprint(production))
    created = create_from_database_plan(research_db=research, production_db=production,
        plan_identifier=first["plan_identifier"], authorization=AUTHORIZATION_PHRASE,
        now=decision + timedelta(minutes=1))
    assert created["status"] == "created" and created["production_unchanged"]
    duplicate = create_from_database_plan(research_db=research, production_db=production,
        plan_identifier=first["plan_identifier"], authorization=AUTHORIZATION_PHRASE,
        now=decision + timedelta(minutes=2))
    assert duplicate["status"] == "already_exists" and duplicate["mutated"] is False


def test_scheduler_can_plan_but_never_authorize_or_create():
    scheduler = SchedulerService(enabled=True)
    at = datetime(2026, 10, 30, 22, tzinfo=UTC)
    planned = scheduler.prospective_shadow_plan(at, lambda: {"mode": "strictly_read_only"})
    assert planned["status"] == "completed"
    assert planned["authorization_supplied"] is False
    assert planned["automatic_creation"] is False
    assert scheduler.prospective_shadow_create(at)["status"] == "prohibited"
