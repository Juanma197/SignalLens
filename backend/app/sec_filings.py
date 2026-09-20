from __future__ import annotations

from datetime import date, datetime, time, timezone
from typing import Iterable

import httpx

from .evidence import EvidenceItem


SEC_TICKER_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
SEC_ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data"
SUPPORTED_FORMS = ("10-K", "10-Q", "8-K")


def _parse_sec_datetime(value: str | None, fallback_date: str) -> datetime:
    if value:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    return datetime.combine(
        date.fromisoformat(fallback_date),
        time.min,
        tzinfo=timezone.utc,
    )


class SECFilingsProvider:
    name = "sec-edgar"

    def __init__(
        self,
        user_agent: str,
        client: httpx.Client | None = None,
    ):
        user_agent = user_agent.strip()
        if not user_agent or "@" not in user_agent:
            raise ValueError(
                "SEC user agent must identify the application and include a contact email"
            )
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

    def __enter__(self) -> "SECFilingsProvider":
        return self

    def __exit__(self, *_args) -> None:
        self.close()

    def resolve_ciks(self, tickers: Iterable[str]) -> dict[str, str]:
        requested = {ticker.strip().upper() for ticker in tickers}
        response = self.client.get(SEC_TICKER_URL)
        response.raise_for_status()
        payload = response.json()

        resolved: dict[str, str] = {}
        for company in payload.values():
            ticker = str(company["ticker"]).upper()
            if ticker in requested:
                resolved[ticker] = str(company["cik_str"]).zfill(10)

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
        forms: tuple[str, ...] = SUPPORTED_FORMS,
        limit_per_ticker: int = 10,
    ) -> dict[str, list[EvidenceItem]]:
        if limit_per_ticker < 1:
            raise ValueError("limit_per_ticker must be positive")
        unsupported = set(forms) - set(SUPPORTED_FORMS)
        if unsupported:
            raise ValueError(
                f"Unsupported SEC forms: {', '.join(sorted(unsupported))}"
            )
        if retrieved_at.tzinfo is None:
            retrieved_at = retrieved_at.replace(tzinfo=timezone.utc)
        else:
            retrieved_at = retrieved_at.astimezone(timezone.utc)

        normalized_tickers = tuple(
            dict.fromkeys(ticker.strip().upper() for ticker in tickers)
        )
        ciks = self.resolve_ciks(normalized_tickers)
        return {
            ticker: self._download_ticker(
                ticker,
                ciks[ticker],
                retrieved_at,
                forms,
                limit_per_ticker,
            )
            for ticker in normalized_tickers
        }

    def _download_ticker(
        self,
        ticker: str,
        cik: str,
        retrieved_at: datetime,
        forms: tuple[str, ...],
        limit: int,
    ) -> list[EvidenceItem]:
        response = self.client.get(SEC_SUBMISSIONS_URL.format(cik=cik))
        response.raise_for_status()
        payload = response.json()
        recent = payload["filings"]["recent"]
        company_name = payload.get("name", ticker)
        items: list[EvidenceItem] = []

        row_count = len(recent["accessionNumber"])
        for index in range(row_count):
            form = recent["form"][index]
            if form not in forms:
                continue

            accession = recent["accessionNumber"][index]
            accession_path = accession.replace("-", "")
            primary_document = recent["primaryDocument"][index]
            filing_date = recent["filingDate"][index]
            acceptance_values = recent.get("acceptanceDateTime", [])
            acceptance = (
                acceptance_values[index]
                if index < len(acceptance_values)
                else None
            )
            published_at = _parse_sec_datetime(acceptance, filing_date)
            if published_at > retrieved_at:
                continue

            source_url = (
                f"{SEC_ARCHIVE_URL}/{int(cik)}/"
                f"{accession_path}/{primary_document}"
            )
            items.append(
                EvidenceItem(
                    evidence_id=f"sec:{cik}:{accession}",
                    ticker=ticker,
                    evidence_type="filing",
                    source_name="SEC EDGAR",
                    source_url=source_url,
                    title=f"{company_name} {form} filed {filing_date}",
                    summary=(
                        f"Official {form} filing accepted by the SEC "
                        f"on {published_at.date().isoformat()}."
                    ),
                    published_at=published_at,
                    retrieved_at=retrieved_at,
                )
            )
            if len(items) == limit:
                break
        return items
