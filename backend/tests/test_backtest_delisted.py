"""Backtest phase 3: delisted companies are discovered, verified, priced, given SEC
facts, replayed only in the months they traded, and sold at their last price."""
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
import re

import duckdb
import pytest

from app import delisted
from app.prototype import service
from app.prototype.measure import Market, Series
from app.sec_ingestion import IngestionLimits, initialize_schema

NOW = datetime(2026, 10, 1, tzinfo=timezone.utc)
FIXTURE = json.loads((Path(__file__).parent / "fixtures/sec_capability.json").read_text())


def databases(tmp_path):
    research, production = tmp_path / "research.duckdb", tmp_path / "production.duckdb"
    with duckdb.connect(str(production)) as db: db.execute("CREATE TABLE operator_data(v INTEGER)")
    with duckdb.connect(str(research)) as db:
        db.execute("CREATE TABLE security_master_retrievals(retrieval_id VARCHAR,retrieved_at TIMESTAMP,status VARCHAR)")
        db.execute("""CREATE TABLE security_listings(retrieval_id VARCHAR,security_id VARCHAR,ticker VARCHAR,
            qualified_symbol VARCHAR,primary_exchange VARCHAR,currency VARCHAR,instrument_type VARCHAR,active BOOLEAN,cik VARCHAR)""")
        db.execute("INSERT INTO security_master_retrievals VALUES ('r','2026-01-01','completed')")
        db.execute("INSERT INTO security_listings VALUES ('r','aaa','AAA','AAA.US','US','USD','common_stock',true,NULL)")
    initialize_schema(research)
    with duckdb.connect(str(research)) as db:
        db.execute("INSERT INTO sec_issuers VALUES ('aaa','AAA.US','AAA','0000009999',NULL,'fixture',?)", [NOW])
    return research, production


class FakeEODHD:
    def __init__(self, responses): self.responses, self.requests = responses, 0
    def get(self, endpoint, params=None):
        self.requests += 1
        return self.responses[endpoint]


class FakeSEC:
    def __init__(self, documents): self.documents, self.count = documents, 0
    def get(self, url):
        self.count += 1
        return self.documents[re.search(r"CIK(\d{10})", url).group(1)]


def listing(code, name): return {"Code": code, "Name": name, "Country": "USA", "Exchange": "NASDAQ", "Currency": "USD", "Type": "Common Stock"}


def filings(forms_and_dates, tickers=()):
    return {"name": "X", "tickers": list(tickers), "filings": {"recent": {"form": [f for f, _ in forms_and_dates], "filingDate": [d for _, d in forms_and_dates]}}}


NAMES = "\n".join(["GONE CORP /DE/:0000001001:", "TWIN INC:0000002001:", "TWIN, INC.:0000002002:", "AMBI INC:0000002101:",
                   "AMBI INC /NV/:0000002102:", "LIVE CO:0000009999:", "FOREIGN PLC:0000003001:", "OLD INC:0000004001:"])
RECENT = [("10-K", "2020-03-01"), ("10-Q", "2021-05-01")]


def test_names_match_across_edgar_and_eodhd_spellings():
    assert delisted.match_name("APPLE INC /CA/") == delisted.match_name("Apple Inc.") == "APPLE"
    assert delisted.match_name("Procter & Gamble Co") == "PROCTER AND GAMBLE"
    assert delisted.sec_name_index(NAMES)["TWIN"] == {"0000002001", "0000002002"}


def test_discovery_verifies_each_filer_and_records_why_others_are_excluded(tmp_path):
    research, production = databases(tmp_path)
    eodhd = FakeEODHD({"exchange-symbol-list/US": [listing("AAA", "Old Aaa Inc"), listing("GONE", "Gone Corp"), listing("TWIN", "Twin Inc"),
        listing("AMBI", "Ambi Inc"), listing("NONE", "Nowhere Ltd"), listing("LIVE", "Live Co"), listing("FRGN", "Foreign plc"),
        listing("OLD", "Old Inc"), {**listing("OTCX", "Otc Co"), "Exchange": "PINK"}]})
    sec = FakeSEC({"0000001001": filings(RECENT), "0000002001": filings(RECENT), "0000002002": filings(RECENT, ["TWIN"]),
                   "0000002101": filings(RECENT), "0000002102": filings(RECENT), "0000003001": filings([("20-F", "2021-04-01")]),
                   "0000004001": filings([("10-K", "2015-03-01")])})
    result = delisted.discover(research=research, production=production, eodhd=eodhd, sec=sec, names_text=lambda: NAMES, now=NOW)
    assert result["filtered_by_type_or_venue"] == 1 and result["verified"] == 2 and result["eodhd_requests_for_prices"] == 4
    assert result["new_candidates"] == 5 and result["new_exclusions"] == 3
    with duckdb.connect(str(research), read_only=True) as db:
        rows = {r[0]: r[1:] for r in db.execute("SELECT ticker, status, reason, cik, ticker_confirmed FROM delisted_listings").fetchall()}
    assert rows["AAA"][:2] == ("excluded", "ticker_used_by_active_listing")
    assert rows["GONE"] == ("mapped", None, "0000001001", False)
    assert rows["TWIN"] == ("mapped", None, "0000002002", True)          # the filer that still lists the ticker
    assert rows["AMBI"][:2] == ("excluded", "filer_ambiguous")
    assert rows["NONE"][:2] == ("excluded", "sec_name_not_found")
    assert rows["LIVE"][:2] == ("excluded", "filer_in_active_catalogue")
    assert rows["FRGN"][:2] == ("excluded", "foreign_filer")
    assert rows["OLD"][:2] == ("excluded", "no_periodic_reports_since_2017")
    again = delisted.discover(research=research, production=production, eodhd=eodhd, sec=sec, names_text=lambda: NAMES, now=NOW)
    assert again["new_candidates"] == 0 and again["verified"] == 0          # resumable, nothing redone


