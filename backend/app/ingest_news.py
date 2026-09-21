from __future__ import annotations

import argparse
from datetime import datetime, timezone

from .config import get_settings
from .evidence import EvidenceRepository
from .evidence_ingest import ingest_evidence
from .google_news import GoogleNewsRSSProvider
from .market_data import MarketDataRepository
from .universe import TICKERS, UNIVERSE


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ingest timestamped, source-linked company news metadata."
    )
    parser.add_argument(
        "--ticker",
        action="append",
        dest="tickers",
        help="Ticker to ingest; repeat for multiple tickers. Defaults to the universe.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=5,
        help="Maximum recent articles per ticker (default: 5).",
    )
    parser.add_argument(
        "--lookback-days",
        type=int,
        default=30,
        help="News lookback in days (default: 30).",
    )
    args = parser.parse_args()

    companies = {security.ticker: security.company for security in UNIVERSE}
    repository = EvidenceRepository(
        MarketDataRepository(get_settings().database_path)
    )
    with GoogleNewsRSSProvider(companies) as provider:
        result = ingest_evidence(
            repository,
            provider,
            args.tickers or TICKERS,
            retrieved_at=datetime.now(timezone.utc),
            limit_per_ticker=args.limit,
            lookback_days=args.lookback_days,
        )

    print(
        "News evidence ingestion completed: "
        f"{result['tickers']} tickers, "
        f"{result['fetched']} fetched, "
        f"{result['stored']} stored, "
        f"{result['duplicates']} already present, "
        f"{result['failed']} failed."
    )


if __name__ == "__main__":
    main()
