from datetime import date
from pathlib import Path
from types import SimpleNamespace
import sys

import pandas as pd

import pytest

from app.market_data import MarketDataRepository, PriceBar, YFinanceProvider, validate_bars
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



def _provider_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "Open": [100.0],
            "High": [105.0],
            "Low": [99.0],
            "Close": [103.0],
            "Adj Close": [102.5],
            "Volume": [1000],
        },
        index=pd.to_datetime(["2026-09-18"]),
    )


def test_yfinance_provider_downloads_sequentially_with_timeouts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, bool, int]] = []

    def fake_download(ticker, **kwargs):
        calls.append((ticker, kwargs["threads"], kwargs["timeout"]))
        return _provider_frame()

    monkeypatch.setitem(sys.modules, "yfinance", SimpleNamespace(download=fake_download))
    provider = YFinanceProvider(request_timeout=7, max_attempts=2, retry_delay=0)

    bars = provider.download(
        ("AAA", "BBB"), date(2026, 9, 1), date(2026, 9, 19)
    )

    assert [bar.ticker for bar in bars] == ["AAA", "BBB"]
    assert calls == [("AAA", False, 7), ("BBB", False, 7)]


def test_yfinance_provider_retries_a_failed_ticker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = 0

    def flaky_download(ticker, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise TimeoutError("request timed out")
        return _provider_frame()

    monkeypatch.setitem(sys.modules, "yfinance", SimpleNamespace(download=flaky_download))
    provider = YFinanceProvider(request_timeout=5, max_attempts=2, retry_delay=0)

    bars = provider.download(
        ("AAA",), date(2026, 9, 1), date(2026, 9, 19)
    )

    assert attempts == 2
    assert len(bars) == 1


def test_yfinance_provider_reports_ticker_after_retry_exhaustion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def empty_download(ticker, **kwargs):
        return pd.DataFrame()

    monkeypatch.setitem(sys.modules, "yfinance", SimpleNamespace(download=empty_download))
    provider = YFinanceProvider(request_timeout=5, max_attempts=2, retry_delay=0)

    with pytest.raises(
        RuntimeError, match="Failed to download AAA after 2 attempts"
    ):
        provider.download(
            ("AAA",), date(2026, 9, 1), date(2026, 9, 19)
        )
