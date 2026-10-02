"""Read-only Milestone 34 model laboratory commands."""
from __future__ import annotations
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from .model_laboratory import (assess_september_reconstruction, public_error_code,
                               top3_preview)


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Strictly read-only frozen model laboratory")
    commands = root.add_subparsers(dest="command", required=True)
    for name in ("research-us-top3-preview", "assess-september-2026-reconstruction"):
        command = commands.add_parser(name)
        command.add_argument("--research-db", required=True, type=Path)
        command.add_argument("--production-db", required=True, type=Path)
        command.add_argument("--decision-at", required=True, type=datetime.fromisoformat)
    return root


def execute(args: argparse.Namespace) -> dict:
    values = {"research_db":args.research_db, "production_db":args.production_db,
              "decision_at":args.decision_at}
    if args.command == "research-us-top3-preview": return top3_preview(**values)
    return assess_september_reconstruction(**values)


def main() -> None:
    try: print(json.dumps(execute(parser().parse_args()), sort_keys=True, default=str))
    except Exception as exc:
        print(json.dumps({"status":"failed", "error":{"code":public_error_code(exc),
            "message":"model laboratory request failed; details redacted"}}), file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__": main()
