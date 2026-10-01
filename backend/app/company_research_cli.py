"""Read-only command line entry points for explainable company briefs."""
from __future__ import annotations
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from .company_research import company_research_brief, prospective_selection_briefs


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Bounded, deterministic company research briefs")
    commands = root.add_subparsers(dest="command", required=True)
    one = commands.add_parser("company-research-brief")
    many = commands.add_parser("prospective-selection-briefs")
    for command in (one, many):
        command.add_argument("--research-db", type=Path, required=True)
        command.add_argument("--production-db", type=Path, required=True)
        command.add_argument("--decision-at", type=datetime.fromisoformat, required=True)
    one.add_argument("--qualified-symbol", required=True)
    one.add_argument("--max-events", type=int, default=6)
    return root


def execute(args: argparse.Namespace) -> dict:
    common = {"research_db": args.research_db, "production_db": args.production_db,
              "decision_at": args.decision_at}
    if args.command == "company-research-brief":
        return company_research_brief(**common, qualified_symbol=args.qualified_symbol,
                                      max_events=args.max_events)
    return prospective_selection_briefs(**common)


def main() -> None:
    try:
        print(json.dumps(execute(parser().parse_args()), sort_keys=True, default=str))
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": {"code": type(exc).__name__,
            "message": "company research brief failed; details redacted"}}), file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__": main()
