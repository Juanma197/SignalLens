from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import duckdb
import pytest

from app.global_universe import (
    GlobalUniverseRepository,
    InvestabilityConfig,
    ListingObservation,
    MarketMetrics,
    choose_canonical,
    company_key,
    exclusion_reasons,
    parse_nasdaq_symbol_directory,
    parse_reference_csv,
    preview,
)
from app.universe import TICKERS


NOW = datetime(2026, 9, 24, 12, tzinfo=timezone.utc)


class Provider:
    name = "fixture_exchange"
    source_label = "lawful fixture"

    def __init__(self, items: list[ListingObservation]):
        self.items = items

    def discover(self) -> list[ListingObservation]:
        return self.items


def common(**changes) -> ListingObservation:
    item = ListingObservation(
        source_key="xlon:AAA", ticker="AAA", exchange="XLON",
        company_name="Alpha plc", listing_country="GB", domicile="GB",
        currency="GBP", instrument_type="ordinary_share", is_primary=True,
        active=True, isin="GB0000000001", lei="LEIALPHA000000000001",
    )
    return replace(item, **changes)


def test_nasdaq_source_parser_types_and_footer() -> None:
    text = "Symbol|Security Name|Test Issue|ETF\nAAA|Alpha Common Stock|N|N\nETF1|Index ETF|N|Y\nTEST|Test|Y|N\nFile Creation Time: 1|\n"
    items = parse_nasdaq_symbol_directory(text, directory="nasdaqlisted")
    assert [(item.ticker, item.instrument_type, item.currency) for item in items] == [
        ("AAA", "common_stock", "USD"), ("ETF1", "etf", "USD")
    ]


def test_provider_neutral_parser_preserves_currency_and_identifiers() -> None:
    text = "source_key,ticker,exchange,company_name,listing_country,domicile,currency,instrument_type,is_primary,active,isin,cik,lei\nxtse:ABC,ABC,XTSE,ABC Inc,CA,CA,CAD,common_stock,true,true,CA0000000001,00012,LEI12\n"
    item = parse_reference_csv(text, source="tmx")[0]
    assert (item.currency, item.cik, item.qualified_symbol) == ("CAD", "00012", "XTSE:ABC")


def test_reference_parser_rejects_incomplete_metadata_contract() -> None:
    with pytest.raises(ValueError, match="Missing reference columns"):
        parse_reference_csv("ticker,company_name\nA,A\n", source="bad")


def test_identifier_deduplication_and_deterministic_canonical_selection() -> None:
    primary = common()
    adr = common(source_key="xnys:ALP", ticker="ALP", exchange="XNYS", listing_country="US",
                 currency="USD", instrument_type="adr", is_primary=False, isin="US0000000001")
    assert company_key(primary) == company_key(adr)
    assert choose_canonical([adr, primary])[company_key(primary)] == primary.source_key
    assert choose_canonical([primary, adr]) == choose_canonical([adr, primary])


def test_ticker_collisions_across_exchanges_are_distinct_listings() -> None:
    first = common(source_key="xlon:ABC", ticker="ABC", exchange="XLON", lei=None, isin=None)
    second = common(source_key="xtse:ABC", ticker="ABC", exchange="XTSE", company_name="Another Corp",
                    listing_country="CA", domicile="CA", currency="CAD", lei=None, isin=None)
    selected = choose_canonical([first, second])
    assert first.qualified_symbol != second.qualified_symbol
    assert len(selected) == 2


def test_eligibility_records_all_reasons_and_requires_point_in_time_fx() -> None:
    item = common(instrument_type="etf", active=False, is_primary=False)
    reasons = exclusion_reasons(
        item, canonical_source_key="another", config=InvestabilityConfig(),
        metrics=MarketMetrics(adjusted_close=4, median_daily_value=1_000, valid_history_days=20,
                              observed_on=date(2026, 9, 23), currency="GBP"), usd_rate=None,
        provider_stale=True,
    )
    assert reasons == sorted(reasons)
    assert {"secondary_listing", "excluded_etf", "inactive_or_delisted", "stale_provider_data",
            "insufficient_price_history", "fx_unavailable"} <= set(reasons)


def test_eligibility_thresholds_and_success() -> None:
    item = common(currency="USD")
    config = InvestabilityConfig()
    low = exclusion_reasons(item, canonical_source_key=item.source_key, config=config,
                            metrics=MarketMetrics(4, 4_000_000, 126, date(2026, 9, 23), "USD"),
                            usd_rate=1)
    assert low == ["below_minimum_price", "below_minimum_traded_value"]
    assert exclusion_reasons(item, canonical_source_key=item.source_key, config=config,
                             metrics=MarketMetrics(6, 6_000_000, 126, date(2026, 9, 23), "USD"),
                             usd_rate=1) == []


