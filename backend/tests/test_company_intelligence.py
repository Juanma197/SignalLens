from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.company_intelligence import (
    CompanyObservation, SourceProvenance, assert_files_immutable,
    assess_news, file_fingerprint, normalize_observations, point_in_time,
    require_live_authorization, load_offline_fixture,
)

UTC = timezone.utc
T = lambda value: datetime.fromisoformat(value).replace(tzinfo=UTC)
SOURCE = SourceProvenance("sec-edgar", "SEC", "https://www.sec.gov/x", "regulator",
                          "US government public record", "0001")


def item(identifier="original", **changes):
    values = dict(observation_id=identifier, security_id="US:AMD", family="growth",
                  metric="revenue", kind="numeric", value=10.0, unit="USD",
                  currency="USD", period_start=T("2025-01-01"),
                  period_end=T("2025-03-31"), public_at=T("2025-05-01"),
                  retrieved_at=T("2025-05-02"), source=SOURCE, filing_type="10-Q")
    values.update(changes)
    return CompanyObservation(**values)


def test_filing_boundary_and_future_information_leakage():
    record = item()
    assert point_in_time([record], T("2025-04-30")) == ()
    assert point_in_time([record], T("2025-05-02")) == (record,)
    with pytest.raises(ValueError, match="future-information leakage"):
        item(public_at=T("2025-05-03")).validated()


def test_amendment_is_visible_only_after_its_public_timestamp():
    original = item()
    amended = item("amended", value=9.0, public_at=T("2025-06-01"),
                   retrieved_at=T("2025-06-01"), amendment_number=1,
                   supersedes_id="original", filing_type="10-Q/A")
    assert point_in_time([original, amended], T("2025-05-20"))[0].value == 10
    assert point_in_time([original, amended], T("2025-06-01"))[0].value == 9


def test_missing_availability_and_period_end_substitution_are_rejected():
    with pytest.raises((ValueError, TypeError)):
        item(public_at=None).validated()
    with pytest.raises(ValueError, match="fiscal-period end"):
        item(public_at=T("2025-03-31"), retrieved_at=T("2025-05-02")).validated()


def test_duplicate_filings_are_idempotent_but_conflicts_fail():
    assert normalize_observations([item(), item()]) == (item(),)
    with pytest.raises(ValueError, match="conflicting duplicate"):
        normalize_observations([item(), item(value=11)])


def test_units_currencies_and_source_provenance_are_enforced():
    with pytest.raises(ValueError, match="must agree"):
        item(currency="EUR").validated()
    with pytest.raises(ValueError, match="provenance"):
        item(source=SourceProvenance("", "SEC", "https://www.sec.gov/x", "regulator", "public", "1")).validated()


def test_stale_news_is_deterministic():
    news = item(family="news", metric="catalyst", kind="news", value="Product event",
                unit=None, currency=None, period_start=None, period_end=None)
    assert assess_news(news, T("2025-05-31")) == "usable"
    assert assess_news(news, T("2025-06-01")) == "stale"


def test_live_actions_are_fail_closed_and_database_fingerprint_detects_change(tmp_path: Path):
    with pytest.raises(PermissionError, match="explicit live authorization"):
        require_live_authorization(False, "network request")
    database = tmp_path / "production.duckdb"
    database.write_bytes(b"unchanged")
    before = {database: file_fingerprint(database)}
    assert_files_immutable(before)
    database.write_bytes(b"changed")
    with pytest.raises(RuntimeError, match="immutability violated"):
        assert_files_immutable(before)


def test_offline_fixture_mode_is_explicit_and_deterministic():
    fixture = Path(__file__).parent / "fixtures" / "company_intelligence.json"
    with pytest.raises(PermissionError, match="offline_fixture_mode"):
        load_offline_fixture(fixture, offline_fixture_mode=False)
    first = load_offline_fixture(fixture, offline_fixture_mode=True)
    second = load_offline_fixture(fixture, offline_fixture_mode=True)
    assert first == second
    assert first[0]["public_at"] != first[0]["period_end"]


def test_frozen_horizon_baseline_cannot_authorize_candidates():
    fixture = Path(__file__).parent / "fixtures" / "frozen_horizon_baseline.json"
    baseline = load_offline_fixture(fixture, offline_fixture_mode=True)
    assert baseline["immutable_baseline"] is True
    assert [row["sessions"] for row in baseline["horizons"]] == [21, 63, 126, 252]
    assert {row["decision"] for row in baseline["horizons"]} == {"failed"}
    assert baseline["candidate_generation_authorized"] is False
    assert baseline["ranking_generated"] is False
    assert baseline["candidates"] == []
