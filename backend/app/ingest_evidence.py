from __future__ import annotations

import argparse
from datetime import datetime, timezone

from .config import get_settings
from .evidence import EvidenceRepository
from .evidence_ingest import ingest_evidence
from .market_data import MarketDataRepository
from .sec_filings import SECFilingsProvider
from .universe import TICKERS


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ingest point-in-time SEC filing evidence."
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
        default=3,
        help="Maximum recent filings per ticker (default: 3).",
    )
    args = parser.parse_args()

    settings = get_settings()
    if not settings.sec_user_agent:
        parser.error(
            "Set SIGNALLENS_SEC_USER_AGENT to an application name and "
            "monitored contact email before accessing SEC EDGAR."
        )

    repository = EvidenceRepository(
        MarketDataRepository(settings.database_path)
    )
    retrieved_at = datetime.now(timezone.utc)
    with SECFilingsProvider(settings.sec_user_agent) as provider:
        result = ingest_evidence(
            repository,
            provider,
            args.tickers or TICKERS,
            retrieved_at=retrieved_at,
            limit_per_ticker=args.limit,
        )

    print(
        "SEC evidence ingestion completed: "
        f"{result['tickers']} tickers, "
        f"{result['fetched']} fetched, "
        f"{result['stored']} stored, "
        f"{result['duplicates']} already present."
    )


if __name__ == "__main__":
    main()
