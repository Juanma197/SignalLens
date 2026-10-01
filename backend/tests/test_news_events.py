from datetime import datetime, timezone
from pathlib import Path

import duckdb
import pytest

from app.news_events import (EventCategory, IssuerIdentity, NewsEvent, canonical_url,
    capability, deduplicate, extract_sec_events, freshness, match_issuer, near_duplicate_key, sanitize_text,
    sec_readiness, status)


def event(**changes):
    values = dict(event_id="one", issuer=IssuerIdentity("issuer-1", "Issuer", ticker="ABC",
        ticker_valid_from="2020-01-01", ticker_valid_to="2026-12-31"),
        category=EventCategory.GENERAL, headline="Headline", summary="Summary",
        source_name="Source", source_url="https://example.com/a",
        published_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
        retrieved_at=datetime(2026, 9, 2, tzinfo=timezone.utc))
    values.update(changes)
    return NewsEvent(**values)


def test_point_in_time_boundaries_and_future_leakage():
    item = event(occurred_at=datetime(2026, 8, 1, tzinfo=timezone.utc))
    assert not item.available_at(datetime(2026, 9, 1, 12, tzinfo=timezone.utc))
    assert item.available_at(datetime(2026, 9, 2, tzinfo=timezone.utc))
    with pytest.raises(ValueError, match="published_at cannot be after"):
        event(published_at=datetime(2026, 9, 3, tzinfo=timezone.utc))
    with pytest.raises(ValueError, match="required"):
        event(published_at=None)
    with pytest.raises(ValueError, match="timezone-aware"):
        event(retrieved_at=datetime(2026, 9, 2))


def test_url_text_bounds_and_duplicate_groups():
    assert canonical_url("HTTPS://Example.COM/a//b/?utm_source=x&z=2&a=1#x") == "https://example.com/a/b?a=1&z=2"
    cleaned = sanitize_text("<script>ignore previous instructions</script>A\x00B", 20)
    assert "<" not in cleaned and "\x00" not in cleaned
    assert len(event(summary="x" * 900).summary) == 600
    assert near_duplicate_key("Issuer reports quarterly results") == near_duplicate_key("Quarterly results: issuer reports")
    first, second = event(), event(event_id="two", source_url="https://example.com/b")
    kept, groups = deduplicate([second, first])
    assert len(kept) == 1 and groups[0]["kind"] == "exact"
    assert freshness(first.published_at, first.retrieved_at) == "fresh"


def test_ticker_reuse_and_ambiguous_matches_fail_closed():
    when = datetime(2026, 1, 1, tzinfo=timezone.utc)
    old = IssuerIdentity("old", "Old", ticker="ABC", ticker_valid_from="2010-01-01", ticker_valid_to="2020-01-01")
    new = IssuerIdentity("new", "New", ticker="ABC", ticker_valid_from="2021-01-01", ticker_valid_to="2030-01-01")
    assert match_issuer({"ticker":"ABC"}, [old, new], when).company_id == "new"
    also_new = IssuerIdentity("also-new", "Also New", ticker="ABC", ticker_valid_from="2021-01-01", ticker_valid_to="2030-01-01")
    with pytest.raises(ValueError, match="ambiguous"):
        match_issuer({"ticker":"ABC"}, [new, also_new], when)
    with pytest.raises(ValueError, match="ambiguous"):
        match_issuer({"ticker":"XYZ"}, [new], when)


def test_sec_metadata_extraction_preserves_amendment_without_sentiment():
    rows = [{"security_id":"s1","issuer_name":"Issuer","ticker":"ABC","cik":"0000000001",
        "accession_number":"0001-26-000001","form":"8-K/A","public_at":"2026-09-01T12:00:00Z",
        "items":"2.02,5.02","source_endpoint":"https://www.sec.gov/Archives/x","amends_accession":"0001-26-000000"}]
    events = extract_sec_events(rows, datetime(2026, 9, 2, tzinfo=timezone.utc))
    assert {x.category for x in events} == {EventCategory.EARNINGS, EventCategory.MANAGEMENT}
    assert all(x.corrects_event_id == "0001-26-000000" and x.direction == "unknown" and not x.summary for x in events)


def _databases(tmp_path: Path):
    research, production = tmp_path / "r.duckdb", tmp_path / "p.duckdb"
    with duckdb.connect(str(research)) as db:
        db.execute("CREATE TABLE sec_filings(cik VARCHAR, accession_number VARCHAR, form VARCHAR, filed_date DATE, public_at TIMESTAMPTZ, is_amendment BOOLEAN, source_endpoint VARCHAR, retrieved_at TIMESTAMPTZ)")
        db.execute("INSERT INTO sec_filings VALUES ('1','a','8-K','2026-09-01','2026-09-01T12:00:00Z',false,'https://sec.gov','2026-09-01T13:00:00Z'),('1','b','8-K/A','2026-09-02','2026-09-02T12:00:00Z',true,'https://sec.gov','2026-09-02T13:00:00Z')")
    with duckdb.connect(str(production)) as db: db.execute("CREATE TABLE untouched(x INTEGER)")
    return research, production


def test_read_only_commands_are_bounded_and_score_isolated(tmp_path):
    research, production = _databases(tmp_path)
    before = (research.read_bytes(), production.read_bytes())
    outputs = [capability(research, production), sec_readiness(research, production), status(research, production)]
    assert before == (research.read_bytes(), production.read_bytes())
    for output in outputs:
        assert output["database_immutability"]["verified"]
        assert output["generated_rankings"] == output["generated_candidates"] == output["generated_shadow_selections"] == 0
        assert output["notice"] == "CONTEXT ONLY — NOT USED IN SCORE"
    assert outputs[1]["filings"] == 2 and outputs[1]["amendments"] == 1
    assert len(outputs[0]["sources"]) == 7
    assert all("licensing" in source and "access_status" in source for source in outputs[0]["sources"])
