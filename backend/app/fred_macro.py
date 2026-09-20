from __future__ import annotations

import csv
import io
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Callable, Iterable

import httpx


@dataclass(frozen=True)
class MacroSeries:
    series_id: str
    metric: str
    unit: str
    frequency: str


FRED_SERIES: dict[str, MacroSeries] = {
    "FEDFUNDS": MacroSeries(
        "FEDFUNDS", "federal_funds_rate", "percent", "monthly"
    ),
    "CPIAUCSL": MacroSeries(
        "CPIAUCSL", "consumer_price_index", "index_1982_1984_100", "monthly"
    ),
    "UNRATE": MacroSeries(
        "UNRATE", "unemployment_rate", "percent", "monthly"
    ),
    "DGS10": MacroSeries(
        "DGS10", "ten_year_treasury_yield", "percent", "daily"
    ),
}


@dataclass(frozen=True)
class MacroObservation:
    observation_id: str
    series_id: str
    metric: str
    value: float
    unit: str
    frequency: str
    observation_date: date
    available_at: datetime
    retrieved_at: datetime
    source_name: str
    source_url: str


@dataclass(frozen=True)
class MacroDownloadResult:
    observations: tuple[MacroObservation, ...]
    errors: dict[str, str]


class FREDMacroProvider:
    name = "FRED"
    csv_url = "https://fred.stlouisfed.org/graph/fredgraph.csv"

    def __init__(
        self,
        client: httpx.Client | None = None,
        request_interval_seconds: float = 0.12,
        sleep: Callable[[float], None] = time.sleep,
    ):
        if request_interval_seconds < 0:
            raise ValueError("Request interval cannot be negative")
        self._owns_client = client is None
        self.client = client or httpx.Client(
            timeout=30,
            headers={"User-Agent": "SignalLens research macro-data client"},
        )
        self.request_interval_seconds = request_interval_seconds
        self.sleep = sleep

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def __enter__(self) -> FREDMacroProvider:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def download(
        self,
        series_ids: Iterable[str] = FRED_SERIES,
        *,
        start: date = date(2015, 1, 1),
        limit_per_series: int | None = None,
        retrieved_at: datetime | None = None,
    ) -> MacroDownloadResult:
        requested = tuple(dict.fromkeys(series_ids))
        unknown = sorted(set(requested) - FRED_SERIES.keys())
        if unknown:
            raise ValueError(f"Unsupported FRED series: {', '.join(unknown)}")
        if limit_per_series is not None and limit_per_series <= 0:
            raise ValueError("limit_per_series must be positive")

        retrieved = _as_utc_naive(
            retrieved_at or datetime.now(timezone.utc)
        )
        observations: list[MacroObservation] = []
        errors: dict[str, str] = {}

        for index, series_id in enumerate(requested):
            if index:
                self.sleep(self.request_interval_seconds)
            try:
                response = self.client.get(
                    self.csv_url,
                    params={"id": series_id, "cosd": start.isoformat()},
                )
                response.raise_for_status()
                parsed = parse_fred_csv(
                    response.text,
                    FRED_SERIES[series_id],
                    retrieved,
                )
                if limit_per_series is not None:
                    parsed = parsed[-limit_per_series:]
                observations.extend(parsed)
            except (httpx.HTTPError, ValueError, csv.Error) as exc:
                errors[series_id] = str(exc) or type(exc).__name__

        return MacroDownloadResult(tuple(observations), errors)


def parse_fred_csv(
    content: str,
    series: MacroSeries,
    retrieved_at: datetime,
) -> list[MacroObservation]:
    reader = csv.DictReader(io.StringIO(content))
    if reader.fieldnames is None:
        raise ValueError(f"FRED returned no CSV header for {series.series_id}")

    date_column = next(
        (
            column
            for column in ("observation_date", "DATE")
            if column in reader.fieldnames
        ),
        None,
    )
    if date_column is None or series.series_id not in reader.fieldnames:
        raise ValueError(f"Unexpected FRED CSV columns for {series.series_id}")

    retrieved = _as_utc_naive(retrieved_at)
    observations: list[MacroObservation] = []
    for row in reader:
        raw_value = (row.get(series.series_id) or "").strip()
        if raw_value in {"", "."}:
            continue
        observation_date = date.fromisoformat((row[date_column] or "").strip())
        observations.append(
            MacroObservation(
                observation_id=f"fred:{series.series_id}:{observation_date.isoformat()}",
                series_id=series.series_id,
                metric=series.metric,
                value=float(raw_value),
                unit=series.unit,
                frequency=series.frequency,
                observation_date=observation_date,
                # FRED history may be revised. Without vintage data, a value is
                # safe for point-in-time use only after SignalLens retrieved it.
                available_at=retrieved,
                retrieved_at=retrieved,
                source_name="FRED",
                source_url=f"https://fred.stlouisfed.org/series/{series.series_id}",
            )
        )

    observations.sort(key=lambda item: item.observation_date)
    return observations


def _as_utc_naive(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)
