from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

import duckdb
import pandas as pd
import pytest

from app.model_readiness import ReadinessError
from app.shadow_portfolios import (LABEL, ShadowPolicy, create_shadow_vintage,
    equal_weight_return, maturity_session, plan_shadow_vintage, shadow_status,
    strategy_manifest)
from tests.model_readiness_fixture import create_research_fixture


CUTOFF = datetime(2026, 9, 28, 20, tzinfo=timezone.utc)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def databases(tmp_path):
    research, production = tmp_path / "research.duckdb", tmp_path / "production.duckdb"
    create_research_fixture(research, periods=300, per_region=2)
    with duckdb.connect(str(production)) as db:
        db.execute("CREATE TABLE production_guard(value INTEGER)")
    return research, production


def test_plan_is_point_in_time_deterministic_and_read_only(tmp_path):
    research, production = databases(tmp_path)
    before = digest(research), digest(production)
    first = plan_shadow_vintage(research_db=research, production_db=production, cutoff=CUTOFF)
    second = plan_shadow_vintage(research_db=research, production_db=production, cutoff=CUTOFF)
    assert first["proposed_selections"] == second["proposed_selections"]
    assert len(first["proposed_selections"]) <= 3
    assert first["label"] == LABEL and first["database_unchanged"]
    assert before == (digest(research), digest(production))
    with pytest.raises(ReadinessError):
        plan_shadow_vintage(research_db=research, production_db=production,
                            cutoff=CUTOFF.replace(tzinfo=None))


def test_configuration_hash_and_zero_selection():
    one = strategy_manifest(CUTOFF, "abc", commit="deadbeef")
    two = strategy_manifest(CUTOFF + timedelta(days=1), "different", commit="other")
    assert one["configuration_hash"] == two["configuration_hash"]
    changed = strategy_manifest(CUTOFF, "abc", commit="deadbeef",
                                policy=ShadowPolicy(minimum_score=101))
    assert changed["configuration_hash"] != one["configuration_hash"]


def test_126_252_boundaries_incomplete_outcomes_and_baseline():
    sessions = pd.bdate_range("2026-09-29", periods=252)
    assert maturity_session(sessions[:125], CUTOFF, 126) is None
    assert maturity_session(sessions[:126], CUTOFF, 126) == sessions[125]
    assert maturity_session(sessions[:251], CUTOFF, 252) is None
    assert maturity_session(sessions, CUTOFF, 252) == sessions[251]
    assert equal_weight_return({"A": .1, "B": -.05}, ["A", "B"]) == pytest.approx(.025)
    assert equal_weight_return({"A": .1}, ["A", "B"]) is None


def test_zero_selection_vintage_plan(tmp_path):
    research, production = databases(tmp_path)
    plan = plan_shadow_vintage(research_db=research, production_db=production,
        cutoff=CUTOFF, policy=ShadowPolicy(minimum_score=101))
    assert plan["proposed_selections"] == []


def test_create_requires_authorization_is_idempotent_and_immutable(tmp_path):
    research, production = databases(tmp_path)
    production_before = digest(production)
    with pytest.raises(PermissionError):
        create_shadow_vintage(research_db=research, production_db=production,
                              cutoff=CUTOFF, authorized=False, now=CUTOFF)
    created = create_shadow_vintage(research_db=research, production_db=production,
                                    cutoff=CUTOFF, authorized=True, now=CUTOFF)
    assert created["status"] == "created" and created["cohorts"] == [126, 252]
    original = shadow_status(research_db=research, production_db=production)
    duplicate = create_shadow_vintage(research_db=research, production_db=production,
                                      cutoff=CUTOFF, authorized=True, now=CUTOFF)
    assert duplicate["status"] == "already_exists" and not duplicate["mutated"]
    assert shadow_status(research_db=research, production_db=production) == original
    assert digest(production) == production_before


def test_no_backfill_and_production_alias_refused(tmp_path):
    research, production = databases(tmp_path)
    with pytest.raises(ReadinessError):
        create_shadow_vintage(research_db=research, production_db=production,
            cutoff=datetime(2026, 9, 27, tzinfo=timezone.utc), authorized=True, now=CUTOFF)
    with pytest.raises(ReadinessError):
        plan_shadow_vintage(research_db=research, production_db=research, cutoff=CUTOFF)
