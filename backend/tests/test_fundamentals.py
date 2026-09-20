from datetime import date, datetime, timezone
from pathlib import Path

from app.fundamentals import (
    FundamentalRepository,
    ingest_fundamentals,
)
from app.market_data import MarketDataRepository
from app.sec_fundamentals import FundamentalFact


RETRIEVED_AT = datetime(2026, 9, 20, tzinfo=timezone.utc)


def make_fact(
    fact_id: str,
    ticker: str = "AMD",
    metric: str = "revenue",
    value: float = 7_685_000_000,
    available_at: datetime = datetime(
        2026, 8, 6, tzinfo=timezone.utc
    ),
    period_end: date = date(2026, 6, 27),
    retrieved_at: datetime = RETRIEVED_AT,
) -> FundamentalFact:
    return FundamentalFact(
        fact_id=fact_id,
        ticker=ticker,
        metric=metric,
        taxonomy="us-gaap",
        concept="RevenueFromContractWithCustomerExcludingAssessedTax",
        unit="USD",
        value=value,
        period_start=date(2026, 3, 29),
        period_end=period_end,
        fiscal_year=2026,
        fiscal_period="Q2",
        form="10-Q",
        accession="0000002488-26-000123",
        filed_at=datetime(2026, 8, 5, tzinfo=timezone.utc),
        available_at=available_at,
        retrieved_at=retrieved_at,
        source_url="https://www.sec.gov/Archives/example",
    )


def repository(tmp_path: Path) -> FundamentalRepository:
    return FundamentalRepository(
        MarketDataRepository(tmp_path / "fundamentals.duckdb")
    )


def test_fundamental_store_is_idempotent_and_point_in_time(
    tmp_path: Path,
) -> None:
    store = repository(tmp_path)
    older = make_fact(
        "older",
        value=6_800_000_000,
        available_at=datetime(2026, 5, 7, tzinfo=timezone.utc),
        period_end=date(2026, 3, 28),
        retrieved_at=datetime(2026, 5, 8, tzinfo=timezone.utc),
    )
    latest = make_fact("latest")

    assert store.save(older) is True
    assert store.save(latest) is True
    assert store.save(latest) is False

    before_latest = store.point_in_time(
        "AMD",
        datetime(2026, 6, 1, tzinfo=timezone.utc),
    )
    after_latest = store.point_in_time(
        "AMD",
        datetime(2026, 9, 21, tzinfo=timezone.utc),
    )

    assert before_latest[0]["fact_id"] == "older"
    assert after_latest[0]["fact_id"] == "latest"
    assert after_latest[0]["value"] == 7_685_000_000


class FakeProvider:
    name = "fake-companyfacts"
    errors: dict[str, str] = {}

    def download(self, tickers, retrieved_at, **_options):
        return {
            ticker: [
                make_fact(
                    f"fact:{ticker}",
                    ticker=ticker,
                )
            ]
            for ticker in tickers
        }


class PartialProvider(FakeProvider):
    name = "partial-companyfacts"

    def download(self, tickers, retrieved_at, **options):
        downloaded = super().download(tickers, retrieved_at, **options)
        self.errors = {"AAPL": "company facts unavailable"}
        downloaded["AAPL"] = []
        return downloaded


def test_fundamental_ingestion_is_audited_and_idempotent(
    tmp_path: Path,
) -> None:
    store = repository(tmp_path)

    first = ingest_fundamentals(
        store,
        FakeProvider(),
        ["amd", "AMD"],
        retrieved_at=RETRIEVED_AT,
    )
    second = ingest_fundamentals(
        store,
        FakeProvider(),
        ["AMD"],
        retrieved_at=RETRIEVED_AT,
    )

    assert first["tickers"] == 1
    assert first["stored"] == 1
    assert second["stored"] == 0
    assert second["duplicates"] == 1

    with store.market_data.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM fundamental_fetches"
        ).fetchone()[0] == 2


def test_fundamental_ingestion_isolates_ticker_failures(
    tmp_path: Path,
) -> None:
    store = repository(tmp_path)

    result = ingest_fundamentals(
        store,
        PartialProvider(),
        ["AMD", "AAPL"],
        retrieved_at=RETRIEVED_AT,
    )

    assert result["stored"] == 1
    assert result["failed"] == 1
    assert len(
        store.point_in_time(
            "AMD",
            datetime(2026, 9, 21, tzinfo=timezone.utc),
        )
    ) == 1

    with store.market_data.connect() as connection:
        status, error = connection.execute(
            """
            SELECT status, error
            FROM fundamental_fetches
            WHERE ticker = 'AAPL'
            """
        ).fetchone()
    assert status == "failed"
    assert error == "company facts unavailable"
