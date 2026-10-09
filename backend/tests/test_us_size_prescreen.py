"""US market-cap pre-screen and the wide US catalogue selection (offline)."""
from datetime import date, datetime, timezone
from pathlib import Path

import duckdb
import httpx
import pytest

from app.eodhd_ingestion import EODHDClient, EODHDIngestion, EODHDLimits
from app.model_readiness import fingerprint
from app.sec_ingestion import BudgetClient, IngestionLimits
from app.us_size_prescreen import SCHEMA, closes_by_ticker, estimates, latest, recent_quarters, run, shares_by_cik
from tests.test_eodhd_ingestion import pilot_transport

NOW = datetime(2026, 10, 9, 12, tzinfo=timezone.utc)


def test_quarters_shares_and_estimates():
    assert recent_quarters(date(2026, 10, 9)) == [(2026, 3), (2026, 2), (2026, 1)]
    assert recent_quarters(date(2026, 2, 1)) == [(2025, 4), (2025, 3), (2025, 2)]
    frames = [{'data': [{'cik': 1, 'val': 100, 'end': '2026-06-30'}, {'cik': 2, 'val': 0, 'end': '2026-06-30'}]},
              {'data': [{'cik': 1, 'val': 90, 'end': '2026-03-31'}, {'cik': 3, 'val': 50, 'end': '2026-03-31'}, {'bad': 1}]}]
    shares = shares_by_cik(frames)
    assert shares == {'0000000001': (100.0, date(2026, 6, 30)), '0000000003': (50.0, date(2026, 3, 31))}
    closes = closes_by_ticker([{'code': 'aaa', 'close': 10, 'date': '2026-10-08'}, {'code': 'BBB', 'close': 'x', 'date': '2026-10-08'},
                               {'code': 'CCC', 'close': 2, 'date': '2026-10-08'}])
    rows = estimates({'AAA': '0000000001', 'CCC': '0000000002'}, shares, closes)
    assert [(r['ticker'], r['market_cap_usd']) for r in rows] == [('AAA', 1000.0)]  # CCC has no share count


def databases(tmp_path):
    research, production = tmp_path / 'research.duckdb', tmp_path / 'production.duckdb'
    for path in (research, production):
        with duckdb.connect(str(path)) as db: db.execute('SELECT 1')
    return research, production


def test_run_records_estimates_without_touching_production(tmp_path):
    research, production = databases(tmp_path)
    before = fingerprint(production)
    def sec_transport(request):
        if 'company_tickers' in request.url.path:
            return httpx.Response(200, json={'0': {'cik_str': 1, 'ticker': 'SPOT'}, '1': {'cik_str': 2, 'ticker': 'AAPL'}})
        if 'CY2026Q3I' in request.url.path: return httpx.Response(404)  # not published yet
        return httpx.Response(200, json={'data': [{'cik': 1, 'val': 2e8, 'end': '2026-06-30'}, {'cik': 2, 'val': 1.5e10, 'end': '2026-06-30'}]})
    def eodhd_transport(request):
        return httpx.Response(200, json=[{'code': 'SPOT', 'close': 25, 'date': '2026-10-08'}, {'code': 'AAPL', 'close': 200, 'date': '2026-10-08'}])
    sec = BudgetClient('SignalLens test admin@example.com', IngestionLimits(max_requests=10), transport=httpx.MockTransport(sec_transport), sleeper=lambda _: None)
    eodhd = EODHDClient('secret', EODHDLimits(daily_requests=5), transport=httpx.MockTransport(eodhd_transport), sleep=lambda _: None)
    report = run(research=research, production=production, eodhd=eodhd, sec=sec, now=NOW)
    assert report['estimates'] == 2 and report['by_size'] == {'under_300m': 0, '300m_to_10b': 1, 'over_10b': 1}
    assert report['frames_missing'] == ['CY2026Q3I'] and report['eodhd_requests'] == 1
    assert fingerprint(production) == before
    with duckdb.connect(str(research), read_only=True) as db:
        assert latest(db) == {'SPOT': 5e9, 'AAPL': 3e12}


def test_wide_us_catalogue_keeps_only_prescreened_companies_in_the_band(tmp_path):
    def operation(limits):
        return EODHDIngestion(tmp_path / 'research.duckdb', tmp_path / 'production.duckdb',
            EODHDClient('secret', limits, transport=httpx.MockTransport(pilot_transport), sleep=lambda _: None))
    wide = EODHDLimits(per_region=2, total=10, requests_per_minute=100000, us_securities=50)
    with duckdb.connect(str(tmp_path / 'research.duckdb')) as db: db.execute('SELECT 1')
    refused = operation(wide).catalogue(retrieved_at=NOW, dry_run=True)
    assert refused['status'] == 'failed_validation' and 'us_size_prescreen' in refused['error']
    with duckdb.connect(str(tmp_path / 'research.duckdb')) as db:
        db.execute(SCHEMA)
        db.execute("INSERT INTO us_size_prescreen VALUES ('r1', ?, 'SPOT', '1', 1, DATE '2026-06-30', 1, DATE '2026-10-08', 5e9)", [NOW])
        db.execute("INSERT INTO us_size_prescreen VALUES ('r1', ?, 'AAPL', '2', 1, DATE '2026-06-30', 1, DATE '2026-10-08', 3e12)", [NOW])
    report = operation(wide).catalogue(retrieved_at=NOW, dry_run=True)
    assert report['status'] == 'validated' and report['selected_by_region']['US'] == 1
    assert report['exclusions_by_reason']['excluded_outside_size_band'] == 1
    assert {k: v for k, v in report['selected_by_region'].items() if k != 'US'} == {'LSE': 2, 'PA': 2, 'TO': 2, 'XETRA': 2}
    assert 'pre-screened market cap' in report['selection_policy']
    # The legacy 100-per-region sample is unchanged when wide mode is off.
    legacy = operation(EODHDLimits(per_region=2, total=10, requests_per_minute=100000)).catalogue(retrieved_at=NOW, dry_run=True)
    assert legacy['selected_by_region']['US'] == 2 and 'excluded_outside_size_band' not in legacy['exclusions_by_reason']
    with pytest.raises(ValueError): EODHDLimits(us_securities=3001)
