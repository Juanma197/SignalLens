from datetime import datetime, timezone

import httpx
import pytest

from app.gdelt_news import GDELTNewsProvider


RETRIEVED_AT = datetime(2026, 9, 20, 16, 0, tzinfo=timezone.utc)


def test_gdelt_provider_returns_traceable_news_metadata() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "articles": [
                    {
                        "url": "https://example.com/amd-launch",
                        "title": "AMD launches a new accelerator",
                        "seendate": "20260920T120000Z",
                        "domain": "example.com",
                        "language": "English",
                        "sourcecountry": "United States",
                    },
                    {
                        "url": "https://example.com/future",
                        "title": "Future-dated item",
                        "seendate": "20260921T120000Z",
                        "domain": "example.com",
                    },
                ]
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = GDELTNewsProvider({"AMD": "Advanced Micro Devices"}, client)
    news = provider.download(
        ["amd"],
        retrieved_at=RETRIEVED_AT,
        limit_per_ticker=3,
    )["AMD"]

    assert len(news) == 1
    assert news[0].evidence_id.startswith("gdelt:")
    assert news[0].evidence_type == "news"
    assert news[0].source_url == "https://example.com/amd-launch"
    assert news[0].published_at <= news[0].retrieved_at
    assert requests[0].url.params["query"] == '"Advanced Micro Devices"'
    assert requests[0].url.params["mode"] == "ArtList"


def test_gdelt_provider_deduplicates_article_urls() -> None:
    article = {
        "url": "https://example.com/story",
        "title": "Company story",
        "seendate": "20260920T120000Z",
        "domain": "example.com",
    }
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                200, json={"articles": [article, article]}
            )
        )
    )
    provider = GDELTNewsProvider({"AAPL": "Apple"}, client)

    news = provider.download(["AAPL"], RETRIEVED_AT)["AAPL"]

    assert len(news) == 1


def test_gdelt_provider_isolates_one_ticker_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "Apple" in request.url.params["query"]:
            return httpx.Response(503, text="unavailable")
        return httpx.Response(200, json={"articles": []})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = GDELTNewsProvider(
        {"AMD": "Advanced Micro Devices", "AAPL": "Apple"},
        client,
        request_interval_seconds=0,
    )

    downloaded = provider.download(["AMD", "AAPL"], RETRIEVED_AT)

    assert downloaded["AMD"] == []
    assert downloaded["AAPL"] == []
    assert "AAPL" in provider.errors
    assert "AMD" not in provider.errors


def test_gdelt_provider_requires_company_mapping() -> None:
    provider = GDELTNewsProvider(
        {"AMD": "Advanced Micro Devices"},
        httpx.Client(
            transport=httpx.MockTransport(
                lambda _request: httpx.Response(200, json={"articles": []})
            )
        ),
    )

    with pytest.raises(ValueError, match="UNKNOWN"):
        provider.download(["UNKNOWN"], RETRIEVED_AT)


def test_gdelt_provider_retries_rate_limit_using_retry_after() -> None:
    attempts = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(
                429,
                headers={"Retry-After": "0"},
                text="rate limited",
            )
        return httpx.Response(200, json={"articles": []})

    provider = GDELTNewsProvider(
        {"AMD": "Advanced Micro Devices"},
        httpx.Client(transport=httpx.MockTransport(handler)),
        max_retries=1,
        retry_delay_seconds=0,
        request_interval_seconds=0,
    )

    assert provider.download(["AMD"], RETRIEVED_AT)["AMD"] == []
    assert attempts == 2
    assert provider.errors == {}
