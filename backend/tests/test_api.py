from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health() -> None:
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_demo_rankings_are_explicitly_demo_and_ordered() -> None:
    response = client.get("/api/v1/rankings/demo")
    assert response.status_code == 200
    body = response.json()
    assert body["is_demo"] is True
    assert [item["rank"] for item in body["rankings"]] == [1, 2, 3]
    assert all(0 <= item["score"] <= 100 for item in body["rankings"])

