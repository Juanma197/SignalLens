from __future__ import annotations

import argparse
from datetime import date, datetime, timezone

from .config import get_settings
from .fred_macro import FRED_SERIES, FREDMacroProvider
from .macro import MacroRepository, ingest_macro
from .market_data import MarketDataRepository


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ingest point-in-time macro observations from FRED."
    )
    parser.add_argument(
        "--series",
        action="append",
        dest="series_ids",
        choices=tuple(FRED_SERIES),
        help="FRED series to ingest; repeat as needed. Defaults to all.",
    )
    parser.add_argument(
        "--start",
        type=date.fromisoformat,
        default=date(2015, 1, 1),
        help="Earliest observation date in YYYY-MM-DD format.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Optional maximum observations retained per series per fetch.",
    )
    args = parser.parse_args()

    settings = get_settings()
    if not settings.fred_api_key:
        parser.error(
            "Set SIGNALLENS_FRED_API_KEY before accessing the FRED API."
        )

    repository = MacroRepository(
        MarketDataRepository(settings.database_path)
    )
    with FREDMacroProvider(api_key=settings.fred_api_key) as provider:
        result = ingest_macro(
            repository,
            provider,
            args.series_ids or tuple(FRED_SERIES),
            start=args.start,
            limit_per_series=args.limit,
            retrieved_at=datetime.now(timezone.utc),
        )

    print(
        "FRED macro ingestion completed: "
        f"{result['series']} series, "
        f"{result['fetched']} fetched, "
        f"{result['stored']} stored, "
        f"{result['duplicates']} already present, "
        f"{result['failed']} failed."
    )


if __name__ == "__main__":
    main()
