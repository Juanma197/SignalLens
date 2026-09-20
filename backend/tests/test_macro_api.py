from datetime import date, datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import get_settings
from app.fred_macro import FRED_SERIES, MacroObservation
from app.macro import MacroRepository
from app.market_data import MarketDataRepository


RETRIEVED_AT = datetime(2026, 9, 20, 16, 0, tzinfo=timezone.utc)


def client_for(tmp_path: Path, monkeypatch) -> tuple[TestClient, Path]:
    database_path = tmp_path / "macro-api.duckdb"
    monkeypatch.setenv("SIGNALLENS_DATABASE_PATH", str(database_path))
    get_settings.cache_clear()
    import app.main as main

    main.settings = get_settings()
    return TestClient(main.app), database_path


def make_observation(
    series_id: str,
    observation_date: date,
    value: float,
) -> MacroObservation:
    series = FRED_SERIES[series_id]
    return MacroObservation(
        observation_id=f"fred:{series_id}:{observation_date.isoformat()}",
        series_id=series_id,
        metric=series.metric,
        value=value,
        unit=series.unit,
        frequency=series.frequency,
        observation_date=observation_date,
        available_at=RETRIEVED_AT,
        retrieved_at=RETRIEVED_AT,
        source_name="FRED",
        source_url=f"https://fred.stlouisfed.org/series/{series_id}",
    )


def test_macro_api_returns_complete_fresh_snapshot(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client, database_path = client_for(tmp_path, monkeypatch)
    repository = MacroRepository(MarketDataRepository(database_path))
    repository.save(make_observation("FEDFUNDS", date(2026, 8, 1), 4.05))
    repository.save(make_observation("CPIAUCSL", date(2026, 8, 1), 326.1))
    repository.save(make_observation("UNRATE", date(2026, 8, 1), 4.2))
    repository.save(make_observation("DGS10", date(2026, 9, 17), 3.77))

    response = client.get(
        "/api/v1/macro/latest",
        params={"as_of": "2026-09-21T00:00:00Z"},
    )
    get_settings.cache_clear()

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "complete"
    assert body["missing_series"] == []
    assert body["stale_series"] == []
    assert len(body["observations"]) == 4
    fed_funds = next(
        item for item in body["observations"]
        if item["series_id"] == "FEDFUNDS"
    )
    assert fed_funds["value"] == 4.05
    assert fed_funds["freshness"] == "fresh"
    assert fed_funds["source_url"].endswith("/FEDFUNDS")


def test_macro_api_enforces_retrieval_time(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client, database_path = client_for(tmp_path, monkeypatch)
    MacroRepository(MarketDataRepository(database_path)).save(
        make_observation("FEDFUNDS", date(2026, 8, 1), 4.05)
    )

    response = client.get(
        "/api/v1/macro/latest",
        params={"as_of": "2026-09-19T00:00:00Z"},
    )
    get_settings.cache_clear()

    assert response.status_code == 200
    assert response.json()["status"] == "missing"
    assert response.json()["observations"] == []


def test_macro_api_reports_partial_and_stale_coverage(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client, database_path = client_for(tmp_path, monkeypatch)
    MacroRepository(MarketDataRepository(database_path)).save(
        make_observation("FEDFUNDS", date(2026, 8, 1), 4.05)
    )

    response = client.get(
        "/api/v1/macro/latest",
        params={"as_of": "2026-12-10T00:00:00Z"},
    )
    get_settings.cache_clear()

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "partial"
    assert body["stale_series"] == ["FEDFUNDS"]
    assert set(body["missing_series"]) == {"CPIAUCSL", "UNRATE", "DGS10"}
