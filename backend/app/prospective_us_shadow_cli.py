"""Bounded command line workflow for the Milestone 28 paper experiment."""
from __future__ import annotations
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from .prospective_us_shadow import (AUTHORIZATION_PHRASE, build_plan,
    create_from_plan, status)


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Prospective US paper shadow research")
    commands = root.add_subparsers(dest="command", required=True)
    for name in ("plan-prospective-us-shadow", "prospective-us-shadow-status",
                 "evaluate-prospective-us-shadows", "create-prospective-us-shadow"):
        command = commands.add_parser(name)
        command.add_argument("--research-db", type=Path, required=True)
        command.add_argument("--production-db", type=Path, required=True)
        if name in {"plan-prospective-us-shadow", "create-prospective-us-shadow"}:
            command.add_argument("--fixture", type=Path, required=True,
                help="Offline decision-time input bundle; live providers are never called")
            command.add_argument("--decision-at", type=datetime.fromisoformat, required=True)
        if name == "create-prospective-us-shadow":
            command.add_argument("--plan-file", type=Path, required=True)
            command.add_argument("--authorization", required=True,
                help=f"exactly: {AUTHORIZATION_PHRASE}")
        if name == "evaluate-prospective-us-shadows":
            command.add_argument("--as-of", type=datetime.fromisoformat, required=True)
    return root


def _fixture(path: Path) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return pd.DataFrame(data["prices"]), pd.DataFrame(data["dilution"]), data["readiness"]


def execute(args: argparse.Namespace, *, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    if args.command == "prospective-us-shadow-status":
        return status(research_db=args.research_db, production_db=args.production_db)
    if args.command == "evaluate-prospective-us-shadows":
        # Evaluation is intentionally a fingerprinted status/readiness operation until
        # exact-session outcomes exist; it never creates or updates cohorts.
        result = status(research_db=args.research_db, production_db=args.production_db)
        return {**result, "command": args.command, "as_of": args.as_of.isoformat(),
                "mode": "strictly_read_only", "outcomes_written": 0}
    prices, dilution, readiness = _fixture(args.fixture)
    if args.command == "plan-prospective-us-shadow":
        return build_plan(prices, dilution, decision_at=args.decision_at, generated_at=now,
            session_ready=readiness["complete_month_end_session"], fx_ready=readiness["fx_ready"])
    plan = json.loads(args.plan_file.read_text(encoding="utf-8"))
    return create_from_plan(research_db=args.research_db, production_db=args.production_db,
        plan=plan, prices=prices, dilution=dilution, authorization=args.authorization, now=now)


def main() -> None:
    try:
        print(json.dumps(execute(parser().parse_args()), sort_keys=True, default=str))
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": {"code": type(exc).__name__,
            "message": "prospective US shadow command failed; details redacted"}}), file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__": main()
