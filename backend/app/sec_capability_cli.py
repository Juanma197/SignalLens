"""CLI entry point for the bounded SEC fundamentals capability pilot."""
from __future__ import annotations
import argparse, json, os, sys
from pathlib import Path
from .sec_capability import Limits, run_assessment

DEFAULT_FIXTURE = Path(__file__).parents[1] / "tests/fixtures/sec_capability.json"

def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Bounded SEC EDGAR fundamentals capability assessment")
    sub = p.add_subparsers(dest="command", required=True)
    offline = sub.add_parser("sec-fundamentals-offline")
    offline.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    live = sub.add_parser("sec-fundamentals-live")
    live.add_argument("--authorize-live-sec", action="store_true")
    for command in (offline, live):
        command.add_argument("--research-db", type=Path, required=True)
        command.add_argument("--production-db", type=Path, required=True)
        command.add_argument("--max-requests", type=int, default=7)
        command.add_argument("--max-attempts", type=int, default=2)
        command.add_argument("--pacing-seconds", type=float, default=.12)
        command.add_argument("--timeout-seconds", type=float, default=10)
        command.add_argument("--max-response-bytes", type=int, default=5_000_000)
    return p

def execute(args: argparse.Namespace) -> dict:
    live = args.command == "sec-fundamentals-live"
    fixture = None if live else json.loads(args.fixture.read_text(encoding="utf-8"))
    return run_assessment(research_db=args.research_db, production_db=args.production_db,
        fixture=fixture, authorize_live_sec=getattr(args, "authorize_live_sec", False),
        user_agent=os.getenv("SIGNALLENS_SEC_USER_AGENT"), limits=Limits(args.max_requests,
        args.max_attempts, args.pacing_seconds, args.timeout_seconds, args.max_response_bytes))

def main() -> None:
    try: print(json.dumps(execute(parser().parse_args()), sort_keys=True))
    except Exception as exc:
        code = str(exc) if str(exc) in {"request_budget_exhausted", "oversized_response"} else type(exc).__name__
        print(json.dumps({"command":"sec-fundamentals-capability", "status":"failed",
            "error":{"code":code, "message":"SEC capability assessment failed; details redacted"}}), file=sys.stderr)
        raise SystemExit(1) from None
if __name__ == "__main__": main()
