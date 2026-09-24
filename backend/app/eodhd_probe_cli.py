"""Command-line entry point for the mutation-free EODHD capability probe."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .config import get_settings
from .eodhd_probe import EODHDCapabilityProbe, ProbeLimits


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Probe bounded EODHD free-tier capabilities without writes")
    parser.add_argument("probe", choices=["probe"])
    parser.add_argument("--include-actions", action="store_true")
    parser.add_argument("--max-requests", type=int, default=6)
    parser.add_argument("--timeout-seconds", type=float, default=10.0)
    parser.add_argument("--rate-limit-seconds", type=float, default=1.0)
    parser.add_argument("--production-db", type=Path)
    parser.add_argument("--research-db", type=Path)
    return parser


def execute(args: argparse.Namespace) -> dict:
    settings = get_settings()
    token = os.environ.get("SIGNALLENS_EODHD_API_TOKEN", "")
    probe = EODHDCapabilityProbe(token, limits=ProbeLimits(
        max_requests=args.max_requests, timeout_seconds=args.timeout_seconds,
        rate_limit_seconds=args.rate_limit_seconds,
    ))
    return probe.run(database_paths=[
        args.production_db or settings.database_path,
        args.research_db or settings.research_database_path,
    ], include_actions=args.include_actions)


def main() -> None:
    try:
        print(json.dumps(execute(build_parser().parse_args()), sort_keys=True))
    except Exception as exc:
        # Exception text is deliberately excluded: HTTP libraries can embed request URLs.
        print(json.dumps({"command": "probe", "status": "failed", "error": {
            "code": type(exc).__name__, "message": "EODHD capability probe failed; details redacted",
        }}, sort_keys=True), file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
