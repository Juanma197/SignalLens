from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Iterable
from uuid import uuid4

from .fred_macro import FREDMacroProvider, MacroObservation
from .market_data import MarketDataRepository


class MacroRepository:
    def __init__(self, market_data: MarketDataRepository):
        self.market_data = market_data
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        with self.market_data.connect() as connection:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS macro_observations (
                    observation_id VARCHAR PRIMARY KEY,
                    series_id VARCHAR NOT NULL,
                    metric VARCHAR NOT NULL,
                    value DOUBLE NOT NULL,
                    unit VARCHAR NOT NULL,
                    frequency VARCHAR NOT NULL,
                    observation_date DATE NOT NULL,
                    available_at TIMESTAMP NOT NULL,
                    retrieved_at TIMESTAMP NOT NULL,
                    source_name VARCHAR NOT NULL,
                    source_url VARCHAR NOT NULL,
                    inserted_at TIMESTAMP NOT NULL
                );
                CREATE TABLE IF NOT EXISTS macro_fetches (
                    fetch_id VARCHAR PRIMARY KEY,
                    series_id VARCHAR NOT NULL,
                    source_name VARCHAR NOT NULL,
                    requested_at TIMESTAMP NOT NULL,
                    completed_at TIMESTAMP NOT NULL,
                    status VARCHAR NOT NULL,
                    observation_count INTEGER NOT NULL,
                    error VARCHAR
                );
            """)

    def contains(self, observation_id: str) -> bool:
        with self.market_data.connect() as connection:
            return connection.execute(
                "SELECT 1 FROM macro_observations WHERE observation_id = ?",
                [observation_id],
            ).fetchone() is not None

    def save(self, observation: MacroObservation) -> bool:
        if self.contains(observation.observation_id):
            return False
        if observation.available_at > observation.retrieved_at:
            raise ValueError(
                "Observation cannot be retrieved before it is available"
            )

        inserted_at = datetime.now(timezone.utc).replace(tzinfo=None)
        with self.market_data.connect() as connection:
            connection.execute(
                "INSERT INTO macro_observations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    observation.observation_id,
                    observation.series_id,
                    observation.metric,
                    observation.value,
                    observation.unit,
                    observation.frequency,
                    observation.observation_date,
                    _utc_naive(observation.available_at),
                    _utc_naive(observation.retrieved_at),
                    observation.source_name,
                    observation.source_url,
                    inserted_at,
                ],
            )
        return True

    def record_fetch(
        self,
        series_id: str,
        source_name: str,
        requested_at: datetime,
        completed_at: datetime,
        status: str,
        observation_count: int = 0,
        error: str | None = None,
    ) -> str:
        if status not in {"completed", "missing", "failed"}:
            raise ValueError(f"Unsupported fetch status: {status}")
        if observation_count < 0:
            raise ValueError("Observation count cannot be negative")
        if status == "failed" and not error:
            raise ValueError("Failed fetch must include an error")
        if status != "completed" and observation_count:
            raise ValueError(
                "Missing or failed fetch cannot contain observations"
            )

        fetch_id = str(uuid4())
        with self.market_data.connect() as connection:
            connection.execute(
                "INSERT INTO macro_fetches VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    fetch_id,
                    series_id,
                    source_name,
                    _utc_naive(requested_at),
                    _utc_naive(completed_at),
                    status,
                    observation_count,
                    error,
                ],
            )
        return fetch_id

    def point_in_time(self, as_of: datetime) -> list[dict]:
        as_of_naive = _utc_naive(as_of)
        with self.market_data.connect() as connection:
            rows = connection.execute(
                """
                SELECT observation_id, series_id, metric, value, unit,
                       frequency, observation_date, available_at,
                       retrieved_at, source_name, source_url
                FROM macro_observations
                WHERE available_at <= ?
                  AND retrieved_at <= ?
                QUALIFY ROW_NUMBER() OVER (
                    PARTITION BY series_id
                    ORDER BY observation_date DESC, retrieved_at DESC
                ) = 1
                ORDER BY series_id
                """,
                [as_of_naive, as_of_naive],
            ).fetchall()

        columns = (
            "observation_id", "series_id", "metric", "value", "unit",
            "frequency", "observation_date", "available_at",
            "retrieved_at", "source_name", "source_url",
        )
        return [dict(zip(columns, row)) for row in rows]


def ingest_macro(
    repository: MacroRepository,
    provider: FREDMacroProvider,
    series_ids: Iterable[str],
    *,
    start: date = date(2015, 1, 1),
    limit_per_series: int | None = None,
    retrieved_at: datetime | None = None,
) -> dict:
    normalized = tuple(dict.fromkeys(series_ids))
    if not normalized:
        raise ValueError("At least one macro series is required")

    requested_at = retrieved_at or datetime.now(timezone.utc)
    if requested_at.tzinfo is None:
        requested_at = requested_at.replace(tzinfo=timezone.utc)

    try:
        result = provider.download(
            normalized,
            start=start,
            limit_per_series=limit_per_series,
            retrieved_at=requested_at,
        )
    except Exception as exc:
        completed_at = max(datetime.now(timezone.utc), requested_at)
        error = str(exc) or type(exc).__name__
        for series_id in normalized:
            repository.record_fetch(
                series_id,
                provider.name,
                requested_at,
                completed_at,
                "failed",
                error=error,
            )
        raise

    completed_at = max(datetime.now(timezone.utc), requested_at)
    by_series: dict[str, list[MacroObservation]] = {
        series_id: [] for series_id in normalized
    }
    for observation in result.observations:
        by_series[observation.series_id].append(observation)

    fetched = stored = duplicates = failed = 0
    for series_id in normalized:
        if series_id in result.errors:
            failed += 1
            repository.record_fetch(
                series_id,
                provider.name,
                requested_at,
                completed_at,
                "failed",
                error=result.errors[series_id],
            )
            continue

        observations = by_series[series_id]
        fetched += len(observations)
        for observation in observations:
            if repository.save(observation):
                stored += 1
            else:
                duplicates += 1
        repository.record_fetch(
            series_id,
            provider.name,
            requested_at,
            completed_at,
            "completed" if observations else "missing",
            observation_count=len(observations),
        )

    return {
        "source": provider.name,
        "series": len(normalized),
        "fetched": fetched,
        "stored": stored,
        "duplicates": duplicates,
        "failed": failed,
        "retrieved_at": requested_at,
    }


def _utc_naive(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)
