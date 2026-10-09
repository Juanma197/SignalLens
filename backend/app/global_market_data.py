"""Research-only global prices, corporate actions, FX, and exchange calendars.

Nothing in this module imports the production universe or publication pipeline.
Network/file providers normalize observations first; only the API-owned repository
may persist them.  Local prices are immutable inputs and GBP values are derived
from historically available FX observations.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import time
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Callable, Iterable, Protocol

import duckdb
import pandas as pd

from .global_universe import utc_naive


@dataclass(frozen=True)
class PriceObservation:
    qualified_symbol: str
    trading_date: date
    exchange: str
    currency: str
    open: Decimal | None
    high: Decimal | None
    low: Decimal | None
    close: Decimal | None
    adjusted_close: Decimal | None
    volume: int | None
    source: str
    retrieved_at: datetime
    status: str = "available"


@dataclass(frozen=True)
class CorporateAction:
    qualified_symbol: str
    ex_date: date
    action_type: str
    value: Decimal
    currency: str | None
    source: str
    retrieved_at: datetime


@dataclass(frozen=True)
class FXObservation:
    base_currency: str
    quote_currency: str
    observed_on: date
    rate: Decimal
    source: str
    retrieved_at: datetime
    available_at: datetime


@dataclass(frozen=True)
class IngestionLimits:
    batch_size: int = 50
    max_items: int = 500
    max_attempts: int = 3
    rate_limit_seconds: float = 0.0
    maximum_run_seconds: float = 900.0

    def __post_init__(self) -> None:
        if min(self.batch_size, self.max_items, self.max_attempts) < 1:
            raise ValueError("batch_size, max_items, and max_attempts must be positive")
        if self.rate_limit_seconds < 0 or self.maximum_run_seconds <= 0:
            raise ValueError("time limits must be non-negative and maximum duration positive")


class BatchPriceProvider(Protocol):
    name: str
    region: str
    def download(self, symbols: list[str], start: date | None) -> tuple[list[PriceObservation], list[CorporateAction]]: ...


PRICE_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS global_price_observations (
 qualified_symbol VARCHAR NOT NULL, trading_date DATE NOT NULL, exchange VARCHAR NOT NULL,
 currency VARCHAR NOT NULL, open DECIMAL(24,8), high DECIMAL(24,8), low DECIMAL(24,8),
 close DECIMAL(24,8), adjusted_close DECIMAL(24,8), volume BIGINT, status VARCHAR NOT NULL,
 source VARCHAR NOT NULL, retrieved_at TIMESTAMP NOT NULL,
 PRIMARY KEY (qualified_symbol, trading_date, source)
);
CREATE TABLE IF NOT EXISTS global_corporate_actions (
 qualified_symbol VARCHAR NOT NULL, ex_date DATE NOT NULL, action_type VARCHAR NOT NULL,
 value DECIMAL(24,10) NOT NULL, currency VARCHAR, source VARCHAR NOT NULL,
 retrieved_at TIMESTAMP NOT NULL,
 PRIMARY KEY (qualified_symbol, ex_date, action_type, source)
);
CREATE TABLE IF NOT EXISTS global_fx_observations (
 base_currency VARCHAR NOT NULL, quote_currency VARCHAR NOT NULL, observed_on DATE NOT NULL,
 rate DECIMAL(24,10) NOT NULL, source VARCHAR NOT NULL, retrieved_at TIMESTAMP NOT NULL,
 available_at TIMESTAMP NOT NULL,
 PRIMARY KEY (base_currency, quote_currency, observed_on, source)
);
CREATE TABLE IF NOT EXISTS global_exchange_sessions (
 exchange VARCHAR NOT NULL, session_date DATE NOT NULL, is_open BOOLEAN NOT NULL,
 source VARCHAR NOT NULL, retrieved_at TIMESTAMP NOT NULL,
 PRIMARY KEY (exchange, session_date)
);
CREATE TABLE IF NOT EXISTS global_ingestion_runs (
 run_id VARCHAR PRIMARY KEY, provider VARCHAR NOT NULL, region VARCHAR NOT NULL,
 started_at TIMESTAMP NOT NULL, finished_at TIMESTAMP, status VARCHAR NOT NULL,
 attempted INTEGER NOT NULL, completed INTEGER NOT NULL, failed INTEGER NOT NULL,
 checkpoint_symbol VARCHAR, dry_run BOOLEAN NOT NULL, report_json VARCHAR NOT NULL
);
CREATE TABLE IF NOT EXISTS global_ingestion_failures (
 run_id VARCHAR NOT NULL, qualified_symbol VARCHAR NOT NULL, stage VARCHAR NOT NULL,
 error_code VARCHAR NOT NULL, message VARCHAR NOT NULL, occurred_at TIMESTAMP NOT NULL,
 PRIMARY KEY (run_id, qualified_symbol, stage)
);
"""


