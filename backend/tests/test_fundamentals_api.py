from datetime import date, datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import get_settings
from app.fundamentals import FundamentalRepository
from app.market_data import MarketDataRepository
from app.sec_fundamentals import FundamentalFact


def client_for(tmp_path: Path, monkeypatch) -> tuple[TestClient, Path]:
    database_path = tmp_path / "fundamentals-api.duckdb"
    monkeypatch.setenv("SIGNALLENS_DATABASE_PATH", str(database_path))
    get_settings.cache_clear()
    import app.main as main

    main.settings = get_settings()
    return TestClient(main.app), database_path


def make_fact(metric: str, value: float) -> FundamentalFact:
    return FundamentalFact(
        fact_id=f"fact:{metric}",
        ticker="AMD",
        metric=metric,
        taxonomy="us-gaap",
        concept=metric,
        unit="USD/shares" if metric == "eps_diluted" else "USD",
        value=value,
        period_start=date(2026, 3, 29),
        period_end=date(2026, 6, 27),
        fiscal_year=2026,
        fiscal_period="Q2",
        form="10-Q",
        accession="0000002488-26-000123",
        filed_at=datetime(2026, 8, 5, tzinfo=timezone.utc),
        available_at=datetime(2026, 8, 6, tzinfo=timezone.utc),
        retrieved_at=datetime(2026, 9, 20, tzinfo=timezone.utc),
        source_url="https://www.sec.gov/Archives/example",
    )


def test_fundamentals_api_returns_values_provenance_and_missing_metrics(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client, database_path = client_for(tmp_path, monkeypatch)
    repository = FundamentalRepository(
        MarketDataRepository(database_path)
    )
    repository.save(make_fact("revenue", 7_685_000_000))
    repository.save(make_fact("net_income", 872_000_000))

    response = client.get(
        "/api/v1/fundamentals/amd",
        params={"as_of": "2026-09-21T00:00:00Z"},
    )
    get_settings.cache_clear()

    assert response.status_code == 200
    body = response.json()
    assert body["ticker"] == "AMD"
    assert body["company"] == "Advanced Micro Devices"
    assert body["status"] == "partial"
    assert {fact["metric"] for fact in body["facts"]} == {
        "revenue",
        "net_income",
    }
    assert body["facts"][0]["source_url"].startswith(
        "https://www.sec.gov/"
    )
    assert "eps_diluted" in body["missing_metrics"]
    assert "revenue" not in body["missing_metrics"]


def test_fundamentals_api_enforces_retrieval_time(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client, database_path = client_for(tmp_path, monkeypatch)
    FundamentalRepository(
        MarketDataRepository(database_path)
    ).save(make_fact("revenue", 7_685_000_000))

    response = client.get(
        "/api/v1/fundamentals/AMD",
        params={"as_of": "2026-09-19T00:00:00Z"},
    )
    get_settings.cache_clear()

    assert response.status_code == 200
    assert response.json()["status"] == "missing"
    assert response.json()["facts"] == []


def test_fundamentals_api_rejects_unknown_ticker(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client, _database_path = client_for(tmp_path, monkeypatch)

    response = client.get("/api/v1/fundamentals/UNKNOWN")
    get_settings.cache_clear()

    assert response.status_code == 400
    assert response.json()["detail"] == "Ticker is not in the universe"
