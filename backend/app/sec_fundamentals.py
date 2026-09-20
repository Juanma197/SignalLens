from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from time import sleep
from typing import Iterable

import httpx

from .sec_filings import SEC_ARCHIVE_URL, SEC_TICKER_URL


SEC_COMPANY_FACTS_URL = (
    "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
)
SUPPORTED_FACT_FORMS = {"10-K", "10-Q", "20-F", "6-K"}

METRIC_CONCEPTS: dict[str, tuple[tuple[str, str], ...]] = {
    "revenue": (
        ("us-gaap", "RevenueFromContractWithCustomerExcludingAssessedTax"),
        ("us-gaap", "Revenues"),
        ("us-gaap", "SalesRevenueNet"),
        ("ifrs-full", "Revenue"),
    ),
    "net_income": (
        ("us-gaap", "NetIncomeLoss"),
        ("ifrs-full", "ProfitLoss"),
    ),
    "eps_diluted": (
        ("us-gaap", "EarningsPerShareDiluted"),
        ("ifrs-full", "DilutedEarningsLossPerShare"),
    ),
    "assets": (
        ("us-gaap", "Assets"),
        ("ifrs-full", "Assets"),
    ),
    "liabilities": (
        ("us-gaap", "Liabilities"),
        ("ifrs-full", "Liabilities"),
    ),
    "cash": (
        ("us-gaap", "CashAndCashEquivalentsAtCarryingValue"),
        ("ifrs-full", "CashAndCashEquivalents"),
    ),
}

PREFERRED_UNITS = {
    "revenue": ("USD",),
    "net_income": ("USD",),
    "eps_diluted": ("USD/shares", "USD / shares"),
    "assets": ("USD",),
    "liabilities": ("USD",),
    "cash": ("USD",),
}


@dataclass(frozen=True)
class FundamentalFact:
    fact_id: str
    ticker: str
    metric: str
    taxonomy: str
    concept: str
    unit: str
    value: float
    period_start: date | None
    period_end: date
    fiscal_year: int | None
    fiscal_period: str | None
    form: str
    accession: str
    filed_at: datetime
    available_at: datetime
    retrieved_at: datetime
    source_url: str


class SECCompanyFactsProvider:
    name = "sec-companyfacts"

    def __init__(
        self,
        user_agent: str,
        client: httpx.Client | None = None,
        request_interval_seconds: float = 0.12,
    ):
        user_agent = user_agent.strip()
        if not user_agent or "@" not in user_agent:
            raise ValueError(
                "SEC user agent must identify the application and include a contact email"
            )
        if request_interval_seconds < 0:
            raise ValueError("Request interval cannot be negative")
        self.request_interval_seconds = request_interval_seconds
        self.errors: dict[str, str] = {}
        self.client = client or httpx.Client(
            headers={
                "User-Agent": user_agent,
                "Accept-Encoding": "gzip, deflate",
            },
            timeout=30,
            follow_redirects=True,
        )
        self._owns_client = client is None

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def __enter__(self) -> "SECCompanyFactsProvider":
        return self

    def __exit__(self, *_args) -> None:
        self.close()

    def resolve_ciks(self, tickers: Iterable[str]) -> dict[str, str]:
        requested = {ticker.strip().upper() for ticker in tickers}
        response = self.client.get(SEC_TICKER_URL)
        response.raise_for_status()
        payload = response.json()
        resolved = {
            str(company["ticker"]).upper(): str(company["cik_str"]).zfill(10)
            for company in payload.values()
            if str(company["ticker"]).upper() in requested
        }
        missing = requested - resolved.keys()
        if missing:
            raise ValueError(
                f"SEC CIK mapping missing for: {', '.join(sorted(missing))}"
            )
        return resolved

    def download(
        self,
        tickers: Iterable[str],
        retrieved_at: datetime,
        limit_periods_per_metric: int = 8,
    ) -> dict[str, list[FundamentalFact]]:
        if limit_periods_per_metric < 1:
            raise ValueError("limit_periods_per_metric must be positive")
        if retrieved_at.tzinfo is None:
            retrieved_at = retrieved_at.replace(tzinfo=timezone.utc)
        else:
            retrieved_at = retrieved_at.astimezone(timezone.utc)

        normalized = tuple(
            dict.fromkeys(ticker.strip().upper() for ticker in tickers)
        )
        ciks = self.resolve_ciks(normalized)
        self.errors = {}
        downloaded: dict[str, list[FundamentalFact]] = {}

        for index, ticker in enumerate(normalized):
            if index > 0 and self.request_interval_seconds:
                sleep(self.request_interval_seconds)
            try:
                response = self.client.get(
                    SEC_COMPANY_FACTS_URL.format(cik=ciks[ticker])
                )
                response.raise_for_status()
                downloaded[ticker] = normalize_company_facts(
                    ticker=ticker,
                    cik=ciks[ticker],
                    payload=response.json(),
                    retrieved_at=retrieved_at,
                    limit_periods_per_metric=limit_periods_per_metric,
                )
            except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
                self.errors[ticker] = str(exc) or type(exc).__name__
                downloaded[ticker] = []
        return downloaded


