from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import get_settings
from app.evidence import EvidenceItem, EvidenceRepository
from app.market_data import MarketDataRepository


def client_for(tmp_path: Path, monkeypatch) -> tuple[TestClient, Path]:
    database_path = tmp_path / "evidence-api.duckdb"
    monkeypatch.setenv("SIGNALLENS_DATABASE_PATH", str(database_path))
    get_settings.cache_clear()
    import app.main as main

    main.settings = get_settings()
    return TestClient(main.app), database_path


def test_evidence_api_returns_provenance_and_freshness(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client, database_path = client_for(tmp_path, monkeypatch)
    EvidenceRepository(MarketDataRepository(database_path)).save(
        EvidenceItem(
            evidence_id="sec:amd:test",
            ticker="AMD",
            evidence_type="filing",
            source_name="SEC EDGAR",
            source_url="https://www.sec.gov/example",
            title="AMD 10-Q",
            summary="Official quarterly filing.",
            published_at=datetime(
                2026, 9, 10, 12, tzinfo=timezone.utc
            ),
            retrieved_at=datetime(
                2026, 9, 11, 12, tzinfo=timezone.utc
            ),
        )
    )

    response = client.get(
        "/api/v1/evidence/amd",
        params={
            "evidence_type": "filing",
            "as_of": "2026-09-12T12:00:00Z",
            "max_age_days": 7,
        },
    )
    get_settings.cache_clear()

    assert response.status_code == 200
    body = response.json()
    assert body["ticker"] == "AMD"
    assert body["status"] == "fresh"
    assert body["items"][0]["source_name"] == "SEC EDGAR"
    assert body["items"][0]["source_url"] == "https://www.sec.gov/example"
    assert body["items"][0]["evidence_id"] == "sec:amd:test"


def test_evidence_api_hides_items_not_retrieved_by_as_of_time(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client, database_path = client_for(tmp_path, monkeypatch)
    EvidenceRepository(MarketDataRepository(database_path)).save(
        EvidenceItem(
            evidence_id="sec:amd:future-retrieval",
            ticker="AMD",
            evidence_type="filing",
            source_name="SEC EDGAR",
            source_url="https://www.sec.gov/future",
            title="AMD filing",
            summary="Not known at the requested time.",
            published_at=datetime(
                2026, 9, 10, 12, tzinfo=timezone.utc
            ),
            retrieved_at=datetime(
                2026, 9, 15, 12, tzinfo=timezone.utc
            ),
        )
    )

    response = client.get(
        "/api/v1/evidence/AMD",
        params={"as_of": "2026-09-14T12:00:00Z"},
    )
    get_settings.cache_clear()

    assert response.status_code == 200
    assert response.json()["status"] == "missing"
    assert response.json()["items"] == []


def test_evidence_api_rejects_unknown_ticker(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client, _database_path = client_for(tmp_path, monkeypatch)

    response = client.get("/api/v1/evidence/UNKNOWN")
    get_settings.cache_clear()

    assert response.status_code == 400
    assert response.json()["detail"] == "Ticker is not in the universe"
