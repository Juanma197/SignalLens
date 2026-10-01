from __future__ import annotations

import json
import warnings
from datetime import datetime, timedelta, timezone

import duckdb
import numpy as np
import pandas as pd
import pytest

from app.model_readiness import ReadinessError, fingerprint
from app.prospective_us_shadow import (AUTHORIZATION_PHRASE, CONFIGURATION_HASH,
    REGISTRATION_AT, SPECIFICATION, build_plan, configuration_hash, create_from_plan,
    evaluate_maturity, score_inputs, status)
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
