from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator, Protocol
from uuid import uuid4

import duckdb

from .universe import Security


@dataclass(frozen=True)
class PriceBar:
    ticker: str
    trading_date: date
    open: float
    high: float
    low: float
    close: float
    adjusted_close: float
    volume: int


class PriceProvider(Protocol):
    name: str

    def download(self, tickers: tuple[str, ...], start: date, end: date) -> list[PriceBar]: ...


class YFinanceProvider:
    name = "yfinance"

    def download(self, tickers: tuple[str, ...], start: date, end: date) -> list[PriceBar]:
        import yfinance as yf

        frame = yf.download(
            list(tickers), start=start.isoformat(), end=end.isoformat(),
            auto_adjust=False, actions=False, group_by="ticker", progress=False,
            threads=True,
        )
        bars: list[PriceBar] = []
        for ticker in tickers:
            ticker_frame = frame[ticker] if len(tickers) > 1 else frame
            for timestamp, row in ticker_frame.dropna(subset=["Close"]).iterrows():
                adjusted = row.get("Adj Close", row["Close"])
                bars.append(PriceBar(
                    ticker=ticker,
                    trading_date=timestamp.date(),
                    open=float(row["Open"]), high=float(row["High"]),
                    low=float(row["Low"]), close=float(row["Close"]),
                    adjusted_close=float(adjusted), volume=int(row["Volume"]),
                ))
        return bars


class MarketDataRepository:
    def __init__(self, path: Path | str):
        self.path = Path(path)

    @contextmanager
    def connect(self) -> Iterator[duckdb.DuckDBPyConnection]:
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)

        connection = duckdb.connect(str(self.path))
        try:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS security_universe (
                    ticker VARCHAR PRIMARY KEY, company VARCHAR NOT NULL,
                    sector VARCHAR NOT NULL, active BOOLEAN NOT NULL
                );
                CREATE TABLE IF NOT EXISTS price_bars (
                    ticker VARCHAR NOT NULL, trading_date DATE NOT NULL,
                    open DOUBLE NOT NULL, high DOUBLE NOT NULL, low DOUBLE NOT NULL,
                    close DOUBLE NOT NULL, adjusted_close DOUBLE NOT NULL,
                    volume BIGINT NOT NULL, source VARCHAR NOT NULL,
                    ingested_at TIMESTAMP NOT NULL,
                    PRIMARY KEY (ticker, trading_date)
                );
                CREATE TABLE IF NOT EXISTS ingestion_runs (
                    run_id VARCHAR PRIMARY KEY, source VARCHAR NOT NULL,
                    started_at TIMESTAMP NOT NULL, completed_at TIMESTAMP,
                    status VARCHAR NOT NULL, requested_tickers INTEGER NOT NULL,
                    rows_written INTEGER NOT NULL, error VARCHAR
                );
            """)
            yield connection
        finally:
            connection.close()

    def seed_universe(self, securities: Iterable[Security]) -> None:
        with self.connect() as connection:
            for item in securities:
                connection.execute(
                    "INSERT OR REPLACE INTO security_universe VALUES (?, ?, ?, TRUE)",
                    [item.ticker, item.company, item.sector],
                )

    def ingest(
        self,
        provider: PriceProvider,
        securities: tuple[Security, ...],
        start: date,
        end: date,
    ) -> str:
        tickers = tuple(item.ticker for item in securities)
        run_id = str(uuid4())
        started = datetime.now(timezone.utc).replace(tzinfo=None)

        self.seed_universe(securities)
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO ingestion_runs VALUES (?, ?, ?, NULL, 'running', ?, 0, NULL)",
                [run_id, provider.name, started, len(tickers)],
            )

        try:
            bars = provider.download(tickers, start, end)
            validate_bars(bars, set(tickers))
            ingested_at = datetime.now(timezone.utc).replace(tzinfo=None)

            rows = [
                [
                    bar.ticker,
                    bar.trading_date,
                    bar.open,
                    bar.high,
                    bar.low,
                    bar.close,
                    bar.adjusted_close,
                    bar.volume,
                    provider.name,
                    ingested_at,
                ]
                for bar in bars
            ]

            with self.connect() as connection:
                connection.executemany(
                    """
                    INSERT OR REPLACE INTO price_bars
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    rows,
                )
                connection.execute(
                    """
                    UPDATE ingestion_runs
                    SET completed_at=?, status='completed', rows_written=?
                    WHERE run_id=?
                    """,
                    [ingested_at, len(bars), run_id],
                )
            return run_id

        except (Exception, KeyboardInterrupt) as exc:
            error = str(exc) or type(exc).__name__
            try:
                with self.connect() as connection:
                    connection.execute(
                        """
                        UPDATE ingestion_runs
                        SET completed_at=?, status='failed', error=?
                        WHERE run_id=?
                        """,
                        [
                            datetime.now(timezone.utc).replace(tzinfo=None),
                            error,
                            run_id,
                        ],
                    )
            except Exception:
                pass
            raise

    def status(self) -> dict:
        with self.connect() as connection:
            universe_size = connection.execute(
                "SELECT COUNT(*) FROM security_universe WHERE active"
            ).fetchone()[0]
            row_count, first_date, last_date, covered = connection.execute("""
                SELECT COUNT(*), MIN(trading_date), MAX(trading_date), COUNT(DISTINCT ticker)
                FROM price_bars
            """).fetchone()
            duplicates = connection.execute("""
                SELECT COUNT(*) FROM (
                    SELECT ticker, trading_date, COUNT(*) AS n FROM price_bars
                    GROUP BY ticker, trading_date HAVING n > 1
                )
            """).fetchone()[0]
            last_run = connection.execute("""
                SELECT run_id, source, status, rows_written, completed_at, error
                FROM ingestion_runs ORDER BY started_at DESC LIMIT 1
            """).fetchone()
        return {
            "universe_size": universe_size,
            "covered_tickers": covered,
            "row_count": row_count,
            "first_date": first_date,
            "last_date": last_date,
            "duplicate_rows": duplicates,
            "last_run": None if last_run is None else {
                "run_id": last_run[0], "source": last_run[1], "status": last_run[2],
                "rows_written": last_run[3], "completed_at": last_run[4], "error": last_run[5],
            },
        }


def validate_bars(bars: list[PriceBar], expected_tickers: set[str]) -> None:
    if not bars:
        raise ValueError("Provider returned no price bars")
    keys: set[tuple[str, date]] = set()
    observed: set[str] = set()
    for bar in bars:
        if bar.ticker not in expected_tickers:
            raise ValueError(f"Unexpected ticker: {bar.ticker}")
        key = (bar.ticker, bar.trading_date)
        if key in keys:
            raise ValueError(f"Duplicate price bar: {bar.ticker} {bar.trading_date}")
        if min(bar.open, bar.high, bar.low, bar.close, bar.adjusted_close) <= 0:
            raise ValueError(f"Non-positive price: {bar.ticker} {bar.trading_date}")
        if bar.low > bar.high or not bar.low <= bar.close <= bar.high:
            raise ValueError(f"Invalid OHLC range: {bar.ticker} {bar.trading_date}")
        if bar.volume < 0:
            raise ValueError(f"Negative volume: {bar.ticker} {bar.trading_date}")
        keys.add(key)
        observed.add(bar.ticker)
    missing = expected_tickers - observed
    if missing:
        raise ValueError(f"No data returned for: {', '.join(sorted(missing))}")
