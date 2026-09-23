from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from uuid import NAMESPACE_URL, uuid5

from .config import get_settings
from .evidence import EvidenceRepository
from .evidence_ingest import ingest_evidence
from .fred_macro import FRED_SERIES, FREDMacroProvider
from .fundamentals import FundamentalRepository, ingest_fundamentals
from .google_news import GoogleNewsRSSProvider
from .macro import MacroRepository, ingest_macro
from .market_data import MarketDataRepository, YFinanceProvider
from .prediction_store import PredictionVintageStore
from .rankings import STRATEGY_NAME, publish_latest_momentum_ranking
from .sec_filings import SECFilingsProvider
from .sec_fundamentals import SECCompanyFactsProvider
from .universe import TICKERS, UNIVERSE


Stage = Callable[[datetime], dict[str, Any]]


def monthly_vintage_id(cycle_key: str) -> str:
    """Return the stable identity reserved for one month's live publication."""
    return str(uuid5(NAMESPACE_URL, f"signallens:{STRATEGY_NAME}:{cycle_key}"))


@dataclass(frozen=True)
class CycleStages:
    prices: Stage
    filings: Stage
    fundamentals: Stage
    macro: Stage
    news: Stage
    publish: Callable[[datetime], str]


class MonthlyCycleStore:
    """Durable, idempotent run ledger stored beside the research data."""

    def __init__(self, repository: MarketDataRepository) -> None:
        self.repository = repository
        with repository.connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS monthly_research_cycles (
                    cycle_key VARCHAR PRIMARY KEY,
                    started_at TIMESTAMP NOT NULL,
                    completed_at TIMESTAMP,
                    status VARCHAR NOT NULL,
                    vintage_id VARCHAR,
                    details_json VARCHAR NOT NULL,
                    error VARCHAR
                )
                """
            )

    def begin(self, cycle_key: str, started_at: datetime) -> str | None:
        with self.repository.connect() as connection:
            row = connection.execute(
                "SELECT status, vintage_id FROM monthly_research_cycles WHERE cycle_key = ?",
                [cycle_key],
            ).fetchone()
            if row and row[0] == "completed":
                return row[1]
            if row and row[0] == "running":
                raise RuntimeError(f"Monthly cycle {cycle_key} is already running")
            timestamp = started_at.astimezone(timezone.utc).replace(tzinfo=None)
            if row:
                connection.execute(
                    """
                    UPDATE monthly_research_cycles
                    SET started_at=?, completed_at=NULL, status='running',
                        vintage_id=NULL, details_json='{}', error=NULL
                    WHERE cycle_key=?
                    """,
                    [timestamp, cycle_key],
                )
            else:
                connection.execute(
                    "INSERT INTO monthly_research_cycles VALUES (?, ?, NULL, 'running', NULL, '{}', NULL)",
                    [cycle_key, timestamp],
                )
        return None

    def finish(
        self,
        cycle_key: str,
        completed_at: datetime,
        vintage_id: str,
        details: dict[str, Any],
    ) -> None:
        with self.repository.connect() as connection:
            connection.execute(
                """
                UPDATE monthly_research_cycles
                SET completed_at=?, status='completed', vintage_id=?,
                    details_json=?, error=NULL
                WHERE cycle_key=? AND status='running'
                """,
                [
                    completed_at.astimezone(timezone.utc).replace(tzinfo=None),
                    vintage_id,
                    json.dumps(details, sort_keys=True, default=str),
                    cycle_key,
                ],
            )

    def fail(self, cycle_key: str, failed_at: datetime, error: str) -> None:
        with self.repository.connect() as connection:
            connection.execute(
                """
                UPDATE monthly_research_cycles
                SET completed_at=?, status='failed', error=?
                WHERE cycle_key=? AND status='running'
                """,
                [
                    failed_at.astimezone(timezone.utc).replace(tzinfo=None),
                    error,
                    cycle_key,
                ],
            )


def run_monthly_cycle(
    repository: MarketDataRepository,
    stages: CycleStages,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Refresh evidence and append at most one momentum vintage per UTC month."""
    captured_at = now or datetime.now(timezone.utc)
    if captured_at.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    captured_at = captured_at.astimezone(timezone.utc)
    cycle_key = captured_at.strftime("%Y-%m")
    store = MonthlyCycleStore(repository)
    existing_vintage = store.begin(cycle_key, captured_at)
    if existing_vintage:
        return {
            "cycle_key": cycle_key,
            "status": "already_completed",
            "vintage_id": existing_vintage,
            "stages": {},
        }

    details: dict[str, Any] = {}
    try:
        for name in ("prices", "filings", "fundamentals", "macro", "news"):
            details[name] = getattr(stages, name)(captured_at)
        vintage_id = stages.publish(captured_at)
        # Refuse to call a non-momentum publisher from the production cycle.
        vintage = PredictionVintageStore(repository).get(vintage_id)
        if vintage is None or vintage["strategy_name"] != STRATEGY_NAME:
            raise RuntimeError("Monthly cycle must publish the live momentum strategy")
        store.finish(cycle_key, datetime.now(timezone.utc), vintage_id, details)
        return {
            "cycle_key": cycle_key,
            "status": "completed",
            "vintage_id": vintage_id,
            "stages": details,
        }
    except BaseException as exc:
        store.fail(cycle_key, datetime.now(timezone.utc), str(exc) or type(exc).__name__)
        raise


