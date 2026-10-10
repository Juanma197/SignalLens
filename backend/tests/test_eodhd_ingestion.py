from __future__ import annotations

import json
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import duckdb
import httpx
import pytest

from app.eodhd_ingestion import (EODHDClient, EODHDIngestion, EODHDLimits,
    _splits, catalogue_diagnostics, classify_type, parse_catalogue, parse_eod, split_ratio)
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
        rows[0]["Exchange"] = {"US": "NASDAQ", "LSE": "London Stock Exchange", "TO": "TO",
                               "XETRA": "XETRA", "PA": "PA"}[region]
        rows[0].pop("Isin", None)
        return httpx.Response(200, json=rows)
    if "/splits/" in path:
        return httpx.Response(200, json=[])
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
    assert report["request_count_bounds"] == {"lower": 83, "upper": 161}
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
    assert report["candidate_accepted"] == 5
    assert report["activated"] is False and report["activated_selection_count"] == 0
    assert path.read_bytes() == before


def test_catalogue_prices_actions_resume_and_existing_schemas(tmp_path: Path) -> None:
    path = tmp_path / "research.duckdb"
    operation = EODHDIngestion(path, tmp_path / "prod.duckdb", client(per_region=1, total=5))
    catalogue = operation.catalogue(retrieved_at=NOW)
    assert catalogue["candidate_accepted"] == catalogue["activated_selection_count"] == 5
    assert catalogue["activated"] is True
    result = operation.prices(retrieved_at=NOW)
    assert result["completed"] == 5 and result["split_status"] == "ingested"
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
    if "/splits/" in request.url.path:
        return httpx.Response(200, json=[])
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


def test_live_to_and_pa_endpoint_venues_accept_ordinary_primary_stocks() -> None:
    toronto, to_excluded = parse_catalogue([{"Code": "RY", "Name": "Royal Bank of Canada",
        "Country": "Canada", "Currency": "CAD", "Exchange": "TO", "Type": "Common Stock",
        "Isin": "CA7800871021"}], "TO")
    paris, pa_excluded = parse_catalogue([{"Code": "OR", "Name": "L'Oreal SA",
        "Country": "France", "Currency": "EUR", "Exchange": "PA", "Type": "Common Stock",
        "Isin": "FR0000120321"}], "PA")
    assert [item.qualified_symbol for item in toronto] == ["RY.TO"] and not to_excluded
    assert [item.qualified_symbol for item in paris] == ["OR.PA"] and not pa_excluded
    assert toronto[0].raw["Exchange"] == "TO" and paris[0].raw["Exchange"] == "PA"


def test_to_cdrs_are_excluded_without_fictional_subvenue() -> None:
    accepted, excluded = parse_catalogue([{"Code": "AAPL", "Name": "Apple CDR (CAD Hedged)",
        "Country": "USA", "Currency": "CAD", "Exchange": "TO", "Type": "Common Stock",
        "Isin": "CA03785Y1007"}], "TO")
    assert accepted == []
    assert excluded[0]["exchange_qualified_symbol"] == "AAPL.TO"
    assert excluded[0]["reason"] == "excluded_depositary_receipt"


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
    good_report = good.catalogue(retrieved_at=NOW)
    assert good_report["activated_selection_count"] == 5
    prior = good._latest_listings(NOW)

    def invalid_transport(request: httpx.Request) -> httpx.Response:
        region = request.url.path.rsplit("/", 1)[-1]
        if region == "PA":
            return httpx.Response(200, json=[{"Code": "ETF", "Name": "Index ETF", "Country": "France",
                                            "Currency": "EUR", "Exchange": "PA", "Type": "ETF"}])
        return httpx.Response(200, json=fixture("pilot_catalogues.json")[region])
    failed = EODHDIngestion(research, production,
        EODHDClient("secret", limits, transport=httpx.MockTransport(invalid_transport), sleep=lambda _: None))
    report = failed.catalogue(retrieved_at=NOW + __import__("datetime").timedelta(seconds=1))
    assert report["status"] == "failed_validation" and report["candidate_accepted"] == 4
    assert report["activated"] is False and report["activated_selection_count"] == 0
    assert report["unexpected_zero_regions"] == ["PA"]
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
    assert coverage["latest_run"]["request_count"] == 12
    assert coverage["security_progress"] == {"attempted": 5, "completed": 5, "pending": 0,
        "actual_failed": 0, "permanently_failed": 0, "retryable_or_other_failed": 0}
    assert coverage["catalogue_selections"] and len(coverage["price_history"]) == 5
    assert production.read_bytes() == production_before


