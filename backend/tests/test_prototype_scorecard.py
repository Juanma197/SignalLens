"""Scorecard: frozen monthly records scored against the assessed-universe benchmark (offline)."""
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import duckdb
from fastapi.testclient import TestClient
import pandas as pd
import pytest

from app.model_readiness import fingerprint
from app.prototype import store as store_module
from app.prototype.fixture import DECISION
from app.prototype.scorecard import score
from app.prototype.store import PrototypeStore, StoreError

GROWTH = {'SYN07.US': 0.02, 'SYN05.US': 0.005}  # per session; everyone else 1%


def add_sessions(path, count):
    dates = list(pd.bdate_range(start='2026-10-01', periods=count).date)
    with duckdb.connect(str(path)) as db:
        last = dict(db.execute("SELECT qualified_symbol, adjusted_close FROM global_price_observations WHERE trading_date = DATE '2026-09-30'").fetchall())
        rows = [(s, d, 'US', 'USD', v, v + 1, v - 1, v, v, 1000, 'available', 'offline-synthetic', datetime(2026, 12, 31))
                for s, base in last.items() for j, d in enumerate(dates, 1) for v in [float(base) * (1 + GROWTH.get(s, 0.01) * j)]]
        db.executemany('INSERT INTO global_price_observations VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)', rows)


@pytest.fixture
def paths(prototype_fixture, tmp_path):
    research, production = (Path(p) for p in prototype_fixture)
    return research, production, tmp_path / 'prototype' / 'signallens-prototype.duckdb'


def record(symbols):
    def item(kind, symbol, decision=None):
        return {'kind': kind, 'security_id': symbol, 'qualified_symbol': symbol, 'decision': decision, 'reasons': []}
    return {'record_id': 'r1', 'month': '2026-10', 'decision_at': DECISION.isoformat(),
            'items': [item('pick', 'SYN07.US'), item('holding', 'SYN05.US', 'REDUCE'), item('holding', 'SYN03.US', 'HOLD'),
                      item('holding', 'NOPE.US', 'REVIEW')],
            'benchmark_symbols': symbols}


def test_calls_are_scored_against_the_benchmark_at_exact_sessions(paths):
    symbols = [f'SYN{i:02}.US' for i in range(18)]
    add_sessions(paths[0], 30)
    before = fingerprint(paths[0])
    result = score([record(symbols)], research_db=paths[0], now=datetime(2027, 1, 5, tzinfo=timezone.utc))
    assert fingerprint(paths[0]) == before
    (month,) = result['months']
    assert month['base_session'] == '2026-09-30'
    bench21 = month['benchmark'][0]
    assert bench21['available'] == 18 and bench21['return'] > 0.2
    by = {i['qualified_symbol']: i for i in month['items']}
    pick, reduce, hold, review = (by[s]['checkpoints'][0] for s in ('SYN07.US', 'SYN05.US', 'SYN03.US', 'NOPE.US'))
    assert pick['return'] == pytest.approx(0.42) and pick['right'] is True          # beat the benchmark
    assert reduce['return'] == pytest.approx(0.105) and reduce['right'] is True     # lagged it: selling was right
    assert hold['right'] is False                                                   # ordinary 1% name lags the mean
    assert review['status'] == 'missing_price' and 'right' not in review and by['NOPE.US']['group'] is None
    assert [c['status'] for c in by['SYN07.US']['checkpoints'][1:]] == ['pending'] * 3
    summary = {(s['group'], s['sessions']): s for s in result['summary']}
    assert {k: summary[('pick', 21)][k] for k in ('scored', 'right', 'hit_rate')} == {'scored': 1, 'right': 1, 'hit_rate': 1.0}
    assert summary[('pick', 21)]['mean_excess'] == pytest.approx(0.42 - bench21['return'])
    assert summary[('pick', 63)]['scored'] == 0 and summary[('pick', 63)]['hit_rate'] is None
    assert score([], research_db=paths[0])['months'] == []
    # With SPY prices for the same sessions (1.5% per session), every call is also compared with the market.
    sessions = [date(2026, 9, 30)] + list(pd.bdate_range(start='2026-10-01', periods=30).date)
    spy = {d: 100 * (1 + 0.015 * j) for j, d in enumerate(sessions)}
    with_market = score([record(symbols)], research_db=paths[0], funds={'SPY.US': spy}, now=datetime(2027, 1, 5, tzinfo=timezone.utc))
    month = with_market['months'][0]
    assert month['funds'][0]['qualified_symbol'] == 'SPY.US' and month['funds'][0]['checkpoints'][0]['return'] == pytest.approx(0.315)
    by = {i['qualified_symbol']: i['checkpoints'][0] for i in month['items']}
    assert by['SYN07.US']['excess_market'] == pytest.approx(0.42 - 0.315) and by['SYN07.US']['right_market'] is True
    assert by['SYN05.US']['right_market'] is True and by['SYN03.US']['right_market'] is False
    pick21 = next(s for s in with_market['summary'] if s['group'] == 'pick' and s['sessions'] == 21)
    assert pick21['market_scored'] == 1 and pick21['market_hit_rate'] == 1.0 and with_market['market'] == 'S&P 500 (SPY)'
    assert summary[('pick', 21)]['market_scored'] == 0  # without fund prices, no market comparison


