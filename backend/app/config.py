from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "SignalLens API"
    environment: str = "development"
    allowed_origins: str = "http://localhost:3000"

    model_config = SettingsConfigDict(
        env_file="../.env",
        env_prefix="SIGNALLENS_",
        extra="ignore",
    )

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.allowed_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()

