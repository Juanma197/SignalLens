from __future__ import annotations

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from hashlib import sha256
from typing import Iterable, Mapping
from xml.etree import ElementTree

import httpx

from .evidence import EvidenceItem


GOOGLE_NEWS_RSS_URL = "https://news.google.com/rss/search"


class GoogleNewsRSSProvider:
    """Retrieve public RSS metadata and preserve links to each news item."""

    name = "google-news-rss"
    evidence_type = "news"

    def __init__(
        self,
        companies: Mapping[str, str],
        client: httpx.Client | None = None,
        language: str = "en-US",
        country: str = "US",
    ):
        self.companies = {
            ticker.strip().upper(): company.strip()
            for ticker, company in companies.items()
        }
        self.language = language
        self.country = country
        self.errors: dict[str, str] = {}
        self.client = client or httpx.Client(
            timeout=30,
            follow_redirects=True,
            headers={"User-Agent": "SignalLens research RSS client"},
        )
        self._owns_client = client is None

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def __enter__(self) -> "GoogleNewsRSSProvider":
        return self

    def __exit__(self, *_args) -> None:
        self.close()

    def download(
        self,
        tickers: Iterable[str],
        retrieved_at: datetime,
        limit_per_ticker: int = 10,
        lookback_days: int = 30,
    ) -> dict[str, list[EvidenceItem]]:
        if limit_per_ticker < 1:
            raise ValueError("limit_per_ticker must be positive")
        if lookback_days < 1 or lookback_days > 365:
            raise ValueError("lookback_days must be between 1 and 365")
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
                    lookback_days,
                )
            except (
                httpx.HTTPError,
                ElementTree.ParseError,
                TypeError,
                ValueError,
            ) as exc:
                self.errors[ticker] = str(exc) or type(exc).__name__
                downloaded[ticker] = []
        return downloaded

    def _download_ticker(
        self,
        ticker: str,
        company: str,
        retrieved_at: datetime,
        limit: int,
        lookback_days: int,
    ) -> list[EvidenceItem]:
        response = self.client.get(
            GOOGLE_NEWS_RSS_URL,
            params={
                "q": f'"{company}" when:{lookback_days}d',
                "hl": self.language,
                "gl": self.country,
                "ceid": f"{self.country}:en",
            },
        )
        response.raise_for_status()
        root = ElementTree.fromstring(response.content)
        items: list[EvidenceItem] = []
        seen: set[str] = set()

        for node in root.findall("./channel/item"):
            title = (node.findtext("title") or "").strip()
            link = (node.findtext("link") or "").strip()
            published_text = (node.findtext("pubDate") or "").strip()
            source = node.find("source")
            publisher = (
                source.text.strip()
                if source is not None and source.text
                else "Unknown publisher"
            )
            publisher_url = (
                source.attrib.get("url", "").strip()
                if source is not None
                else ""
            )
            if not title or not link or not published_text or link in seen:
                continue

            published_at = parsedate_to_datetime(published_text)
            if published_at.tzinfo is None:
                published_at = published_at.replace(tzinfo=timezone.utc)
            else:
                published_at = published_at.astimezone(timezone.utc)
            if published_at > retrieved_at:
                continue

            digest = sha256(link.encode("utf-8")).hexdigest()
            source_detail = (
                f" Publisher site: {publisher_url}."
                if publisher_url
                else ""
            )
            items.append(
                EvidenceItem(
                    evidence_id=f"google-news:{digest}",
                    ticker=ticker,
                    evidence_type="news",
                    source_name=f"Google News RSS / {publisher}",
                    source_url=link,
                    title=title,
                    summary=(
                        f"Public RSS metadata for an article from {publisher}."
                        f"{source_detail}"
                    ),
                    published_at=published_at,
                    retrieved_at=retrieved_at,
                )
            )
            seen.add(link)
            if len(items) == limit:
                break
        return items
