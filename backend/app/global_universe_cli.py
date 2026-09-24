"""Explicit JSON commands for the shadow global universe."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import httpx

from .config import get_settings
from .global_universe import (
    GlobalUniverseRepository,
    InvestabilityConfig,
    ListingObservation,
    StoredFXProvider,
    parse_nasdaq_symbol_directory,
    parse_reference_csv,
    preview,
)


class HttpReferenceProvider:
    def __init__(self, name: str, source_label: str, urls: tuple[str, ...], *, timeout: float = 30):
        self.name, self.source_label, self.urls, self.timeout = name, source_label, urls, timeout

    def discover(self) -> list[ListingObservation]:
        results: list[ListingObservation] = []
        with httpx.Client(timeout=self.timeout, follow_redirects=True, headers={"User-Agent": "SignalLens research security-master/1.0"}) as client:
            for url in self.urls:
                response = client.get(url)
                response.raise_for_status()
                directory = "nasdaqlisted" if "nasdaqlisted" in url else "otherlisted"
                results.extend(parse_nasdaq_symbol_directory(response.text, directory=directory))
        return results


class FileReferenceProvider:
    def __init__(self, path: Path, name: str):
        self.path, self.name = path, name
        self.source_label = f"operator-approved reference file: {path.name}"

    def discover(self) -> list[ListingObservation]:
        return parse_reference_csv(self.path.read_text(encoding="utf-8-sig"), source=self.name)


def provider_from_args(args: argparse.Namespace):
    if args.reference_file:
        return FileReferenceProvider(args.reference_file, args.provider)
    if args.provider != "nasdaq_trader":
        raise ValueError("Non-US providers require an operator-approved --reference-file")
    return HttpReferenceProvider(
        "nasdaq_trader", "Nasdaq Trader Symbol Directory",
        ("https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt",
         "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt"),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage the shadow global investable universe")
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("preview", "refresh"):
        item = sub.add_parser(command)
        item.add_argument("--provider", default="nasdaq_trader")
        item.add_argument("--reference-file", type=Path)
    snapshot = sub.add_parser("snapshot")
    snapshot.add_argument("--month", type=date.fromisoformat, required=True)
    snapshot.add_argument("--reporting-currency", default="GBP")
    sub.add_parser("status")
    return parser


def execute(args: argparse.Namespace) -> dict:
    repository = GlobalUniverseRepository(get_settings().database_path)
    now = datetime.now(timezone.utc)
    if args.command == "preview":
        return preview(provider_from_args(args))
    if args.command == "refresh":
        return repository.refresh(provider_from_args(args), retrieved_at=now)
    if args.command == "snapshot":
        config = InvestabilityConfig(reporting_currency=args.reporting_currency.upper())
        return repository.create_snapshot(snapshot_month=args.month, snapshot_at=now, config=config,
                                          fx=StoredFXProvider(repository.path))
    return {"command": "status", **repository.coverage(now=now)}


def main() -> None:
    try:
        result = execute(build_parser().parse_args())
        print(json.dumps(result, default=str, sort_keys=True))
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": {"code": type(exc).__name__, "message": str(exc)}}), file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
