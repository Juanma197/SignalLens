from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from typing import Iterable, Mapping

import httpx

from .evidence import EvidenceItem


GDELT_DOC_URL = "https://api.gdeltproject.org/api/v2/doc/doc"


def _parse_seen_date(value: str) -> datetime:
    value = value.strip()
    for pattern in ("%Y%m%dT%H%M%SZ", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            return datetime.strptime(value, pattern).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


class GDELTNewsProvider:
    """Retrieve source-linked article metadata without storing article bodies."""

    name = "gdelt-doc"
    evidence_type = "news"

    def __init__(
        self,
        companies: Mapping[str, str],
        client: httpx.Client | None = None,
    ):
        self.companies = {
            ticker.strip().upper(): company.strip()
            for ticker, company in companies.items()
        }
        self.errors: dict[str, str] = {}
        self.client = client or httpx.Client(
            timeout=30,
            follow_redirects=True,
            headers={"User-Agent": "SignalLens research news metadata client"},
        )
        self._owns_client = client is None

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def __enter__(self) -> "GDELTNewsProvider":
        return self

    def __exit__(self, *_args) -> None:
        self.close()

    def download(
        self,
        tickers: Iterable[str],
        retrieved_at: datetime,
        limit_per_ticker: int = 10,
        timespan: str = "3months",
    ) -> dict[str, list[EvidenceItem]]:
        if limit_per_ticker < 1 or limit_per_ticker > 250:
            raise ValueError("limit_per_ticker must be between 1 and 250")
        if retrieved_at.tzinfo is None:
            retrieved_at = retrieved_at.replace(tzinfo=timezone.utc)
        else:
            retrieved_at = retrieved_at.astimezone(timezone.utc)

        normalized = tuple(
            dict.fromkeys(ticker.strip().upper() for ticker in tickers)
        )
        unknown = [ticker for ticker in normalized if ticker not in self.companies]
        if unknown:
            raise ValueError(
                f"Company name mapping missing for: {', '.join(sorted(unknown))}"
            )

        self.errors = {}
        downloaded: dict[str, list[EvidenceItem]] = {}
        for ticker in normalized:
            try:
                downloaded[ticker] = self._download_ticker(
                    ticker,
                    self.companies[ticker],
                    retrieved_at,
                    limit_per_ticker,
                    timespan,
                )
            except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
                self.errors[ticker] = str(exc) or type(exc).__name__
                downloaded[ticker] = []
        return downloaded

    def _download_ticker(
        self,
        ticker: str,
        company: str,
        retrieved_at: datetime,
        limit: int,
        timespan: str,
    ) -> list[EvidenceItem]:
        response = self.client.get(
            GDELT_DOC_URL,
            params={
                "query": f'"{company}"',
                "mode": "ArtList",
                "maxrecords": limit,
                "format": "json",
                "sort": "DateDesc",
                "timespan": timespan,
            },
        )
        response.raise_for_status()
        articles = response.json().get("articles", [])
        items: list[EvidenceItem] = []
        seen_urls: set[str] = set()

        for article in articles:
            url = str(article["url"]).strip()
            title = str(article["title"]).strip()
            if not url or not title or url in seen_urls:
                continue
            published_at = _parse_seen_date(str(article["seendate"]))
            if published_at > retrieved_at:
                continue

            domain = str(article.get("domain") or "Unknown publisher").strip()
            language = str(article.get("language") or "unknown").strip()
            country = str(article.get("sourcecountry") or "unknown").strip()
            digest = sha256(url.encode("utf-8")).hexdigest()
            items.append(
                EvidenceItem(
                    evidence_id=f"gdelt:{digest}",
                    ticker=ticker,
                    evidence_type="news",
                    source_name=f"GDELT / {domain}",
                    source_url=url,
                    title=title,
                    summary=(
                        f"Article indexed by GDELT from {domain}; "
                        f"language: {language}; source country: {country}."
                    ),
                    published_at=published_at,
                    retrieved_at=retrieved_at,
                )
            )
            seen_urls.add(url)
            if len(items) == limit:
                break
        return items
