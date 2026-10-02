from datetime import date, datetime, timezone

import duckdb

from app.model_readiness import fingerprint
from app.paper_portfolio import (plan_monthly_cycle, validation_ledger,
                                 vintage_list)
from app.prospective_us_shadow import CONFIGURATION_HASH
from app.prospective_us_shadow_cli import parser


def databases(tmp_path):
    research = tmp_path / "research db.duckdb"
    production = tmp_path / "production db.duckdb"
    duckdb.connect(str(research)).close()
    duckdb.connect(str(production)).close()
    return research, production


def test_cycle_refusal_is_bounded_read_only_and_fingerprinted(tmp_path):
    research, production = databases(tmp_path)
    before = fingerprint(research), fingerprint(production)
    report = plan_monthly_cycle(research_db=research, production_db=production,
        decision_at=datetime(2026, 10, 30, 21, tzinfo=timezone.utc),
        session_date=date(2026, 10, 30),
        now=datetime(2026, 10, 30, 22, tzinfo=timezone.utc))
    assert report["ready"] is False
    assert report["blocking_reason_codes"] == ["PRICE_OR_SESSION_DATA_STALE"]
    assert report["configuration_hash"] == CONFIGURATION_HASH
    assert report["plan_identifier"] is None
    assert before == (fingerprint(research), fingerprint(production))


def test_empty_vintage_and_ledger_reports_are_read_only(tmp_path):
    research, production = databases(tmp_path)
    before = fingerprint(research), fingerprint(production)
    assert vintage_list(research_db=research, production_db=production)["count"] == 0
    ledger = validation_ledger(research_db=research, production_db=production,
                               as_of=datetime(2027, 1, 1, tzinfo=timezone.utc))
    assert ledger["official_vintage_count"] == 0
    assert ledger["gate_state"] == "INSUFFICIENT PROSPECTIVE EVIDENCE"
    assert before == (fingerprint(research), fingerprint(production))


def test_operator_commands_are_explicit():
    commands = parser()._subparsers._group_actions[0].choices
    assert {"plan-prospective-monthly-cycle", "paper-vintage-status",
            "paper-mark-to-market", "prospective-validation-ledger"} <= set(commands)
