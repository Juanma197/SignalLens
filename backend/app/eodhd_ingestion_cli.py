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
from .extreme_label_diagnostics import diagnose_extreme_labels, plan_label_repair
from .model_readiness import assess_model_readiness
from .research_scoring import assess_research_scoring
from .horizon_evaluation import assess_horizon_evaluation
from .shadow_portfolios import (create_shadow_vintage, evaluate_matured_shadows,
    plan_shadow_vintage, shadow_status)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Bounded EODHD global research ingestion")
    parser.add_argument("command", choices=["plan", "diagnose-catalogue", "diagnose-extreme-labels", "plan-label-repair", "dry-run", "ingest-catalogue", "ingest-prices", "ingest-fx", "resume", "audit", "plan-refresh", "refresh", "retry-failures", "reconcile", "status", "coverage", "model-readiness", "research-scoring", "research-horizon-evaluation", "plan-shadow-vintage", "create-shadow-vintage", "shadow-status", "evaluate-matured-shadows"])
    parser.add_argument("--catalogue-fixture", type=Path,
                        help="local sanitized JSON object keyed by region (diagnose-catalogue only)")
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
    parser.add_argument("--affected-limit", type=int, default=25)
    parser.add_argument("--decision-at", type=datetime.fromisoformat,
                        help="timezone-aware point-in-time assessment boundary (defaults to now)")
    parser.add_argument("--dry-run", action="store_true", help="plan and validate without mutating the research database")
    parser.add_argument("--authorize-full-reconciliation", action="store_true",
                        help="deliberate authorization required by reconcile")
    parser.add_argument("--authorize-permanent-failures", action="store_true",
                        help="deliberate authorization to retry permanent failures (retry-failures only)")
    parser.add_argument("--authorize-research-shadow", action="store_true",
                        help="explicit authorization for research-only shadow persistence")
    parser.add_argument("--verbose-planned-requests", action="store_true",
                        help="include every planned request instead of the bounded sample")
    parser.add_argument("--verbose-scores", action="store_true",
                        help="include at most 100 diagnostic scores in shadow plan output")
    parser.add_argument("--expected-session", action="append", default=[], metavar="REGION=YYYY-MM-DD",
                        help="explicit final session date; repeat for every required region")
    parser.add_argument("--latest-required-fx-date",
                        help="explicit YYYY-MM-DD month-end FX requirement")
    return parser


