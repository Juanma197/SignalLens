from datetime import datetime, timezone

import httpx
import pytest

from app.google_news import GoogleNewsRSSProvider


RETRIEVED_AT = datetime(2026, 9, 20, 18, 0, tzinfo=timezone.utc)
RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <item>
      <title>AMD announces a new product</title>
      <link>https://news.google.com/rss/articles/example</link>
      <guid>example-guid</guid>
      <pubDate>Sun, 20 Sep 2026 12:00:00 GMT</pubDate>
      <source url="https://publisher.example">Example Publisher</source>
    </item>
    <item>
      <title>Future story</title>
      <link>https://news.google.com/rss/articles/future</link>
      <pubDate>Mon, 21 Sep 2026 12:00:00 GMT</pubDate>
      <source url="https://publisher.example">Example Publisher</source>
    </item>
  </channel>
</rss>
"""


def test_rss_provider_returns_traceable_point_in_time_metadata() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            content=RSS.encode("utf-8"),
            headers={"Content-Type": "application/rss+xml"},
        )

    provider = GoogleNewsRSSProvider(
        {"AMD": "Advanced Micro Devices"},
        httpx.Client(transport=httpx.MockTransport(handler)),
    )
    items = provider.download(
        ["amd"],
        RETRIEVED_AT,
        limit_per_ticker=3,
        lookback_days=30,
    )["AMD"]

    assert len(items) == 1
    assert items[0].evidence_type == "news"
    assert items[0].evidence_id.startswith("google-news:")
    assert items[0].source_name == "Google News RSS / Example Publisher"
    assert items[0].published_at <= items[0].retrieved_at
    assert requests[0].url.params["q"] == '"Advanced Micro Devices" when:30d'


def test_rss_provider_isolates_ticker_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "Apple" in request.url.params["q"]:
            return httpx.Response(503, text="unavailable")
        return httpx.Response(200, content=b"<rss><channel /></rss>")

    provider = GoogleNewsRSSProvider(
        {"AMD": "Advanced Micro Devices", "AAPL": "Apple"},
        httpx.Client(transport=httpx.MockTransport(handler)),
    )
    downloaded = provider.download(["AMD", "AAPL"], RETRIEVED_AT)

    assert downloaded["AMD"] == []
    assert downloaded["AAPL"] == []
    assert "AMD" not in provider.errors
    assert "AAPL" in provider.errors


def test_rss_provider_rejects_unknown_company_mapping() -> None:
    provider = GoogleNewsRSSProvider(
        {"AMD": "Advanced Micro Devices"},
        httpx.Client(
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(
                    200, content=b"<rss><channel /></rss>"
                )
            )
        ),
    )

    with pytest.raises(ValueError, match="UNKNOWN"):
        provider.download(["UNKNOWN"], RETRIEVED_AT)
