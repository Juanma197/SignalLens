"""Bounded, storage-free SEC EDGAR company-fundamentals pilot."""
from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass
from datetime import datetime, time as datetime_time, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

import duckdb
import httpx

from .active_catalogue import eodhd_ticker, select_active_catalogue

SEC_TICKERS = "https://www.sec.gov/files/company_tickers.json"
SEC_SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik}.json"
SEC_FACTS = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
OFFICIAL_DOMAINS = {"www.sec.gov", "data.sec.gov"}
FORMS = {"10-K", "10-K/A", "10-Q", "10-Q/A"}
MAX_SECURITIES = 3

# Ordered concepts make fallback deterministic.  This is deliberately narrower than
# a production taxonomy resolver.
CONCEPTS: dict[str, tuple[str, ...]] = {
    "revenue": ("RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet"),
    "net_income": ("NetIncomeLoss",),
    "eps": ("EarningsPerShareDiluted", "EarningsPerShareBasic"),
    "operating_income_margin": ("OperatingIncomeLoss",),
    "operating_cash_flow": ("NetCashProvidedByUsedInOperatingActivities",),
    "capital_expenditure_free_cash_flow": ("PaymentsToAcquirePropertyPlantAndEquipment",),
    "assets_return": ("Assets", "StockholdersEquity"),
    "debt_interest_coverage": ("LongTermDebtCurrent", "LongTermDebtNoncurrent", "InterestExpenseNonOperating"),
    "shares_dilution": ("EntityCommonStockSharesOutstanding", "WeightedAverageNumberOfDilutedSharesOutstanding"),
}
ALLOWED_UNITS = {"USD", "USD/shares", "shares"}
REASON_CODES = {"missing_concept", "missing_availability_date", "conflicting_units",
                "malformed_response", "oversized_response", "request_budget_exhausted",
                "ticker_mapping_missing", "transport_failure", "unsupported_form"}
UA_PLACEHOLDERS = {"", "test", "placeholder", "changeme", "signalLens@example.com".lower()}


def valid_user_agent(value: str | None) -> bool:
    value = (value or "").strip()
    return (value.lower() not in UA_PLACEHOLDERS and len(value) >= 10
            and bool(re.search(r"\S+@\S+\.\S+", value)))


def fingerprint(path: Path) -> dict[str, Any]:
    digest = hashlib.sha256(); size = 0
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block); size += len(block)
    return {"bytes": size, "sha256": digest.hexdigest()}


def select_representatives(research_db: Path) -> list[dict[str, str]]:
    """Select up to three US names from the authoritative active catalogue."""
    with duckdb.connect(str(research_db), read_only=True) as db:
        tables = {row[0] for row in db.execute("SHOW TABLES").fetchall()}
        if not {"security_master_retrievals", "security_listings"} <= tables:
            raise ValueError("active research catalogue unavailable")
        catalogue = select_active_catalogue(db)
        if catalogue is None:
            return []
        selected = catalogue.listings.loc[
            catalogue.listings["eligible"].astype(bool)
            & catalogue.listings["region"].eq("US")
        ].head(MAX_SECURITIES)
        ids = selected["security_id"].astype(str).tolist()
        if not ids:
            return []
        metadata = {
            str(row[0]): row[1:]
            for row in db.execute(
                "SELECT security_id,ticker,cik FROM security_listings WHERE retrieval_id=?",
                [catalogue.retrieval_id],
            ).fetchall()
        }
    return [{"security_id": security_id, "ticker": eodhd_ticker(metadata[security_id][0]),
             "catalogue_cik": metadata[security_id][1]} for security_id in ids]


@dataclass(frozen=True)
class Limits:
    max_requests: int = 7
    max_attempts: int = 2
    pacing_seconds: float = 0.12
    timeout_seconds: float = 10
    max_response_bytes: int = 5_000_000

    def __post_init__(self) -> None:
        if not 1 <= self.max_requests <= 7: raise ValueError("max_requests must be between 1 and 7")
        if not 1 <= self.max_attempts <= 2: raise ValueError("max_attempts must be between 1 and 2")
        if not 0.1 <= self.timeout_seconds <= 30: raise ValueError("timeout_seconds out of range")
        if not 0 <= self.pacing_seconds <= 1: raise ValueError("pacing_seconds out of range")
        if not 1 <= self.max_response_bytes <= 5_000_000: raise ValueError("max_response_bytes out of range")


