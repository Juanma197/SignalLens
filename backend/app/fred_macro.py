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
    api_url = "https://api.stlouisfed.org/fred/series/observations"

    def __init__(
        self,
        client: httpx.Client | None = None,
        api_key: str | None = None,
        request_interval_seconds: float = 0.12,
        max_attempts: int = 3,
        retry_delay_seconds: float = 1.0,
        sleep: Callable[[float], None] = time.sleep,
    ):
        if request_interval_seconds < 0 or retry_delay_seconds < 0:
            raise ValueError("Request and retry intervals cannot be negative")
        if max_attempts <= 0:
            raise ValueError("max_attempts must be positive")
        self._owns_client = client is None
        self.client = client or httpx.Client(
            timeout=httpx.Timeout(120.0, connect=20.0),
            follow_redirects=True,
            headers={"User-Agent": "SignalLens research macro-data client"},
        )
        self.api_key = api_key.strip() if api_key else None
        self.request_interval_seconds = request_interval_seconds
        self.max_attempts = max_attempts
        self.retry_delay_seconds = retry_delay_seconds
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
                response = self._request(series_id, start)
                if self.api_key:
                    parsed = parse_fred_api(
                        response.json(),
                        FRED_SERIES[series_id],
                        retrieved,
                    )
                else:
                    parsed = parse_fred_csv(
                        response.text,
                        FRED_SERIES[series_id],
                        retrieved,
                    )
                if limit_per_series is not None:
                    parsed = parsed[-limit_per_series:]
                observations.extend(parsed)
            except (
                httpx.HTTPError,
                ValueError,
                csv.Error,
                TypeError,
            ) as exc:
                errors[series_id] = str(exc) or type(exc).__name__

        return MacroDownloadResult(tuple(observations), errors)

    def _request(self, series_id: str, start: date) -> httpx.Response:
        if self.api_key:
            url = self.api_url
            params = {
                "api_key": self.api_key,
                "series_id": series_id,
                "observation_start": start.isoformat(),
                "file_type": "json",
                "sort_order": "asc",
            }
        else:
            url = self.csv_url
            params = {"id": series_id, "cosd": start.isoformat()}

        last_error: httpx.HTTPError | None = None
        for attempt in range(self.max_attempts):
            try:
                response = self.client.get(url, params=params)
                response.raise_for_status()
                return response
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                last_error = exc
            except httpx.HTTPStatusError as exc:
                last_error = exc
                if (
                    exc.response.status_code < 500
                    and exc.response.status_code != 429
                ):
                    raise

            if attempt + 1 < self.max_attempts:
                self.sleep(self.retry_delay_seconds * (attempt + 1))

        assert last_error is not None
        raise last_error


def parse_fred_api(
    payload: object,
    series: MacroSeries,
    retrieved_at: datetime,
) -> list[MacroObservation]:
    if not isinstance(payload, dict) or not isinstance(
        payload.get("observations"), list
    ):
        raise ValueError(f"Unexpected FRED API response for {series.series_id}")

    rows = payload["observations"]
    parsed: list[MacroObservation] = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError(
                f"Unexpected FRED observation for {series.series_id}"
            )
        observation = _parse_observation(
            row.get("date"),
            row.get("value"),
            series,
            retrieved_at,
        )
        if observation is not None:
            parsed.append(observation)
    parsed.sort(key=lambda item: item.observation_date)
    return parsed


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

    parsed: list[MacroObservation] = []
    for row in reader:
        observation = _parse_observation(
            row.get(date_column),
            row.get(series.series_id),
            series,
            retrieved_at,
        )
        if observation is not None:
            parsed.append(observation)
    parsed.sort(key=lambda item: item.observation_date)
    return parsed


def _parse_observation(
    raw_date: object,
    raw_value: object,
    series: MacroSeries,
    retrieved_at: datetime,
) -> MacroObservation | None:
    value_text = str(raw_value or "").strip()
    if value_text in {"", "."}:
        return None
    observation_date = date.fromisoformat(str(raw_date or "").strip())
    retrieved = _as_utc_naive(retrieved_at)
    return MacroObservation(
        observation_id=(
            f"fred:{series.series_id}:{observation_date.isoformat()}"
        ),
        series_id=series.series_id,
        metric=series.metric,
        value=float(value_text),
        unit=series.unit,
        frequency=series.frequency,
        observation_date=observation_date,
        # Latest FRED history may include revisions. Without ALFRED vintage
        # data, an observation is safe only after SignalLens retrieves it.
        available_at=retrieved,
        retrieved_at=retrieved,
        source_name="FRED",
        source_url=f"https://fred.stlouisfed.org/series/{series.series_id}",
    )


def _as_utc_naive(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)
