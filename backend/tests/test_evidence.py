from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.evidence import EvidenceItem, EvidenceRepository
from app.market_data import MarketDataRepository


def timestamp(day: int) -> datetime:
    return datetime(2026, 9, day, 12, tzinfo=timezone.utc)


def make_repository(tmp_path: Path) -> EvidenceRepository:
    return EvidenceRepository(
        MarketDataRepository(tmp_path / "evidence.duckdb")
    )


def filing(**overrides) -> EvidenceItem:
    values = {
        "ticker": "amd",
        "evidence_type": "filing",
        "source_name": "SEC",
        "source_url": "https://www.sec.gov/example",
        "title": "Quarterly report",
        "summary": "Revenue and risk disclosures.",
        "published_at": timestamp(10),
        "retrieved_at": timestamp(11),
        "evidence_id": "evidence-1",
    }
    values.update(overrides)
    return EvidenceItem(**values)


def test_evidence_is_immutable_and_preserves_provenance(tmp_path: Path) -> None:
    repository = make_repository(tmp_path)

    saved = repository.save(filing())
    available = repository.point_in_time("AMD", timestamp(12))

    assert saved["ticker"] == "AMD"
    assert len(saved["content_hash"]) == 64
    assert available[0]["source_name"] == "SEC"
    assert available[0]["source_url"] == "https://www.sec.gov/example"
    assert available[0]["retrieved_at"] == timestamp(11).replace(tzinfo=None)

    with pytest.raises(Exception):
        repository.save(filing(summary="Attempted rewrite"))


def test_point_in_time_query_excludes_evidence_not_yet_known(
    tmp_path: Path,
) -> None:
    repository = make_repository(tmp_path)
    repository.save(
        filing(
            published_at=timestamp(10),
            retrieved_at=timestamp(15),
        )
    )

    assert repository.point_in_time("AMD", timestamp(14)) == []
    assert len(repository.point_in_time("AMD", timestamp(15))) == 1


def test_availability_reports_fresh_stale_missing_and_failed(
    tmp_path: Path,
) -> None:
    repository = make_repository(tmp_path)
    repository.save(filing())

    fresh = repository.availability(
        "AMD", "filing", timestamp(12), timedelta(days=7)
    )
    stale = repository.availability(
        "AMD", "filing", timestamp(20), timedelta(days=7)
    )
    missing = repository.availability(
        "AMD", "news", timestamp(20), timedelta(days=1)
    )

    repository.record_fetch(
        ticker="AMD",
        evidence_type="fundamental",
        source_name="Example fundamentals",
        requested_at=timestamp(18),
        completed_at=timestamp(18),
        status="failed",
        error="provider unavailable",
    )
    failed = repository.availability(
        "AMD", "fundamental", timestamp(20), timedelta(days=7)
    )

    assert fresh["status"] == "fresh"
    assert stale["status"] == "stale"
    assert missing["status"] == "missing"
    assert failed["status"] == "failed"
    assert failed["error"] == "provider unavailable"


def test_validation_rejects_future_and_untraceable_evidence(
    tmp_path: Path,
) -> None:
    repository = make_repository(tmp_path)

    with pytest.raises(ValueError, match="before publication"):
        repository.save(
            filing(
                published_at=timestamp(12),
                retrieved_at=timestamp(11),
            )
        )

    with pytest.raises(ValueError, match="HTTP"):
        repository.save(filing(source_url="not-a-url"))