class SECClient:
    def __init__(self, user_agent: str, limits: Limits, transport: httpx.BaseTransport | None = None,
                 sleep: Callable[[float], None] = time.sleep):
        if not valid_user_agent(user_agent): raise ValueError("invalid SEC User-Agent")
        self.limits, self.count, self._sleep = limits, 0, sleep
        self.client = httpx.Client(transport=transport, headers={"User-Agent": user_agent,
            "Accept-Encoding": "gzip, deflate"}, timeout=limits.timeout_seconds, follow_redirects=False)

    def get(self, url: str) -> Any:
        if urlparse(url).hostname not in OFFICIAL_DOMAINS: raise ValueError("non-SEC endpoint prohibited")
        last: Exception | None = None
        for attempt in range(self.limits.max_attempts):
            if self.count >= self.limits.max_requests: raise RuntimeError("request_budget_exhausted")
            if self.count: self._sleep(self.limits.pacing_seconds)
            self.count += 1
            try:
                response = self.client.get(url)
                if len(response.content) > self.limits.max_response_bytes: raise ValueError("oversized_response")
                if response.status_code in {429, 500, 502, 503, 504} and attempt + 1 < self.limits.max_attempts:
                    continue
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict): raise ValueError("malformed_response")
                return payload
            except (httpx.HTTPError, json.JSONDecodeError) as exc:
                last = exc
                if attempt + 1 == self.limits.max_attempts: raise RuntimeError("transport_failure") from None
        raise RuntimeError("transport_failure") from last


def ticker_ciks(payload: dict[str, Any], tickers: list[str]) -> dict[str, str]:
    wanted = set(tickers); result = {}
    for row in payload.values():
        if not isinstance(row, dict): continue
        ticker = str(row.get("ticker", "")).upper()
        if ticker in wanted and isinstance(row.get("cik_str"), int): result[ticker] = str(row["cik_str"]).zfill(10)
    return result


def accession_availability(payload: dict[str, Any]) -> dict[str, tuple[str, str, str]]:
    recent = payload.get("filings", {}).get("recent", {})
    if not isinstance(recent, dict): return {}
    keys = ("accessionNumber", "form", "filingDate", "acceptanceDateTime")
    values = [recent.get(key, []) for key in keys]
    if not all(isinstance(value, list) for value in values): return {}
    result = {}
    for accn, form, filed, accepted in zip(*values):
        if form not in FORMS: continue
        availability = accepted or filed
        if accn and availability: result[str(accn)] = (str(form), str(filed or ""), str(availability))
    return result


def _timestamp(value: str) -> datetime | None:
    try:
        if len(value) == 14 and value.isdigit():
            return datetime.strptime(value, "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
        if len(value) == 8 and value.isdigit():
            return datetime.strptime(value, "%Y%m%d").replace(tzinfo=timezone.utc)
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None: parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError): return None


def normalize_facts(ticker: str, cik: str, facts: dict[str, Any], availability: dict[str, tuple[str, str, str]],
                    retrieved_at: datetime) -> tuple[list[dict[str, Any]], dict[str, set[str]], list[str]]:
    records: dict[tuple[Any, ...], dict[str, Any]] = {}; found = {family: set() for family in CONCEPTS}; reasons = set()
    gaap = facts.get("facts", {}).get("us-gaap", {})
    for family, concepts in CONCEPTS.items():
        for concept in concepts:
            units = gaap.get(concept, {}).get("units", {}) if isinstance(gaap, dict) else {}
            present = set(units) & ALLOWED_UNITS if isinstance(units, dict) else set()
            if len(present) > 1: reasons.add("conflicting_units")
            for unit in sorted(present):
                for item in units[unit]:
                    if not isinstance(item, dict): continue
                    accn = item.get("accn"); form_avail = availability.get(str(accn))
                    if not form_avail:
                        reasons.add("missing_availability_date"); continue
                    form, filed, public_text = form_avail; public_at = _timestamp(public_text)
                    try: period_end = datetime.combine(datetime.fromisoformat(str(item["end"])).date(), datetime_time.min, tzinfo=timezone.utc)
                    except (KeyError, ValueError): continue
                    if public_at is None or public_at == period_end or public_at > retrieved_at:
                        reasons.add("missing_availability_date"); continue
                    try: value = float(item["val"])
                    except (KeyError, TypeError, ValueError): continue
                    amended = form.endswith("/A")
                    key = (family, concept, unit, accn, item.get("start"), item.get("end"), value)
                    records[key] = {"security_ticker": ticker, "cik": cik, "family": family,
                        "taxonomy": "us-gaap", "concept": concept, "unit": unit,
                        "currency": "USD" if unit.startswith("USD") else None, "value": value,
                        "period_start": item.get("start"), "period_end": item["end"],
                        "fiscal_year": item.get("fy"), "fiscal_period": item.get("fp"),
                        "frame": item.get("frame"),
                        "accession": accn, "form": form, "filed_at": filed,
                        "public_at": public_at.isoformat(),
                        "amendment": amended, "retrieved_at": retrieved_at.isoformat(),
                        "source_domain": "data.sec.gov", "source_url": SEC_FACTS.format(cik=cik)}
                    found[family].add(concept)
        if not found[family]: reasons.add("missing_concept")
    ordered = sorted(records.values(), key=lambda x: (x["public_at"], x["accession"], x["family"], x["concept"], x["unit"], x["value"]))
    return ordered, found, sorted(reasons & REASON_CODES)


