"""CLI for research-only SEC ingestion and readiness reporting."""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path

from .sec_ingestion import (AUTHORIZATION_PHRASE, IngestionLimits, ingest, plan,
                            status)
from .sec_liquidity_plan import plan_sec_liquidity_evidence_ingestion


def parser() -> argparse.ArgumentParser:
    root=argparse.ArgumentParser(description="Point-in-time SEC research ingestion")
    commands=root.add_subparsers(dest="command",required=True)
    for name in ("plan-sec-ingestion","sec-ingestion-status"):
        command=commands.add_parser(name); _paths(command)
    liquidity=commands.add_parser("plan-sec-liquidity-evidence-ingestion"); _paths(liquidity)
    liquidity.add_argument("--decision-at",required=True)
    liquidity.add_argument("--max-request-budget",type=int,default=205)
    for name in ("ingest-sec-fundamentals","retry-sec-failures"):
        command=commands.add_parser(name); _paths(command)
        command.add_argument("--authorization",help=f"exactly: {AUTHORIZATION_PHRASE}")
        command.add_argument("--dry-run",action="store_true")
        command.add_argument("--fixture",type=Path,help=argparse.SUPPRESS)
        command.add_argument("--max-requests",type=int,default=205)
        command.add_argument("--runtime-seconds",type=float,default=900)
        command.add_argument("--max-attempts",type=int,default=2)
        command.add_argument("--pacing-seconds",type=float,default=.12)
        command.add_argument("--timeout-seconds",type=float,default=20)
        command.add_argument("--max-response-bytes",type=int,default=5_000_000)
    return root


def _paths(command: argparse.ArgumentParser) -> None:
    command.add_argument("--research-db",type=Path,required=True)
    command.add_argument("--production-db",type=Path,required=True)


def execute(args: argparse.Namespace) -> dict:
    if args.command=="plan-sec-liquidity-evidence-ingestion":
        return plan_sec_liquidity_evidence_ingestion(research_db=args.research_db,
            production_db=args.production_db,decision_at=args.decision_at,
            max_request_budget=args.max_request_budget)
    if args.command=="plan-sec-ingestion": return plan(args.research_db,args.production_db)
    if args.command=="sec-ingestion-status": return status(args.research_db,args.production_db)
    fixture=json.loads(args.fixture.read_text(encoding="utf-8")) if args.fixture else None
    limits=IngestionLimits(args.max_requests,args.runtime_seconds,args.max_attempts,
        args.pacing_seconds,args.timeout_seconds,args.max_response_bytes)
    return ingest(research=args.research_db,production=args.production_db,
        authorization=args.authorization,dry_run=args.dry_run,limits=limits,
        fixture=fixture,retry_only=args.command=="retry-sec-failures")


def main() -> None:
    try: print(json.dumps(execute(parser().parse_args()),sort_keys=True,default=str))
    except Exception as exc:
        print(json.dumps({"status":"failed","error":{"code":type(exc).__name__,
            "message":"SEC ingestion command failed; details redacted"}}),file=sys.stderr)
        raise SystemExit(1) from None


if __name__=="__main__": main()
