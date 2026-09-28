"""Sanitized five-region DuckDB fixture for read-only readiness tests."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import duckdb
import pandas as pd

DECISION = datetime(2026, 9, 28, 20, tzinfo=timezone.utc)
LISTINGS = (
    ("security-us", "ALPHA.US", "US", "USD"),
    ("security-lse", "BRAVO.LSE", "LSE", "GBX"),
    ("security-to", "CHARLIE.TO", "TO", "CAD"),
    ("security-xetra", "DELTA.XETRA", "XETRA", "EUR"),
    ("security-pa", "ECHO.PA", "PA", "EUR"),
)


def create_research_fixture(path: Path) -> None:
    """Create synthetic schema-compatible data; no provider payloads are included."""
    days = pd.bdate_range(end="2026-09-25", periods=130)
    with duckdb.connect(str(path)) as db:
        db.execute("""CREATE TABLE security_master_retrievals
            (retrieval_id VARCHAR, retrieved_at TIMESTAMP, status VARCHAR)""")
        db.execute("""CREATE TABLE security_listings
            (retrieval_id VARCHAR, security_id VARCHAR, qualified_symbol VARCHAR,
             primary_exchange VARCHAR, currency VARCHAR, instrument_type VARCHAR, active BOOLEAN)""")
        db.execute("""CREATE TABLE global_price_observations
            (qualified_symbol VARCHAR, trading_date DATE, currency VARCHAR, open DOUBLE,
             high DOUBLE, low DOUBLE, close DOUBLE, adjusted_close DOUBLE, volume BIGINT,
             status VARCHAR, source VARCHAR, retrieved_at TIMESTAMP)""")
        db.execute("""CREATE TABLE global_fx_observations
            (base_currency VARCHAR, quote_currency VARCHAR, observed_on DATE, rate DOUBLE,
             available_at TIMESTAMP)""")
        db.execute("""CREATE TABLE global_corporate_actions
            (qualified_symbol VARCHAR, ex_date DATE, action_type VARCHAR, value DOUBLE)""")
        db.execute("""CREATE TABLE eodhd_ingestion_checkpoints
            (stage VARCHAR, qualified_symbol VARCHAR, status VARCHAR, error_code VARCHAR,
             updated_at TIMESTAMP)""")
        db.execute("""CREATE TABLE eodhd_catalogue_validations
            (validated_at TIMESTAMP, status VARCHAR, accepted INTEGER, zero_regions_json VARCHAR)""")
        db.execute("INSERT INTO security_master_retrievals VALUES ('fixture-r1', ?, 'completed')",
                   [datetime(2026, 9, 26, 8)])
        db.execute("INSERT INTO eodhd_catalogue_validations VALUES (?, 'validated', 5, '[]')",
                   [datetime(2026, 9, 26, 8)])
        db.executemany("INSERT INTO security_listings VALUES ('fixture-r1',?,?,?,?,'common_stock',true)",
                       LISTINGS)
        prices = []
        for offset, (_, symbol, _, currency) in enumerate(LISTINGS):
            for index, day in enumerate(days):
                close = 50 + offset + index / 10
                prices.append((symbol, day.date(), currency, close - .1, close + .2,
                               close - .2, close, close * 1.01, 100_000 + index,
                               "available", "sanitized-fixture", datetime(2026, 9, 26, 8)))
        db.executemany("INSERT INTO global_price_observations VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", prices)
        rates = {"USD": .75, "CAD": .55, "EUR": .86}
        db.executemany("INSERT INTO global_fx_observations VALUES (?,?,?,?,?)", [
            (currency, "GBP", day.date(), rate, (day + pd.Timedelta(days=1)).to_pydatetime())
            for currency, rate in rates.items() for day in days
        ])
        db.execute("INSERT INTO global_corporate_actions VALUES ('ALPHA.US', ?, 'cash_distribution', .25)",
                   [days[-30].date()])


def mutate(path: Path, sql: str, parameters: list | None = None) -> None:
    with duckdb.connect(str(path)) as db:
        db.execute(sql, parameters or [])
