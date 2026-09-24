from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "SignalLens API"
    environment: str = "development"
    allowed_origins: str = "http://localhost:3000"
    database_path: Path = Path("data/signallens.duckdb")
    research_database_path: Path = Path("data/research/signallens-research.duckdb")
    persistent_volume_path: Path | None = None
    backup_path: Path = Path("/data/backups")
    backup_retention_count: int = 3
    backup_max_age_hours: int = 48
    sec_user_agent: str = ""
    fred_api_key: str = ""
    api_token: SecretStr | None = None

    model_config = SettingsConfigDict(
        env_file="../.env",
        env_prefix="SIGNALLENS_",
        extra="ignore",
        populate_by_name=True,
    )

    @field_validator("api_token", mode="before")
    @classmethod
    def empty_api_token_is_disabled(cls, value: object) -> object:
        return None if value == "" else value

    @model_validator(mode="after")
    def validate_production_security(self) -> "Settings":
        if self.environment.strip().lower() != "production":
            return self
        if self.api_token is None:
            raise ValueError(
                "SIGNALLENS_API_TOKEN is required in production"
            )
        if len(self.api_token.get_secret_value()) < 32:
            raise ValueError(
                "SIGNALLENS_API_TOKEN must contain at least 32 characters"
            )
        return self

    @property
    def cors_origins(self) -> list[str]:
        return [
            item.strip()
            for item in self.allowed_origins.split(",")
            if item.strip()
        ]


@lru_cache
def get_settings() -> Settings:
    return Settings()
