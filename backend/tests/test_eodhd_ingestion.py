from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

import duckdb
import httpx
import pytest

from app.eodhd_ingestion import (EODHDClient, EODHDIngestion, EODHDLimits,
    classify_type, parse_catalogue, parse_eod)

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
    accepted, excluded = parse_catalogue(fixture("catalogue.json"), "LSE")
    assert accepted[0].qualified_symbol == "AAA.LSE"
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
    assert report["estimated_requests"] == 58
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
    resumed = operation.prices(retrieved_at=NOW, resume=True)
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
