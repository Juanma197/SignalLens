from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import duckdb
import pytest

from app.global_market_data import (
    CorporateAction, ExchangeCalendar, FXObservation, GlobalMarketDataRepository,
    IngestionLimits, PriceObservation, gbp_total_return, local_price_in_gbp,
    parse_actions_csv, parse_fx_csv, parse_price_csv, total_return,
)
from app.global_universe import GlobalUniverseRepository, ListingObservation
from app.universe import TICKERS

NOW = datetime(2026, 9, 24, 12, tzinfo=timezone.utc)


def price(symbol="XLON:AAA", day=date(2026, 9, 23), currency="GBX", adjusted="125"):
    return PriceObservation(symbol, day, symbol.split(":")[0], currency, Decimal("120"),
                            Decimal("130"), Decimal("119"), Decimal("125"),
                            Decimal(adjusted), 1_000_000, "fixture", NOW)


def test_operator_price_and_action_parsing_preserves_provenance_and_actions() -> None:
    prices = parse_price_csv("qualified_symbol,trading_date,exchange,currency,open,high,low,close,adjusted_close,volume,status\nXLON:AAA,2026-09-23,XLON,GBX,120,130,119,125,123,1000,available\n",
                             source="licensed_fixture", retrieved_at=NOW)
    actions = parse_actions_csv("qualified_symbol,ex_date,action_type,value,currency\nXLON:AAA,2026-09-20,split,2,\nXLON:AAA,2026-09-21,cash_distribution,1.5,GBX\n",
                                source="licensed_fixture", retrieved_at=NOW)
    assert prices[0].adjusted_close == Decimal("123") and prices[0].retrieved_at == NOW
    assert [item.action_type for item in actions] == ["split", "cash_distribution"]


def test_fx_parser_requires_positive_point_in_time_availability() -> None:
    row = parse_fx_csv("base_currency,quote_currency,observed_on,rate,available_at\nEUR,GBP,2026-09-23,0.86,2026-09-24T15:00:00Z\n",
                       source="official_fixture", retrieved_at=NOW)[0]
    assert row.observed_on == date(2026, 9, 23) and row.available_at > NOW
    with pytest.raises(ValueError, match="positive"):
        parse_fx_csv("base_currency,quote_currency,observed_on,rate,available_at\nEUR,GBP,2026-09-23,0,2026-09-24T15:00:00Z\n", source="x", retrieved_at=NOW)


def test_local_and_gbp_total_returns_and_gbx_scaling() -> None:
    assert local_price_in_gbp(Decimal("125"), "GBX", None) == Decimal("1.25")
    assert total_return(Decimal("100"), Decimal("110")) == Decimal("0.1")
    assert gbp_total_return(Decimal("100"), Decimal("110"), "EUR", Decimal("0.8"), Decimal("0.9")) == Decimal("0.2375")
    assert gbp_total_return(Decimal("100"), Decimal("110"), "USD", None, Decimal("0.8")) is None


def test_historical_fx_alignment_never_uses_future_or_stale_rate(tmp_path: Path) -> None:
    repository = GlobalMarketDataRepository(tmp_path / "research.duckdb")
    repository.store(fx=[
        FXObservation("EUR", "GBP", date(2026, 9, 19), Decimal("0.85"), "fixture", NOW, datetime(2026, 9, 20, tzinfo=timezone.utc)),
        FXObservation("EUR", "GBP", date(2026, 9, 23), Decimal("0.86"), "fixture", NOW, datetime(2026, 9, 25, tzinfo=timezone.utc)),
    ])
    assert repository.fx_rate("EUR", date(2026, 9, 22), as_of=NOW) == Decimal("0.8500000000")
    assert repository.fx_rate("EUR", date(2026, 9, 24), as_of=NOW, tolerance_days=4) is None
    assert repository.fx_rate("EUR", date(2026, 9, 24), as_of=datetime(2026, 9, 26, tzinfo=timezone.utc)) == Decimal("0.8600000000")


def test_exchange_calendars_are_market_specific() -> None:
    london = ExchangeCalendar("XLON", [date(2026, 8, 31)])
    toronto = ExchangeCalendar("XTSE", [date(2026, 9, 7)])
    assert not london.is_session(date(2026, 8, 31)) and toronto.is_session(date(2026, 8, 31))
    assert london.session_age(date(2026, 8, 28), date(2026, 9, 1)) == 1


class BatchProvider:
    name, region = "fixture", "europe"
    def __init__(self): self.calls = 0
    def download(self, symbols, start):
        self.calls += 1
        if "FAIL" in symbols:
            raise RuntimeError("temporary provider failure")
        return [price(symbol, currency="EUR") for symbol in symbols], []


