from __future__ import annotations

import argparse
from datetime import datetime, timezone

from .config import get_settings
from .fundamentals import FundamentalRepository, ingest_fundamentals
from .market_data import MarketDataRepository
from .sec_fundamentals import SECCompanyFactsProvider
from .universe import TICKERS


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ingest point-in-time SEC Company Facts fundamentals."
    )
    parser.add_argument(
        "--ticker",
        action="append",
        dest="tickers",
        help="Ticker to ingest; repeat for multiple tickers. Defaults to the universe.",
    )
    parser.add_argument(
        "--periods",
        type=int,
        default=8,
        help="Maximum periods retained per metric and ticker (default: 8).",
    )
    args = parser.parse_args()

    settings = get_settings()
    if not settings.sec_user_agent:
        parser.error(
            "Set SIGNALLENS_SEC_USER_AGENT to an application name and "
            "monitored contact email before accessing SEC EDGAR."
        )

    repository = FundamentalRepository(
        MarketDataRepository(settings.database_path)
    )
    with SECCompanyFactsProvider(settings.sec_user_agent) as provider:
        result = ingest_fundamentals(
            repository,
            provider,
            args.tickers or TICKERS,
            retrieved_at=datetime.now(timezone.utc),
            limit_periods_per_metric=args.periods,
        )

    print(
        "SEC fundamentals ingestion completed: "
        f"{result['tickers']} tickers, "
        f"{result['fetched']} fetched, "
        f"{result['stored']} stored, "
        f"{result['duplicates']} already present, "
        f"{result['failed']} failed."
    )


if __name__ == "__main__":
    main()
