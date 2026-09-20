from pathlib import Path

from fastapi.testclient import TestClient

from app.config import get_settings
from app.market_data import MarketDataRepository
from app.watchlist import WatchlistRepository


def test_watchlist_notes_create_update_and_delete(tmp_path: Path) -> None:
    repository = WatchlistRepository(
        MarketDataRepository(tmp_path / "watchlist.duckdb")
    )

    created = repository.upsert("amd", "Watch momentum after earnings.")
    updated = repository.upsert("AMD", "Review valuation and catalysts.")

    assert created["ticker"] == "AMD"
    assert updated["note"] == "Review valuation and catalysts."
    assert updated["added_at"] == created["added_at"]
    assert repository.list()[0]["ticker"] == "AMD"
    assert repository.remove("AMD") is True
    assert repository.list() == []
    assert repository.remove("AMD") is False


def test_watchlist_api_round_trip(tmp_path: Path, monkeypatch) -> None:
    database_path = tmp_path / "watchlist.duckdb"
    monkeypatch.setenv("SIGNALLENS_DATABASE_PATH", str(database_path))
    get_settings.cache_clear()
    import app.main as main

    main.settings = get_settings()
    client = TestClient(main.app)

    saved = client.put(
        "/api/v1/watchlist/AMD",
        json={"note": "Check whether momentum remains supported."},
    )
    listed = client.get("/api/v1/watchlist")
    deleted = client.delete("/api/v1/watchlist/AMD")
    get_settings.cache_clear()

    assert saved.status_code == 200
    assert saved.json()["company"] == "Advanced Micro Devices"
    assert listed.json()["items"][0]["ticker"] == "AMD"
    assert deleted.json() == {"deleted": True}


def test_watchlist_api_rejects_unknown_ticker(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv(
        "SIGNALLENS_DATABASE_PATH",
        str(tmp_path / "watchlist.duckdb"),
    )
    get_settings.cache_clear()
    import app.main as main

    main.settings = get_settings()
    response = TestClient(main.app).put(
        "/api/v1/watchlist/UNKNOWN",
        json={"note": "Should not be accepted."},
    )
    get_settings.cache_clear()

    assert response.status_code == 400
    assert response.json()["detail"] == "Ticker is not in the universe"
