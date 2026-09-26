from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

import duckdb
import httpx
import pytest

from app.eodhd_ingestion import (EODHDClient, EODHDIngestion, EODHDLimits,
    catalogue_diagnostics, classify_type, parse_catalogue, parse_eod)
from app.eodhd_ingestion_cli import build_parser, execute

NOW = datetime(2026, 9, 26, 12, tzinfo=timezone.utc)


def fixture(name: str):
    return json.loads((Path(__file__).parent / "fixtures" / "eodhd_ingestion" / name).read_text())


def transport(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if "exchange-symbol-list" in path:
        region = path.rsplit("/", 1)[-1]
        rows = fixture("catalogue.json")
        rows[0]["Code"] = f"AAA{region}"
        rows[0]["Name"] = f"Alpha {region} Holdings"
        country, currency = {"US": ("US", "USD"), "LSE": ("GB", "GBP"), "TO": ("CA", "CAD"), "XETRA": ("DE", "EUR"), "PA": ("FR", "EUR")}[region]
        rows[0]["Country"], rows[0]["Currency"] = country, currency
        rows[0]["Exchange"] = {"US": "NASDAQ", "LSE": "London Stock Exchange", "TO": "TSX",
                               "XETRA": "XETRA", "PA": "Euronext Paris"}[region]
        rows[0].pop("Isin", None)
        return httpx.Response(200, json=rows)
    if "/div/" in path:
        return httpx.Response(200, json=fixture("dividends.json"))
    return httpx.Response(200, json=fixture("prices.json"))


def client(**changes) -> EODHDClient:
    limits = EODHDLimits(requests_per_minute=100000, **changes)
    return EODHDClient("secret-never-output", limits, transport=httpx.MockTransport(transport), sleep=lambda _: None)


def test_classification_is_conservative_and_has_explicit_reasons() -> None:
    assert classify_type("Common Stock") == ("common_stock", None)
    assert classify_type("ETF") == (None, "excluded_etf")
    assert classify_type("Preferred Stock") == (None, "excluded_preferred_share")
    assert classify_type("Mystery") == (None, "excluded_unreliable_instrument_classification")


def test_catalogue_preserves_qualified_symbol_and_exclusions() -> None:
    accepted, excluded = parse_catalogue(fixture("catalogue.json"), "US")
    assert accepted[0].qualified_symbol == "AAA.US"
    assert accepted[0].raw["Code"] == "AAA"
    assert {x["reason"] for x in excluded} == {"excluded_etf", "excluded_warrant"}


def test_price_validation_rejects_duplicates_and_bad_ohlc() -> None:
    listing = parse_catalogue(fixture("catalogue.json"), "US")[0][0]
    rows = parse_eod(fixture("prices.json"), listing, NOW, date(2026, 9, 1), NOW.date())
    assert rows[0].adjusted_close is not None and rows[0].source == "eodhd"
    duplicate = fixture("prices.json") * 2
    with pytest.raises(ValueError, match="duplicate"):
        parse_eod(duplicate, listing, NOW, date(2026, 9, 1), NOW.date())


def test_plan_reports_bounded_cost_and_honest_limitations(tmp_path: Path) -> None:
    operation = EODHDIngestion(tmp_path / "research.duckdb", tmp_path / "production.duckdb", client(per_region=5, total=25))
    report = operation.plan()
    assert report["maximum_securities"] == 25
    assert report["request_count_bounds"] == {"lower": 58, "upper": 111}
    assert "pacing_only_lower" in report["runtime_estimates_minutes"]
    assert "not survivorship-free" in report["warnings"][0]
    assert not (tmp_path / "research.duckdb").exists()


def test_refuses_production_database_path(tmp_path: Path) -> None:
    path = tmp_path / "same.duckdb"
    with pytest.raises(ValueError, match="refusing production"):
        EODHDIngestion(path, path, client())


def test_dry_run_is_byte_for_byte_mutation_free(tmp_path: Path) -> None:
    path = tmp_path / "research.duckdb"
    path.write_bytes(b"unchanged")
    before = path.read_bytes()
    operation = EODHDIngestion(path, tmp_path / "prod.duckdb", client(per_region=1, total=5))
    report = operation.catalogue(retrieved_at=NOW, dry_run=True)
    assert report["accepted"] == 5 and path.read_bytes() == before


def test_catalogue_prices_actions_resume_and_existing_schemas(tmp_path: Path) -> None:
    path = tmp_path / "research.duckdb"
    operation = EODHDIngestion(path, tmp_path / "prod.duckdb", client(per_region=1, total=5))
    assert operation.catalogue(retrieved_at=NOW)["accepted"] == 5
    result = operation.prices(retrieved_at=NOW)
    assert result["completed"] == 5 and result["split_status"] == "provider_unsupported"
    resumed = operation.prices(retrieved_at=NOW + __import__("datetime").timedelta(seconds=1), resume=True)
    assert resumed["completed"] == 0
    with duckdb.connect(str(path), read_only=True) as db:
        tables = {x[0] for x in db.execute("SHOW TABLES").fetchall()}
        assert {"security_listings", "global_price_observations", "global_corporate_actions"} <= tables
        assert db.execute("SELECT COUNT(*) FROM global_price_observations").fetchone()[0] == 10


def test_fx_has_point_in_time_availability_not_todays_rate(tmp_path: Path) -> None:
    operation = EODHDIngestion(tmp_path / "research.duckdb", tmp_path / "prod.duckdb", client())
    report = operation.fx(retrieved_at=NOW)
    assert report["observations"] == 6
    with duckdb.connect(str(operation.path), read_only=True) as db:
        observed, available = db.execute("SELECT observed_on,available_at FROM global_fx_observations LIMIT 1").fetchone()
        assert available.date() == observed.replace(day=observed.day) + __import__('datetime').timedelta(days=1)


def pilot_transport(request: httpx.Request) -> httpx.Response:
    if "exchange-symbol-list" in request.url.path:
        region = request.url.path.rsplit("/", 1)[-1]
        return httpx.Response(200, json=fixture("pilot_catalogues.json")[region])
    if "/div/" in request.url.path:
        return httpx.Response(200, json=fixture("dividends.json"))
    return httpx.Response(200, json=fixture("prices.json"))


def test_primary_venue_selection_allows_foreign_domiciles_and_excludes_receipts(tmp_path: Path) -> None:
    limits = EODHDLimits(per_region=2, total=10, requests_per_minute=100000)
    operation = EODHDIngestion(tmp_path / "research.duckdb", tmp_path / "production.duckdb",
        EODHDClient("secret", limits, transport=httpx.MockTransport(pilot_transport), sleep=lambda _: None))
    report = operation.catalogue(retrieved_at=NOW, dry_run=True)
    assert report["selected_by_currency"] == {"CAD": 2, "EUR": 4, "GBP": 1, "GBX": 1, "USD": 2}
    assert report["selected_by_region"] == {"LSE": 2, "PA": 2, "TO": 2, "US": 2, "XETRA": 2}
    assert report["unexpected_zero_regions"] == []
    assert report["exclusions_by_reason"]["excluded_depositary_receipt"] == 2
    assert report["exclusions_by_reason"]["excluded_secondary_listing"] == 1
    assert report["exclusions_by_reason"]["excluded_otc_or_secondary_venue"] == 2
    assert report["selection_policy"] == "deterministic_sha256_not_liquidity_ranked"
    # Provider country names describe domicile, not venue: foreign-domiciled
    # Spotify, BHP, Airbus, and Stellantis remain eligible ordinary listings.
    assert report["status"] == "validated"


def test_aggregate_diagnostic_normalizes_live_aliases_without_records() -> None:
    diagnostic = catalogue_diagnostics(fixture("pilot_catalogues.json")["US"], "exchange-symbol-list/US")
    assert diagnostic["records"] == 4
    assert diagnostic["distinct"]["country"]["US"] == 1
    assert diagnostic["distinct"]["exchange"]["NYSE"] == 2
    assert diagnostic["distinct"]["currency"] == {"USD": 4}
    assert diagnostic["field_presence"]["isin"] == 4
    assert "Code" not in json.dumps(diagnostic) and "AAPL" not in json.dumps(diagnostic)


def test_read_only_diagnostic_cli_needs_no_token_and_changes_no_database(tmp_path: Path, monkeypatch) -> None:
    fixture_path = Path(__file__).parent / "fixtures" / "eodhd_ingestion" / "pilot_catalogues.json"
    research, production = tmp_path / "research.duckdb", tmp_path / "production.duckdb"
    production.write_bytes(b"production-unchanged")
    monkeypatch.delenv("SIGNALLENS_EODHD_API_TOKEN", raising=False)
    args = build_parser().parse_args(["diagnose-catalogue", "--catalogue-fixture", str(fixture_path),
                                      "--research-db", str(research), "--production-db", str(production)])
    report = execute(args, now=NOW)
    assert report["status"] == "completed" and len(report["provider_diagnostics"]) == 5
    assert not research.exists() and production.read_bytes() == b"production-unchanged"


def test_zero_refresh_fails_closed_preserves_prior_selection_and_production(tmp_path: Path) -> None:
    research, production = tmp_path / "research.duckdb", tmp_path / "production.duckdb"
    production.write_bytes(b"production diagnostic evidence")
    before = production.read_bytes()
    limits = EODHDLimits(per_region=1, total=5, requests_per_minute=100000)
    good = EODHDIngestion(research, production,
        EODHDClient("secret", limits, transport=httpx.MockTransport(pilot_transport), sleep=lambda _: None))
    assert good.catalogue(retrieved_at=NOW)["accepted"] == 5
    prior = good._latest_listings(NOW)

    def empty_transport(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[{"Code": "ETF", "Name": "Index ETF", "Country": "USA",
                                        "Currency": "USD", "Exchange": "NASDAQ", "Type": "ETF"}])
    failed = EODHDIngestion(research, production,
        EODHDClient("secret", limits, transport=httpx.MockTransport(empty_transport), sleep=lambda _: None))
    report = failed.catalogue(retrieved_at=NOW + __import__("datetime").timedelta(seconds=1))
    assert report["status"] == "failed_validation" and report["accepted"] == 0
    assert report["unexpected_zero_regions"] == ["LSE", "PA", "TO", "US", "XETRA"]
    assert len(failed._latest_listings(NOW + __import__("datetime").timedelta(seconds=2))) == len(prior)
    assert failed.prices(retrieved_at=NOW)["status"] == "failed_validation"
    assert production.read_bytes() == before


def test_selection_is_reproducible_and_not_alphabetical(tmp_path: Path) -> None:
    limits = EODHDLimits(per_region=1, total=5, requests_per_minute=100000)
    def run(name: str) -> dict:
        return EODHDIngestion(tmp_path / name, tmp_path / "prod.duckdb",
            EODHDClient("secret", limits, transport=httpx.MockTransport(pilot_transport), sleep=lambda _: None)).catalogue(retrieved_at=NOW, dry_run=True)
    first, second = run("one.duckdb"), run("two.duckdb")
    assert first == second
    assert first["excluded_by_region"]["US"] >= 3


def test_runtime_stop_is_checkpointed_and_resume_only_processes_pending(tmp_path: Path) -> None:
    path, clock = tmp_path / "research.duckdb", [0.0]
    production = tmp_path / "prod.duckdb"
    production.write_bytes(b"production-unchanged")
    production_before = production.read_bytes()
    def timed_transport(request: httpx.Request) -> httpx.Response:
        if "exchange-symbol-list" not in request.url.path:
            clock[0] += 1.0
        return pilot_transport(request)
    limits = EODHDLimits(per_region=1, total=5, requests_per_minute=100000, maximum_runtime_seconds=2.5)
    first_client = EODHDClient("secret", limits, transport=httpx.MockTransport(timed_transport), sleep=lambda _: None, monotonic=lambda: clock[0])
    operation = EODHDIngestion(path, production, first_client)
    operation.catalogue(retrieved_at=NOW)
    first_client.started = clock[0]
    first = operation.prices(retrieved_at=NOW)
    assert first["status"] == "partial_checkpointed"
    assert first["completed"] == 1 and first["pending"] == 4 and first["actual_failed"] == 0
    assert first["stop_reason"] == "maximum_runtime_exceeded"

    resume_client = EODHDClient("secret", EODHDLimits(per_region=1, total=5, requests_per_minute=100000), transport=httpx.MockTransport(pilot_transport), sleep=lambda _: None)
    resumed = EODHDIngestion(path, production, resume_client).prices(retrieved_at=NOW + __import__("datetime").timedelta(seconds=1), resume=True)
    assert resumed["completed"] == 4 and resumed["pending"] == 0 and resumed["actual_failed"] == 0
    coverage = EODHDIngestion(path, production, resume_client).coverage()
    assert coverage["latest_run"]["attempted"] == 4
    assert coverage["latest_run"]["request_count"] == 8
    assert coverage["security_progress"] == {"attempted": 5, "completed": 5, "pending": 0, "actual_failed": 0}
    assert coverage["catalogue_selections"] and len(coverage["price_history"]) == 5
    assert production.read_bytes() == production_before
