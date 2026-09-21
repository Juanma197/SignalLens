from datetime import date, datetime, timezone

import httpx

from app.fred_macro import FREDMacroProvider


def test_fred_provider_normalizes_observations_and_keeps_latest_limit() -> None:
    retrieved_at = datetime(2026, 9, 20, 12, 30, tzinfo=timezone.utc)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["id"] == "FEDFUNDS"
        assert request.url.params["cosd"] == "2015-01-01"
        return httpx.Response(
            200,
            text=(
                "observation_date,FEDFUNDS\n"
                "2026-06-01,4.25\n"
                "2026-07-01,.\n"
                "2026-08-01,4.10\n"
                "2026-09-01,4.05\n"
            ),
        )

    provider = FREDMacroProvider(
        httpx.Client(transport=httpx.MockTransport(handler)),
        request_interval_seconds=0,
    )
    result = provider.download(
        ["FEDFUNDS"],
        limit_per_series=2,
        retrieved_at=retrieved_at,
    )

    assert result.errors == {}
    assert [item.observation_date for item in result.observations] == [
        date(2026, 8, 1),
        date(2026, 9, 1),
    ]
    latest = result.observations[-1]
    assert latest.observation_id == "fred:FEDFUNDS:2026-09-01"
    assert latest.metric == "federal_funds_rate"
    assert latest.value == 4.05
    assert latest.unit == "percent"
    assert latest.frequency == "monthly"
    assert latest.source_name == "FRED"
    assert latest.source_url.endswith("/FEDFUNDS")


def test_fred_observations_are_available_only_after_retrieval() -> None:
    retrieved_at = datetime(2026, 9, 20, 14, 0, tzinfo=timezone.utc)

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text="DATE,CPIAUCSL\n2020-01-01,259.127\n",
        )

    provider = FREDMacroProvider(
        httpx.Client(transport=httpx.MockTransport(handler)),
        request_interval_seconds=0,
    )
    result = provider.download(["CPIAUCSL"], retrieved_at=retrieved_at)

    observation = result.observations[0]
    assert observation.observation_date == date(2020, 1, 1)
    assert observation.available_at == datetime(2026, 9, 20, 14, 0)
    assert observation.retrieved_at == observation.available_at


def test_fred_provider_isolates_series_failures() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        series_id = request.url.params["id"]
        if series_id == "UNRATE":
            return httpx.Response(503, text="temporarily unavailable")
        return httpx.Response(
            200,
            text="observation_date,DGS10\n2026-09-18,3.77\n",
        )

    provider = FREDMacroProvider(
        httpx.Client(transport=httpx.MockTransport(handler)),
        request_interval_seconds=0,
    )
    result = provider.download(["UNRATE", "DGS10"])

    assert [item.series_id for item in result.observations] == ["DGS10"]
    assert result.observations[0].value == 3.77
    assert "UNRATE" in result.errors
    assert "503" in result.errors["UNRATE"]


def test_fred_provider_retries_a_read_timeout() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise httpx.ReadTimeout("slow FRED response", request=request)
        return httpx.Response(
            200,
            text="observation_date,FEDFUNDS\n2026-09-01,4.05\n",
        )

    provider = FREDMacroProvider(
        httpx.Client(transport=httpx.MockTransport(handler)),
        request_interval_seconds=0,
        retry_delay_seconds=0,
    )
    result = provider.download(["FEDFUNDS"])

    assert attempts == 2
    assert result.errors == {}
    assert result.observations[0].value == 4.05


def test_fred_provider_uses_authenticated_observations_api() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url).startswith(
            "https://api.stlouisfed.org/fred/series/observations"
        )
        assert request.url.params["api_key"] == "test-key"
        assert request.url.params["series_id"] == "UNRATE"
        assert request.url.params["observation_start"] == "2025-01-01"
        assert request.url.params["file_type"] == "json"
        return httpx.Response(
            200,
            json={
                "observations": [
                    {"date": "2026-07-01", "value": "4.3"},
                    {"date": "2026-08-01", "value": "."},
                    {"date": "2026-09-01", "value": "4.2"},
                ]
            },
        )

    provider = FREDMacroProvider(
        httpx.Client(transport=httpx.MockTransport(handler)),
        api_key="test-key",
        request_interval_seconds=0,
    )
    result = provider.download(
        ["UNRATE"],
        start=date(2025, 1, 1),
    )

    assert result.errors == {}
    assert [item.value for item in result.observations] == [4.3, 4.2]
    assert result.observations[-1].observation_date == date(2026, 9, 1)
