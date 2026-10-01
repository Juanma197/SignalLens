"""Operator CLI for research-only SEC material-event metadata."""
import argparse
import json
import sys
from pathlib import Path

from .sec_events import AUTHORIZATION_PHRASE, ingest, plan, status
from .sec_ingestion import IngestionLimits


def parser() -> argparse.ArgumentParser:
    root=argparse.ArgumentParser(description="SEC event metadata ingestion")
    commands=root.add_subparsers(dest="command",required=True)
    for name in ("plan-sec-event-ingestion","sec-event-ingestion-status"):
        command=commands.add_parser(name); _paths(command)
    for name in ("ingest-sec-events","retry-sec-event-failures"):
        command=commands.add_parser(name); _paths(command)
        command.add_argument("--authorization",help=f"exactly: {AUTHORIZATION_PHRASE}")
        command.add_argument("--fixture",type=Path,help=argparse.SUPPRESS)
        command.add_argument("--include-historical",action="store_true")
        command.add_argument("--max-requests",type=int,default=100)
        command.add_argument("--runtime-seconds",type=float,default=900)
        command.add_argument("--max-attempts",type=int,default=2)
        command.add_argument("--pacing-seconds",type=float,default=.12)
        command.add_argument("--timeout-seconds",type=float,default=20)
        command.add_argument("--max-response-bytes",type=int,default=5_000_000)
    return root


def _paths(command):
    command.add_argument("--research-db",type=Path,required=True)
    command.add_argument("--production-db",type=Path,required=True)


def execute(args):
    if args.command=="plan-sec-event-ingestion": return plan(args.research_db,args.production_db)
    if args.command=="sec-event-ingestion-status": return status(args.research_db,args.production_db)
    fixture=json.loads(args.fixture.read_text(encoding="utf-8")) if args.fixture else None
    limits=IngestionLimits(args.max_requests,args.runtime_seconds,args.max_attempts,args.pacing_seconds,args.timeout_seconds,args.max_response_bytes)
    return ingest(research=args.research_db,production=args.production_db,authorization=args.authorization,
        limits=limits,fixture=fixture,retry_only=args.command=="retry-sec-event-failures",include_historical=args.include_historical)


def main():
    try: print(json.dumps(execute(parser().parse_args()),sort_keys=True,default=str))
    except Exception:
        print(json.dumps({"status":"failed","error":{"code":"sec_event_ingestion_failed","message":"SEC event ingestion command failed; details redacted"}}),file=sys.stderr)
        raise SystemExit(1) from None


if __name__=="__main__": main()
