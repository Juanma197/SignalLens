"""Bounded, read-only point-in-time fundamentals capability assessment.

Provider documents are inspected in memory and only aggregate, allow-listed facts
leave this module.  The normalized records are also useful for proving temporal
semantics with synthetic fixtures; they are not an ingestion format.
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import httpx

PILOT_SYMBOLS = {"US": "AAPL.US", "LSE": "AZN.LSE", "TO": "RY.TO", "XETRA": "SAP.XETRA", "PA": "OR.PA"}
STATEMENTS = ("Income_Statement", "Balance_Sheet", "Cash_Flow")
PERIODICITIES = ("quarterly", "yearly")
DATE_FIELDS = ("filing_date", "filingDate", "accepted_date", "acceptedDate", "reporting_date", "reportedDate")
FEATURE_CLASSIFICATIONS = {
    "revenue_growth": "derivable_with_constraints",
    "earnings_growth": "derivable_with_constraints",
    "operating_margin": "derivable_with_constraints",
    "net_margin": "derivable_with_constraints",
    "return_on_equity": "derivable_with_constraints",
    "return_on_assets": "derivable_with_constraints",
    "return_on_invested_capital": "derivable_with_constraints",
    "free_cash_flow": "derivable_with_constraints",
    "leverage": "derivable_with_constraints",
    "interest_coverage": "derivable_with_constraints",
    "share_count_dilution": "derivable_with_constraints",
    "earnings_yield": "derivable_with_constraints",
    "free_cash_flow_yield": "derivable_with_constraints",
    "book_to_market": "derivable_with_constraints",
    "enterprise_value_measures": "ambiguous_requires_validation",
    "current_summary_ratios": "latest_only_not_backtestable",
}
ALLOWED_FIELDS = {
    "totalRevenue", "netIncome", "operatingIncome", "totalAssets", "totalStockholderEquity",
    "shortLongTermDebtTotal", "longTermDebt", "shortTermDebt", "interestExpense",
    "totalCashFromOperatingActivities", "capitalExpenditures", "commonStockSharesOutstanding",
    "weightedAverageShsOut", "cash", "cashAndEquivalents", "date", "period", "currency_symbol",
    *DATE_FIELDS,
}
SAFE_TOP_LEVEL_FIELDS = {
    "General", "Highlights", "Valuation", "SharesStats", "Technicals",
    "SplitsDividends", "AnalystRatings", "Holders", "InsiderTransactions",
    "ESG_Scores", "outstandingShares", "Earnings", "Financials", "error",
    "errors", "code", "status", "message",
}
INACCESSIBLE_CLASSIFICATIONS = {
    "subscription_restricted", "authentication_failed", "rate_limited",
    "endpoint_not_found", "response_too_large", "malformed_json",
    "transport_error", "unknown_provider_failure",
}


def fingerprint(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"exists": False, "bytes": 0, "sha256": None}
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block); size += len(block)
    return {"exists": True, "bytes": size, "sha256": digest.hexdigest()}


def _date(value: Any) -> str | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return None


def normalize_document(payload: Any, region: str) -> list[dict[str, Any]]:
    """Extract statement rows, never treating fiscal end as availability."""
    if not isinstance(payload, dict):
        return []
    financials = payload.get("Financials")
    if not isinstance(financials, dict):
        return []
    rows: list[dict[str, Any]] = []
    for statement in STATEMENTS:
        groups = financials.get(statement, {})
        if not isinstance(groups, dict):
            continue
        for periodicity in PERIODICITIES:
            records = groups.get(periodicity, {})
            iterable = records.values() if isinstance(records, dict) else records if isinstance(records, list) else []
            for record in iterable:
                if not isinstance(record, dict):
                    continue
                fiscal_end = _date(record.get("date"))
                available_field = next((field for field in DATE_FIELDS if _date(record.get(field))), None)
                values = {key: record[key] for key in sorted(ALLOWED_FIELDS & record.keys())
                          if key not in DATE_FIELDS and key not in {"date", "period", "currency_symbol"}}
                rows.append({"region": region, "statement": statement, "periodicity": periodicity,
                             "fiscal_period_end": fiscal_end,
                             "available_at": _date(record.get(available_field)) if available_field else None,
                             "availability_field": available_field, "currency": record.get("currency_symbol"),
                             "field_names": sorted(values), "values": values})
    return rows


def records_as_of(rows: list[dict[str, Any]], decision_date: str) -> list[dict[str, Any]]:
    """Return the latest publicly available revision per statement/period."""
    boundary = _date(decision_date)
    if boundary is None:
        raise ValueError("decision_date must be ISO formatted")
    visible = [row for row in rows if row["available_at"] and row["available_at"] <= boundary]
    chosen: dict[tuple[str, str, str, str | None], dict[str, Any]] = {}
    for row in sorted(visible, key=lambda item: (item["available_at"], json.dumps(item["values"], sort_keys=True))):
        key = (row["region"], row["statement"], row["periodicity"], row["fiscal_period_end"])
        chosen[key] = row
    return list(chosen.values())


@dataclass(frozen=True)
class CapabilityLimits:
    max_requests: int = 5
    max_attempts: int = 2
    timeout_seconds: float = 10
    pacing_seconds: float = 1
    max_response_bytes: int = 5_000_000

    def __post_init__(self) -> None:
        if not 1 <= self.max_requests <= 5: raise ValueError("max_requests must be between 1 and 5")
        if not 1 <= self.max_attempts <= 2: raise ValueError("max_attempts must be between 1 and 2")
        if not 0 < self.timeout_seconds <= 30: raise ValueError("timeout_seconds must be between 0 and 30")
        if not 0 <= self.pacing_seconds <= 10: raise ValueError("pacing_seconds must be between 0 and 10")
        if not 1 <= self.max_response_bytes <= 5_000_000: raise ValueError("max_response_bytes out of range")


class FundamentalsCapabilityAssessment:
    base_url = "https://eodhd.com/api"
    def __init__(self, token: str | None = None, *, limits: CapabilityLimits = CapabilityLimits(),
                 transport: httpx.BaseTransport | None = None, sleep: Callable[[float], None] = time.sleep):
        self._token, self.limits, self._transport, self._sleep = token, limits, transport, sleep
        self.request_count = 0

    @staticmethod
    def _json_type(payload: Any) -> str:
        if payload is None: return "null"
        if isinstance(payload, bool): return "boolean"
        if isinstance(payload, dict): return "object"
        if isinstance(payload, list): return "array"
        if isinstance(payload, str): return "string"
        if isinstance(payload, (int, float)): return "number"
        return "unknown"

    @staticmethod
    def _safe_fields(payload: Any) -> list[str]:
        if not isinstance(payload, dict): return []
        fields = [key for key in payload if isinstance(key, str) and key in SAFE_TOP_LEVEL_FIELDS]
        return sorted(fields)[:25]

    @staticmethod
    def _safe_content_type(value: str) -> str | None:
        media_type = value.split(";", 1)[0].strip().lower()
        if media_type in {"application/json", "text/json", "text/plain", "text/html",
                          "application/octet-stream"}:
            return media_type
        return "other" if media_type else None

    @staticmethod
    def _provider_error_classification(payload: Any) -> str | None:
        """Classify a provider error internally; no provider value leaves this method."""
        if not isinstance(payload, dict): return None
        error_keys = {"error", "errors", "message"}
        if not error_keys.intersection(payload): return None
        material = " ".join(str(payload.get(key, "")) for key in error_keys).lower()
        code = str(payload.get("code", payload.get("status", ""))).lower()
        if code in {"402", "403", "payment_required", "forbidden"} or any(
            word in material for word in ("subscription", "entitlement", "not available in your plan", "upgrade")
        ):
            return "subscription_restricted"
        if code in {"401", "unauthorized"} or any(word in material for word in ("invalid api", "api key", "token", "unauthor")):
            return "authentication_failed"
        if code == "429" or "rate limit" in material or "too many request" in material:
            return "rate_limited"
        if code == "404" or "not found" in material:
            return "endpoint_not_found"
        return "unknown_provider_failure"

    def _fetch(self, symbol: str) -> tuple[Any, dict[str, Any]]:
        retries = 0
        diagnostic: dict[str, Any] = {"http_status": None, "content_type": None,
            "response_bytes": None, "top_level_json_type": None, "top_level_field_names": [],
            "classification": "transport_error", "retry_count": 0}
        for attempt in range(self.limits.max_attempts):
            if self.request_count >= self.limits.max_requests:
                diagnostic["retry_count"] = retries
                return None, diagnostic
            if self.request_count: self._sleep(self.limits.pacing_seconds)
            self.request_count += 1
            try:
                with httpx.Client(transport=self._transport, timeout=self.limits.timeout_seconds,
                                  headers={"User-Agent": "SignalLens bounded fundamentals assessment"}) as client:
                    response = client.get(f"{self.base_url}/fundamentals/{symbol}",
                                          params={"api_token": self._token, "fmt": "json"})
                diagnostic.update(http_status=response.status_code,
                                  content_type=self._safe_content_type(response.headers.get("content-type", "")),
                                  response_bytes=len(response.content))
                status_class = {401: "authentication_failed", 402: "subscription_restricted",
                                403: "subscription_restricted", 404: "endpoint_not_found",
                                413: "response_too_large", 429: "rate_limited"}.get(response.status_code)
                if len(response.content) > self.limits.max_response_bytes:
                    diagnostic["classification"] = "response_too_large"
                    return None, diagnostic
                if response.status_code in {429, 500, 502, 503, 504} and attempt + 1 < self.limits.max_attempts:
                    retries += 1; continue
                if response.status_code != 200:
                    diagnostic["classification"] = status_class or "unknown_provider_failure"
                    diagnostic["retry_count"] = retries
                    return None, diagnostic
                try:
                    payload = response.json()
                except (json.JSONDecodeError, ValueError):
                    diagnostic["classification"] = "malformed_json"
                    return None, diagnostic
                diagnostic["top_level_json_type"] = self._json_type(payload)
                diagnostic["top_level_field_names"] = self._safe_fields(payload)
                provider_failure = self._provider_error_classification(payload)
                if provider_failure:
                    diagnostic["classification"] = provider_failure
                    diagnostic["retry_count"] = retries
                    return None, diagnostic
                rows = normalize_document(payload, "probe")
                if rows: diagnostic["classification"] = "available"
                elif payload in ({}, []) or payload is None: diagnostic["classification"] = "empty_payload"
                else: diagnostic["classification"] = "schema_mismatch"
                diagnostic["retry_count"] = retries
                return payload, diagnostic
            except httpx.HTTPError:
                if attempt + 1 < self.limits.max_attempts:
                    retries += 1; continue
                diagnostic["retry_count"] = retries
                return None, diagnostic
        diagnostic["retry_count"] = retries
        return None, diagnostic

    def run(self, *, database_paths: list[Path], fixtures: dict[str, Any] | None = None,
            diagnostic_one_request: bool = False) -> dict[str, Any]:
        if fixtures is None and not self._token: raise ValueError("live mode requires an EODHD token")
        if diagnostic_one_request and fixtures is not None: raise ValueError("one-request diagnostic is live-only")
        if diagnostic_one_request and (self.limits.max_requests != 1 or self.limits.max_attempts != 1):
            raise ValueError("one-request diagnostic requires max_requests=1 and max_attempts=1")
        before = {str(path): fingerprint(path) for path in database_paths}
        all_rows: list[dict[str, Any]] = []
        per_region = []
        pilots = [("US", PILOT_SYMBOLS["US"])] if diagnostic_one_request else list(PILOT_SYMBOLS.items())
        for region, symbol in pilots:
            if fixtures is not None:
                payload = fixtures.get(region)
                rows_for_classification = normalize_document(payload, region)
                classification = "available" if rows_for_classification else "empty_payload" if payload in ({}, [], None) else "schema_mismatch"
                diagnostic = {"http_status": None, "content_type": None, "response_bytes": None,
                    "top_level_json_type": self._json_type(payload), "top_level_field_names": self._safe_fields(payload),
                    "classification": classification, "retry_count": 0}
            else:
                payload, diagnostic = self._fetch(symbol)
            rows = normalize_document(payload, region)
            all_rows.extend(rows)
            periods = sorted({r["fiscal_period_end"] for r in rows if r["fiscal_period_end"]})
            per_region.append({"region": region, "records": len(rows),
                               "records_with_availability_date": sum(bool(r["available_at"]) for r in rows),
                               "date_fields": sorted({r["availability_field"] for r in rows if r["availability_field"]}),
                               "currencies": sorted({str(r["currency"]) for r in rows if r["currency"]}),
                               "earliest_period": periods[0] if periods else None, "latest_period": periods[-1] if periods else None,
                               "field_names": sorted(set().union(*(r["field_names"] for r in rows))) if rows else [],
                               "point_in_time_reconstruction": bool(rows) and all(r["available_at"] for r in rows),
                               "request_diagnostic": diagnostic})
        after = {str(path): fingerprint(path) for path in database_paths}
        availability = sum(bool(r["available_at"]) for r in all_rows)
        classifications = [item["request_diagnostic"]["classification"] for item in per_region]
        inaccessible = bool(classifications) and all(item in INACCESSIBLE_CLASSIFICATIONS for item in classifications)
        live_entitlement = "not_assessed" if fixtures is not None else (
            "unavailable" if inaccessible else "confirmed" if "available" in classifications else "indeterminate")
        confirmed_features = {
            name: ("not_assessed" if fixtures is not None else classification if all_rows else "unavailable")
            for name, classification in FEATURE_CLASSIFICATIONS.items()
        }
        return {"command": "fundamentals-capability", "mode": "fixture" if fixtures is not None else "live",
                "status": "provider_access_unavailable" if inaccessible else "completed",
                "provider": "eodhd", "scope": {"regions": [region for region, _ in pilots],
                "security_count": len(pilots), "diagnostic_one_request": diagnostic_one_request},
                "request_count": self.request_count, "request_limit": self.limits.max_requests,
                "aggregate": {"records": len(all_rows), "records_with_availability_date": availability,
                              "point_in_time_reconstruction": bool(all_rows) and availability == len(all_rows)},
                "regions": per_region,
                "theoretical_schema_classification": FEATURE_CLASSIFICATIONS,
                "live_entitlement_classification": live_entitlement,
                "confirmed_live_feature_classification": confirmed_features,
                "constraints": ["Fiscal-period end is never an availability timestamp.",
                                "Missing public-availability dates make records unusable.",
                                "Current summary ratios are excluded from historical vintages.",
                                "Cross-currency ratios require period-consistent currency and units."],
                "database_immutability": {"verified": before == after, "files": {
                    key: {"before": before[key], "after": after[key], "unchanged": before[key] == after[key]} for key in before}}}
