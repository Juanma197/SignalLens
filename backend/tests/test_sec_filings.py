from datetime import datetime, timezone

import httpx
import pytest

from app.sec_filings import SECFilingsProvider


def test_sec_provider_normalizes_traceable_point_in_time_filings() -> None:
    observed_user_agents: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        observed_user_agents.append(request.headers["User-Agent"])
        if request.url.path.endswith("company_tickers.json"):
            return httpx.Response(
                200,
                json={
                    "0": {
                        "cik_str": 2488,
                        "ticker": "AMD",
                        "title": "ADVANCED MICRO DEVICES INC",
                    }
                },
            )
        return httpx.Response(
            200,
            json={
                "name": "Advanced Micro Devices, Inc.",
                "filings": {
                    "recent": {
                        "accessionNumber": [
                            "0000002488-26-000010",
                            "0000002488-26-000009",
                            "0000002488-26-000011",
                        ],
                        "form": ["10-Q", "8-K", "10-K"],
                        "filingDate": [
                            "2026-09-19",
                            "2026-09-18",
                            "2026-09-21",
                        ],
                        "acceptanceDateTime": [
                            "2026-09-19T16:00:00Z",
                            "2026-09-18T12:00:00Z",
                            "2026-09-21T12:00:00Z",
                        ],
                        "primaryDocument": [
                            "amd-20260919.htm",
                            "amd-20260918.htm",
                            "amd-20260921.htm",
                        ],
                    }
                },
            },
        )

    client = httpx.Client(
        transport=httpx.MockTransport(handler),
        headers={"User-Agent": "SignalLens research contact@example.com"},
    )
    provider = SECFilingsProvider(
        "SignalLens research contact@example.com",
        client=client,
    )

    downloaded = provider.download(
        ["amd"],
        retrieved_at=datetime(2026, 9, 20, tzinfo=timezone.utc),
    )
    filings = downloaded["AMD"]

    assert len(filings) == 2
    assert filings[0].evidence_id == "sec:0000002488:0000002488-26-000010"
    assert filings[0].evidence_type == "filing"
    assert filings[0].source_name == "SEC EDGAR"
    assert filings[0].source_url == (
        "https://www.sec.gov/Archives/edgar/data/2488/"
        "000000248826000010/amd-20260919.htm"
    )
    assert filings[0].published_at <= filings[0].retrieved_at
    assert all(
        value == "SignalLens research contact@example.com"
        for value in observed_user_agents
    )


def test_sec_provider_rejects_unknown_ticker_mapping() -> None:
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(200, json={})
        ),
        headers={"User-Agent": "SignalLens research contact@example.com"},
    )
    provider = SECFilingsProvider(
        "SignalLens research contact@example.com",
        client=client,
    )

    with pytest.raises(ValueError, match="missing for: UNKNOWN"):
        provider.resolve_ciks(["UNKNOWN"])


def test_sec_provider_requires_identifiable_user_agent() -> None:
    with pytest.raises(ValueError, match="contact email"):
        SECFilingsProvider("SignalLens")


def test_sec_provider_supports_foreign_issuer_forms() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("company_tickers.json"):
            return httpx.Response(
                200,
                json={
                    "0": {
                        "cik_str": 1594805,
                        "ticker": "SHOP",
                        "title": "SHOPIFY INC.",
                    }
                },
            )
        return httpx.Response(
            200,
            json={
                "name": "SHOPIFY INC.",
                "filings": {
                    "recent": {
                        "accessionNumber": [
                            "0001594805-26-000001",
                            "0001594805-26-000002",
                        ],
                        "form": ["20-F", "6-K"],
                        "filingDate": ["2026-02-11", "2026-08-01"],
                        "acceptanceDateTime": [
                            "2026-02-11T12:00:00Z",
                            "2026-08-01T12:00:00Z",
                        ],
                        "primaryDocument": [
                            "shop-20f.htm",
                            "shop-6k.htm",
                        ],
                    }
                },
            },
        )

    client = httpx.Client(
        transport=httpx.MockTransport(handler),
        headers={"User-Agent": "SignalLens research contact@example.com"},
    )
    provider = SECFilingsProvider(
        "SignalLens research contact@example.com",
        client=client,
        request_interval_seconds=0,
    )

    filings = provider.download(
        ["SHOP"],
        retrieved_at=datetime(2026, 9, 20, tzinfo=timezone.utc),
    )["SHOP"]

    assert len(filings) == 2
    assert "20-F" in filings[0].title
    assert "6-K" in filings[1].title