def test_incremental_plan_uses_bounded_overlap_not_ten_years(tmp_path: Path) -> None:
    path = tmp_path / "research.duckdb"
    operation = EODHDIngestion(path, tmp_path / "prod.duckdb", client(per_region=1, total=5))
    operation.catalogue(retrieved_at=NOW)
    operation.prices(retrieved_at=NOW)
    operation.fx(retrieved_at=NOW)
    plan = operation.plan_refresh(as_of=NOW)
    price = next(x for x in plan["planned_requests"] if not x["target"].endswith(".FOREX"))
    assert price["mode"] == "incremental_refresh"
    assert price["from"] == date(2026, 9, 19)  # latest stored date minus six days
    assert (price["to"] - price["from"]).days < 10
    assert plan["overlap_days"] == 7


def test_refresh_plan_excludes_permanent_failure_and_bounds_request_details(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "research.duckdb"
    operation = EODHDIngestion(path, tmp_path / "prod.duckdb", client(per_region=1, total=5))
    operation.catalogue(retrieved_at=NOW)
    base = operation._latest_listings(NOW)[0]
    listings = [replace(base, exchange_symbol="AIIA-U.US")]
    listings.extend(replace(base, exchange_symbol=f"SAFE-{index}.US")
                    for index in range(499))
    monkeypatch.setattr(operation, "_latest_listings", lambda _: listings)
    operation._state_schema()
    operation._checkpoint("prices", "AIIA-U.US", "failed", "invalid_provider_payload")

    plan = operation.plan_refresh(as_of=NOW)

    assert plan["eligible_securities"] == 499
    assert plan["skipped_permanent_securities"] == 1
    assert plan["skipped_nonretryable_securities"] == 0
    assert plan["pending_securities"] == 0
    assert plan["provider_request_estimate"] == 1500
    assert plan["planned_requests_total"] == 502
    assert len(plan["planned_requests"]) == 10
    assert plan["planned_requests_truncated"] is True
    assert all(request["target"] != "AIIA-U.US" for request in plan["planned_requests"])


def test_normal_refresh_never_executes_permanent_initial_backfill_and_retry_requires_authorization(
        tmp_path: Path) -> None:
    path = tmp_path / "research.duckdb"
    called_permanent = []

    def guarded_transport(request: httpx.Request) -> httpx.Response:
        if "AAAUS.US" in request.url.path:
            called_permanent.append(request.url.path)
        return transport(request)

    guarded_client = EODHDClient("secret", EODHDLimits(per_region=1, total=5, requests_per_minute=100000),
        transport=httpx.MockTransport(guarded_transport), sleep=lambda _: None)
    operation = EODHDIngestion(path, tmp_path / "prod.duckdb", guarded_client)
    operation.catalogue(retrieved_at=NOW)
    operation._state_schema()
    operation._checkpoint("prices", "AAAUS.US", "failed", "invalid_provider_payload")
    guarded_client.requests = 0

    routine = operation.refresh(retrieved_at=NOW)
    assert called_permanent == []
    assert routine["completed"] == 4
    assert routine["requests"] == 15  # Four securities x three endpoints plus three FX pairs.

    guarded_client.requests = 0
    retry = operation.refresh(retrieved_at=NOW, retry_failures=True)
    assert retry["completed"] == 0 and guarded_client.requests == 0
    authorized = operation.refresh(retrieved_at=NOW, retry_failures=True,
                                   authorize_permanent_failures=True)
    assert authorized["completed"] == 1 and guarded_client.requests == 3
    assert len(called_permanent) == 3


def test_plan_reports_pending_and_nonretryable_aggregates(tmp_path: Path) -> None:
    path = tmp_path / "research.duckdb"
    operation = EODHDIngestion(path, tmp_path / "prod.duckdb", client(per_region=1, total=5))
    operation.catalogue(retrieved_at=NOW)
    operation._state_schema()
    symbols = [item.qualified_symbol for item in operation._latest_listings(NOW)]
    operation._checkpoint("prices", symbols[0], "pending", None)
    operation._checkpoint("prices", symbols[1], "failed", "unexpected_provider_error")
    operation._checkpoint("prices", symbols[2], "failed", "provider_request_failed")

    plan = operation.plan_refresh(as_of=NOW)

    assert plan["eligible_securities"] == 4
    assert plan["pending_securities"] == 1
    assert plan["skipped_nonretryable_securities"] == 1
    assert plan["skipped_permanent_securities"] == 0


def test_refresh_is_idempotent_and_reports_provider_corrections(tmp_path: Path) -> None:
    path = tmp_path / "research.duckdb"
    operation = EODHDIngestion(path, tmp_path / "prod.duckdb", client(per_region=1, total=5))
    operation.catalogue(retrieved_at=NOW); operation.prices(retrieved_at=NOW); operation.fx(retrieved_at=NOW)
    first = operation.refresh(retrieved_at=NOW)
    assert first["revisions"] == 0 and first["historical_deletes"] == 0
    with duckdb.connect(str(path), read_only=True) as db:
        count = db.execute("SELECT COUNT(*) FROM global_price_observations").fetchone()[0]
    second = EODHDIngestion(path, tmp_path / "prod.duckdb", client()).refresh(retrieved_at=NOW)
    assert second["revisions"] == 0
    with duckdb.connect(str(path), read_only=True) as db:
        assert db.execute("SELECT COUNT(*) FROM global_price_observations").fetchone()[0] == count

    def corrected(request: httpx.Request) -> httpx.Response:
        response = transport(request)
        if "/eod/" in request.url.path and not request.url.path.endswith("FOREX"):
            rows = fixture("prices.json"); rows[-1]["close"] = 12.5; rows[-1]["high"] = 13.5
            return httpx.Response(200, json=rows)
        return response
    corrected_client = EODHDClient("secret", EODHDLimits(requests_per_minute=100000),
        transport=httpx.MockTransport(corrected), sleep=lambda _: None)
    revised = EODHDIngestion(path, tmp_path / "prod.duckdb", corrected_client).refresh(retrieved_at=NOW)
    assert revised["revisions"] == 5


def test_audit_is_immutable_and_classifies_short_history_and_missing_fx(tmp_path: Path) -> None:
    path = tmp_path / "research.duckdb"
    operation = EODHDIngestion(path, tmp_path / "prod.duckdb", client(per_region=1, total=5))
    operation.catalogue(retrieved_at=NOW); operation.prices(retrieved_at=NOW)
    before = path.read_bytes()
    report = operation.audit(as_of=NOW, affected_limit=2)
    assert report["classifications"]["missing_fx"] == 4
    assert report["classifications"]["short_history"] == 1
    assert len(report["affected"]) == 2 and report["database_unchanged"] is True
    assert path.read_bytes() == before


def test_reconciliation_requires_deliberate_authorization_and_dry_run_is_immutable(tmp_path: Path) -> None:
    path = tmp_path / "research.duckdb"
    operation = EODHDIngestion(path, tmp_path / "prod.duckdb", client(per_region=1, total=5))
    operation.catalogue(retrieved_at=NOW)
    before = path.read_bytes()
    with pytest.raises(ValueError, match="requires"):
        operation.refresh(retrieved_at=NOW, reconcile=True)
    dry = operation.refresh(retrieved_at=NOW, reconcile=True, authorized=True, dry_run=True)
    assert dry["full_reconciliation_required"] is True and path.read_bytes() == before


def test_split_ratio_is_shares_after_per_share_before() -> None:
    assert split_ratio("4.000000/1.000000") == 4
    assert split_ratio("1.000000/25.000000") == Decimal("0.04")
    assert split_ratio("3/2") == Decimal("1.5")
    for bad in ("4", "0/1", "1/0", "x/1", None):
        with pytest.raises(ValueError): split_ratio(bad)
    listing = parse_catalogue(fixture("catalogue.json"), "US")[0][0]
    rows = _splits([{"date": "2020-08-31", "split": "4.000000/1.000000"}, {"date": "2021-01-04", "value": "1/2"}],
                   listing, NOW, date(2016, 9, 26), date(2026, 9, 26))
    assert [(r.ex_date, r.action_type, r.value, r.currency) for r in rows] == [
        (date(2020, 8, 31), "split", 4, None), (date(2021, 1, 4), "split", Decimal("0.5"), None)]
    with pytest.raises(ValueError): _splits(rows and [{"date": "2020-08-31", "split": "2/1"}] * 2, listing, NOW, date(2016, 9, 26), date(2026, 9, 26))


def _split_transport(ex_date: str, history: list[str]):
    def handler(request: httpx.Request) -> httpx.Response:
        if "/splits/" in request.url.path:
            return httpx.Response(200, json=[{"date": ex_date, "split": "2.000000/1.000000"}])
        if "/eod/" in request.url.path and not request.url.path.endswith("FOREX"):
            history.append(request.url.params["from"])
            return httpx.Response(200, json=[{"date": request.url.params["from"], "open": 5, "high": 6, "low": 4, "close": 5.5, "adjusted_close": 5.5, "volume": 10}])
        return transport(request)
    return handler


def test_refresh_downloads_everything_again_after_a_split(tmp_path: Path) -> None:
    path = tmp_path / "research.duckdb"
    operation = EODHDIngestion(path, tmp_path / "prod.duckdb", client(per_region=1, total=5))
    operation.catalogue(retrieved_at=NOW); operation.prices(retrieved_at=NOW); operation.fx(retrieved_at=NOW)
    with duckdb.connect(str(path)) as db:  # an older stored session, outside the refresh window
        db.execute("""INSERT INTO global_price_observations SELECT qualified_symbol, DATE '2026-09-01', exchange, currency, open, high,
            low, close, adjusted_close, volume, status, source, retrieved_at FROM global_price_observations WHERE trading_date = DATE '2026-09-24'""")
    later, history = NOW + timedelta(days=3), []
    split_client = EODHDClient("secret", EODHDLimits(requests_per_minute=100000), transport=httpx.MockTransport(_split_transport("2026-09-28", history)), sleep=lambda _: None)
    result = EODHDIngestion(path, tmp_path / "prod.duckdb", split_client).refresh(retrieved_at=later)
    assert result["rebased_count"] == 5
    # Each listing: the window request, then the full history from its first stored day.
    assert history.count("2026-09-19") == history.count("2026-09-01") == 5
    with duckdb.connect(str(path), read_only=True) as db:
        assert db.execute("SELECT COUNT(*) FROM global_corporate_actions WHERE action_type='split' AND value=2").fetchone()[0] == 5
    # The split is now stored and older rows were re-fetched after it: no further rebase.
    again = EODHDIngestion(path, tmp_path / "prod.duckdb", EODHDClient("secret", EODHDLimits(requests_per_minute=100000),
        transport=httpx.MockTransport(_split_transport("2026-09-28", [])), sleep=lambda _: None)).refresh(retrieved_at=later)
    assert again["rebased_count"] == 0


def test_split_backfill_stores_history_rebases_stale_listings_and_resumes(tmp_path: Path) -> None:
    path = tmp_path / "research.duckdb"
    operation = EODHDIngestion(path, tmp_path / "prod.duckdb", client(per_region=1, total=5))
    operation.catalogue(retrieved_at=NOW); operation.prices(retrieved_at=NOW)
    with duckdb.connect(str(path)) as db:  # as if priced before splits were fetched
        db.execute("DELETE FROM eodhd_ingestion_checkpoints WHERE stage='splits'")
    history = []
    backfill_client = EODHDClient("secret", EODHDLimits(requests_per_minute=100000, daily_requests=7),
        transport=httpx.MockTransport(_split_transport("2026-09-26", history)), sleep=lambda _: None)
    first = EODHDIngestion(path, tmp_path / "prod.duckdb", backfill_client).splits(retrieved_at=NOW)
    # Rows retrieved on the split day itself may predate it: treated as stale, fetched again from the first day.
    assert first["status"] == "partial_checkpointed" and first["completed"] == 3 and first["rebased_count"] == 3
    assert set(history) == {"2026-09-24"}
    rest = EODHDIngestion(path, tmp_path / "prod.duckdb", EODHDClient("secret", EODHDLimits(requests_per_minute=100000),
        transport=httpx.MockTransport(_split_transport("2026-09-26", [])), sleep=lambda _: None)).splits(retrieved_at=NOW)
    assert rest["status"] == "completed" and rest["targets"] == 2 and rest["completed"] == 2
    with duckdb.connect(str(path), read_only=True) as db:
        assert db.execute("SELECT COUNT(*) FROM global_corporate_actions WHERE action_type='split'").fetchone()[0] == 5