def _required_columns(reader: csv.DictReader, required: set[str], label: str) -> None:
    missing = required - set(reader.fieldnames or ())
    if missing:
        raise ValueError(f"Missing {label} columns: {', '.join(sorted(missing))}")


def _decimal(value: str | None) -> Decimal | None:
    value = (value or "").strip()
    return Decimal(value) if value else None


def parse_price_csv(text: str, *, source: str, retrieved_at: datetime) -> list[PriceObservation]:
    reader = csv.DictReader(io.StringIO(text))
    required = {"qualified_symbol", "trading_date", "exchange", "currency", "open", "high", "low", "close", "adjusted_close", "volume"}
    _required_columns(reader, required, "price")
    rows = []
    for row in reader:
        status = (row.get("status") or "available").strip().lower()
        if status not in {"available", "missing", "stale", "failed"}:
            raise ValueError(f"Invalid price status: {status}")
        rows.append(PriceObservation(
            row["qualified_symbol"].strip().upper(), date.fromisoformat(row["trading_date"]),
            row["exchange"].strip().upper(), row["currency"].strip().upper(),
            _decimal(row["open"]), _decimal(row["high"]), _decimal(row["low"]),
            _decimal(row["close"]), _decimal(row["adjusted_close"]),
            int(row["volume"]) if row["volume"].strip() else None, source, retrieved_at, status,
        ))
    return rows


def parse_actions_csv(text: str, *, source: str, retrieved_at: datetime) -> list[CorporateAction]:
    reader = csv.DictReader(io.StringIO(text))
    _required_columns(reader, {"qualified_symbol", "ex_date", "action_type", "value", "currency"}, "action")
    rows = []
    for row in reader:
        kind = row["action_type"].strip().lower()
        if kind not in {"split", "cash_distribution"}:
            raise ValueError(f"Invalid corporate action: {kind}")
        rows.append(CorporateAction(row["qualified_symbol"].strip().upper(), date.fromisoformat(row["ex_date"]),
                                    kind, Decimal(row["value"]), row["currency"].strip().upper() or None,
                                    source, retrieved_at))
    return rows


def parse_fx_csv(text: str, *, source: str, retrieved_at: datetime) -> list[FXObservation]:
    reader = csv.DictReader(io.StringIO(text))
    _required_columns(reader, {"base_currency", "quote_currency", "observed_on", "rate", "available_at"}, "FX")
    rows = []
    for row in reader:
        available = datetime.fromisoformat(row["available_at"].replace("Z", "+00:00"))
        rate = Decimal(row["rate"])
        if rate <= 0 or available.tzinfo is None:
            raise ValueError("FX rate must be positive and available_at timezone-aware")
        rows.append(FXObservation(row["base_currency"].upper(), row["quote_currency"].upper(),
                                  date.fromisoformat(row["observed_on"]), rate, source,
                                  retrieved_at, available))
    return rows


def local_price_in_gbp(price: Decimal, currency: str, fx_to_gbp: Decimal | None) -> Decimal | None:
    """Convert a local quote without losing GBX/GBP semantics."""
    currency = currency.upper()
    if currency == "GBP":
        return price
    if currency == "GBX":
        return price / Decimal("100")
    return None if fx_to_gbp is None else price * fx_to_gbp


def total_return(previous_adjusted: Decimal, current_adjusted: Decimal) -> Decimal:
    if previous_adjusted <= 0:
        raise ValueError("previous adjusted close must be positive")
    return current_adjusted / previous_adjusted - Decimal("1")


def gbp_total_return(previous_adjusted: Decimal, current_adjusted: Decimal, currency: str,
                     previous_fx: Decimal | None, current_fx: Decimal | None) -> Decimal | None:
    previous = local_price_in_gbp(previous_adjusted, currency, previous_fx)
    current = local_price_in_gbp(current_adjusted, currency, current_fx)
    return None if previous is None or current is None else total_return(previous, current)


