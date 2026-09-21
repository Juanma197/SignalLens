from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd
from fastapi.testclient import TestClient

from app.config import get_settings
from app.evidence import EvidenceItem, EvidenceRepository
from app.fred_macro import FRED_SERIES, MacroObservation
from app.fundamentals import FundamentalRepository
from app.macro import MacroRepository
from app.market_data import MarketDataRepository
from app.prediction_store import PredictionVintageStore
from app.sec_fundamentals import FundamentalFact
from app.rankings import (
    build_latest_momentum_ranking,
    publish_latest_momentum_ranking,
)


def repository_with_prices(path: Path) -> MarketDataRepository:
    repository = MarketDataRepository(path)
    dates = pd.bdate_range("2026-01-02", periods=140)
    growth = {"AAA": 0.001, "BBB": 0.002, "CCC": -0.0005}
    rows = []
    for ticker, daily_return in growth.items():
        for index, trading_date in enumerate(dates):
            price = 100.0 * (1.0 + daily_return) ** index
            rows.append(
                [
                    ticker,
                    trading_date.date(),
                    price,
                    price,
                    price,
                    price,
                    price,
                    1_000_000,
                    "test",
                    datetime(2026, 9, 20),
                ]
            )
    with repository.connect() as connection:
        connection.executemany(
            """
            INSERT INTO price_bars
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
    return repository


def test_latest_ranking_uses_only_available_126_day_momentum(
    tmp_path: Path,
) -> None:
    repository = repository_with_prices(tmp_path / "rankings.duckdb")

    as_of_date, ranking = build_latest_momentum_ranking(repository)

    assert as_of_date == pd.Timestamp("2026-07-16")
    assert ranking["ticker"].tolist() == ["BBB", "AAA", "CCC"]
    assert ranking["rank"].tolist() == [1, 2, 3]
    assert ranking["score"].tolist() == sorted(
        ranking["score"].tolist(),
        reverse=True,
    )


def test_published_ranking_is_retrievable_as_latest(tmp_path: Path) -> None:
    repository = repository_with_prices(tmp_path / "rankings.duckdb")

    vintage_id = publish_latest_momentum_ranking(repository)
    stored = PredictionVintageStore(repository).get_latest("momentum_126d")

    assert stored["vintage_id"] == vintage_id
    assert stored["metadata"]["is_backtest"] is False
    assert stored["metadata"]["lookback_trading_days"] == 126
    assert [row["ticker"] for row in stored["predictions"]] == [
        "BBB",
        "AAA",
        "CCC",
    ]


def test_latest_ranking_api_returns_published_vintage(
    tmp_path: Path,
    monkeypatch,
) -> None:
    database_path = tmp_path / "rankings.duckdb"
    repository = repository_with_prices(database_path)
    vintage_id = publish_latest_momentum_ranking(repository)

    monkeypatch.setenv("SIGNALLENS_DATABASE_PATH", str(database_path))
    get_settings.cache_clear()
    import app.main as main

    main.settings = get_settings()
    response = TestClient(main.app).get("/api/v1/rankings/latest")
    get_settings.cache_clear()

    assert response.status_code == 200
    body = response.json()
    assert body["vintage_id"] == vintage_id
    assert body["is_demo"] is False
    assert body["strategy"] == "momentum_126d"
    assert [item["ticker"] for item in body["rankings"]] == [
        "BBB",
        "AAA",
        "CCC",
    ]
    assert "not investment advice" in body["disclaimer"].lower()


def test_published_ranking_freezes_available_macro_context(
    tmp_path: Path,
) -> None:
    repository = repository_with_prices(tmp_path / "macro-vintage.duckdb")
    macro = MacroRepository(repository)
    retrieved_at = datetime(2026, 9, 20, 16, 0, tzinfo=timezone.utc)
    series = FRED_SERIES["FEDFUNDS"]
    macro.save(
        MacroObservation(
            observation_id="fred:FEDFUNDS:2026-08-01",
            series_id="FEDFUNDS",
            metric=series.metric,
            value=3.63,
            unit=series.unit,
            frequency=series.frequency,
            observation_date=date(2026, 8, 1),
            available_at=retrieved_at,
            retrieved_at=retrieved_at,
            source_name="FRED",
            source_url="https://fred.stlouisfed.org/series/FEDFUNDS",
        )
    )
    published_at = datetime(2026, 9, 21, 9, 0, tzinfo=timezone.utc)

    vintage_id = publish_latest_momentum_ranking(
        repository,
        published_at=published_at,
    )
    stored_before = PredictionVintageStore(repository).get(vintage_id)
    context_before = stored_before["metadata"]["macro_context"]

    assert context_before["captured_at"] == published_at.isoformat()
    assert context_before["status"] == "partial"
    assert context_before["missing_series"] == ["CPIAUCSL", "UNRATE", "DGS10"]
    assert context_before["observations"][0]["series_id"] == "FEDFUNDS"
    assert context_before["observations"][0]["value"] == 3.63

    later_retrieval = datetime(2026, 10, 20, 16, 0, tzinfo=timezone.utc)
    macro.save(
        MacroObservation(
            observation_id="fred:FEDFUNDS:2026-09-01",
            series_id="FEDFUNDS",
            metric=series.metric,
            value=3.50,
            unit=series.unit,
            frequency=series.frequency,
            observation_date=date(2026, 9, 1),
            available_at=later_retrieval,
            retrieved_at=later_retrieval,
            source_name="FRED",
            source_url="https://fred.stlouisfed.org/series/FEDFUNDS",
        )
    )

    stored_after = PredictionVintageStore(repository).get(vintage_id)
    assert stored_after["metadata"]["macro_context"] == context_before


def test_latest_ranking_api_exposes_frozen_macro_context(
    tmp_path: Path,
    monkeypatch,
) -> None:
    database_path = tmp_path / "ranking-macro-api.duckdb"
    repository = repository_with_prices(database_path)
    retrieved_at = datetime(2026, 9, 20, 16, 0, tzinfo=timezone.utc)
    series = FRED_SERIES["DGS10"]
    MacroRepository(repository).save(
        MacroObservation(
            observation_id="fred:DGS10:2026-09-17",
            series_id="DGS10",
            metric=series.metric,
            value=4.94,
            unit=series.unit,
            frequency=series.frequency,
            observation_date=date(2026, 9, 17),
            available_at=retrieved_at,
            retrieved_at=retrieved_at,
            source_name="FRED",
            source_url="https://fred.stlouisfed.org/series/DGS10",
        )
    )
    vintage_id = publish_latest_momentum_ranking(
        repository,
        published_at=datetime(2026, 9, 21, 9, 0, tzinfo=timezone.utc),
    )

    monkeypatch.setenv("SIGNALLENS_DATABASE_PATH", str(database_path))
    get_settings.cache_clear()
    import app.main as main

    main.settings = get_settings()
    response = TestClient(main.app).get("/api/v1/rankings/latest")
    get_settings.cache_clear()

    assert response.status_code == 200
    body = response.json()
    assert body["vintage_id"] == vintage_id
    assert body["macro_context"]["status"] == "partial"
    assert body["macro_context"]["observations"][0]["series_id"] == "DGS10"
    assert body["macro_context"]["observations"][0]["value"] == 4.94
    assert body["macro_context"]["captured_at"] == "2026-09-21T09:00:00Z"


def test_published_ranking_freezes_point_in_time_fundamentals(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repository = repository_with_prices(tmp_path / "fundamental-vintage.duckdb")
    fundamentals = FundamentalRepository(repository)
    available_at = datetime(2026, 7, 17, 9, 0, tzinfo=timezone.utc)
    fundamentals.save(
        FundamentalFact(
            fact_id="sec-fact:BBB:revenue:q2",
            ticker="BBB",
            metric="revenue",
            taxonomy="us-gaap",
            concept="Revenues",
            unit="USD",
            value=1_500_000_000,
            period_start=date(2026, 4, 1),
            period_end=date(2026, 6, 30),
            fiscal_year=2026,
            fiscal_period="Q2",
            form="10-Q",
            accession="0000000000-26-000001",
            filed_at=datetime(2026, 7, 16, tzinfo=timezone.utc),
            available_at=available_at,
            retrieved_at=available_at,
            source_url="https://www.sec.gov/Archives/example",
        )
    )
    published_at = datetime(2026, 7, 18, 9, 0, tzinfo=timezone.utc)

    vintage_id = publish_latest_momentum_ranking(
        repository,
        published_at=published_at,
    )
    stored_before = PredictionVintageStore(repository).get(vintage_id)
    context_before = stored_before["metadata"]["fundamental_context"]

    assert context_before["captured_at"] == published_at.isoformat()
    assert [item["ticker"] for item in context_before["tickers"]] == [
        "BBB",
        "AAA",
        "CCC",
    ]
    bbb = context_before["tickers"][0]
    assert bbb["status"] == "partial"
    assert bbb["facts"][0]["metric"] == "revenue"
    assert bbb["facts"][0]["value"] == 1_500_000_000
    assert "revenue" not in bbb["missing_metrics"]
    assert context_before["tickers"][1]["status"] == "missing"

    monkeypatch.setenv(
        "SIGNALLENS_DATABASE_PATH",
        str(tmp_path / "fundamental-vintage.duckdb"),
    )
    get_settings.cache_clear()
    import app.main as main

    main.settings = get_settings()
    response = TestClient(main.app).get("/api/v1/rankings/latest")
    get_settings.cache_clear()

    assert response.status_code == 200
    api_context = response.json()["fundamental_context"]
    assert api_context["captured_at"] == "2026-07-18T09:00:00Z"
    assert api_context["tickers"][0]["ticker"] == "BBB"
    assert api_context["tickers"][0]["facts"][0]["metric"] == "revenue"
    assert api_context["tickers"][0]["facts"][0]["value"] == 1_500_000_000

    later = datetime(2026, 8, 20, 9, 0, tzinfo=timezone.utc)
    fundamentals.save(
        FundamentalFact(
            fact_id="sec-fact:BBB:revenue:q3",
            ticker="BBB",
            metric="revenue",
            taxonomy="us-gaap",
            concept="Revenues",
            unit="USD",
            value=1_800_000_000,
            period_start=date(2026, 7, 1),
            period_end=date(2026, 9, 30),
            fiscal_year=2026,
            fiscal_period="Q3",
            form="10-Q",
            accession="0000000000-26-000002",
            filed_at=later,
            available_at=later,
            retrieved_at=later,
            source_url="https://www.sec.gov/Archives/example-later",
        )
    )

    stored_after = PredictionVintageStore(repository).get(vintage_id)
    assert stored_after["metadata"]["fundamental_context"] == context_before


def test_published_ranking_freezes_and_exposes_evidence(
    tmp_path: Path,
    monkeypatch,
) -> None:
    database_path = tmp_path / "evidence-vintage.duckdb"
    repository = repository_with_prices(database_path)
    evidence = EvidenceRepository(repository)
    retrieved_at = datetime(2026, 7, 17, 9, 0, tzinfo=timezone.utc)
    evidence.save(
        EvidenceItem(
            evidence_id="filing:BBB:q2",
            ticker="BBB",
            evidence_type="filing",
            source_name="SEC EDGAR",
            source_url="https://www.sec.gov/Archives/example-filing",
            title="BBB 10-Q filed 2026-07-16",
            summary="Official quarterly filing.",
            published_at=datetime(2026, 7, 16, 16, 0, tzinfo=timezone.utc),
            retrieved_at=retrieved_at,
        )
    )
    evidence.save(
        EvidenceItem(
            evidence_id="news:BBB:launch",
            ticker="BBB",
            evidence_type="news",
            source_name="Google News RSS / example.com",
            source_url="https://example.com/bbb-launch",
            title="BBB launches a new product",
            summary="Public RSS metadata.",
            published_at=datetime(2026, 7, 17, 8, 0, tzinfo=timezone.utc),
            retrieved_at=retrieved_at,
        )
    )
    published_at = datetime(2026, 7, 18, 9, 0, tzinfo=timezone.utc)

    vintage_id = publish_latest_momentum_ranking(
        repository,
        published_at=published_at,
    )
    stored = PredictionVintageStore(repository).get(vintage_id)
    context = stored["metadata"]["evidence_context"]

    assert context["captured_at"] == published_at.isoformat()
    assert [item["ticker"] for item in context["tickers"]] == [
        "BBB",
        "AAA",
        "CCC",
    ]
    bbb = context["tickers"][0]
    assert bbb["filing"]["status"] == "fresh"
    assert bbb["filing"]["max_age_days"] == 90
    assert bbb["filing"]["items"][0]["evidence_id"] == "filing:BBB:q2"
    assert bbb["news"]["status"] == "fresh"
    assert bbb["news"]["max_age_days"] == 30
    assert bbb["news"]["items"][0]["evidence_id"] == "news:BBB:launch"
    assert context["tickers"][1]["filing"]["status"] == "missing"

    monkeypatch.setenv("SIGNALLENS_DATABASE_PATH", str(database_path))
    get_settings.cache_clear()
    import app.main as main

    main.settings = get_settings()
    response = TestClient(main.app).get("/api/v1/rankings/latest")
    get_settings.cache_clear()

    assert response.status_code == 200
    api_context = response.json()["evidence_context"]
    assert api_context["captured_at"] == "2026-07-18T09:00:00Z"
    assert api_context["tickers"][0]["filing"]["items"][0]["title"] == (
        "BBB 10-Q filed 2026-07-16"
    )
    assert api_context["tickers"][0]["news"]["items"][0]["title"] == (
        "BBB launches a new product"
    )
