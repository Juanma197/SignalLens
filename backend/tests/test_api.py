from pathlib import Path

from fastapi.testclient import TestClient

from app.config import get_settings


def test_health() -> None:
    from app.main import app
    response = TestClient(app).get("/api/v1/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_data_status_starts_with_declared_universe(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SIGNALLENS_DATABASE_PATH", str(tmp_path / "test.duckdb"))
    get_settings.cache_clear()
    import app.main as main
    main.settings = get_settings()
    response = TestClient(main.app).get("/api/v1/data/status")
    assert response.status_code == 200
    body = response.json()
    assert body["universe_size"] == 30
    assert body["forward_horizon_trading_days"] == 21
    assert body["row_count"] == 0
    get_settings.cache_clear()


def test_demo_rankings_remain_explicitly_demo() -> None:
    from app.main import app
    body = TestClient(app).get("/api/v1/rankings/demo").json()
    assert body["is_demo"] is True
    assert [item["rank"] for item in body["rankings"]] == [1, 2, 3]
