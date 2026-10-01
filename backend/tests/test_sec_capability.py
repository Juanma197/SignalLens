import json
from datetime import datetime, timezone
from pathlib import Path
import duckdb, httpx, pytest

from app.active_catalogue import select_active_catalogue
from app.sec_capability import (Limits, SECClient, accession_availability, normalize_facts,
                                run_assessment, select_representatives, ticker_ciks, valid_user_agent)
from app.sec_capability_cli import execute, parser

FIXTURE = Path(__file__).parent / "fixtures/sec_capability.json"

def databases(tmp_path):
    research, production = tmp_path / "research.duckdb", tmp_path / "production.duckdb"
    with duckdb.connect(str(production)) as db: db.execute("CREATE TABLE untouched(i INT)")
    with duckdb.connect(str(research)) as db:
        db.execute("CREATE TABLE security_master_retrievals(retrieval_id VARCHAR,retrieved_at TIMESTAMP,status VARCHAR)")
        db.execute("CREATE TABLE universe_snapshots(snapshot_id VARCHAR,snapshot_at TIMESTAMP)")
        db.execute("CREATE TABLE universe_snapshot_members(snapshot_id VARCHAR,security_id VARCHAR,qualified_symbol VARCHAR,canonical BOOLEAN,eligible BOOLEAN)")
        db.execute("""CREATE TABLE security_listings(retrieval_id VARCHAR,security_id VARCHAR,
            ticker VARCHAR,qualified_symbol VARCHAR,primary_exchange VARCHAR,currency VARCHAR,
            instrument_type VARCHAR,active BOOLEAN,listing_country VARCHAR,cik VARCHAR,last_seen_at TIMESTAMP)""")
        db.execute("INSERT INTO security_master_retrievals VALUES ('r','2026-01-01','completed')")
        db.execute("INSERT INTO universe_snapshots VALUES ('s','2026-01-01')")
        # Reproduce the operator shape: the snapshot exists but has no applicable
        # members, while the authoritative retrieval has 500 active listings.
        rows = []
        for index in range(500):
            region = " us " if index < 100 else ("LSE", "TO", "XETRA", "PA")[(index - 100) % 4]
            ticker = f" us{index:03d} " if index < 100 else f"ROW{index:03d}"
            rows.append(("r", f"id-{index:03d}", ticker, f"{ticker.strip()}.{region.strip()}",
                         region, "usd" if index < 100 else "EUR", "common_stock", True,
                         "US" if index < 100 else "GB", None, datetime(2026, 1, 1)))
        db.executemany("INSERT INTO security_listings VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
    return research, production

def test_offline_default_is_network_free_and_databases_are_immutable(tmp_path):
    research, production = databases(tmp_path); before = (research.read_bytes(), production.read_bytes())
    report = execute(parser().parse_args(["sec-fundamentals-offline", "--research-db",
        str(research), "--production-db", str(production)]))
    assert report["request_count"] == 0 and report["securities_attempted"] == 1
    assert report["catalogue_representatives_selected"] == 3
    assert report["evidence_scope"] == "sanitized_fixture"
    assert report["fixture_issuer_match_claimed"] is False
    assert report["amendments"] == 1 and report["database_immutability"]["verified"]
    assert before == (research.read_bytes(), production.read_bytes())

def test_sec_representatives_are_a_bounded_view_of_shared_active_catalogue(tmp_path):
    research, _ = databases(tmp_path)
    with duckdb.connect(str(research), read_only=True) as db:
        active = select_active_catalogue(db)
        expected_ids = active.listings.loc[
            active.listings["eligible"].astype(bool) & active.listings["region"].eq("US"),
            "security_id",
        ].head(3).tolist()
    selected = select_representatives(research)
    assert [row["security_id"] for row in selected] == expected_ids
    assert [row["ticker"] for row in selected] == ["US000", "US001", "US002"]

def test_missing_authorization_and_invalid_user_agent_fail_before_network(tmp_path):
    research, production = databases(tmp_path)
    with pytest.raises(PermissionError):
        run_assessment(research_db=research, production_db=production, fixture=None,
            authorize_live_sec=False, user_agent="SignalLens ops@example.com")
    with pytest.raises(ValueError, match="User-Agent"):
        run_assessment(research_db=research, production_db=production, fixture=None,
            authorize_live_sec=True, user_agent="placeholder")
    assert valid_user_agent("SignalLens research ops@example.com") and not valid_user_agent("test")

def test_request_budget_retry_malformed_and_oversized_enforcement():
    retry = httpx.MockTransport(lambda request: httpx.Response(503, json={}))
    client = SECClient("SignalLens ops@example.com", Limits(max_requests=1), retry, lambda _: None)
    with pytest.raises(RuntimeError, match="request_budget_exhausted"): client.get("https://www.sec.gov/files/company_tickers.json")
    malformed = SECClient("SignalLens ops@example.com", Limits(max_attempts=1),
        httpx.MockTransport(lambda request: httpx.Response(200, content=b"bad")), lambda _: None)
    with pytest.raises(RuntimeError, match="transport_failure"): malformed.get("https://data.sec.gov/x")
    oversized = SECClient("SignalLens ops@example.com", Limits(max_attempts=1, max_response_bytes=2),
        httpx.MockTransport(lambda request: httpx.Response(200, content=b"{}x")), lambda _: None)
    with pytest.raises(ValueError, match="oversized_response"): oversized.get("https://data.sec.gov/x")

def test_mapping_availability_boundaries_amendments_duplicates_and_units():
    fixture = json.loads(FIXTURE.read_text()); assert ticker_ciks(fixture["ticker_mapping"], ["AAA"]) == {"AAA":"0000001001"}
    available = accession_availability(fixture["submissions"]["0000001001"])
    rows, _, reasons = normalize_facts("AAA", "0000001001", fixture["companyfacts"]["0000001001"], available,
        datetime(2026, 5, 5, tzinfo=timezone.utc))
    assert all(row["public_at"] <= "2026-05-05" for row in rows) and not any(row["amendment"] for row in rows)
    rows2, _, _ = normalize_facts("AAA", "0000001001", fixture["companyfacts"]["0000001001"], available,
        datetime(2026, 6, 1, tzinfo=timezone.utc))
    assert len([r for r in rows2 if r["family"] == "revenue"]) == 2
    assert len(rows2) == len({(r["family"],r["concept"],r["unit"],r["accession"],r["value"]) for r in rows2})
    assert "missing_concept" in reasons
    broken = {"facts":{"us-gaap":{"NetIncomeLoss":{"units":{"USD":[{"end":"2026-03-31","val":1,"accn":"x"}],"shares":[{"end":"2026-03-31","val":1,"accn":"x"}]}}}}}
    _, _, conflicts = normalize_facts("AAA","1",broken,{"x":("10-Q","2026-05-01","20260501")},datetime(2026,6,1,tzinfo=timezone.utc))
    assert "conflicting_units" in conflicts

def test_missing_availability_is_not_replaced_by_period_end():
    facts={"facts":{"us-gaap":{"Assets":{"units":{"USD":[{"end":"2026-03-31","val":1,"accn":"x"}]}}}}}
    rows, _, reasons=normalize_facts("AAA","1",facts,{},datetime(2026,6,1,tzinfo=timezone.utc))
    assert rows == [] and "missing_availability_date" in reasons