def eod(first, last):
    days, day = [], first
    while day <= last:
        if day.weekday() < 5: days.append({"date": day.isoformat(), "open": 10, "high": 11, "low": 9, "close": 10, "adjusted_close": 10, "volume": 100})
        day += timedelta(days=7)
    return days


def mapped(db, ticker, first_filing, last_filing):
    db.execute("INSERT INTO delisted_listings (security_id, qualified_symbol, ticker, company_name, cik, status, first_filing, last_filing, "
               "discovered_at, updated_at) VALUES (?,?,?,?,?,'mapped',?,?,?,?)",
               [f"delisted:{ticker}.US", f"{ticker}.US", ticker, f"{ticker} Inc", "0000001001", first_filing, last_filing, NOW, NOW])


def test_prices_keep_companies_that_stopped_trading_in_the_window_and_sec_facts_follow(tmp_path):
    research, production = databases(tmp_path)
    with duckdb.connect(str(research)) as db:
        db.execute(delisted.SCHEMA)
        mapped(db, "GONE", date(2017, 3, 1), date(2021, 5, 1))
        mapped(db, "EARLY", date(2017, 3, 1), date(2018, 5, 1))
        mapped(db, "STIL", date(2017, 3, 1), date(2026, 8, 1))
        mapped(db, "MISS", date(2017, 3, 1), date(2018, 1, 1))
    start = date(2017, 1, 2)
    eodhd = FakeEODHD({"eod/GONE.US": eod(start, date(2021, 6, 28)), "div/GONE.US": [],
                       "eod/EARLY.US": eod(start, date(2018, 6, 25)), "eod/STIL.US": eod(start, date(2026, 9, 28)),
                       "eod/MISS.US": eod(start, date(2021, 6, 28))})
    result = delisted.prices(research=research, production=production, eodhd=eodhd, now=NOW)
    assert result["counts"] == {"excluded": 3, "priced": 1}
    assert result["exclusions"] == {"filings_do_not_match_trading": 1, "still_trading_or_ticker_reused": 1, "traded_before_2019_only": 1}
    with duckdb.connect(str(research), read_only=True) as db:
        assert db.execute("SELECT DISTINCT qualified_symbol FROM global_price_observations").fetchall() == [("GONE.US",)]
        assert db.execute("SELECT last_session FROM delisted_listings WHERE ticker='GONE'").fetchone()[0] == date(2021, 6, 28)
    out = delisted.sec(research=research, production=production, limits=IngestionLimits(), now=NOW, fixture=FIXTURE)
    assert out["counts"]["ready"] == 1 and out["ingestion"]["selected"] == 1
    with duckdb.connect(str(research), read_only=True) as db:
        assert db.execute("SELECT cik, mapping_source FROM sec_issuers WHERE security_id='delisted:GONE.US'").fetchone() == ("0000001001", delisted.MAPPING_SOURCE)
        assert db.execute("SELECT count(*) FROM sec_facts WHERE security_id='delisted:GONE.US'").fetchone()[0] > 0


def test_a_replay_lists_a_delisted_company_only_while_it_traded_and_live_never_does(prototype_fixture):
    research, production = (Path(p) for p in prototype_fixture)
    before = service.assess(research_db=research, production_db=production, decision_at=service_decision())
    company = next(c for c in before['companies'] if c['eligible'])
    sid = company['security_id']
    with duckdb.connect(str(research)) as db:  # the company leaves the active catalogue and becomes a delisted listing
        db.execute("DELETE FROM security_listings WHERE security_id=?", [sid])
        db.execute(delisted.SCHEMA)
        db.execute("INSERT INTO delisted_listings (security_id, qualified_symbol, ticker, company_name, cik, status, first_session, last_session, "
                   "discovered_at, updated_at) VALUES (?,?,?,?,?,'ready',?,?,?,?)",
                   [sid, company['qualified_symbol'], company['qualified_symbol'].split('.')[0], company['company_name'], company['cik'],
                    date(2020, 1, 1), service_decision().date() + timedelta(days=5), NOW, NOW])
    live = service.assess(research_db=research, production_db=production, decision_at=service_decision())
    assert sid not in {c['security_id'] for c in live['companies']}
    with duckdb.connect(str(research), read_only=True) as db:
        replay = service._build(db, service_decision(), 15, known=service_decision() + timedelta(days=30))
        ended = service._build(db, service_decision() + timedelta(days=10), 15, known=service_decision() + timedelta(days=30))
    replayed = next(c for c in replay['companies'] if c['security_id'] == sid)
    assert replayed['eligible'] and replayed['calculation']['momentum_return'] == company['calculation']['momentum_return']
    assert sid not in {c['security_id'] for c in ended['companies']}    # after its last session it is gone


def service_decision():
    from app.prototype.fixture import DECISION
    return DECISION


def test_a_delisted_holding_is_sold_at_its_last_price_and_survivors_still_need_a_later_price():
    fx = Series([(date(2020, 1, 1) + timedelta(days=i), 0.8) for i in range(400)])
    gone = Series([(date(2020, 1, 2), 10.0), (date(2020, 3, 2), 5.0)])
    market = Market({"GONE.US": gone}, fx, {}, ended={"GONE.US"})
    assert market.gbp_return(gone, date(2020, 1, 1), date(2020, 6, 1), ended=True) == pytest.approx(-0.5)
    assert market.gbp_return(gone, date(2020, 1, 1), date(2020, 6, 1)) is None   # not known to have ended: no return
