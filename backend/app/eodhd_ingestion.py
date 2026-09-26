"""Bounded EODHD ingestion into the isolated global-research schema.

This is an operator tool, not a production publisher.  Every network response is
validated before it reaches the existing security-master, price, action, and FX
tables.  The current provider catalogue is explicitly *not* historical membership.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable

import duckdb
import httpx

from .global_market_data import CorporateAction, FXObservation, GlobalMarketDataRepository, PriceObservation
from .global_universe import GlobalUniverseRepository, ListingObservation, choose_canonical, company_key, utc_naive

REGIONS = {"US": ("US", "USD"), "LSE": ("GB", "GBP"), "TO": ("CA", "CAD"),
           "XETRA": ("DE", "EUR"), "PA": ("FR", "EUR")}
TYPE_MAP = {"common stock": "common_stock", "ordinary shares": "ordinary_share",
            "ordinary share": "ordinary_share", "common shares": "common_stock"}
EXCLUDED_TYPES = {"etf": "excluded_etf", "fund": "excluded_fund", "index": "excluded_index",
                  "preferred": "excluded_preferred_share", "warrant": "excluded_warrant",
                  "adr": "excluded_adr", "gdr": "excluded_depositary_receipt"}
TEN_YEARS_DAYS = 3653


@dataclass(frozen=True)
class EODHDLimits:
    per_region: int = 100
    total: int = 500
    daily_requests: int = 700
    requests_per_minute: int = 20
    retries: int = 2
    timeout_seconds: float = 15
    max_response_bytes: int = 16 * 1024 * 1024
    maximum_runtime_seconds: float = 1800

    def __post_init__(self) -> None:
        if not 1 <= self.per_region <= 100 or not 1 <= self.total <= 500:
            raise ValueError("pilot caps are 100 per region and 500 total")
        if min(self.daily_requests, self.requests_per_minute, self.retries) < 1:
            raise ValueError("request budgets and retries must be positive")
        if not 0 < self.timeout_seconds <= 60 or self.max_response_bytes > 16 * 1024 * 1024:
            raise ValueError("timeout/response limit exceeds safety ceiling")
        if self.maximum_runtime_seconds <= 0:
            raise ValueError("maximum runtime must be positive")


class EODHDClient:
    base_url = "https://eodhd.com/api"
    def __init__(self, token: str, limits: EODHDLimits, *, transport: httpx.BaseTransport | None = None,
                 sleep: Callable[[float], None] = time.sleep):
        if not token: raise ValueError("SIGNALLENS_EODHD_API_TOKEN is required")
        self._token, self.limits, self.transport, self.sleep = token, limits, transport, sleep
        self.requests = 0
        self.started = time.monotonic()

    def get(self, endpoint: str, params: dict[str, str] | None = None) -> Any:
        if self.requests >= self.limits.daily_requests: raise RuntimeError("daily_request_budget_exhausted")
        if time.monotonic() - self.started >= self.limits.maximum_runtime_seconds: raise RuntimeError("maximum_runtime_exceeded")
        for attempt in range(self.limits.retries):
            if self.requests:
                self.sleep(60 / self.limits.requests_per_minute)
            self.requests += 1
            try:
                with httpx.Client(timeout=self.limits.timeout_seconds, transport=self.transport,
                                  headers={"User-Agent": "SignalLens bounded research ingestion"}) as client:
                    response = client.get(f"{self.base_url}/{endpoint}", params={**(params or {}), "api_token": self._token, "fmt": "json"})
                if len(response.content) > self.limits.max_response_bytes: raise RuntimeError("response_too_large")
                if response.status_code in {429, 500, 502, 503, 504} and attempt + 1 < self.limits.retries: continue
                response.raise_for_status()
                return response.json()
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                if attempt + 1 == self.limits.retries: raise RuntimeError("provider_request_failed") from exc
        raise RuntimeError("provider_request_failed")


def _decimal(value: Any, *, allow_zero: bool = False) -> Decimal:
    try: number = Decimal(str(value))
    except (InvalidOperation, TypeError) as exc: raise ValueError("invalid numeric provider field") from exc
    if not number.is_finite() or number < 0 or (number == 0 and not allow_zero): raise ValueError("invalid numeric provider field")
    return number


def classify_type(value: str) -> tuple[str | None, str | None]:
    normalized = " ".join(value.lower().replace("_", " ").split())
    if normalized in TYPE_MAP: return TYPE_MAP[normalized], None
    for marker, reason in EXCLUDED_TYPES.items():
        if marker in normalized: return None, reason
    return None, "excluded_unreliable_instrument_classification"


def parse_catalogue(payload: Any, region: str) -> tuple[list[ListingObservation], list[dict]]:
    if region not in REGIONS or not isinstance(payload, list): raise ValueError("invalid catalogue response")
    country, default_currency = REGIONS[region]
    accepted, excluded = [], []
    for row in payload:
        if not isinstance(row, dict): raise ValueError("invalid catalogue record")
        code, name = str(row.get("Code", "")).strip(), str(row.get("Name", "")).strip()
        kind, reason = classify_type(str(row.get("Type", "")))
        if not code or not name: reason = "excluded_missing_identity"
        if reason:
            excluded.append({"exchange_qualified_symbol": f"{code}.{region}", "reason": reason})
            continue
        currency = str(row.get("Currency") or default_currency).upper()
        isin = str(row.get("Isin") or row.get("ISIN") or "").strip() or None
        accepted.append(ListingObservation(f"eodhd:{code}.{region}", code, region, name, country,
            currency, kind or "", domicile=str(row.get("Country") or country).upper(),
            is_primary=None, active=True, isin=isin, exchange_symbol=f"{code}.{region}",
            raw={k: str(v) for k, v in row.items()}))
    return accepted, excluded


def parse_eod(payload: Any, listing: ListingObservation, retrieved_at: datetime,
              start: date, end: date) -> list[PriceObservation]:
    if not isinstance(payload, list): raise ValueError("invalid EOD response")
    rows, seen, previous = [], set(), None
    for item in payload:
        if not isinstance(item, dict): raise ValueError("invalid EOD row")
        day = date.fromisoformat(str(item.get("date")))
        if day < start or day > end or day in seen or (previous and day <= previous): raise ValueError("invalid, duplicate, or non-monotonic EOD date")
        seen.add(day); previous = day
        op, high, low, close = (_decimal(item.get(k)) for k in ("open", "high", "low", "close"))
        adjusted = _decimal(item.get("adjusted_close"))
        volume = int(_decimal(item.get("volume"), allow_zero=True))
        if low > min(op, close, high) or high < max(op, close, low): raise ValueError("invalid OHLC relationship")
        rows.append(PriceObservation(listing.qualified_symbol, day, listing.exchange, listing.currency,
            op, high, low, close, adjusted, volume, "eodhd", retrieved_at))
    return rows


class CatalogueProvider:
    name, source_label = "eodhd", "EODHD current exchange catalogue (not historical membership)"
    def __init__(self, items: list[ListingObservation]): self.items = items
    def discover(self) -> list[ListingObservation]: return self.items


class EODHDIngestion:
    def __init__(self, research_path: Path, production_path: Path, client: EODHDClient):
        if research_path.resolve() == production_path.resolve(): raise ValueError("refusing production database path")
        self.path, self.client = research_path, client
        self.market = GlobalMarketDataRepository(research_path)
        self.universe = GlobalUniverseRepository(research_path)

    def plan(self, *, securities_per_region: int | None = None) -> dict:
        count = min(securities_per_region or self.client.limits.per_region, self.client.limits.per_region)
        securities = count * len(REGIONS); requests = 5 + securities * 2 + 3
        rows = securities * 2520
        return {"command": "plan", "mode": "read_only", "status": "authorization_required",
                "regions": list(REGIONS), "maximum_securities_per_region": count, "maximum_securities": securities,
                "estimated_requests": requests, "estimated_price_rows": rows,
                "estimated_storage_bytes": rows * 160, "estimated_runtime_minutes": round(requests / self.client.limits.requests_per_minute, 1),
                "warnings": ["Current catalogues are not survivorship-free or historical membership.",
                 "Research only: invalid for production promotion and historical-membership backtests."]}

    def catalogue(self, *, retrieved_at: datetime, dry_run: bool = False) -> dict:
        all_items, exclusions = [], []
        for region in REGIONS:
            items, rejected = parse_catalogue(self.client.get(f"exchange-symbol-list/{region}"), region)
            all_items.extend(items); exclusions.extend(rejected)
        canonical = choose_canonical(all_items)
        selected = []
        for region in REGIONS:
            candidates = sorted((x for x in all_items if x.exchange == region), key=lambda x: x.qualified_symbol)
            for item in candidates:
                if item.source_key != canonical[company_key(item)]:
                    exclusions.append({"exchange_qualified_symbol": item.qualified_symbol, "reason": "noncanonical_secondary_or_adr"})
                elif len([x for x in selected if x.exchange == region]) < self.client.limits.per_region and len(selected) < self.client.limits.total:
                    selected.append(item)
                else: exclusions.append({"exchange_qualified_symbol": item.qualified_symbol, "reason": "pilot_cap"})
        result = {"command": "ingest-catalogue", "mode": "dry_run" if dry_run else "write", "accepted": len(selected),
                  "excluded": len(exclusions), "exclusions_by_reason": _counts(exclusions), "requests": self.client.requests}
        if not dry_run:
            result.update(self.universe.refresh(CatalogueProvider(selected), retrieved_at=retrieved_at))
        return result

    def _latest_listings(self, at: datetime) -> list[ListingObservation]:
        return self.universe.latest_items(as_of=at)[2]

    def prices(self, *, retrieved_at: datetime, resume: bool = False) -> dict:
        listings = self._latest_listings(retrieved_at)
        start, end = retrieved_at.date() - timedelta(days=TEN_YEARS_DAYS), retrieved_at.date()
        done = self._completed("prices") if resume else set()
        completed, failures, rows = 0, [], 0
        self._state_schema()
        for item in listings:
            if item.qualified_symbol in done: continue
            try:
                prices = parse_eod(self.client.get(f"eod/{item.qualified_symbol}", {"from": start.isoformat(), "to": end.isoformat(), "period": "d"}), item, retrieved_at, start, end)
                div_payload = self.client.get(f"div/{item.qualified_symbol}", {"from": start.isoformat(), "to": end.isoformat()})
                actions = _dividends(div_payload, item, retrieved_at, start, end)
                self.market.store(prices, actions); rows += len(prices); completed += 1
                self._checkpoint("prices", item.qualified_symbol, "completed", None)
            except Exception as exc:
                failures.append({"symbol": item.qualified_symbol, "code": type(exc).__name__})
                self._checkpoint("prices", item.qualified_symbol, "failed", type(exc).__name__)
        return {"command": "resume" if resume else "ingest-prices", "status": "completed" if not failures else "partial",
                "completed": completed, "failed": len(failures), "price_rows": rows, "failures": failures,
                "split_status": "provider_unsupported", "requests": self.client.requests}

    def fx(self, *, retrieved_at: datetime) -> dict:
        start, end = retrieved_at.date() - timedelta(days=TEN_YEARS_DAYS), retrieved_at.date()
        observations, pairs = [], {}
        for currency in ("USD", "CAD", "EUR"):
            symbol = f"{currency}GBP.FOREX"
            try:
                payload = self.client.get(f"eod/{symbol}", {"from": start.isoformat(), "to": end.isoformat(), "period": "d"})
                listing = ListingObservation(symbol, symbol, "FOREX", symbol, "", "GBP", "common_stock", exchange_symbol=symbol)
                parsed = parse_eod(payload, listing, retrieved_at, start, end)
                observations.extend(FXObservation(currency, "GBP", x.trading_date, x.close or Decimal(0), "eodhd", retrieved_at,
                    datetime.combine(x.trading_date, datetime.min.time(), tzinfo=timezone.utc) + timedelta(days=1)) for x in parsed)
                pairs[currency] = "available"
            except Exception: pairs[currency] = "missing_fx"
        if observations: self.market.store(fx=observations)
        return {"command": "ingest-fx", "status": "completed" if observations else "missing_fx",
                "pairs": pairs, "observations": len(observations), "requests": self.client.requests}

    def status(self) -> dict: return {"command": "status", **self.market.coverage()}
    def coverage(self) -> dict: return {"command": "coverage", **self.market.coverage()}

    def _state_schema(self) -> None:
        self.market.initialize()
        with duckdb.connect(str(self.path)) as db: db.execute("""CREATE TABLE IF NOT EXISTS eodhd_ingestion_checkpoints
            (stage VARCHAR, qualified_symbol VARCHAR, status VARCHAR, error_code VARCHAR, updated_at TIMESTAMP,
             PRIMARY KEY(stage, qualified_symbol))""")
    def _completed(self, stage: str) -> set[str]:
        if not self.path.exists(): return set()
        with duckdb.connect(str(self.path), read_only=True) as db:
            if "eodhd_ingestion_checkpoints" not in {x[0] for x in db.execute("SHOW TABLES").fetchall()}: return set()
            return {x[0] for x in db.execute("SELECT qualified_symbol FROM eodhd_ingestion_checkpoints WHERE stage=? AND status='completed'", [stage]).fetchall()}
    def _checkpoint(self, stage: str, symbol: str, status: str, error: str | None) -> None:
        with duckdb.connect(str(self.path)) as db: db.execute("INSERT OR REPLACE INTO eodhd_ingestion_checkpoints VALUES (?,?,?,?,?)",
            [stage, symbol, status, error, utc_naive(datetime.now(timezone.utc))])


def _dividends(payload: Any, listing: ListingObservation, at: datetime, start: date, end: date) -> list[CorporateAction]:
    if not isinstance(payload, list): raise ValueError("invalid dividend response")
    result, seen = [], set()
    for row in payload:
        day = date.fromisoformat(str(row.get("date"))); value = _decimal(row.get("value"))
        if day < start or day > end or day in seen: raise ValueError("invalid or duplicate dividend date")
        seen.add(day); result.append(CorporateAction(listing.qualified_symbol, day, "cash_distribution", value, listing.currency, "eodhd", at))
    return result


def _counts(rows: list[dict]) -> dict[str, int]:
    result: dict[str, int] = {}
    for row in rows: result[row["reason"]] = result.get(row["reason"], 0) + 1
    return dict(sorted(result.items()))