def test_records_are_frozen_once_per_month_and_never_backfilled(paths):
    store = PrototypeStore(paths[2], protected_paths=paths[:2])
    r = {'decision_at': DECISION.isoformat(), 'items': [], 'benchmark_symbols': []}
    with pytest.raises(StoreError, match='PROTOTYPE_RECORD_BACKFILL_REFUSED'): store.create_decision_record(r, now=DECISION + timedelta(days=15))
    with pytest.raises(StoreError, match='PROTOTYPE_FUTURE_CUTOFF'): store.create_decision_record(r, now=DECISION - timedelta(hours=1))
    saved = store.create_decision_record(r, now=DECISION + timedelta(days=1))
    assert saved['month'] == '2026-10' and saved['integrity_verified']
    with pytest.raises(StoreError, match='PROTOTYPE_RECORD_MONTH_EXISTS'): store.create_decision_record(r, now=DECISION + timedelta(days=2))
    with duckdb.connect(str(paths[2])) as db:
        db.execute("UPDATE monthly_decision_records SET record_json = replace(record_json, 'items', 'itemz')")
    with pytest.raises(StoreError, match='PROTOTYPE_RECORD_INTEGRITY_FAILED'): store.decision_records()


def test_record_and_scorecard_api(paths, monkeypatch):
    from app.config import Settings
    from app import main
    from app.prototype import api
    monkeypatch.setattr(store_module, '_now', lambda: DECISION + timedelta(days=1))
    settings = Settings(database_path=paths[1], research_database_path=paths[0], prototype_database_path=paths[2],
                        staging_mode=True, api_token='test-token', prototype_writes_enabled=True)
    monkeypatch.setattr(main, 'settings', settings); monkeypatch.setattr(api, 'get_settings', lambda: settings)
    api._CACHE.clear(); api._MARKET.clear(); api._SCORECARD.clear()
    headers = {'Authorization': 'Bearer test-token'}
    base = '/api/v1/research/prototype'
    with TestClient(main.app) as client:
        client.post(f'{base}/store/portfolio/trades', headers=headers,
                    json={'kind': 'buy', 'qualified_symbol': 'SYN05', 'shares': 10, 'price': 1, 'traded_on': '2026-09-01'})
        created = client.post(f'{base}/store/decision-records', headers=headers, json={'decision_at': DECISION.isoformat(), 'cash': 100})
        assert created.status_code == 200, created.text
        body = created.json()
        # The only holding is the whole portfolio: above your limit it is a review (HOLD), never a forced sale.
        assert [i['decision'] for i in body['items']] == ['HOLD'] and len(body['benchmark_symbols']) == 18  # every eligible company
        again = client.post(f'{base}/store/decision-records', headers=headers, json={'decision_at': DECISION.isoformat()})
        assert again.json()['detail']['code'] == 'PROTOTYPE_RECORD_MONTH_EXISTS'
        listed = client.get(f'{base}/store/decision-records', headers=headers).json()['records']
        assert [r['month'] for r in listed] == ['2026-10'] and listed[0]['items'] == 1
        # A cutoff where nothing is eligible could never be scored, so it is refused.
        empty = client.post(f'{base}/store/decision-records', headers=headers, json={'decision_at': (DECISION - timedelta(days=60)).isoformat()})
        assert empty.json()['detail']['code'] == 'PROTOTYPE_RECORD_NO_ASSESSED_COMPANIES'
        card = client.get(f'{base}/scorecard', headers=headers).json()
        assert card['months'][0]['items'][0]['group'] == 'HOLD' and 'not validation' in card['label']