def execute(args: argparse.Namespace, *, transport=None, now: datetime | None = None) -> dict:
    settings, captured = get_settings(), now or datetime.now(timezone.utc)
    if args.command in {"plan-shadow-vintage", "create-shadow-vintage", "shadow-status", "evaluate-matured-shadows"}:
        if args.research_db is None or args.production_db is None:
            raise ValueError(f"{args.command} requires explicit --research-db and --production-db paths")
        cutoff = args.decision_at or captured
        sessions = dict(item.split("=", 1) for item in args.expected_session)
        if args.command == "plan-shadow-vintage":
            return plan_shadow_vintage(research_db=args.research_db, production_db=args.production_db, cutoff=cutoff,
                expected_session_dates=sessions, latest_required_fx_date=args.latest_required_fx_date,
                verbose_scores=args.verbose_scores)
        if args.command == "create-shadow-vintage":
            return create_shadow_vintage(research_db=args.research_db, production_db=args.production_db,
                cutoff=cutoff, authorized=args.authorize_research_shadow, now=captured,
                expected_session_dates=sessions, latest_required_fx_date=args.latest_required_fx_date,
                require_month_end_readiness=True)
        if args.command == "shadow-status":
            return shadow_status(research_db=args.research_db, production_db=args.production_db)
        return evaluate_matured_shadows(research_db=args.research_db, production_db=args.production_db, as_of=cutoff)
    if args.command == "model-readiness":
        if args.research_db is None or args.production_db is None:
            raise ValueError("model-readiness requires explicit --research-db and --production-db paths")
        return assess_model_readiness(
            research_db=args.research_db, production_db=args.production_db,
            decision_at=args.decision_at or captured,
            sample_limit=min(args.affected_limit, 25),
        )
    if args.command == "research-scoring":
        if args.research_db is None or args.production_db is None:
            raise ValueError("research-scoring requires explicit --research-db and --production-db paths")
        return assess_research_scoring(
            research_db=args.research_db, production_db=args.production_db,
            decision_at=args.decision_at or captured,
        )
    if args.command == "research-horizon-evaluation":
        if args.research_db is None or args.production_db is None:
            raise ValueError("research-horizon-evaluation requires explicit --research-db and --production-db paths")
        return assess_horizon_evaluation(research_db=args.research_db,
            production_db=args.production_db, decision_at=args.decision_at or captured)
    if args.command == "diagnose-extreme-labels":
        if args.research_db is None or args.production_db is None:
            raise ValueError("diagnose-extreme-labels requires explicit --research-db and --production-db paths")
        return diagnose_extreme_labels(
            research_db=args.research_db, production_db=args.production_db,
            decision_at=args.decision_at or captured,
            affected_limit=min(args.affected_limit, 25),
        )
    if args.command == "plan-label-repair":
        if args.research_db is None or args.production_db is None:
            raise ValueError("plan-label-repair requires explicit --research-db and --production-db paths")
        return plan_label_repair(research_db=args.research_db, production_db=args.production_db,
            decision_at=args.decision_at or captured, affected_limit=min(args.affected_limit, 25))
    if args.authorize_permanent_failures and args.command != "retry-failures":
        raise ValueError("--authorize-permanent-failures requires retry-failures")
    limits = EODHDLimits(args.per_region, args.total, args.daily_request_budget,
        args.requests_per_minute, args.retries, args.timeout_seconds,
        args.max_response_bytes, args.maximum_runtime_seconds)
    # A dummy value is sufficient for read-only local commands and never leaves the process.
    token = os.environ.get("SIGNALLENS_EODHD_API_TOKEN", "")
    if args.command not in {"plan", "diagnose-catalogue", "audit", "plan-refresh", "status", "coverage"} and not token and not args.dry_run:
        raise ValueError("SIGNALLENS_EODHD_API_TOKEN is required")
    client = EODHDClient(token or "offline-read-only", limits, transport=transport)
    operation = EODHDIngestion(args.research_db or settings.research_database_path,
                               args.production_db or settings.database_path, client)
    if args.command == "plan": return operation.plan()
    if args.command == "diagnose-catalogue":
        if args.catalogue_fixture is None: raise ValueError("--catalogue-fixture is required")
        return operation.diagnose(json.loads(args.catalogue_fixture.read_text(encoding="utf-8")))
    if args.command == "dry-run": return operation.catalogue(retrieved_at=captured, dry_run=True)
    if args.command == "ingest-catalogue": return operation.catalogue(retrieved_at=captured)
    if args.command == "ingest-prices": return operation.prices(retrieved_at=captured)
    if args.command == "resume": return operation.prices(retrieved_at=captured, resume=True)
    if args.command == "ingest-fx": return operation.fx(retrieved_at=captured)
    if args.command == "status": return operation.status()
    if args.command == "audit": return operation.audit(as_of=captured, affected_limit=args.affected_limit)
    if args.command == "plan-refresh": return operation.plan_refresh(
        as_of=captured, include_request_details=args.verbose_planned_requests)
    if args.command == "refresh": return operation.refresh(retrieved_at=captured, dry_run=args.dry_run)
    if args.command == "retry-failures": return operation.refresh(retrieved_at=captured, retry_failures=True,
        authorize_permanent_failures=args.authorize_permanent_failures, dry_run=args.dry_run)
    if args.command == "reconcile": return operation.refresh(retrieved_at=captured, reconcile=True,
        authorized=args.authorize_full_reconciliation, dry_run=args.dry_run)
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
