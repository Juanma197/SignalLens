from __future__ import annotations

import hashlib
import os
from pathlib import Path

import duckdb
import pytest

from app.eodhd_ingestion_cli import build_parser, execute
from app.model_readiness import ReadinessError, assess_model_readiness
from app.research_observations import ObservationValidationError
from tests.model_readiness_fixture import DECISION, create_research_fixture, mutate


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def databases(tmp_path: Path) -> tuple[Path, Path]:
    research, production = tmp_path / "research.duckdb", tmp_path / "production.duckdb"
    create_research_fixture(research)
    with duckdb.connect(str(production)) as db:
        db.execute("CREATE TABLE production_guard(value VARCHAR)")
        db.execute("INSERT INTO production_guard VALUES ('must-not-change')")
    return research, production


def assess(paths: tuple[Path, Path]):
    return assess_model_readiness(
        research_db=paths[0], production_db=paths[1], decision_at=DECISION,
    )


def test_successful_assessment_is_aggregate_read_only_and_five_region(tmp_path: Path):
    paths = databases(tmp_path)
    before = tuple(digest(path) for path in paths)
    report = assess(paths)
    assert report["selected_securities"] == report["loaded_securities"] == 5
    assert report["model_ready_securities"] == 5
    assert report["selected_by_region"] == {"LSE": 1, "PA": 1, "TO": 1, "US": 1, "XETRA": 1}
    assert report["selected_by_currency"] == {"CAD": 1, "EUR": 2, "GBX": 1, "USD": 1}
    assert report["fx_quality"]["point_in_time_semantics"] == "latest_available_on_or_before_price_date"
    assert report["fx_quality"]["gbx_divisor"] == 100
    assert report["ranking_generated"] is report["top_3_generated"] is False
    assert report["highest_conviction_candidate_generated"] is False
    assert all(value["unchanged"] for value in report["database_fingerprints"].values())
    assert before == tuple(digest(path) for path in paths)
    assert not list(tmp_path.glob("*.wal"))


def test_missing_region_fails_closed(tmp_path: Path):
    paths = databases(tmp_path)
    mutate(paths[0], "DELETE FROM security_listings WHERE primary_exchange='PA'")
    with pytest.raises(ObservationValidationError, match="all five regions"):
        assess(paths)


@pytest.mark.parametrize(
    ("sql", "reason"),
    [
        ("DELETE FROM global_fx_observations WHERE base_currency='USD'", "missing_fx"),
        ("UPDATE global_fx_observations SET observed_on=observed_on-INTERVAL 20 DAY", "stale_fx"),
        ("DELETE FROM global_price_observations WHERE qualified_symbol='CHARLIE.TO' AND trading_date < '2026-09-01'", "insufficient_history"),
        ("UPDATE global_price_observations SET trading_date=trading_date-INTERVAL 20 DAY", "stale_price"),
    ],
)
def test_missing_stale_fx_and_short_stale_prices_are_withheld(tmp_path: Path, sql: str, reason: str):
    paths = databases(tmp_path)
    mutate(paths[0], sql)
    report = assess(paths)
    assert report["exclusions"][reason] >= 1
    assert report["withheld_securities"] >= 1


def test_permanent_failure_and_bounded_sanitized_sample(tmp_path: Path):
    paths = databases(tmp_path)
    mutate(paths[0], """INSERT INTO eodhd_ingestion_checkpoints VALUES
        ('refresh','ALPHA.US','failed','invalid_provider_payload','2026-09-27')""")
    report = assess(paths)
    assert report["provider_failures"] == {"permanent": 1, "retryable": 0, "other": 0}
    assert report["affected_symbols_sample"] == [
        {"symbol": "ALPHA.US", "reasons": ["permanent_provider_failure"]}
    ]


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO global_price_observations SELECT * FROM global_price_observations LIMIT 1",
        "INSERT INTO global_fx_observations SELECT * FROM global_fx_observations LIMIT 1",
        "INSERT INTO global_corporate_actions SELECT * FROM global_corporate_actions LIMIT 1",
    ],
)
def test_duplicate_natural_keys_fail_closed(tmp_path: Path, sql: str):
    paths = databases(tmp_path)
    mutate(paths[0], sql)
    with pytest.raises(ObservationValidationError, match="Duplicate"):
        assess(paths)


@pytest.mark.parametrize(
    ("sql", "reason"),
    [
        ("UPDATE global_price_observations SET high=low-1 WHERE qualified_symbol='ECHO.PA'", "invalid_price_history"),
        ("UPDATE global_price_observations SET adjusted_close=-1 WHERE qualified_symbol='ECHO.PA'", "invalid_price_history"),
        ("UPDATE global_corporate_actions SET value=-1", "invalid_corporate_action"),
    ],
)
def test_invalid_prices_adjusted_close_and_actions_are_withheld(tmp_path: Path, sql: str, reason: str):
    paths = databases(tmp_path)
    mutate(paths[0], sql)
    report = assess(paths)
    assert report["exclusions"][reason] == 1


def test_wrong_aliased_missing_and_incomplete_database_paths_are_refused(tmp_path: Path):
    research, production = databases(tmp_path)
    with pytest.raises(ReadinessError, match="identical or aliased"):
        assess_model_readiness(research_db=research, production_db=research, decision_at=DECISION)
    alias = tmp_path / "alias.duckdb"
    os.link(research, alias)
    with pytest.raises(ReadinessError, match="identical or aliased"):
        assess_model_readiness(research_db=research, production_db=alias, decision_at=DECISION)
    missing = tmp_path / "missing.duckdb"
    with pytest.raises(ReadinessError, match="does not exist"):
        assess_model_readiness(research_db=missing, production_db=production, decision_at=DECISION)
    assert not missing.exists()
    incomplete = tmp_path / "incomplete.duckdb"
    with duckdb.connect(str(incomplete)) as db:
        db.execute("CREATE TABLE unrelated(value INTEGER)")
    with pytest.raises(ReadinessError, match="incomplete research schema"):
        assess_model_readiness(research_db=incomplete, production_db=production, decision_at=DECISION)


def test_cli_requires_explicit_paths_and_emits_readiness_report(tmp_path: Path):
    parser = build_parser()
    with pytest.raises(ValueError, match="explicit --research-db and --production-db"):
        execute(parser.parse_args(["model-readiness"]), now=DECISION)
    research, production = databases(tmp_path)
    report = execute(parser.parse_args([
        "model-readiness", "--research-db", str(research),
        "--production-db", str(production), "--decision-at", DECISION.isoformat(),
    ]))
    assert report["command"] == "model-readiness"
    assert report["mode"] == "strictly_read_only"