def production_stages(repository: MarketDataRepository) -> CycleStages:
    settings = get_settings()
    if not settings.sec_user_agent:
        raise ValueError("SIGNALLENS_SEC_USER_AGENT is required")
    if not settings.fred_api_key:
        raise ValueError("SIGNALLENS_FRED_API_KEY is required")

    def prices(captured_at: datetime) -> dict[str, Any]:
        with repository.connect() as connection:
            latest = connection.execute("SELECT MAX(trading_date) FROM price_bars").fetchone()[0]
        if latest is None:
            raise RuntimeError("Production database has no price history; refusing to bootstrap it")
        start = latest + timedelta(days=1)
        end = captured_at.date() + timedelta(days=1)  # yfinance end is exclusive
        if start >= end:
            return {"status": "current", "latest_date": latest.isoformat()}
        run_id = repository.ingest(YFinanceProvider(), UNIVERSE, start, end)
        return {"status": "completed", "run_id": run_id, "start": start, "end": end}

    def filings(captured_at: datetime) -> dict[str, Any]:
        evidence = EvidenceRepository(repository)
        with SECFilingsProvider(settings.sec_user_agent) as provider:
            return ingest_evidence(
                evidence, provider, TICKERS, captured_at,
                limit_per_ticker=3,
            )

    def fundamentals(captured_at: datetime) -> dict[str, Any]:
        with SECCompanyFactsProvider(settings.sec_user_agent) as provider:
            return ingest_fundamentals(
                FundamentalRepository(repository), provider, TICKERS, captured_at,
                limit_periods_per_metric=8,
            )

    def macro(captured_at: datetime) -> dict[str, Any]:
        with FREDMacroProvider(api_key=settings.fred_api_key) as provider:
            return ingest_macro(
                MacroRepository(repository), provider, tuple(FRED_SERIES),
                start=date(2015, 1, 1), retrieved_at=captured_at,
            )

    def news(captured_at: datetime) -> dict[str, Any]:
        companies = {security.ticker: security.company for security in UNIVERSE}
        with GoogleNewsRSSProvider(companies) as provider:
            return ingest_evidence(
                EvidenceRepository(repository), provider, TICKERS, captured_at,
                limit_per_ticker=3, lookback_days=30,
            )

    def publish(captured_at: datetime) -> str:
        vintage_id = monthly_vintage_id(captured_at.strftime("%Y-%m"))
        # A process may have died after the append but before completing the
        # cycle ledger. Reuse that immutable vintage rather than appending a
        # second publication on retry.
        if PredictionVintageStore(repository).get(vintage_id) is not None:
            return vintage_id
        return publish_latest_momentum_ranking(
            repository, published_at=captured_at, vintage_id=vintage_id
        )

    return CycleStages(
        prices=prices,
        filings=filings,
        fundamentals=fundamentals,
        macro=macro,
        news=news,
        publish=publish,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the idempotent monthly research cycle")
    parser.add_argument("--database", type=Path, help="Override the configured database path")
    args = parser.parse_args()
    path = args.database or get_settings().database_path
    if str(path) != ":memory:" and not path.is_file():
        parser.error(f"Database does not exist: {path}. Refusing to create a replacement.")
    repository = MarketDataRepository(path)
    print(json.dumps(run_monthly_cycle(repository, production_stages(repository)), default=str))


if __name__ == "__main__":
    main()
