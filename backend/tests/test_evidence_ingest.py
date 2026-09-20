from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.evidence import EvidenceItem, EvidenceRepository
from app.evidence_ingest import ingest_evidence
from app.market_data import MarketDataRepository


RETRIEVED_AT = datetime(2020, 1, 2, tzinfo=timezone.utc)


class FakeProvider:
    name = "fake-filings"

    def download(self, tickers, retrieved_at, **_options):
        return {
            ticker: [
                EvidenceItem(
                    evidence_id=f"fake:{ticker}:1",
                    ticker=ticker,
                    evidence_type="filing",
                    source_name=self.name,
                    source_url=f"https://example.com/{ticker}/filing",
                    title=f"{ticker} filing",
                    summary="Official test filing.",
                    published_at=datetime(
                        2020, 1, 1, tzinfo=timezone.utc
                    ),
                    retrieved_at=retrieved_at,
                )
            ]
            for ticker in tickers
        }


class FailingProvider:
    name = "failed-filings"

    def download(self, tickers, retrieved_at, **_options):
        raise RuntimeError("upstream unavailable")


def repository(tmp_path: Path) -> EvidenceRepository:
    return EvidenceRepository(
        MarketDataRepository(tmp_path / "evidence.duckdb")
    )


def test_ingestion_is_audited_and_idempotent(tmp_path: Path) -> None:
    evidence = repository(tmp_path)

    first = ingest_evidence(
        evidence,
        FakeProvider(),
        ["amd", "AMD", "AAPL"],
        retrieved_at=RETRIEVED_AT,
    )
    second = ingest_evidence(
        evidence,
        FakeProvider(),
        ["AMD", "AAPL"],
        retrieved_at=RETRIEVED_AT,
    )

    assert first["tickers"] == 2
    assert first["stored"] == 2
    assert second["stored"] == 0
    assert second["duplicates"] == 2
    assert len(
        evidence.point_in_time(
            "AMD",
            RETRIEVED_AT + timedelta(days=1),
        )
    ) == 1

    with evidence.market_data.connect() as connection:
        fetch_count = connection.execute(
            "SELECT COUNT(*) FROM evidence_fetches"
        ).fetchone()[0]
    assert fetch_count == 4


def test_ingestion_records_provider_failure_and_reraises(
    tmp_path: Path,
) -> None:
    evidence = repository(tmp_path)

    with pytest.raises(RuntimeError, match="upstream unavailable"):
        ingest_evidence(
            evidence,
            FailingProvider(),
            ["AMD"],
            retrieved_at=RETRIEVED_AT,
        )

    availability = evidence.availability(
        "AMD",
        "filing",
        datetime.now(timezone.utc),
        timedelta(days=30),
    )
    assert availability["status"] == "failed"
    assert availability["error"] == "upstream unavailable"
