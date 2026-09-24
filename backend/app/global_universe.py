"""Point-in-time global security discovery for shadow research.

This module is deliberately disconnected from ``app.universe.UNIVERSE`` and the
production ranking cycle.  Providers return normalized observations; callers
may preview them without opening DuckDB or explicitly persist a retrieval.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Iterable, Protocol
from uuid import NAMESPACE_URL, uuid5

import duckdb


ORDINARY_TYPES = frozenset({"common_stock", "ordinary_share"})
EXCLUDED_TYPE_REASONS = {
    "etf": "excluded_etf",
    "fund": "excluded_fund",
    "warrant": "excluded_warrant",
    "right": "excluded_right",
    "preferred_stock": "excluded_preferred_share",
    "adr": "adr_duplicate",
}


def utc_naive(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("timestamps must be timezone-aware")
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def stable_id(kind: str, *parts: str | None) -> str:
    value = "|".join((part or "").strip().upper() for part in parts)
    return str(uuid5(NAMESPACE_URL, f"signallens:{kind}:{value}"))


def normalize_exchange(value: str) -> str:
    aliases = {
        "N": "NYSE", "A": "NYSEAMERICAN", "P": "NYSEARCA",
        "Q": "NASDAQ", "Z": "BATS", "V": "IEX",
    }
    normalized = value.strip().upper()
    return aliases.get(normalized, normalized)


def normalized_company_name(value: str) -> str:
    value = re.sub(r"[^A-Z0-9 ]", " ", value.upper())
    suffixes = r"\b(INCORPORATED|INC|CORPORATION|CORP|LIMITED|LTD|PLC|SA|NV|AG|SE|S A)\b"
    return re.sub(r"\s+", " ", re.sub(suffixes, "", value)).strip()


@dataclass(frozen=True)
class ListingObservation:
    source_key: str
    ticker: str
    exchange: str
    company_name: str
    listing_country: str
    currency: str
    instrument_type: str
    domicile: str | None = None
    is_primary: bool | None = None
    active: bool = True
    isin: str | None = None
    cik: str | None = None
    lei: str | None = None
    exchange_symbol: str | None = None
    raw: dict[str, str] | None = None

    @property
    def qualified_symbol(self) -> str:
        return self.exchange_symbol or f"{normalize_exchange(self.exchange)}:{self.ticker.upper()}"


class DiscoveryProvider(Protocol):
    name: str
    source_label: str

    def discover(self) -> list[ListingObservation]: ...


class FXProvider(Protocol):
    name: str

    def rate(self, base: str, quote: str, observed_on: date, as_of: datetime) -> Decimal | None: ...


class StoredFXProvider:
    """Point-in-time FX lookup; never forward-fills from a later retrieval."""

    name = "stored_point_in_time"

    def __init__(self, path: Path | str):
        self.path = Path(path)

    def rate(self, base: str, quote: str, observed_on: date, as_of: datetime) -> Decimal | None:
        if base == quote:
            return Decimal("1")
        if not self.path.exists():
            return None
        connection = duckdb.connect(str(self.path), read_only=True)
        try:
            tables = {row[0] for row in connection.execute("SHOW TABLES").fetchall()}
            if "fx_observations" not in tables:
                return None
            row = connection.execute(
                """SELECT rate FROM fx_observations
                   WHERE base_currency=? AND quote_currency=? AND observed_on<=?
                     AND retrieved_at<=?
                   ORDER BY observed_on DESC, retrieved_at DESC LIMIT 1""",
                [base, quote, observed_on, utc_naive(as_of)],
            ).fetchone()
            return None if row is None else Decimal(str(row[0]))
        finally:
            connection.close()


def parse_nasdaq_symbol_directory(text: str, *, directory: str) -> list[ListingObservation]:
    """Parse Nasdaq Trader pipe files, excluding their footer record."""
    rows = list(csv.DictReader(io.StringIO(text), delimiter="|"))
    results: list[ListingObservation] = []
    for row in rows:
        ticker = (row.get("Symbol") or row.get("ACT Symbol") or "").strip()
        if not ticker or ticker.startswith("File Creation Time"):
            continue
        test_issue = (row.get("Test Issue") or "N").strip().upper() == "Y"
        if test_issue:
            continue
        exchange = "NASDAQ" if directory == "nasdaqlisted" else normalize_exchange(row.get("Exchange", ""))
        name = (row.get("Security Name") or "").strip()
        etf = (row.get("ETF") or "N").strip().upper() == "Y"
        lowered = name.lower()
        instrument = "etf" if etf else (
            "warrant" if "warrant" in lowered else
            "right" if re.search(r"\bright(s)?\b", lowered) else
            "preferred_stock" if "preferred" in lowered else
            "adr" if "depositary" in lowered or " adr" in lowered else
            "common_stock"
        )
        results.append(ListingObservation(
            source_key=f"{directory}:{ticker}", ticker=ticker, exchange=exchange,
            company_name=name, listing_country="US", currency="USD",
            instrument_type=instrument, active=True, raw={k: v or "" for k, v in row.items() if k},
        ))
    return results


def parse_reference_csv(text: str, *, source: str) -> list[ListingObservation]:
    """Parse the documented provider-neutral exchange reference CSV contract."""
    required = {"source_key", "ticker", "exchange", "company_name", "listing_country", "currency", "instrument_type"}
    reader = csv.DictReader(io.StringIO(text))
    missing = required - set(reader.fieldnames or ())
    if missing:
        raise ValueError(f"Missing reference columns: {', '.join(sorted(missing))}")
    results = []
    for row in reader:
        def optional(name: str) -> str | None:
            value = (row.get(name) or "").strip()
            return value or None
        primary = optional("is_primary")
        active = (optional("active") or "true").lower() in {"true", "1", "yes", "active"}
        results.append(ListingObservation(
            source_key=row["source_key"].strip(), ticker=row["ticker"].strip().upper(),
            exchange=normalize_exchange(row["exchange"]), company_name=row["company_name"].strip(),
            listing_country=row["listing_country"].strip().upper(), domicile=optional("domicile"),
            currency=row["currency"].strip().upper(), instrument_type=row["instrument_type"].strip().lower(),
            is_primary=None if primary is None else primary.lower() in {"true", "1", "yes", "primary"},
            active=active, isin=optional("isin"), cik=optional("cik"), lei=optional("lei"),
            exchange_symbol=optional("exchange_symbol"), raw={k: v or "" for k, v in row.items() if k},
        ))
    return results


def company_key(item: ListingObservation) -> str:
    if item.lei:
        return f"LEI:{item.lei.upper()}"
    if item.cik:
        return f"CIK:{item.cik.lstrip('0') or '0'}"
    if item.isin and len(item.isin) == 12:
        return f"ISIN:{item.isin.upper()}"
    domicile = (item.domicile or item.listing_country).upper()
    return f"NAME:{domicile}:{normalized_company_name(item.company_name)}"


def choose_canonical(items: Iterable[ListingObservation]) -> dict[str, str]:
    grouped: dict[str, list[ListingObservation]] = {}
    for item in items:
        grouped.setdefault(company_key(item), []).append(item)

    def score(item: ListingObservation) -> tuple:
        home = item.domicile is None or item.domicile.upper() == item.listing_country.upper()
        return (
            not item.active,
            item.is_primary is not True,
            item.instrument_type not in ORDINARY_TYPES,
            not home,
            not bool(item.currency and item.exchange),
            normalize_exchange(item.exchange),
            item.ticker.upper(),
            item.source_key,
        )

    return {key: min(group, key=score).source_key for key, group in grouped.items()}


@dataclass(frozen=True)
class InvestabilityConfig:
    reporting_currency: str = "GBP"
    minimum_price_usd: float = 5.0
    minimum_median_daily_value_usd: float = 5_000_000.0
    liquidity_window_days: int = 60
    minimum_history_days: int = 126
    provider_stale_after_days: int = 31


@dataclass(frozen=True)
class MarketMetrics:
    adjusted_close: float | None = None
    median_daily_value: float | None = None
    valid_history_days: int = 0
    observed_on: date | None = None
    currency: str | None = None


def exclusion_reasons(
    item: ListingObservation, *, canonical_source_key: str, config: InvestabilityConfig,
    metrics: MarketMetrics | None, usd_rate: Decimal | None, provider_stale: bool = False,
) -> list[str]:
    reasons: list[str] = []
    if item.source_key != canonical_source_key:
        reasons.append("secondary_listing" if item.is_primary is False else "noncanonical_listing")
    if item.instrument_type not in ORDINARY_TYPES:
        reasons.append(EXCLUDED_TYPE_REASONS.get(item.instrument_type, "excluded_instrument_type"))
    if normalize_exchange(item.exchange) in {"OTC", "OTCQX", "OTCQB", "PINK", ""}:
        reasons.append("excluded_otc_or_invalid_exchange")
    if not item.active:
        reasons.append("inactive_or_delisted")
    if not item.currency or not item.listing_country or not item.company_name:
        reasons.append("invalid_security_metadata")
    if provider_stale:
        reasons.append("stale_provider_data")
    if metrics is None:
        reasons.append("missing_price_data")
    else:
        if metrics.valid_history_days < config.minimum_history_days:
            reasons.append("insufficient_price_history")
        if metrics.adjusted_close is None:
            reasons.append("missing_adjusted_close")
        if metrics.median_daily_value is None:
            reasons.append("missing_liquidity_data")
        if item.currency != "USD" and usd_rate is None:
            reasons.append("fx_unavailable")
        elif usd_rate is not None:
            if metrics.adjusted_close is not None and Decimal(str(metrics.adjusted_close)) * usd_rate < Decimal(str(config.minimum_price_usd)):
                reasons.append("below_minimum_price")
            if metrics.median_daily_value is not None and Decimal(str(metrics.median_daily_value)) * usd_rate < Decimal(str(config.minimum_median_daily_value_usd)):
                reasons.append("below_minimum_traded_value")
    return sorted(set(reasons))


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS security_master_retrievals (
 retrieval_id VARCHAR PRIMARY KEY, provider VARCHAR NOT NULL, source_label VARCHAR NOT NULL,
 retrieved_at TIMESTAMP NOT NULL, source_as_of TIMESTAMP, status VARCHAR NOT NULL,
 content_hash VARCHAR NOT NULL, listing_count INTEGER NOT NULL, error VARCHAR
);
CREATE TABLE IF NOT EXISTS security_listings (
 retrieval_id VARCHAR NOT NULL, security_id VARCHAR NOT NULL, company_id VARCHAR NOT NULL,
 source_key VARCHAR NOT NULL, ticker VARCHAR NOT NULL, qualified_symbol VARCHAR NOT NULL,
 company_name VARCHAR NOT NULL, primary_exchange VARCHAR NOT NULL, listing_country VARCHAR NOT NULL,
 domicile VARCHAR, currency VARCHAR NOT NULL, instrument_type VARCHAR NOT NULL,
 is_primary BOOLEAN, active BOOLEAN NOT NULL, isin VARCHAR, cik VARCHAR, lei VARCHAR,
 first_seen_at TIMESTAMP NOT NULL, last_seen_at TIMESTAMP NOT NULL, raw_json VARCHAR NOT NULL,
 PRIMARY KEY (retrieval_id, source_key)
);
CREATE TABLE IF NOT EXISTS fx_observations (
 base_currency VARCHAR NOT NULL, quote_currency VARCHAR NOT NULL, observed_on DATE NOT NULL,
 rate DECIMAL(24,10) NOT NULL, source VARCHAR NOT NULL, retrieved_at TIMESTAMP NOT NULL,
 PRIMARY KEY (base_currency, quote_currency, observed_on, source, retrieved_at)
);
CREATE TABLE IF NOT EXISTS universe_snapshots (
 snapshot_id VARCHAR PRIMARY KEY, snapshot_month DATE NOT NULL UNIQUE, snapshot_at TIMESTAMP NOT NULL,
 reporting_currency VARCHAR NOT NULL, config_json VARCHAR NOT NULL, retrieval_ids_json VARCHAR NOT NULL,
 content_hash VARCHAR NOT NULL
);
CREATE TABLE IF NOT EXISTS universe_snapshot_members (
 snapshot_id VARCHAR NOT NULL, security_id VARCHAR NOT NULL, company_id VARCHAR NOT NULL,
 qualified_symbol VARCHAR NOT NULL, canonical BOOLEAN NOT NULL, eligible BOOLEAN NOT NULL,
 exclusion_reasons_json VARCHAR NOT NULL, metrics_json VARCHAR NOT NULL, source_retrieved_at TIMESTAMP NOT NULL,
 PRIMARY KEY (snapshot_id, security_id)
);
"""