def normalize_company_facts(
    ticker: str,
    cik: str,
    payload: dict,
    retrieved_at: datetime,
    limit_periods_per_metric: int = 8,
) -> list[FundamentalFact]:
    facts_by_taxonomy = payload.get("facts", {})
    results: list[FundamentalFact] = []

    for metric, candidates in METRIC_CONCEPTS.items():
        normalized_metric: list[FundamentalFact] = []
        for taxonomy, concept in candidates:
            concept_payload = facts_by_taxonomy.get(taxonomy, {}).get(concept)
            if not concept_payload:
                continue
            units = concept_payload.get("units", {})
            unit = next(
                (
                    preferred
                    for preferred in PREFERRED_UNITS[metric]
                    if preferred in units
                ),
                None,
            )
            if unit is None:
                continue

            for item in units[unit]:
                form = item.get("form")
                accession = item.get("accn")
                filed = item.get("filed")
                period_end = item.get("end")
                if (
                    form not in SUPPORTED_FACT_FORMS
                    or not accession
                    or not filed
                    or not period_end
                    or "val" not in item
                ):
                    continue

                filed_at = datetime.combine(
                    date.fromisoformat(filed),
                    time.min,
                    tzinfo=timezone.utc,
                )
                available_at = filed_at + timedelta(days=1)
                if available_at > retrieved_at:
                    continue

                normalized_metric.append(
                    FundamentalFact(
                        fact_id=(
                            f"sec-fact:{cik}:{metric}:"
                            f"{accession}:{period_end}"
                        ),
                        ticker=ticker,
                        metric=metric,
                        taxonomy=taxonomy,
                        concept=concept,
                        unit=unit,
                        value=float(item["val"]),
                        period_start=(
                            date.fromisoformat(item["start"])
                            if item.get("start")
                            else None
                        ),
                        period_end=date.fromisoformat(period_end),
                        fiscal_year=(
                            int(item["fy"])
                            if item.get("fy") is not None
                            else None
                        ),
                        fiscal_period=item.get("fp"),
                        form=form,
                        accession=accession,
                        filed_at=filed_at,
                        available_at=available_at,
                        retrieved_at=retrieved_at,
                        source_url=(
                            f"{SEC_ARCHIVE_URL}/{int(cik)}/"
                            f"{accession.replace('-', '')}"
                        ),
                    )
                )
            if normalized_metric:
                break

        unique: dict[str, FundamentalFact] = {
            fact.fact_id: fact for fact in normalized_metric
        }
        ordered = sorted(
            unique.values(),
            key=lambda fact: (
                fact.available_at,
                fact.period_end,
                fact.accession,
            ),
            reverse=True,
        )
        results.extend(ordered[:limit_periods_per_metric])

    return sorted(
        results,
        key=lambda fact: (fact.available_at, fact.metric),
        reverse=True,
    )
