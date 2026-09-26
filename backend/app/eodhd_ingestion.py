"""Bounded EODHD ingestion into the isolated global-research schema.

This is an operator tool, not a production publisher.  Every network response is
validated before it reaches the existing security-master, price, action, and FX
tables.  The current provider catalogue is explicitly *not* historical membership.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable

import duckdb
import httpx

from .global_market_data import CorporateAction, FXObservation, GlobalMarketDataRepository, PriceObservation
from .global_universe import GlobalUniverseRepository, ListingObservation, normalized_company_name, utc_naive

REGIONS = {"US": ("US", frozenset({"USD"})), "LSE": ("GB", frozenset({"GBP", "GBX"})),
           "TO": ("CA", frozenset({"CAD"})), "XETRA": ("DE", frozenset({"EUR"})),
           "PA": ("FR", frozenset({"EUR"}))}
TYPE_MAP = {"common stock": "common_stock", "ordinary shares": "ordinary_share",
            "ordinary share": "ordinary_share", "common shares": "common_stock"}
EXCLUDED_TYPES = {"etf": "excluded_etf", "fund": "excluded_fund", "index": "excluded_index",
                  "preferred": "excluded_preferred_share", "warrant": "excluded_warrant",
                  "adr": "excluded_adr", "gdr": "excluded_depositary_receipt"}
TEN_YEARS_DAYS = 3653


class BudgetStop(RuntimeError):
    """A global safety bound stopped work; this is not a provider failure."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


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
                 sleep: Callable[[float], None] = time.sleep,
                 monotonic: Callable[[], float] = time.monotonic):
        if not token: raise ValueError("SIGNALLENS_EODHD_API_TOKEN is required")
        self._token, self.limits, self.transport, self.sleep, self.monotonic = token, limits, transport, sleep, monotonic
        self.requests = 0
        self.started = self.monotonic()

    def get(self, endpoint: str, params: dict[str, str] | None = None) -> Any:
        if self.requests >= self.limits.daily_requests: raise BudgetStop("request_budget_exhausted")
        if self.monotonic() - self.started >= self.limits.maximum_runtime_seconds: raise BudgetStop("maximum_runtime_exceeded")
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
    country, domestic_currencies = REGIONS[region]
    accepted, excluded = [], []
    for row in payload:
        if not isinstance(row, dict): raise ValueError("invalid catalogue record")
        code, name = str(row.get("Code", "")).strip(), str(row.get("Name", "")).strip()
        kind, reason = classify_type(str(row.get("Type", "")))
        if not code or not name: reason = "excluded_missing_identity"
        if reason:
            excluded.append({"exchange_qualified_symbol": f"{code}.{region}", "region": region,
                             "currency": str(row.get("Currency") or "UNKNOWN").upper(), "reason": reason})
            continue
        # Missing/unknown currency is not safe to infer (especially on LSE, where
        # GBP and GBX have different units).
        currency = str(row.get("Currency") or "").strip().upper()
        row_country = str(row.get("Country") or "").strip().upper()
        name_lower = name.lower()
        if str(row.get("IsPrimary") or "").strip().lower() in {"false", "0", "no", "secondary"}:
            reason = "excluded_secondary_listing"
        elif currency not in domestic_currencies:
            reason = "excluded_non_domestic_currency" if currency else "excluded_missing_currency"
        elif row_country != country:
            reason = "excluded_foreign_or_secondary_listing"
        elif re.search(r"\b(adr|gdr|cdr|depositary|depository receipt)s?\b", name_lower):
            reason = "excluded_depositary_receipt"
        elif re.search(r"\b(acquisition|blank check|spac)\b", name_lower):
            reason = "excluded_acquisition_vehicle"
        if reason:
            excluded.append({"exchange_qualified_symbol": f"{code}.{region}", "region": region,
                             "currency": currency or "UNKNOWN", "reason": reason})
            continue
        isin = str(row.get("Isin") or row.get("ISIN") or "").strip() or None
        accepted.append(ListingObservation(f"eodhd:{code}.{region}", code, region, name, country,
            currency, kind or "", domicile=row_country, is_primary=True, active=True, isin=isin, exchange_symbol=f"{code}.{region}",
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
        securities = count * len(REGIONS); lower = 5 + securities * 2 + 3
        upper = 5 + securities * 2 * self.client.limits.retries + 3 * self.client.limits.retries
        rows = securities * 2520
        pacing = lower / self.client.limits.requests_per_minute
        timeout_upper = upper * self.client.limits.timeout_seconds / 60
        remaining = self._pending_count() if self.path.exists() else None
        observed_seconds = self._observed_seconds_per_request()
        if remaining is not None:
            lower, upper, securities = remaining * 2, remaining * 2 * self.client.limits.retries, remaining
            rows, pacing, timeout_upper = securities * 2520, lower / self.client.limits.requests_per_minute, upper * self.client.limits.timeout_seconds / 60
        observed_minutes = None if observed_seconds is None else round(lower * observed_seconds / 60, 1)
        runtime_warning = self.client.limits.maximum_runtime_seconds / 60 < max(
            pacing, observed_minutes if observed_minutes is not None else timeout_upper)
        return {"command": "plan", "mode": "read_only", "status": "authorization_required",
                "regions": list(REGIONS), "maximum_securities_per_region": count, "maximum_securities": securities,
                "remaining_securities": remaining, "request_count_bounds": {"lower": lower, "upper": upper},
                "estimated_price_rows": rows, "estimated_storage_bytes": rows * 160,
                "runtime_estimates_minutes": {"pacing_only_lower": round(pacing, 1),
                    "observed_provider_lower": observed_minutes, "provider_timeout_upper": round(timeout_upper, 1),
                    "configured_maximum": round(self.client.limits.maximum_runtime_seconds / 60, 1)},
                "runtime_insufficient_warning": runtime_warning,
                "warnings": ["Current catalogues are not survivorship-free or historical membership.",
                 "Metadata cannot rank liquidity; selection uses a deterministic hash, not an alphabetical or liquidity-ranked universe.",
                 "Research only: invalid for production promotion and historical-membership backtests."]}

    def catalogue(self, *, retrieved_at: datetime, dry_run: bool = False) -> dict:
        all_items, exclusions = [], []
        for region in REGIONS:
            items, rejected = parse_catalogue(self.client.get(f"exchange-symbol-list/{region}"), region)
            all_items.extend(items); exclusions.extend(rejected)
        selected, seen_companies = [], set()
        for region in REGIONS:
            candidates = sorted((x for x in all_items if x.exchange == region),
                key=lambda x: (hashlib.sha256(f"signallens-eodhd-pilot-v2|{x.qualified_symbol}".encode()).hexdigest(), x.qualified_symbol))
            for item in candidates:
                issuer = normalized_company_name(re.sub(r"\b(ADR|GDR|CDR|DEPOSITARY|DEPOSITORY|RECEIPTS?)\b", "", item.company_name.upper()))
                if issuer in seen_companies:
                    exclusions.append({"exchange_qualified_symbol": item.qualified_symbol, "region": region,
                                       "currency": item.currency, "reason": "excluded_cross_region_duplicate_company"})
                elif len([x for x in selected if x.exchange == region]) < self.client.limits.per_region and len(selected) < self.client.limits.total:
                    selected.append(item); seen_companies.add(issuer)
                else: exclusions.append({"exchange_qualified_symbol": item.qualified_symbol, "region": region,
                                         "currency": item.currency, "reason": "pilot_cap"})
        result = {"command": "ingest-catalogue", "mode": "dry_run" if dry_run else "write", "accepted": len(selected),
                  "excluded": len(exclusions), "exclusions_by_reason": _counts(exclusions),
                  "selected_by_region": _item_counts(selected, "exchange"), "selected_by_currency": _item_counts(selected, "currency"),
                  "excluded_by_region": _row_counts(exclusions, "region"), "excluded_by_currency": _row_counts(exclusions, "currency"),
                  "selection_policy": "deterministic_sha256_not_liquidity_ranked", "requests": self.client.requests}
        if not dry_run:
            result.update(self.universe.refresh(CatalogueProvider(selected), retrieved_at=retrieved_at))
        return result

    def _latest_listings(self, at: datetime) -> list[ListingObservation]:
        return self.universe.latest_items(as_of=at)[2]

    def prices(self, *, retrieved_at: datetime, resume: bool = False) -> dict:
        listings = self._latest_listings(retrieved_at)
        start, end = retrieved_at.date() - timedelta(days=TEN_YEARS_DAYS), retrieved_at.date()
        self._state_schema()
        if not resume:
            for item in listings:
                if self._checkpoint_status("prices", item.qualified_symbol) != "completed":
                    self._checkpoint("prices", item.qualified_symbol, "pending", None)
        pending = self._pending("prices")
        targets = [item for item in listings if item.qualified_symbol in pending]
        completed, failures, rows, attempted = 0, [], 0, 0
        request_start, run_started = self.client.requests, self.client.monotonic()
        run_id = hashlib.sha256(f"eodhd|ALL|{utc_naive(retrieved_at).isoformat()}|{resume}|{request_start}|{run_started}".encode()).hexdigest()[:24]
        self._start_run(run_id, retrieved_at)
        stop_reason = None
        checkpoint = None
        for item in targets:
            item_request_start = self.client.requests
            try:
                attempted += 1
                prices = parse_eod(self.client.get(f"eod/{item.qualified_symbol}", {"from": start.isoformat(), "to": end.isoformat(), "period": "d"}), item, retrieved_at, start, end)
                div_payload = self.client.get(f"div/{item.qualified_symbol}", {"from": start.isoformat(), "to": end.isoformat()})
                actions = _dividends(div_payload, item, retrieved_at, start, end)
                self.market.store(prices, actions); rows += len(prices); completed += 1
                self._checkpoint("prices", item.qualified_symbol, "completed", None)
                checkpoint = item.qualified_symbol
            except BudgetStop as exc:
                if self.client.requests == item_request_start:
                    attempted -= 1
                stop_reason = exc.reason
                self._checkpoint("prices", item.qualified_symbol, "pending", None)
                break
            except Exception as exc:
                code = _failure_code(exc)
                failures.append({"symbol": item.qualified_symbol, "stage": "prices", "code": code})
                self._checkpoint("prices", item.qualified_symbol, "failed", code)
        pending_count = self._pending_count()
        status = "partial_checkpointed" if pending_count else ("completed_with_failures" if failures else "completed")
        elapsed = max(0.0, self.client.monotonic() - run_started)
        report = {"attempted": attempted, "completed": completed, "actual_failed": len(failures),
                  "pending": pending_count, "request_count": self.client.requests - request_start,
                  "elapsed_seconds": round(elapsed, 3), "stop_reason": stop_reason,
                  "checkpoint": checkpoint, "failures": failures}
        self._finish_run(run_id, retrieved_at, status, report)
        return {"command": "resume" if resume else "ingest-prices", "status": status,
                **report, "failed": len(failures), "price_rows": rows,
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

    def status(self) -> dict: return {"command": "status", **self._coverage()}
    def coverage(self) -> dict: return {"command": "coverage", **self._coverage()}

    def _coverage(self) -> dict:
        result = self.market.coverage()
        if not self.path.exists(): return result
        with duckdb.connect(str(self.path), read_only=True) as db:
            tables = {x[0] for x in db.execute("SHOW TABLES").fetchall()}
            if "security_listings" in tables:
                retrieval = db.execute("SELECT retrieval_id FROM security_master_retrievals WHERE status='completed' ORDER BY retrieved_at DESC LIMIT 1").fetchone()
                if retrieval:
                    rows = db.execute("SELECT primary_exchange,currency,COUNT(*) FROM security_listings WHERE retrieval_id=? GROUP BY 1,2 ORDER BY 1,2", [retrieval[0]]).fetchall()
                    result["catalogue_selections"] = [{"region": x[0], "currency": x[1], "securities": x[2]} for x in rows]
            if "eodhd_ingestion_checkpoints" in tables:
                states = dict(db.execute("SELECT status,COUNT(*) FROM eodhd_ingestion_checkpoints WHERE stage='prices' GROUP BY status").fetchall())
                result["security_progress"] = {"attempted": states.get("completed", 0) + states.get("failed", 0),
                    "completed": states.get("completed", 0), "pending": states.get("pending", 0), "actual_failed": states.get("failed", 0)}
            if "global_price_observations" in tables:
                depth = db.execute("SELECT qualified_symbol,COUNT(*),MIN(trading_date),MAX(trading_date) FROM global_price_observations GROUP BY 1 ORDER BY 1").fetchall()
                today = datetime.now(timezone.utc).date()
                result["price_history"] = [{"symbol": x[0], "observations": x[1], "first_date": x[2],
                    "latest_date": x[3], "freshness_days": (today - x[3]).days,
                    "freshness": "fresh" if (today - x[3]).days <= 7 else "stale"} for x in depth]
            if "global_fx_observations" in tables:
                present = {x[0] for x in db.execute("SELECT DISTINCT base_currency FROM global_fx_observations").fetchall()}
                result["missing_fx_currencies"] = sorted({"USD", "CAD", "EUR"} - present)
            else: result["missing_fx_currencies"] = ["CAD", "EUR", "USD"]
        result["partial_run"] = bool(result.get("latest_run", {}).get("status") == "partial_checkpointed") if result.get("latest_run") else False
        return result

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
    def _pending(self, stage: str) -> set[str]:
        with duckdb.connect(str(self.path), read_only=True) as db:
            return {x[0] for x in db.execute("SELECT qualified_symbol FROM eodhd_ingestion_checkpoints WHERE stage=? AND status='pending'", [stage]).fetchall()}
    def _pending_count(self) -> int:
        if not self.path.exists(): return 0
        with duckdb.connect(str(self.path), read_only=True) as db:
            tables = {x[0] for x in db.execute("SHOW TABLES").fetchall()}
            if "eodhd_ingestion_checkpoints" not in tables: return 0
            return db.execute("SELECT COUNT(*) FROM eodhd_ingestion_checkpoints WHERE stage='prices' AND status='pending'").fetchone()[0]
    def _observed_seconds_per_request(self) -> float | None:
        if not self.path.exists(): return None
        try:
            with duckdb.connect(str(self.path), read_only=True) as db:
                tables = {x[0] for x in db.execute("SHOW TABLES").fetchall()}
                if "global_ingestion_runs" not in tables: return None
                reports = db.execute("SELECT report_json FROM global_ingestion_runs WHERE provider='eodhd' AND status<>'running' ORDER BY started_at DESC LIMIT 5").fetchall()
            rates = []
            for (payload,) in reports:
                report = json.loads(payload or "{}")
                if report.get("request_count") and report.get("elapsed_seconds"):
                    rates.append(report["elapsed_seconds"] / report["request_count"])
            return None if not rates else sum(rates) / len(rates)
        except (duckdb.Error, json.JSONDecodeError):
            return None
    def _checkpoint_status(self, stage: str, symbol: str) -> str | None:
        with duckdb.connect(str(self.path), read_only=True) as db:
            row = db.execute("SELECT status FROM eodhd_ingestion_checkpoints WHERE stage=? AND qualified_symbol=?", [stage, symbol]).fetchone()
            return None if row is None else row[0]
    def _checkpoint(self, stage: str, symbol: str, status: str, error: str | None) -> None:
        with duckdb.connect(str(self.path)) as db: db.execute("INSERT OR REPLACE INTO eodhd_ingestion_checkpoints VALUES (?,?,?,?,?)",
            [stage, symbol, status, error, utc_naive(datetime.now(timezone.utc))])

    def _start_run(self, run_id: str, at: datetime) -> None:
        with duckdb.connect(str(self.path)) as db:
            db.execute("INSERT OR REPLACE INTO global_ingestion_runs VALUES (?,?,?,?,NULL,'running',0,0,0,NULL,false,'{}')",
                       [run_id, "eodhd", "ALL", utc_naive(at)])

    def _finish_run(self, run_id: str, at: datetime, status: str, report: dict) -> None:
        with duckdb.connect(str(self.path)) as db:
            for failure in report["failures"]:
                db.execute("INSERT OR REPLACE INTO global_ingestion_failures VALUES (?,?,?,?,?,?)",
                    [run_id, failure["symbol"], failure["stage"], failure["code"], "sanitized provider failure", utc_naive(at)])
            db.execute("""UPDATE global_ingestion_runs SET finished_at=?,status=?,attempted=?,completed=?,failed=?,
                checkpoint_symbol=?,report_json=? WHERE run_id=?""", [utc_naive(at), status, report["attempted"],
                report["completed"], report["actual_failed"], report["checkpoint"], json.dumps(report, sort_keys=True), run_id])


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


def _item_counts(items: list[ListingObservation], field: str) -> dict[str, int]:
    result: dict[str, int] = {}
    for item in items:
        value = str(getattr(item, field))
        result[value] = result.get(value, 0) + 1
    return dict(sorted(result.items()))


def _row_counts(rows: list[dict], field: str) -> dict[str, int]:
    result: dict[str, int] = {}
    for row in rows:
        value = str(row.get(field) or "UNKNOWN")
        result[value] = result.get(value, 0) + 1
    return dict(sorted(result.items()))


def _failure_code(exc: Exception) -> str:
    """Return a bounded classification without persisting provider text or URLs."""
    if isinstance(exc, (ValueError, json.JSONDecodeError)):
        return "invalid_provider_payload"
    if isinstance(exc, httpx.HTTPStatusError):
        return "provider_http_error"
    if isinstance(exc, RuntimeError):
        return "provider_request_failed"
    return "unexpected_provider_error"