class GlobalUniverseRepository:
    def __init__(self, path: Path | str):
        self.path = Path(path)

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with duckdb.connect(str(self.path)) as connection:
            connection.execute(SCHEMA_SQL)

    def refresh(self, provider: DiscoveryProvider, *, retrieved_at: datetime, source_as_of: datetime | None = None) -> dict:
        items = provider.discover()
        serialized = json.dumps([asdict(item) for item in items], sort_keys=True)
        digest = hashlib.sha256(serialized.encode()).hexdigest()
        retrieval_id = stable_id("retrieval", provider.name, digest)
        captured = utc_naive(retrieved_at)
        self.initialize()
        with duckdb.connect(str(self.path)) as connection:
            existing = connection.execute("SELECT listing_count FROM security_master_retrievals WHERE retrieval_id=?", [retrieval_id]).fetchone()
            if existing:
                return {"command": "refresh", "status": "already_exists", "retrieval_id": retrieval_id, "listings": existing[0]}
            connection.execute("BEGIN")
            try:
                connection.execute("INSERT INTO security_master_retrievals VALUES (?, ?, ?, ?, ?, 'completed', ?, ?, NULL)",
                                   [retrieval_id, provider.name, provider.source_label, captured,
                                    None if source_as_of is None else utc_naive(source_as_of), digest, len(items)])
                for item in items:
                    key = company_key(item)
                    company_id = stable_id("company", key)
                    security_id = stable_id("listing", item.source_key, item.qualified_symbol)
                    prior = connection.execute("SELECT MIN(first_seen_at) FROM security_listings WHERE security_id=?", [security_id]).fetchone()[0]
                    connection.execute("INSERT INTO security_listings VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", [
                        retrieval_id, security_id, company_id, item.source_key, item.ticker.upper(), item.qualified_symbol,
                        item.company_name, normalize_exchange(item.exchange), item.listing_country.upper(), item.domicile,
                        item.currency.upper(), item.instrument_type, item.is_primary, item.active, item.isin, item.cik, item.lei,
                        prior or captured, captured, json.dumps(item.raw or {}, sort_keys=True),
                    ])
                connection.execute("COMMIT")
            except BaseException:
                connection.execute("ROLLBACK")
                raise
        return {"command": "refresh", "status": "completed", "retrieval_id": retrieval_id, "listings": len(items)}

    def latest_items(self, *, as_of: datetime) -> tuple[str, datetime, list[ListingObservation]]:
        with duckdb.connect(str(self.path), read_only=True) as connection:
            row = connection.execute("SELECT retrieval_id, retrieved_at FROM security_master_retrievals WHERE status='completed' AND retrieved_at<=? ORDER BY retrieved_at DESC LIMIT 1", [utc_naive(as_of)]).fetchone()
            if row is None:
                raise ValueError("No completed security-master retrieval is available")
            records = connection.execute("""SELECT source_key,ticker,primary_exchange,company_name,listing_country,currency,instrument_type,domicile,is_primary,active,isin,cik,lei,qualified_symbol
                FROM security_listings WHERE retrieval_id=? ORDER BY source_key""", [row[0]]).fetchall()
        return row[0], row[1], [ListingObservation(*record[:7], domicile=record[7], is_primary=record[8], active=record[9], isin=record[10], cik=record[11], lei=record[12], exchange_symbol=record[13]) for record in records]

    def create_snapshot(self, *, snapshot_month: date, snapshot_at: datetime, config: InvestabilityConfig,
                        metrics: dict[str, MarketMetrics] | None = None, fx: FXProvider | None = None) -> dict:
        if snapshot_month.day != 1:
            raise ValueError("snapshot_month must be the first calendar day")
        retrieval_id, retrieved_at, items = self.latest_items(as_of=snapshot_at)
        canonical = choose_canonical(items)
        metrics = metrics or {}
        rows = []
        stale = snapshot_at.replace(tzinfo=None) - retrieved_at > timedelta(days=config.provider_stale_after_days)
        for item in items:
            metric = metrics.get(item.qualified_symbol)
            rate = Decimal("1") if item.currency == "USD" else (
                fx.rate(item.currency, "USD", metric.observed_on, snapshot_at)
                if fx and metric and metric.observed_on else None
            )
            reasons = exclusion_reasons(item, canonical_source_key=canonical[company_key(item)], config=config,
                                        metrics=metric, usd_rate=rate, provider_stale=stale)
            rows.append((stable_id("listing", item.source_key, item.qualified_symbol), item, reasons, metric))
        payload = [{"symbol": item.qualified_symbol, "reasons": reasons} for _, item, reasons, _ in rows]
        digest_payload = {
            "config": asdict(config),
            "retrieval_id": retrieval_id,
            "members": payload,
        }
        digest = hashlib.sha256(json.dumps(digest_payload, sort_keys=True).encode()).hexdigest()
        snapshot_id = stable_id("snapshot", snapshot_month.isoformat(), digest)
        with duckdb.connect(str(self.path)) as connection:
            existing = connection.execute("SELECT snapshot_id, content_hash FROM universe_snapshots WHERE snapshot_month=?", [snapshot_month]).fetchone()
            if existing:
                if existing[1] != digest:
                    raise ValueError("Monthly universe snapshot is immutable")
                return {"command": "snapshot", "status": "already_exists", "snapshot_id": existing[0], "month": snapshot_month.isoformat()}
            connection.execute("BEGIN")
            try:
                connection.execute("INSERT INTO universe_snapshots VALUES (?, ?, ?, ?, ?, ?, ?)", [snapshot_id, snapshot_month, utc_naive(snapshot_at), config.reporting_currency, json.dumps(asdict(config), sort_keys=True), json.dumps([retrieval_id]), digest])
                for security_id, item, reasons, metric in rows:
                    connection.execute("INSERT INTO universe_snapshot_members VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", [snapshot_id, security_id, stable_id("company", company_key(item)), item.qualified_symbol, item.source_key == canonical[company_key(item)], not reasons, json.dumps(reasons), json.dumps(asdict(metric) if metric else {}, default=str, sort_keys=True), retrieved_at])
                connection.execute("COMMIT")
            except BaseException:
                connection.execute("ROLLBACK")
                raise
        return {"command": "snapshot", "status": "completed", "snapshot_id": snapshot_id, "month": snapshot_month.isoformat(), "listings": len(rows), "eligible": sum(not reasons for _, _, reasons, _ in rows)}

    def coverage(self, *, now: datetime | None = None) -> dict:
        captured = utc_naive(now or datetime.now(timezone.utc))
        if not self.path.exists():
            return empty_coverage("database_missing")
        with duckdb.connect(str(self.path), read_only=True) as connection:
            tables = {row[0] for row in connection.execute("SHOW TABLES").fetchall()}
            if "security_master_retrievals" not in tables:
                return empty_coverage("never_retrieved")
            latest = connection.execute("SELECT retrieval_id,retrieved_at,listing_count FROM security_master_retrievals WHERE status='completed' ORDER BY retrieved_at DESC LIMIT 1").fetchone()
            if latest is None:
                return empty_coverage("no_completed_retrieval")
            listings = connection.execute("SELECT company_id,listing_country,primary_exchange,currency FROM security_listings WHERE retrieval_id=?", [latest[0]]).fetchall()
            snapshot = connection.execute("SELECT snapshot_id,snapshot_month,snapshot_at FROM universe_snapshots ORDER BY snapshot_month DESC LIMIT 1").fetchone()
            excluded: dict[str, int] = {}
            eligible = 0
            if snapshot:
                member_rows = connection.execute("SELECT eligible,exclusion_reasons_json FROM universe_snapshot_members WHERE snapshot_id=?", [snapshot[0]]).fetchall()
                eligible = sum(row[0] for row in member_rows)
                for _, reasons_json in member_rows:
                    for reason in json.loads(reasons_json):
                        excluded[reason] = excluded.get(reason, 0) + 1
        def counts(index: int) -> dict[str, int]:
            result: dict[str, int] = {}
            for row in listings:
                result[row[index]] = result.get(row[index], 0) + 1
            return dict(sorted(result.items()))
        age = (captured - latest[1]).days
        return {"status": "stale" if age > 31 else "available", "listings_discovered": latest[2],
                "canonical_companies": len({row[0] for row in listings}), "eligible_securities": eligible,
                "excluded_by_reason": dict(sorted(excluded.items())), "by_country": counts(1), "by_exchange": counts(2),
                "by_currency": counts(3), "missing": [] if snapshot else ["monthly_snapshot"],
                "stale": ["security_master"] if age > 31 else [], "latest_retrieval_at": latest[1],
                "latest_snapshot": None if snapshot is None else {"snapshot_id": snapshot[0], "month": snapshot[1], "snapshot_at": snapshot[2]}}


def empty_coverage(reason: str) -> dict:
    return {"status": "unavailable", "listings_discovered": 0, "canonical_companies": 0,
            "eligible_securities": 0, "excluded_by_reason": {}, "by_country": {}, "by_exchange": {},
            "by_currency": {}, "missing": [reason], "stale": [], "latest_retrieval_at": None,
            "latest_snapshot": None}


def preview(provider: DiscoveryProvider) -> dict:
    items = provider.discover()
    canonical = choose_canonical(items)
    return {"command": "discovery_preview", "mode": "dry_run", "status": "validated",
            "provider": provider.name, "listings": len(items), "canonical_companies": len(canonical),
            "by_country": _item_counts(items, "listing_country"), "by_exchange": _item_counts(items, "exchange"),
            "by_currency": _item_counts(items, "currency")}


def _item_counts(items: Iterable[ListingObservation], field: str) -> dict[str, int]:
    result: dict[str, int] = {}
    for item in items:
        value = getattr(item, field)
        result[value] = result.get(value, 0) + 1
    return dict(sorted(result.items()))
