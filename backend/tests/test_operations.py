from __future__ import annotations

import hashlib
import threading
from datetime import datetime, timezone

import duckdb
import pytest

from app.model_readiness import ReadinessError
from app.operations import safe_error
from app.research_scheduler import SchedulerService
from app.shadow_portfolios import create_shadow_vintage, plan_shadow_vintage
from tests.model_readiness_fixture import create_research_fixture

NOW = datetime(2026, 9, 28, 20, tzinfo=timezone.utc)


def databases(tmp_path):
    research, production = tmp_path / "research.duckdb", tmp_path / "production.duckdb"
    create_research_fixture(research, periods=300, per_region=2)
    with duckdb.connect(str(production)) as db:
        db.execute("CREATE TABLE production_guard(value INTEGER)")
    return research, production


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_default_plan_is_bounded_non_disclosing_and_read_only(tmp_path):
    research, production = databases(tmp_path)
    before = digest(research), digest(production)
    plan = plan_shadow_vintage(research_db=research, production_db=production, cutoff=NOW)
    assert "scores" not in plan and "diagnostic_scores" not in plan
    assert plan["total_scored_count"] >= len(plan["proposed_selections"])
    assert len(plan["proposed_selections"]) <= 3
    assert len(plan["affected_sample"]) <= 25 and len(plan["withheld_sample"]) <= 25
    assert before == (digest(research), digest(production))


def test_verbose_scores_are_explicitly_capped(tmp_path):
    research, production = databases(tmp_path)
    plan = plan_shadow_vintage(research_db=research, production_db=production,
                               cutoff=NOW, verbose_scores=True)
    assert len(plan["diagnostic_scores"]) <= plan["diagnostic_score_limit"] == 100


def test_month_end_readiness_fails_closed_then_succeeds(tmp_path):
    research, production = databases(tmp_path)
    with pytest.raises(ReadinessError):
        create_shadow_vintage(research_db=research, production_db=production, cutoff=NOW,
            authorized=True, now=NOW, require_month_end_readiness=True)
    sessions = {region: "2026-09-25" for region in ["US", "LSE", "PA", "TO", "XETRA"]}
    result = create_shadow_vintage(research_db=research, production_db=production, cutoff=NOW,
        authorized=True, now=NOW, expected_session_dates=sessions,
        latest_required_fx_date="2026-09-25", require_month_end_readiness=True)
    assert result["status"] == "created" and result["month_end_readiness"]["confirmed"]


def test_scheduler_disabled_idempotent_and_locked():
    calls = []
    assert SchedulerService().incremental_refresh(NOW, lambda: calls.append(1))["status"] == "disabled"
    scheduler = SchedulerService(enabled=True)
    assert scheduler.month_end_shadow_plan(NOW, lambda: calls.append(1))["status"] == "completed"
    assert scheduler.month_end_shadow_plan(NOW, lambda: calls.append(2))["status"] == "already_completed"
    scheduler._locks["matured_shadow_evaluation"] = threading.Lock()
    scheduler._locks["matured_shadow_evaluation"].acquire()
    assert scheduler.matured_shadow_evaluation(NOW, lambda: None)["status"] == "locked"
    assert calls == [1]


def test_public_errors_are_redacted():
    error = safe_error()
    assert "path" not in str(error).lower() and "token" not in str(error).lower()
