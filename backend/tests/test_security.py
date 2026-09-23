from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.config import Settings


def test_production_requires_api_token() -> None:
    with pytest.raises(
        ValidationError,
        match="SIGNALLENS_API_TOKEN is required in production",
    ):
        Settings(environment="production", api_token=None, _env_file=None)


def test_production_rejects_short_api_token() -> None:
    with pytest.raises(
        ValidationError,
        match="must contain at least 32 characters",
    ):
        Settings(
            environment="production",
            api_token="too-short",
            _env_file=None,
        )


def test_health_remains_public_when_authentication_is_enabled(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import app.main as main

    monkeypatch.setattr(
        main,
        "settings",
        Settings(
            database_path=tmp_path / "security.duckdb",
            api_token="a" * 32,
            _env_file=None,
        ),
    )

    response = TestClient(main.app).get("/api/v1/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_private_api_requires_matching_bearer_token(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import app.main as main

    token = "correct-private-token-value-123456"
    monkeypatch.setattr(
        main,
        "settings",
        Settings(
            database_path=tmp_path / "security.duckdb",
            api_token=token,
            _env_file=None,
        ),
    )
    client = TestClient(main.app)

    missing = client.get("/api/v1/data/status")
    documentation = client.get("/docs")
    incorrect = client.get(
        "/api/v1/data/status",
        headers={"Authorization": "Bearer incorrect"},
    )
    authorized = client.get(
        "/api/v1/data/status",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert missing.status_code == 401
    assert missing.headers["www-authenticate"] == "Bearer"
    assert documentation.status_code == 401
    assert incorrect.status_code == 401
    assert authorized.status_code == 200