def sanitized_report(securities: list[dict[str, str]], normalized: list[dict[str, Any]], found: dict[str, set[str]],
                     reasons: list[str], request_count: int, limits: Limits, mode: str,
                     before: dict[str, dict[str, Any]], after: dict[str, dict[str, Any]],
                     catalogue_representatives: int) -> dict[str, Any]:
    forms = sorted({row["form"] for row in normalized}); dates = sorted(row["public_at"][:10] for row in normalized)
    concepts_found = sorted({concept for values in found.values() for concept in values})
    expected = sorted({concept for values in CONCEPTS.values() for concept in values})
    return {"command": "sec-fundamentals-capability", "mode": mode, "status": "completed",
        "provider": "sec-edgar", "securities_attempted": len(securities), "request_count": request_count,
        "evidence_scope": "sanitized_fixture" if mode == "fixture" else "live_catalogue_representatives",
        "catalogue_representatives_selected": catalogue_representatives,
        "fixture_issuer_match_claimed": False if mode == "fixture" else None,
        "request_budget": limits.max_requests, "forms": forms,
        "public_availability_date_range": {"earliest": dates[0] if dates else None, "latest": dates[-1] if dates else None},
        "concepts_found": concepts_found, "concepts_missing": sorted(set(expected)-set(concepts_found)),
        "usable_point_in_time_records": len(normalized),
        "units": sorted({row["unit"] for row in normalized}), "currencies": sorted({row["currency"] for row in normalized if row["currency"]}),
        "amendments": sum(row["amendment"] for row in normalized),
        "revisions": len({(row["family"], row["period_end"]) for row in normalized if row["amendment"]}),
        "feature_family_classifications": {family: ("available" if values else "missing") for family, values in sorted(found.items())},
        "reason_codes": sorted(set(reasons) & REASON_CODES),
        "database_immutability": {"verified": before == after, "files": {path: {"before": state, "after": after[path], "unchanged": state == after[path]} for path, state in before.items()}},
        "constraints": ["No observations were stored.", "Fiscal-period end was never used as public availability.",
                        "No ranking, recommendation, or candidate was produced."]}


def run_assessment(*, research_db: Path, production_db: Path, fixture: dict[str, Any] | None,
                   authorize_live_sec: bool, user_agent: str | None, limits: Limits = Limits(),
                   transport: httpx.BaseTransport | None = None, retrieved_at: datetime | None = None) -> dict[str, Any]:
    if research_db.resolve() == production_db.resolve(): raise ValueError("database paths must be distinct")
    if not research_db.is_file() or not production_db.is_file(): raise ValueError("both database paths must exist")
    # Prove both files are valid DuckDB databases and open them read-only.
    for path in (research_db, production_db):
        with duckdb.connect(str(path), read_only=True) as db: db.execute("SELECT 1").fetchone()
    before = {str(path): fingerprint(path) for path in (research_db, production_db)}
    catalogue_securities = select_representatives(research_db)
    if not catalogue_securities: raise ValueError("no eligible US securities in active research catalogue")
    if fixture is None:
        if authorize_live_sec is not True: raise PermissionError("explicit --authorize-live-sec required")
        if not valid_user_agent(user_agent): raise ValueError("invalid SIGNALLENS_SEC_USER_AGENT (SEC User-Agent)")
        client = SECClient(user_agent or "", limits, transport=transport)
        mapping_payload = client.get(SEC_TICKERS); securities = catalogue_securities
    else:
        client = None; mapping_payload = fixture.get("ticker_mapping", {})
        # Fixture issuers are deliberately independent of the operator catalogue:
        # they prove normalization, never facts about whichever live names were selected.
        fixture_tickers = sorted({eodhd_ticker(row.get("ticker"))
            for row in mapping_payload.values() if isinstance(row, dict) and row.get("ticker")})
        securities = [{"security_id": "fixture-evidence", "ticker": ticker,
                       "catalogue_cik": None} for ticker in fixture_tickers[:MAX_SECURITIES]]
    mapping = ticker_ciks(mapping_payload, [row["ticker"] for row in securities])
    normalized: list[dict[str, Any]] = []; found = {family: set() for family in CONCEPTS}; reasons: list[str] = []
    now = (retrieved_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    for security in securities:
        ticker = security["ticker"]; cik = mapping.get(ticker)
        if not cik: reasons.append("ticker_mapping_missing"); continue
        if fixture is None:
            submissions = client.get(SEC_SUBMISSIONS.format(cik=cik)); facts = client.get(SEC_FACTS.format(cik=cik))
        else:
            submissions = fixture.get("submissions", {}).get(cik, {}); facts = fixture.get("companyfacts", {}).get(cik, {})
        rows, security_found, security_reasons = normalize_facts(ticker, cik, facts, accession_availability(submissions), now)
        normalized.extend(rows); reasons.extend(security_reasons)
        for family in found: found[family].update(security_found[family])
    after = {str(path): fingerprint(path) for path in (research_db, production_db)}
    if before != after: raise RuntimeError("database immutability violated")
    return sanitized_report(securities, normalized, found, reasons, client.count if client else 0,
                            limits, "live" if fixture is None else "fixture", before, after,
                            len(catalogue_securities))
