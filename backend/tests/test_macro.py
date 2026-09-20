from datetime import date, datetime, timezone
from pathlib import Path

from app.fred_macro import MacroDownloadResult, MacroObservation
from app.macro import MacroRepository, ingest_macro
from app.market_data import MarketDataRepository


RETRIEVED_AT = datetime(2026, 9, 20, 16, 0, tzinfo=timezone.utc)


def make_observation(
    observation_date: date,
    value: float,
    *,
    series_id: str = "FEDFUNDS",
    retrieved_at: datetime = RETRIEVED_AT,
) -> MacroObservation:
    return MacroObservation(
        observation_id=f"fred:{series_id}:{observation_date.isoformat()}",
        series_id=series_id,
        metric="federal_funds_rate",
        value=value,
        unit="percent",
        frequency="monthly",
        observation_date=observation_date,
        available_at=retrieved_at,
        retrieved_at=retrieved_at,
        source_name="FRED",
        source_url=f"https://fred.stlouisfed.org/series/{series_id}",
    )


def repository(tmp_path: Path) -> MacroRepository:
    return MacroRepository(
        MarketDataRepository(tmp_path / "macro.duckdb")
    )


def test_macro_store_is_immutable_idempotent_and_point_in_time(
    tmp_path: Path,
) -> None:
    store = repository(tmp_path)
    first_retrieval = datetime(2026, 8, 1, tzinfo=timezone.utc)
    older = make_observation(
        date(2026, 6, 1),
        4.25,
        retrieved_at=first_retrieval,
    )
    latest = make_observation(date(2026, 8, 1), 4.10)

    assert store.save(older) is True
    assert store.save(latest) is True
    assert store.save(latest) is False

    before_retrieval = store.point_in_time(
        datetime(2026, 7, 1, tzinfo=timezone.utc)
    )
    after_first = store.point_in_time(
        datetime(2026, 8, 2, tzinfo=timezone.utc)
    )
    after_latest = store.point_in_time(
        datetime(2026, 9, 21, tzinfo=timezone.utc)
    )

    assert before_retrieval == []
    assert after_first[0]["value"] == 4.25
    assert after_latest[0]["value"] == 4.10


class FakeProvider:
    name = "FRED"

    def __init__(self, fail: bool = False):
        self.fail = fail

    def download(self, series_ids, **_options):
        observations = []
        errors = {}
        for series_id in series_ids:
            if self.fail and series_id == "UNRATE":
                errors[series_id] = "temporarily unavailable"
            else:
                observations.append(
                    make_observation(
                        date(2026, 9, 1),
                        4.05,
                        series_id=series_id,
                    )
                )
        return MacroDownloadResult(tuple(observations), errors)


def test_macro_ingestion_is_audited_and_idempotent(tmp_path: Path) -> None:
    store = repository(tmp_path)

    first = ingest_macro(
        store,
        FakeProvider(),
        ["FEDFUNDS", "FEDFUNDS"],
        retrieved_at=RETRIEVED_AT,
    )
    second = ingest_macro(
        store,
        FakeProvider(),
        ["FEDFUNDS"],
        retrieved_at=RETRIEVED_AT,
    )

    assert first["series"] == 1
    assert first["stored"] == 1
    assert second["stored"] == 0
    assert second["duplicates"] == 1

    with store.market_data.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM macro_fetches"
        ).fetchone()[0] == 2


def test_macro_ingestion_isolates_series_failures(tmp_path: Path) -> None:
    store = repository(tmp_path)

    result = ingest_macro(
        store,
        FakeProvider(fail=True),
        ["FEDFUNDS", "UNRATE"],
        retrieved_at=RETRIEVED_AT,
    )

    assert result["stored"] == 1
    assert result["failed"] == 1

    with store.market_data.connect() as connection:
        status, error = connection.execute(
            """
            SELECT status, error
            FROM macro_fetches
            WHERE series_id = 'UNRATE'
            """
        ).fetchone()
    assert status == "failed"
    assert error == "temporarily unavailable"
