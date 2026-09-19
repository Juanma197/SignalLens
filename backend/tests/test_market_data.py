from datetime import date
from pathlib import Path

import pytest

from app.market_data import MarketDataRepository, PriceBar, validate_bars
from app.universe import Security


class FakeProvider:
    name = "fake"

    def download(self, tickers, start, end):
        return [PriceBar(ticker, date(2026, 9, 18), 100, 105, 99, 103, 102.5, 1000) for ticker in tickers]

class FailingProvider:
    name = "failing"

    def download(self, tickers, start, end):
        raise RuntimeError("provider unavailable")



def test_ingestion_is_reproducible_and_upserts(tmp_path: Path) -> None:
    repository = MarketDataRepository(tmp_path / "prices.duckdb")
    universe = (Security("AAA", "Alpha", "Test"), Security("BBB", "Beta", "Test"))
    repository.ingest(FakeProvider(), universe, date(2026, 9, 1), date(2026, 9, 19))
    repository.ingest(FakeProvider(), universe, date(2026, 9, 1), date(2026, 9, 19))
    status = repository.status()
    assert status["row_count"] == 2
    assert status["duplicate_rows"] == 0
    assert status["covered_tickers"] == 2
    assert status["last_run"]["status"] == "completed"

def test_repository_creates_missing_database_directory(tmp_path: Path) -> None:
    database_path = tmp_path / "nested" / "data" / "prices.duckdb"
    repository = MarketDataRepository(database_path)

    status = repository.status()

    assert database_path.exists()
    assert status["row_count"] == 0


def test_provider_failure_is_recorded_and_original_error_is_raised(
    tmp_path: Path,
) -> None:
    repository = MarketDataRepository(tmp_path / "prices.duckdb")
    universe = (Security("AAA", "Alpha", "Test"),)

    with pytest.raises(RuntimeError, match="provider unavailable"):
        repository.ingest(
            FailingProvider(),
            universe,
            date(2026, 9, 1),
            date(2026, 9, 19),
        )

    last_run = repository.status()["last_run"]
    assert last_run["status"] == "failed"
    assert last_run["error"] == "provider unavailable"



def test_validation_rejects_duplicate_keys() -> None:
    bar = PriceBar("AAA", date(2026, 9, 18), 100, 105, 99, 103, 102.5, 1000)
    with pytest.raises(ValueError, match="Duplicate"):
        validate_bars([bar, bar], {"AAA"})


def test_validation_rejects_missing_ticker() -> None:
    bar = PriceBar("AAA", date(2026, 9, 18), 100, 105, 99, 103, 102.5, 1000)
    with pytest.raises(ValueError, match="No data returned"):
        validate_bars([bar], {"AAA", "BBB"})