class ExchangeCalendar:
    """Explicit session calendar; supplied holidays prevent US-calendar assumptions."""
    def __init__(self, exchange: str, holidays: Iterable[date] = ()):
        self.exchange = exchange.upper()
        self.holidays = frozenset(holidays)

    def is_session(self, day: date) -> bool:
        return day.weekday() < 5 and day not in self.holidays

    def sessions(self, start: date, end: date) -> list[date]:
        days, current = [], start
        while current <= end:
            if self.is_session(current):
                days.append(current)
            current += timedelta(days=1)
        return days

    def session_age(self, observed: date, as_of: date) -> int:
        return len(self.sessions(observed + timedelta(days=1), as_of))


class GlobalMarketDataRepository:
    def __init__(self, path: Path | str):
        self.path = Path(path)

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with duckdb.connect(str(self.path)) as connection:
            connection.execute(PRICE_SCHEMA_SQL)

    def store(self, prices: Iterable[PriceObservation] = (), actions: Iterable[CorporateAction] = (),
              fx: Iterable[FXObservation] = ()) -> None:
        self.initialize()
        with duckdb.connect(str(self.path)) as connection:
            connection.execute("BEGIN")
            try:
                price_values = [[
                        row.qualified_symbol, row.trading_date, row.exchange, row.currency, row.open, row.high,
                        row.low, row.close, row.adjusted_close, row.volume, row.status, row.source, utc_naive(row.retrieved_at)]
                    for row in prices]
                bulk_insert(connection, "global_price_observations", price_values, conflict="REPLACE")
                action_values = [[
                        row.qualified_symbol, row.ex_date, row.action_type, row.value, row.currency,
                        row.source, utc_naive(row.retrieved_at)] for row in actions]
                bulk_insert(connection, "global_corporate_actions", action_values, conflict="REPLACE")
                fx_values = [[
                        row.base_currency, row.quote_currency, row.observed_on, row.rate, row.source,
                        utc_naive(row.retrieved_at), utc_naive(row.available_at)] for row in fx]
                bulk_insert(connection, "global_fx_observations", fx_values, conflict="REPLACE")
                connection.execute("COMMIT")
            except BaseException:
                connection.execute("ROLLBACK")
                raise

    def fx_rate(self, currency: str, observed_on: date, *, as_of: datetime,
                tolerance_days: int = 4) -> Decimal | None:
        currency = currency.upper()
        if currency in {"GBP", "GBX"}:
            return Decimal("1")
        if not self.path.exists():
            return None
        with duckdb.connect(str(self.path), read_only=True) as connection:
            tables = {row[0] for row in connection.execute("SHOW TABLES").fetchall()}
            if "global_fx_observations" not in tables:
                return None
            row = connection.execute("""SELECT rate, observed_on FROM global_fx_observations
                WHERE base_currency=? AND quote_currency='GBP' AND observed_on<=?
                  AND available_at<=? ORDER BY observed_on DESC, available_at DESC LIMIT 1""",
                [currency, observed_on, utc_naive(as_of)]).fetchone()
        return None if row is None or (observed_on - row[1]).days > tolerance_days else Decimal(str(row[0]))

    def ingest(self, provider: BatchPriceProvider, symbols: list[str], *, limits: IngestionLimits,
               now: datetime, dry_run: bool = False, sleep: Callable[[float], None] = time.sleep) -> dict:
        selected = list(dict.fromkeys(symbols))[:limits.max_items]
        if dry_run:
            return {"status": "validated", "mode": "dry_run", "provider": provider.name,
                    "region": provider.region, "symbols": len(selected), "batches": (len(selected) + limits.batch_size - 1) // limits.batch_size}
        self.initialize()
        run_id = hashlib.sha256(f"{provider.name}|{provider.region}|{utc_naive(now).isoformat()}".encode()).hexdigest()[:24]
        failures: list[dict] = []
        completed = 0
        started = time.monotonic()
        with duckdb.connect(str(self.path)) as connection:
            connection.execute("INSERT INTO global_ingestion_runs VALUES (?,?,?,?,NULL,'running',0,0,0,NULL,false,'{}')",
                               [run_id, provider.name, provider.region, utc_naive(now)])
        checkpoint = None
        for offset in range(0, len(selected), limits.batch_size):
            batch = selected[offset:offset + limits.batch_size]
            if time.monotonic() - started > limits.maximum_run_seconds:
                for symbol in batch:
                    failures.append({"symbol": symbol, "stage": "duration", "error_code": "maximum_run_duration", "message": "run duration exceeded"})
                break
            error = None
            for attempt in range(1, limits.max_attempts + 1):
                try:
                    prices, actions = provider.download(batch, None)
                    self.store(prices, actions)
                    completed += len(batch)
                    checkpoint = batch[-1]
                    error = None
                    break
                except Exception as exc:  # provider boundary; recorded and isolated
                    error = exc
                    if attempt < limits.max_attempts:
                        sleep(limits.rate_limit_seconds)
            if error is not None:
                failures.extend({"symbol": symbol, "stage": "prices", "error_code": type(error).__name__, "message": str(error)[:500]} for symbol in batch)
            if offset + limits.batch_size < len(selected):
                sleep(limits.rate_limit_seconds)
        report = {"failures": failures}
        finished = utc_naive(datetime.now(timezone.utc))
        with duckdb.connect(str(self.path)) as connection:
            for failure in failures:
                connection.execute("INSERT OR REPLACE INTO global_ingestion_failures VALUES (?,?,?,?,?,?)",
                                   [run_id, failure["symbol"], failure["stage"], failure["error_code"], failure["message"], finished])
            status = "completed" if not failures else ("partial" if completed else "failed")
            connection.execute("""UPDATE global_ingestion_runs SET finished_at=?,status=?,attempted=?,completed=?,failed=?,checkpoint_symbol=?,report_json=? WHERE run_id=?""",
                               [finished, status, len(selected), completed, len(failures), checkpoint, json.dumps(report, sort_keys=True), run_id])
        return {"run_id": run_id, "status": status, "attempted": len(selected), "completed": completed,
                "failed": len(failures), "checkpoint": checkpoint, **report}

    def coverage(self, *, now: datetime | None = None) -> dict:
        if not self.path.exists():
            return _empty_market_coverage()
        captured = utc_naive(now or datetime.now(timezone.utc))
        with duckdb.connect(str(self.path), read_only=True) as connection:
            tables = {row[0] for row in connection.execute("SHOW TABLES").fetchall()}
            if "global_price_observations" not in tables:
                return _empty_market_coverage()
            prices = connection.execute("""SELECT exchange,currency,COUNT(DISTINCT qualified_symbol),MAX(trading_date),MAX(retrieved_at),
                SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) FROM global_price_observations GROUP BY exchange,currency ORDER BY exchange,currency""").fetchall()
            fx = connection.execute("SELECT base_currency,MAX(observed_on),MAX(retrieved_at) FROM global_fx_observations GROUP BY base_currency ORDER BY base_currency").fetchall()
            run = connection.execute("SELECT finished_at,status,attempted,completed,failed,report_json FROM global_ingestion_runs ORDER BY started_at DESC LIMIT 1").fetchone()
        latest_run = None
        if run is not None:
            report = json.loads(run[5] or "{}")
            latest_run = {"finished_at": run[0], "status": run[1], "attempted": run[2],
                "completed": run[3], "actual_failed": run[4], "pending": report.get("pending", 0),
                "request_count": report.get("request_count"), "elapsed_seconds": report.get("elapsed_seconds"),
                "stop_reason": report.get("stop_reason")}
        return {"status": "available" if prices else "unavailable",
                "price_coverage": [{"exchange": r[0], "currency": r[1], "securities": r[2], "latest_trading_date": r[3], "latest_retrieval_at": r[4], "failed_observations": r[5]} for r in prices],
                "fx_coverage": [{"currency": r[0], "latest_observation_date": r[1], "latest_retrieval_at": r[2], "stale": (captured.date() - r[1]).days > 4} for r in fx],
                "latest_run": latest_run}

    def eligibility_report(self, *, as_of: datetime, minimum_history: int = 126,
                           minimum_median_value_gbp: Decimal = Decimal("1000000")) -> list[dict]:
        """Explain research eligibility without changing a universe or vintage."""
        if not self.path.exists():
            return []
        captured = utc_naive(as_of)
        with duckdb.connect(str(self.path), read_only=True) as connection:
            tables = {row[0] for row in connection.execute("SHOW TABLES").fetchall()}
            required = {"security_master_retrievals", "security_listings", "global_price_observations"}
            if not required <= tables:
                return []
            retrieval = connection.execute("SELECT retrieval_id FROM security_master_retrievals WHERE status='completed' AND retrieved_at<=? ORDER BY retrieved_at DESC LIMIT 1", [captured]).fetchone()
            if retrieval is None:
                return []
            listings = connection.execute("SELECT qualified_symbol,currency,active,instrument_type,primary_exchange FROM security_listings WHERE retrieval_id=? ORDER BY qualified_symbol", [retrieval[0]]).fetchall()
            result = []
            for symbol, currency, active, instrument, exchange in listings:
                stats = connection.execute("""SELECT COUNT(adjusted_close),MAX(trading_date),MEDIAN(adjusted_close*volume)
                    FROM global_price_observations WHERE qualified_symbol=? AND status='available' AND trading_date<=?""",
                    [symbol, captured.date()]).fetchone()
                history = int(stats[0] or 0)
                rate = self.fx_rate(currency, stats[1], as_of=as_of) if stats[1] else None
                metadata = bool(active and currency and exchange and instrument in {"common_stock", "ordinary_share"})
                price_history = history > 0
                sufficient = history >= minimum_history
                liquidity_gbp = None if stats[2] is None else local_price_in_gbp(Decimal(str(stats[2])), currency, rate)
                liquidity = liquidity_gbp is not None and liquidity_gbp >= minimum_median_value_gbp
                valid_fx = currency in {"GBP", "GBX"} or rate is not None
                reasons = []
                if not metadata: reasons.append("invalid_metadata")
                if not price_history: reasons.append("missing_price_history")
                elif not sufficient: reasons.append("insufficient_momentum_history")
                if not liquidity: reasons.append("inadequate_liquidity")
                if not valid_fx: reasons.append("missing_or_stale_fx")
                result.append({"qualified_symbol": symbol, "valid_metadata": metadata,
                               "adequate_price_history": price_history, "adequate_liquidity": liquidity,
                               "valid_fx_coverage": valid_fx, "sufficient_momentum_history": sufficient,
                               "eligible": not reasons, "reasons": reasons})
        return result


def _empty_market_coverage() -> dict:
    return {"status": "unavailable", "price_coverage": [], "fx_coverage": [], "latest_run": None}


def bulk_insert(connection, table: str, rows: list[list], conflict: str | None = None) -> None:
    """INSERT (OR REPLACE / OR IGNORE when `conflict` says so) `rows` as one set-based statement.

    Row-by-row inserts cost milliseconds each (executemany far more) once a table
    holds millions of keyed rows; scanning a DataFrame is about a thousand times
    faster. Each row's values fill the table's leading columns in order, as a
    positional VALUES list would; columns added later by other code (optional,
    after these) are left NULL. Decimals travel as text and are cast back to the
    column's exact type."""
    if not rows: return
    if conflict not in (None, "REPLACE", "IGNORE"): raise ValueError("conflict must be REPLACE, IGNORE or None")
    width = len(rows[0])
    if any(len(row) != width for row in rows): raise ValueError("rows must all have the same number of values")
    columns = connection.execute(
        "SELECT column_name, data_type FROM information_schema.columns WHERE table_schema = 'main' AND table_name = ? ORDER BY ordinal_position",
        [table]).fetchall()[:width]
    if len(columns) != width: raise ValueError(f"{table} has fewer columns than the values supplied")
    names = [f"c{i}" for i in range(width)]
    frame = pd.DataFrame([[str(v) if isinstance(v, Decimal) else v for v in row] for row in rows], columns=names, dtype=object)
    select = ", ".join(f'CAST("{n}" AS {kind})' for n, (_, kind) in zip(names, columns))
    target = ", ".join(f'"{name}"' for name, _ in columns)
    verb = f"INSERT OR {conflict}" if conflict else "INSERT"
    connection.register("replacement_rows", frame)
    try: connection.execute(f"{verb} INTO {table} ({target}) SELECT {select} FROM replacement_rows")
    finally: connection.unregister("replacement_rows")
