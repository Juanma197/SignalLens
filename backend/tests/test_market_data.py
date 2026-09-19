from datetime import date
from pathlib import Path

import pytest

from app.market_data import MarketDataRepository, PriceBar, validate_bars
from app.universe import Security


class FakeProvider:
    name = "fake"

    def download(self, tickers, start, end):
        return [PriceBar(ticker, date(2026, 9, 18), 100, 105, 99, 103, 102.5, 1000) for ticker in tickers]


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


def test_validation_rejects_duplicate_keys() -> None:
    bar = PriceBar("AAA", date(2026, 9, 18), 100, 105, 99, 103, 102.5, 1000)
    with pytest.raises(ValueError, match="Duplicate"):
        validate_bars([bar, bar], {"AAA"})


def test_validation_rejects_missing_ticker() -> None:
    bar = PriceBar("AAA", date(2026, 9, 18), 100, 105, 99, 103, 102.5, 1000)
    with pytest.raises(ValueError, match="No data returned"):
        validate_bars([bar], {"AAA", "BBB"})
