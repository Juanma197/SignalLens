"""Bounded, read-only capability discovery for the configured EODHD account.

The adapter intentionally returns normalized capability facts, not provider payloads.
It never opens either SignalLens database and never includes the API token in a
result or exception.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable

import httpx


CLASSIFICATIONS = {
    "available", "restricted", "unauthorized", "rate_limited", "unsupported",
    "response_too_large",
}
FREE_TEST_SYMBOL = "AAPL.US"
FREE_TEST_EXCHANGE = "US"


@dataclass(frozen=True)
class ProbeLimits:
    max_requests: int = 6
    max_attempts: int = 2
    timeout_seconds: float = 10.0
    rate_limit_seconds: float = 1.0
    max_response_bytes: int = 5_000_000
    metadata_max_response_bytes: int = 16 * 1024 * 1024

    def __post_init__(self) -> None:
        if not 1 <= self.max_requests <= 8:
            raise ValueError("max_requests must be between 1 and 8")
        if not 1 <= self.max_attempts <= 2:
            raise ValueError("max_attempts must be between 1 and 2")
        if not 0 < self.timeout_seconds <= 30:
            raise ValueError("timeout_seconds must be between 0 and 30")
        if not 0 <= self.rate_limit_seconds <= 10:
            raise ValueError("rate_limit_seconds must be between 0 and 10")
        if not 1 <= self.max_response_bytes <= 5_000_000:
            raise ValueError("max_response_bytes must be between 1 and 5000000")
        if not 1 <= self.metadata_max_response_bytes <= 16 * 1024 * 1024:
            raise ValueError("metadata_max_response_bytes must be between 1 and 16777216")


def _fingerprint(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"exists": False, "bytes": 0, "sha256": None}
    content = path.read_bytes()
    return {"exists": True, "bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()}


def _classification(status: int | None) -> str:
    if status == 401:
        return "unauthorized"
    if status in {402, 403}:
        return "restricted"
    if status == 429:
        return "rate_limited"
    return "unsupported"


def _valid_number(value: object, *, positive: bool = False) -> bool:
    try:
        number = Decimal(str(value))
        return number.is_finite() and (number > 0 if positive else number >= 0)
    except (InvalidOperation, TypeError, ValueError):
        return False


class EODHDCapabilityProbe:
    """Provider-specific transport behind a provider-independent capability report."""

    base_url = "https://eodhd.com/api"

    def __init__(self, token: str, *, limits: ProbeLimits = ProbeLimits(),
                 transport: httpx.BaseTransport | None = None,
                 sleep: Callable[[float], None] = time.sleep):
        if not token:
            raise ValueError("SIGNALLENS_EODHD_API_TOKEN is required")
        self._token = token
        self.limits = limits
        self._transport = transport
        self._sleep = sleep
        self.request_count = 0

    def _get(self, path: str, params: dict[str, str] | None = None, *, metadata: bool = False) -> tuple[str, Any, int | None]:
        attempts = 0
        while attempts < self.limits.max_attempts and self.request_count < self.limits.max_requests:
            if self.request_count and self.limits.rate_limit_seconds:
                self._sleep(self.limits.rate_limit_seconds)
            attempts += 1
            self.request_count += 1
            try:
                with httpx.Client(timeout=self.limits.timeout_seconds, transport=self._transport,
                                  headers={"User-Agent": "SignalLens research capability probe"}) as client:
                    response = client.get(f"{self.base_url}/{path}", params={
                        **(params or {}), "api_token": self._token, "fmt": "json",
                    })
                if response.status_code in {429, 500, 502, 503, 504} and attempts < self.limits.max_attempts:
                    continue
                if response.status_code != 200:
                    return _classification(response.status_code), None, response.status_code
                response_limit = (self.limits.metadata_max_response_bytes if metadata
                                  else self.limits.max_response_bytes)
                if len(response.content) > response_limit:
                    return "response_too_large", None, response.status_code
                try:
                    return "available", response.json(), response.status_code
                except ValueError:
                    return "unsupported", None, response.status_code
            except (httpx.TimeoutException, httpx.NetworkError):
                if attempts >= self.limits.max_attempts:
                    return "unsupported", None, None
        # An endpoint that was skipped because the global budget was exhausted is
        # unsupported by this run; it must not be mislabeled as a provider 429.
        return "unsupported", None, None

    def _prices(self) -> dict[str, Any]:
        classification, payload, status = self._get(
            f"eod/{FREE_TEST_SYMBOL}", {"from": "1900-01-01", "to": date.today().isoformat()},
        )
        rows = payload if isinstance(payload, list) else []
        required = {"date", "open", "high", "low", "close", "volume"}
        valid_rows = []
        adjusted = 0
        for row in rows:
            if not isinstance(row, dict) or not required <= row.keys():
                continue
            try:
                date.fromisoformat(str(row["date"]))
            except ValueError:
                continue
            numeric_ok = all(_valid_number(row[field], positive=field != "volume") for field in required - {"date"})
            ohlc_ok = numeric_ok and Decimal(str(row["high"])) >= max(Decimal(str(row["open"])), Decimal(str(row["close"])), Decimal(str(row["low"])))
            ohlc_ok = ohlc_ok and Decimal(str(row["low"])) <= min(Decimal(str(row["open"])), Decimal(str(row["close"])), Decimal(str(row["high"])))
            if ohlc_ok:
                valid_rows.append(row)
                adjusted += int(_valid_number(row.get("adjusted_close"), positive=True))
        if classification == "available" and not valid_rows:
            classification = "unsupported"
        dates = sorted(str(row["date"]) for row in valid_rows)
        return {
            "endpoint": "end_of_day", "classification": classification, "http_status": status,
            "symbol": FREE_TEST_SYMBOL, "usable_fields": sorted(required | ({"adjusted_close"} if adjusted else set())),
            "valid_rows": len(valid_rows), "invalid_rows": len(rows) - len(valid_rows),
            "adjusted_price_rows": adjusted, "historical_depth": {
                "earliest": dates[0] if dates else None, "latest": dates[-1] if dates else None,
                "calendar_days": (date.fromisoformat(dates[-1]) - date.fromisoformat(dates[0])).days if dates else 0,
            },
        }

    def _exchanges(self) -> dict[str, Any]:
        classification, payload, status = self._get("exchanges-list/", metadata=True)
        rows = payload if isinstance(payload, list) else []
        required = {"Code", "Name", "Country", "Currency"}
        valid_rows = [row for row in rows if isinstance(row, dict) and required <= row.keys()
                      and all(str(row[field]).strip() for field in required)]
        if classification == "available" and (not rows or len(valid_rows) != len(rows)):
            classification = "unsupported"
        fields = sorted(set().union(*(row.keys() for row in valid_rows))) if valid_rows else []
        return {
            "endpoint": "exchange_list", "classification": classification, "http_status": status,
            "records_returned": len(rows), "valid_records": len(valid_rows),
            "usable_fields": fields,
        }

    def _metadata(self) -> dict[str, Any]:
        classification, payload, status = self._get(
            f"exchange-symbol-list/{FREE_TEST_EXCHANGE}", metadata=True,
        )
        rows = payload if isinstance(payload, list) else []
        match = next((row for row in rows if isinstance(row, dict) and row.get("Code") == "AAPL"), None)
        fields = {"Code", "Name", "Country", "Exchange", "Currency", "Type"}
        valid = bool(match and fields <= match.keys() and all(str(match[field]).strip() for field in fields))
        if classification == "available" and not valid:
            classification = "unsupported"
        exchanges = sorted({str(row.get("Exchange")) for row in rows if isinstance(row, dict) and row.get("Exchange")})
        return {
            "endpoint": "symbol_metadata", "classification": classification, "http_status": status,
            "symbol": FREE_TEST_SYMBOL, "metadata_valid": valid,
            "usable_fields": sorted(fields & set(match or {})), "available_exchanges": exchanges,
            "records_returned": len(rows), "symbols_returned": len(rows),
        }

    def _action(self, kind: str) -> dict[str, Any]:
        classification, payload, status = self._get(f"{kind}/{FREE_TEST_SYMBOL}", {"from": "2000-01-01"})
        rows = payload if isinstance(payload, list) else []
        valid = sum(isinstance(row, dict) and "date" in row and "value" in row for row in rows)
        if classification == "available" and rows and not valid:
            classification = "unsupported"
        return {"endpoint": kind, "classification": classification, "http_status": status,
                "symbol": FREE_TEST_SYMBOL, "valid_rows": valid, "usable_fields": ["date", "value"] if valid else []}

    def run(self, *, database_paths: list[Path], include_actions: bool = False) -> dict[str, Any]:
        before = {str(path): _fingerprint(path) for path in database_paths}
        endpoints = [self._prices(), self._exchanges(), self._metadata()]
        if include_actions:
            endpoints.extend([self._action("splits"), self._action("div")])
        after = {str(path): _fingerprint(path) for path in database_paths}
        unchanged = before == after
        return {
            "command": "probe", "provider": "eodhd", "tier": "configured", "mode": "read_only",
            "status": "usable" if endpoints[0]["classification"] == "available" else "unusable",
            "request_count": self.request_count, "request_limit": self.limits.max_requests,
            "test_scope": {"symbols": [FREE_TEST_SYMBOL], "exchanges": [FREE_TEST_EXCHANGE]},
            "endpoints": endpoints,
            "available_exchanges": next((item["available_exchanges"] for item in endpoints if item["endpoint"] == "symbol_metadata"), []),
            "free_tier_limitations": [
                "Capability results apply only to the configured account and tested scope and can change upstream.",
                "Untested exchanges and symbols are not claimed as available.",
                "Splits and dividends are optional and may be restricted without making EOD prices unusable.",
                "No bulk ingestion, ranking, or database mutation was performed.",
            ],
            "database_immutability": {"verified": unchanged, "files": {
                key: {"before": before[key], "after": after[key], "unchanged": before[key] == after[key]}
                for key in before
            }},
        }
