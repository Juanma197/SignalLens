from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable, Protocol
from uuid import uuid4

from .market_data import MarketDataRepository
from .sec_fundamentals import FundamentalFact


class FundamentalProvider(Protocol):
    name: str
    errors: dict[str, str]

    def download(
        self,
        tickers: Iterable[str],
        retrieved_at: datetime,
        **kwargs,
    ) -> dict[str, list[FundamentalFact]]: ...


class FundamentalRepository:
    def __init__(self, market_data: MarketDataRepository):
        self.market_data = market_data
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        with self.market_data.connect() as connection:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS fundamental_facts (
                    fact_id VARCHAR PRIMARY KEY,
                    ticker VARCHAR NOT NULL,
                    metric VARCHAR NOT NULL,
                    taxonomy VARCHAR NOT NULL,
                    concept VARCHAR NOT NULL,
                    unit VARCHAR NOT NULL,
                    value DOUBLE NOT NULL,
                    period_start DATE,
                    period_end DATE NOT NULL,
                    fiscal_year INTEGER,
                    fiscal_period VARCHAR,
                    form VARCHAR NOT NULL,
                    accession VARCHAR NOT NULL,
                    filed_at TIMESTAMP NOT NULL,
                    available_at TIMESTAMP NOT NULL,
                    retrieved_at TIMESTAMP NOT NULL,
                    source_url VARCHAR NOT NULL,
                    inserted_at TIMESTAMP NOT NULL
                );
                CREATE TABLE IF NOT EXISTS fundamental_fetches (
                    fetch_id VARCHAR PRIMARY KEY,
                    ticker VARCHAR NOT NULL,
                    source_name VARCHAR NOT NULL,
                    requested_at TIMESTAMP NOT NULL,
                    completed_at TIMESTAMP NOT NULL,
                    status VARCHAR NOT NULL,
                    fact_count INTEGER NOT NULL,
                    error VARCHAR
                );
            """)

    def contains(self, fact_id: str) -> bool:
        with self.market_data.connect() as connection:
            return connection.execute(
                "SELECT 1 FROM fundamental_facts WHERE fact_id = ?",
                [fact_id],
            ).fetchone() is not None

    def save(self, fact: FundamentalFact) -> bool:
        if self.contains(fact.fact_id):
            return False
        if fact.available_at > fact.retrieved_at:
            raise ValueError("Fact cannot be retrieved before it is available")
        if fact.filed_at > fact.available_at:
            raise ValueError("Fact cannot be available before it is filed")

        inserted_at = datetime.now(timezone.utc).replace(tzinfo=None)
        with self.market_data.connect() as connection:
            connection.execute(
                """
                INSERT INTO fundamental_facts VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                [
                    fact.fact_id,
                    fact.ticker,
                    fact.metric,
                    fact.taxonomy,
                    fact.concept,
                    fact.unit,
                    fact.value,
                    fact.period_start,
                    fact.period_end,
                    fact.fiscal_year,
                    fact.fiscal_period,
                    fact.form,
                    fact.accession,
                    fact.filed_at.replace(tzinfo=None),
                    fact.available_at.replace(tzinfo=None),
                    fact.retrieved_at.replace(tzinfo=None),
                    fact.source_url,
                    inserted_at,
                ],
            )
        return True

    def record_fetch(
        self,
        ticker: str,
        source_name: str,
        requested_at: datetime,
        completed_at: datetime,
        status: str,
        fact_count: int = 0,
        error: str | None = None,
    ) -> str:
        if status not in {"completed", "missing", "failed"}:
            raise ValueError(f"Unsupported fetch status: {status}")
        if fact_count < 0:
            raise ValueError("Fact count cannot be negative")
        if status == "failed" and not error:
            raise ValueError("Failed fetch must include an error")
        if status != "completed" and fact_count:
            raise ValueError("Missing or failed fetch cannot contain facts")

        fetch_id = str(uuid4())
        with self.market_data.connect() as connection:
            connection.execute(
                "INSERT INTO fundamental_fetches VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    fetch_id,
                    ticker.strip().upper(),
                    source_name,
                    requested_at.replace(tzinfo=None),
                    completed_at.replace(tzinfo=None),
                    status,
                    fact_count,
                    error,
                ],
            )
        return fetch_id

    def point_in_time(
        self,
        ticker: str,
        as_of: datetime,
    ) -> list[dict]:
        as_of_naive = as_of.astimezone(timezone.utc).replace(tzinfo=None)
        with self.market_data.connect() as connection:
            rows = connection.execute(
                """
                SELECT fact_id, ticker, metric, taxonomy, concept, unit,
                       value, period_start, period_end, fiscal_year,
                       fiscal_period, form, accession, filed_at,
                       available_at, retrieved_at, source_url
                FROM fundamental_facts
                WHERE ticker = ?
                  AND available_at <= ?
                  AND retrieved_at <= ?
                QUALIFY ROW_NUMBER() OVER (
                    PARTITION BY metric
                    ORDER BY available_at DESC, period_end DESC, accession DESC
                ) = 1
                ORDER BY metric
                """,
                [ticker.strip().upper(), as_of_naive, as_of_naive],
            ).fetchall()

        columns = (
            "fact_id", "ticker", "metric", "taxonomy", "concept", "unit",
            "value", "period_start", "period_end", "fiscal_year",
            "fiscal_period", "form", "accession", "filed_at",
            "available_at", "retrieved_at", "source_url",
        )
        return [dict(zip(columns, row)) for row in rows]


def ingest_fundamentals(
    repository: FundamentalRepository,
    provider: FundamentalProvider,
    tickers: Iterable[str],
    retrieved_at: datetime | None = None,
    **provider_options,
) -> dict:
    normalized = tuple(
        dict.fromkeys(ticker.strip().upper() for ticker in tickers)
    )
    if not normalized:
        raise ValueError("At least one ticker is required")

    requested_at = retrieved_at or datetime.now(timezone.utc)
    if requested_at.tzinfo is None:
        requested_at = requested_at.replace(tzinfo=timezone.utc)

    try:
        downloaded = provider.download(
            normalized,
            retrieved_at=requested_at,
            **provider_options,
        )
    except Exception as exc:
        completed_at = max(datetime.now(timezone.utc), requested_at)
        error = str(exc) or type(exc).__name__
        for ticker in normalized:
            repository.record_fetch(
                ticker,
                provider.name,
                requested_at,
                completed_at,
                "failed",
                error=error,
            )
        raise

    completed_at = max(datetime.now(timezone.utc), requested_at)
    errors = getattr(provider, "errors", {})
    fetched = stored = duplicates = failed = 0

    for ticker in normalized:
        if ticker in errors:
            failed += 1
            repository.record_fetch(
                ticker,
                provider.name,
                requested_at,
                completed_at,
                "failed",
                error=errors[ticker],
            )
            continue

        facts = downloaded.get(ticker, [])
        fetched += len(facts)
        for fact in facts:
            if repository.save(fact):
                stored += 1
            else:
                duplicates += 1
        repository.record_fetch(
            ticker,
            provider.name,
            requested_at,
            completed_at,
            "completed" if facts else "missing",
            fact_count=len(facts),
        )

    return {
        "source": provider.name,
        "tickers": len(normalized),
        "fetched": fetched,
        "stored": stored,
        "duplicates": duplicates,
        "failed": failed,
        "retrieved_at": requested_at,
    }
