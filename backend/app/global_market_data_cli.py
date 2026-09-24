"""Explicit, bounded commands for shadow global price and FX imports."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from .config import get_settings
from .global_market_data import (
    GlobalMarketDataRepository,
    parse_actions_csv,
    parse_fx_csv,
    parse_price_csv,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Import licensed global research market data")
    sub = parser.add_subparsers(dest="command", required=True)
    prices = sub.add_parser("import-prices")
    prices.add_argument("--price-file", type=Path, required=True)
    prices.add_argument("--actions-file", type=Path)
    prices.add_argument("--source", required=True)
    prices.add_argument("--dry-run", action="store_true")
    fx = sub.add_parser("import-fx")
    fx.add_argument("--fx-file", type=Path, required=True)
    fx.add_argument("--source", required=True)
    fx.add_argument("--dry-run", action="store_true")
    sub.add_parser("status")
    return parser


def execute(args: argparse.Namespace, *, now: datetime | None = None) -> dict:
    captured = now or datetime.now(timezone.utc)
    repository = GlobalMarketDataRepository(get_settings().database_path)
    if args.command == "status":
        return {"command": "status", **repository.coverage(now=captured)}
    if args.command == "import-prices":
        prices = parse_price_csv(args.price_file.read_text(encoding="utf-8-sig"), source=args.source, retrieved_at=captured)
        actions = [] if args.actions_file is None else parse_actions_csv(
            args.actions_file.read_text(encoding="utf-8-sig"), source=args.source, retrieved_at=captured)
        if not args.dry_run:
            repository.store(prices, actions)
        return {"command": "import-prices", "mode": "dry_run" if args.dry_run else "write",
                "status": "validated" if args.dry_run else "completed", "prices": len(prices), "actions": len(actions)}
    rows = parse_fx_csv(args.fx_file.read_text(encoding="utf-8-sig"), source=args.source, retrieved_at=captured)
    if not args.dry_run:
        repository.store(fx=rows)
    return {"command": "import-fx", "mode": "dry_run" if args.dry_run else "write",
            "status": "validated" if args.dry_run else "completed", "observations": len(rows)}


def main() -> None:
    try:
        print(json.dumps(execute(build_parser().parse_args()), default=str, sort_keys=True))
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": {"code": type(exc).__name__, "message": str(exc)}}), file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