def test_preview_is_read_only_and_structured(tmp_path: Path) -> None:
    path = tmp_path / "never-created.duckdb"
    result = preview(Provider([common()]))
    assert result["mode"] == "dry_run"
    assert result["canonical_companies"] == 1
    assert not path.exists()


def test_refresh_retry_is_idempotent_and_tracks_new_delisted_listing(tmp_path: Path) -> None:
    repository = GlobalUniverseRepository(tmp_path / "data.duckdb")
    provider = Provider([common()])
    first = repository.refresh(provider, retrieved_at=NOW)
    second = repository.refresh(provider, retrieved_at=NOW + timedelta(hours=1))
    assert first["status"] == "completed"
    assert second["status"] == "already_exists"
    provider.items = [common(), common(source_key="xlon:OLD", ticker="OLD", active=False, isin="GB0000000002", lei="LEIOLD")]
    third = repository.refresh(provider, retrieved_at=NOW + timedelta(days=1))
    assert third["listings"] == 2
    with duckdb.connect(str(repository.path), read_only=True) as connection:
        assert connection.execute("SELECT COUNT(*) FROM security_master_retrievals").fetchone()[0] == 2
        assert connection.execute("SELECT active FROM security_listings WHERE source_key='xlon:OLD'").fetchone()[0] is False


def test_monthly_snapshot_is_immutable_and_preserves_decisions(tmp_path: Path) -> None:
    repository = GlobalUniverseRepository(tmp_path / "data.duckdb")
    item = common(currency="USD")
    repository.refresh(Provider([item]), retrieved_at=NOW)
    metrics = {item.qualified_symbol: MarketMetrics(10, 8_000_000, 130, date(2026, 9, 23), "USD")}
    result = repository.create_snapshot(snapshot_month=date(2026, 9, 1), snapshot_at=NOW,
                                        config=InvestabilityConfig(), metrics=metrics)
    retry = repository.create_snapshot(snapshot_month=date(2026, 9, 1), snapshot_at=NOW,
                                       config=InvestabilityConfig(), metrics=metrics)
    assert result["eligible"] == 1
    assert retry["status"] == "already_exists"
    with pytest.raises(ValueError, match="immutable"):
        repository.create_snapshot(snapshot_month=date(2026, 9, 1), snapshot_at=NOW,
                                   config=InvestabilityConfig(minimum_price_usd=20), metrics=metrics)


def test_snapshot_marks_stale_and_missing_provider_data(tmp_path: Path) -> None:
    repository = GlobalUniverseRepository(tmp_path / "data.duckdb")
    repository.refresh(Provider([common()]), retrieved_at=NOW - timedelta(days=40))
    repository.create_snapshot(snapshot_month=date(2026, 9, 1), snapshot_at=NOW,
                               config=InvestabilityConfig())
    status = repository.coverage(now=NOW)
    assert status["status"] == "stale"
    assert status["excluded_by_reason"]["missing_price_data"] == 1
    assert status["excluded_by_reason"]["stale_provider_data"] == 1


def test_snapshot_rejects_non_month_boundary(tmp_path: Path) -> None:
    repository = GlobalUniverseRepository(tmp_path / "data.duckdb")
    repository.refresh(Provider([common()]), retrieved_at=NOW)
    with pytest.raises(ValueError, match="first calendar day"):
        repository.create_snapshot(snapshot_month=date(2026, 9, 2), snapshot_at=NOW,
                                   config=InvestabilityConfig())


def test_shadow_schema_cannot_change_production_universe_or_rankings(tmp_path: Path) -> None:
    original_tickers = TICKERS
    repository = GlobalUniverseRepository(tmp_path / "data.duckdb")
    repository.refresh(Provider([common()]), retrieved_at=NOW)
    tables = set()
    with duckdb.connect(str(repository.path), read_only=True) as connection:
        tables = {row[0] for row in connection.execute("SHOW TABLES").fetchall()}
    assert TICKERS == original_tickers and len(TICKERS) == 30
    assert "prediction_vintages" not in tables
    assert "security_universe" not in tables


def test_empty_coverage_does_not_create_database(tmp_path: Path) -> None:
    path = tmp_path / "absent.duckdb"
    result = GlobalUniverseRepository(path).coverage(now=NOW)
    assert result["missing"] == ["database_missing"]
    assert not path.exists()
