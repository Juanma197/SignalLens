"""Operator CLI for the bounded, research-only EODHD pilot."""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from .config import get_settings
from .eodhd_ingestion import EODHDClient, EODHDIngestion, EODHDLimits


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Bounded EODHD global research ingestion")
    parser.add_argument("command", choices=["plan", "dry-run", "ingest-catalogue", "ingest-prices", "ingest-fx", "resume", "status", "coverage"])
    parser.add_argument("--research-db", type=Path)
    parser.add_argument("--production-db", type=Path)
    parser.add_argument("--per-region", type=int, default=100)
    parser.add_argument("--total", type=int, default=500)
    parser.add_argument("--daily-request-budget", type=int, default=700)
    parser.add_argument("--requests-per-minute", type=int, default=20)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--timeout-seconds", type=float, default=15)
    parser.add_argument("--max-response-bytes", type=int, default=16 * 1024 * 1024)
    parser.add_argument("--maximum-runtime-seconds", type=float, default=1800)
    return parser


def execute(args: argparse.Namespace, *, transport=None, now: datetime | None = None) -> dict:
    settings, captured = get_settings(), now or datetime.now(timezone.utc)
    limits = EODHDLimits(args.per_region, args.total, args.daily_request_budget,
        args.requests_per_minute, args.retries, args.timeout_seconds,
        args.max_response_bytes, args.maximum_runtime_seconds)
    # A dummy value is sufficient for read-only local commands and never leaves the process.
    token = os.environ.get("SIGNALLENS_EODHD_API_TOKEN", "")
    if args.command not in {"plan", "status", "coverage"} and not token:
        raise ValueError("SIGNALLENS_EODHD_API_TOKEN is required")
    client = EODHDClient(token or "offline-read-only", limits, transport=transport)
    operation = EODHDIngestion(args.research_db or settings.research_database_path,
                               args.production_db or settings.database_path, client)
    if args.command == "plan": return operation.plan()
    if args.command == "dry-run": return operation.catalogue(retrieved_at=captured, dry_run=True)
    if args.command == "ingest-catalogue": return operation.catalogue(retrieved_at=captured)
    if args.command == "ingest-prices": return operation.prices(retrieved_at=captured)
    if args.command == "resume": return operation.prices(retrieved_at=captured, resume=True)
    if args.command == "ingest-fx": return operation.fx(retrieved_at=captured)
    if args.command == "status": return operation.status()
    return operation.coverage()


def main() -> None:
    try:
        print(json.dumps(execute(build_parser().parse_args()), default=str, sort_keys=True))
    except Exception as exc:
        # Provider URLs contain credentials, so exception text is intentionally suppressed.
        print(json.dumps({"status": "failed", "error": {"code": type(exc).__name__,
            "message": "bounded EODHD operation failed; details redacted"}}, sort_keys=True), file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__": main()