def test_batching_retry_partial_failure_checkpoint_and_idempotency(tmp_path: Path) -> None:
    repository = GlobalMarketDataRepository(tmp_path / "research.duckdb")
    provider = BatchProvider()
    result = repository.ingest(provider, ["XPAR:ONE", "FAIL", "XPAR:TWO"],
                               limits=IngestionLimits(batch_size=1, max_attempts=2), now=NOW)
    assert result["status"] == "partial" and result["completed"] == 2 and result["failed"] == 1
    assert result["checkpoint"] == "XPAR:TWO" and provider.calls == 4
    repository.store([price("XPAR:ONE", currency="EUR")])
    with duckdb.connect(str(repository.path), read_only=True) as connection:
        assert connection.execute("SELECT COUNT(*) FROM global_price_observations WHERE qualified_symbol='XPAR:ONE'").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM global_ingestion_failures").fetchone()[0] == 1


def test_dry_run_has_byte_level_database_immutability(tmp_path: Path) -> None:
    path = tmp_path / "research.duckdb"
    repository = GlobalMarketDataRepository(path)
    repository.store([price()])
    before = path.read_bytes()
    result = repository.ingest(BatchProvider(), ["XPAR:ONE"], limits=IngestionLimits(), now=NOW, dry_run=True)
    assert result["mode"] == "dry_run" and path.read_bytes() == before


class ListingProvider:
    name, source_label = "fixture", "licensed fixture"
    def discover(self):
        return [ListingObservation("xpar:AAA", "AAA", "XPAR", "Alpha SA", "FR", "EUR", "ordinary_share", domicile="FR", is_primary=True)]


def test_research_eligibility_is_explanatory_and_production_isolated(tmp_path: Path) -> None:
    original = TICKERS
    path = tmp_path / "research.duckdb"
    GlobalUniverseRepository(path).refresh(ListingProvider(), retrieved_at=NOW)
    repository = GlobalMarketDataRepository(path)
    repository.store([price("XPAR:AAA", currency="EUR")], fx=[FXObservation("EUR", "GBP", date(2026, 9, 23), Decimal("0.86"), "fixture", NOW, NOW)])
    report = repository.eligibility_report(as_of=NOW)
    assert report[0]["valid_metadata"] is True
    assert report[0]["sufficient_momentum_history"] is False
    assert "insufficient_momentum_history" in report[0]["reasons"]
    assert TICKERS == original and len(TICKERS) == 30
    with duckdb.connect(str(path), read_only=True) as connection:
        assert "prediction_vintages" not in {row[0] for row in connection.execute("SHOW TABLES").fetchall()}


def test_representative_global_fixture_covers_required_cases() -> None:
    from app.global_universe import choose_canonical, company_key, parse_reference_csv
    fixture = Path(__file__).with_name("fixtures") / "global_listings.csv"
    items = parse_reference_csv(fixture.read_text(), source="fixture")
    assert {item.listing_country for item in items} >= {"US", "GB", "CA", "FR"}
    assert {item.currency for item in items} >= {"USD", "GBX", "CAD", "EUR"}
    assert any(not item.active for item in items)
    euro = [item for item in items if item.lei == "LEIEUR"]
    assert len(euro) == 2
    assert choose_canonical(euro)[company_key(euro[0])] == "xpar:EUR"


def test_bulk_insert_fills_leading_columns_exactly_and_honours_conflicts(tmp_path):
    from decimal import Decimal
    import duckdb
    from app.global_market_data import bulk_insert
    with duckdb.connect(str(tmp_path / "t.duckdb")) as db:
        db.execute("CREATE TABLE t(k VARCHAR PRIMARY KEY, v DECIMAL(24,10), d DATE, extra VARCHAR)")
        bulk_insert(db, "t", [["a", Decimal("1.0123456789"), "2020-01-02"], ["b", None, None]])
        bulk_insert(db, "t", [["a", Decimal("9"), "2021-01-01"]], conflict="IGNORE")
        assert db.execute("SELECT * FROM t ORDER BY k").fetchall() == [("a", Decimal("1.0123456789"), date(2020, 1, 2), None), ("b", None, None, None)]
        bulk_insert(db, "t", [["a", Decimal("9"), "2021-01-01"]], conflict="REPLACE")
        assert db.execute("SELECT v FROM t WHERE k = 'a'").fetchone()[0] == Decimal("9")
        with pytest.raises(Exception): bulk_insert(db, "t", [["a", 1, None]])          # plain INSERT keeps the key check
        with pytest.raises(ValueError): bulk_insert(db, "t", [["c", 1, None, "x", "too many"]])
        with pytest.raises(ValueError): bulk_insert(db, "t", [["c", 1], ["d"]])
