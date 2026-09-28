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

    def _fetch(self, symbol: str) -> Any:
        for attempt in range(self.limits.max_attempts):
            if self.request_count >= self.limits.max_requests: return None
            if self.request_count: self._sleep(self.limits.pacing_seconds)
            self.request_count += 1
            try:
                with httpx.Client(transport=self._transport, timeout=self.limits.timeout_seconds,
                                  headers={"User-Agent": "SignalLens bounded fundamentals assessment"}) as client:
                    response = client.get(f"{self.base_url}/fundamentals/{symbol}",
                                          params={"api_token": self._token, "fmt": "json"})
                if response.status_code in {429, 500, 502, 503, 504} and attempt + 1 < self.limits.max_attempts: continue
                if response.status_code != 200 or len(response.content) > self.limits.max_response_bytes: return None
                return response.json()
            except (httpx.HTTPError, ValueError):
                if attempt + 1 == self.limits.max_attempts: return None
        return None

    def run(self, *, database_paths: list[Path], fixtures: dict[str, Any] | None = None) -> dict[str, Any]:
        if fixtures is None and not self._token: raise ValueError("live mode requires an EODHD token")
        before = {str(path): fingerprint(path) for path in database_paths}
        all_rows: list[dict[str, Any]] = []
        per_region = []
        for region, symbol in PILOT_SYMBOLS.items():
            payload = fixtures.get(region) if fixtures is not None else self._fetch(symbol)
            rows = normalize_document(payload, region)
            all_rows.extend(rows)
            periods = sorted({r["fiscal_period_end"] for r in rows if r["fiscal_period_end"]})
            per_region.append({"region": region, "records": len(rows),
                               "records_with_availability_date": sum(bool(r["available_at"]) for r in rows),
                               "date_fields": sorted({r["availability_field"] for r in rows if r["availability_field"]}),
                               "currencies": sorted({str(r["currency"]) for r in rows if r["currency"]}),
                               "earliest_period": periods[0] if periods else None, "latest_period": periods[-1] if periods else None,
                               "field_names": sorted(set().union(*(r["field_names"] for r in rows))) if rows else [],
                               "point_in_time_reconstruction": bool(rows) and all(r["available_at"] for r in rows)})
        after = {str(path): fingerprint(path) for path in database_paths}
        availability = sum(bool(r["available_at"]) for r in all_rows)
        return {"command": "fundamentals-capability", "mode": "fixture" if fixtures is not None else "live",
                "provider": "eodhd", "scope": {"regions": list(PILOT_SYMBOLS), "security_count": 5},
                "request_count": self.request_count, "request_limit": self.limits.max_requests,
                "aggregate": {"records": len(all_rows), "records_with_availability_date": availability,
                              "point_in_time_reconstruction": bool(all_rows) and availability == len(all_rows)},
                "regions": per_region, "feature_classifications": FEATURE_CLASSIFICATIONS,
                "constraints": ["Fiscal-period end is never an availability timestamp.",
                                "Missing public-availability dates make records unusable.",
                                "Current summary ratios are excluded from historical vintages.",
                                "Cross-currency ratios require period-consistent currency and units."],
                "database_immutability": {"verified": before == after, "files": {
                    key: {"before": before[key], "after": after[key], "unchanged": before[key] == after[key]} for key in before}}}
