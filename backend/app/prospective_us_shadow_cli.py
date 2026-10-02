"""Bounded command line workflow for the Milestone 28 paper experiment."""
from __future__ import annotations
import argparse
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

from .prospective_us_shadow import (AUTHORIZATION_PHRASE, build_plan,
    create_from_database_plan, plan_from_databases, readiness, status)
from .paper_portfolio import (mark_to_market, plan_monthly_cycle,
                              validation_ledger, vintage_detail, vintage_list)


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Prospective US paper shadow research")
    commands = root.add_subparsers(dest="command", required=True)
    for name in ("plan-prospective-us-shadow-offline-fixture",
                 "plan-prospective-us-shadow-from-db", "prospective-us-shadow-readiness",
                 "prospective-us-shadow-status", "evaluate-prospective-us-shadows",
                 "create-prospective-us-shadow", "plan-prospective-monthly-cycle",
                 "paper-vintage-status", "paper-vintage-detail",
                 "paper-mark-to-market", "prospective-validation-ledger"):
        command = commands.add_parser(name)
        command.add_argument("--research-db", type=Path, required=True)
        command.add_argument("--production-db", type=Path, required=True)
        if name == "plan-prospective-us-shadow-offline-fixture":
            command.add_argument("--fixture", type=Path, required=True,
                help="OFFLINE/TEST-ONLY input bundle; prohibited as an operational source")
        if name in {"plan-prospective-us-shadow-offline-fixture",
                    "plan-prospective-us-shadow-from-db", "prospective-us-shadow-readiness",
                    "plan-prospective-monthly-cycle"}:
            command.add_argument("--decision-at", type=datetime.fromisoformat, required=True)
        if name in {"plan-prospective-us-shadow-from-db", "prospective-us-shadow-readiness",
                    "plan-prospective-monthly-cycle"}:
            command.add_argument("--us-session-date", type=date.fromisoformat, required=True)
            command.add_argument("--require-fx", action="store_true",
                help="Require session-date FX only when the catalogue genuinely needs it")
        if name == "create-prospective-us-shadow":
            command.add_argument("--plan-identifier", required=True,
                help="exact identifier emitted by the immediately preceding database plan")
            command.add_argument("--authorization", required=True,
                help=f"exactly: {AUTHORIZATION_PHRASE}")
        if name == "evaluate-prospective-us-shadows":
            command.add_argument("--as-of", type=datetime.fromisoformat, required=True)
        if name in {"paper-mark-to-market", "prospective-validation-ledger"}:
            command.add_argument("--as-of", type=datetime.fromisoformat)
        if name in {"paper-vintage-detail", "paper-mark-to-market"}:
            command.add_argument("--vintage-id")
    return root


def _fixture(path: Path) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return pd.DataFrame(data["prices"]), pd.DataFrame(data["dilution"]), data["readiness"]


def execute(args: argparse.Namespace, *, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    if args.command == "plan-prospective-monthly-cycle":
        return plan_monthly_cycle(research_db=args.research_db, production_db=args.production_db,
            decision_at=args.decision_at, session_date=args.us_session_date, now=now)
    if args.command == "paper-vintage-status":
        return vintage_list(research_db=args.research_db, production_db=args.production_db)
    if args.command == "paper-vintage-detail":
        if not args.vintage_id: raise ValueError("--vintage-id is required")
        return vintage_detail(research_db=args.research_db, production_db=args.production_db,
                              vintage_id=args.vintage_id)
    if args.command == "paper-mark-to-market":
        return mark_to_market(research_db=args.research_db, production_db=args.production_db,
            vintage_id=args.vintage_id, as_of=args.as_of)
    if args.command == "prospective-validation-ledger":
        return validation_ledger(research_db=args.research_db, production_db=args.production_db,
                                 as_of=args.as_of)
    if args.command == "prospective-us-shadow-status":
        return status(research_db=args.research_db, production_db=args.production_db)
    if args.command == "evaluate-prospective-us-shadows":
        # Evaluation is intentionally a fingerprinted status/readiness operation until
        # exact-session outcomes exist; it never creates or updates cohorts.
        result = status(research_db=args.research_db, production_db=args.production_db)
        return {**result, "command": args.command, "as_of": args.as_of.isoformat(),
                "mode": "strictly_read_only", "outcomes_written": 0}
    if args.command == "plan-prospective-us-shadow-offline-fixture":
        prices, dilution, fixture_readiness = _fixture(args.fixture)
        result = build_plan(prices, dilution, decision_at=args.decision_at, generated_at=now,
            session_ready=fixture_readiness["complete_month_end_session"],
            fx_ready=fixture_readiness["fx_ready"])
        return {**result, "command": args.command, "mode": "offline_test_only",
                "operational_source": False}
    if args.command == "plan-prospective-us-shadow-from-db":
        return plan_from_databases(research_db=args.research_db, production_db=args.production_db,
            decision_at=args.decision_at, session_date=args.us_session_date, now=now,
            require_fx=args.require_fx)
    if args.command == "prospective-us-shadow-readiness":
        return readiness(research_db=args.research_db, production_db=args.production_db,
            decision_at=args.decision_at, session_date=args.us_session_date, now=now,
            require_fx=args.require_fx)
    return create_from_database_plan(research_db=args.research_db,
        production_db=args.production_db, plan_identifier=args.plan_identifier,
        authorization=args.authorization, now=now)


def main() -> None:
    try:
        print(json.dumps(execute(parser().parse_args()), sort_keys=True, default=str))
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": {"code": type(exc).__name__,
            "message": "prospective US shadow command failed; details redacted"}}), file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__": main()
