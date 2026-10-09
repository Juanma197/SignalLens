"""Suggested allocation: sales, score-proportional buys, position limit, whole shares, cash left (offline)."""
from datetime import timedelta
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from app.prototype import store as store_module
from app.prototype.allocation import allocate
from app.prototype.fixture import DECISION


def holding(symbol, decision, value, total, *, price=10.0, upside=0.3, score=0.3, currency='USD'):
    return {'qualified_symbol': symbol, 'security_id': symbol.lower(), 'company_name': symbol, 'currency': currency,
            'shares': value / price, 'price': {'close': price}, 'market_value': value, 'weight': value / total,
            'decision': decision, 'evidence': {'upside': upside, 'score': score}}


def pick(symbol, score, price=10.0, held=False):
    return {'security_id': symbol.lower(), 'qualified_symbol': symbol, 'company_name': symbol, 'price': price, 'score': score, 'held': held}


def test_new_cash_is_split_by_score_in_whole_shares():
    plan = allocate([], [pick('A', 0.75), pick('B', 0.25)], 1000)
    # An empty portfolio: the 25% limit applies to the 1000 invested, so each gets at most 250.
    assert [(b['qualified_symbol'], b['shares'], b['amount']) for b in plan['buys']] == [('A', 25, 250.0), ('B', 25, 250.0)]
    assert plan['left_as_cash'] == 500 and plan['invested'] == 500


def test_cap_excess_is_reoffered_and_scores_set_the_split():
    holdings = [holding('H', 'HOLD', 9000, 9000, score=None)]
    plan = allocate(holdings, [pick('A', 0.6), pick('B', 0.2), pick('C', 0.2)], 3000)
    by = {b['qualified_symbol']: b for b in plan['buys']}
    # Portfolio after = 12000, limit 3000 each: nothing is capped, so the split is 60/20/20.
    assert (by['A']['amount'], by['B']['amount'], by['C']['amount']) == (1800, 600, 600)
    assert by['A']['weight_after'] == pytest.approx(0.15)
    capped = allocate(holdings, [pick('A', 0.9), pick('B', 0.1)], 6000)
    by = {b['qualified_symbol']: b for b in capped['buys']}
    # Limit 25% of 15000 = 3750: A is capped and its excess goes to B, also capped at 3750 -> rest stays cash.
    assert (by['A']['amount'], by['B']['amount']) == (3750, 2250)
    assert capped['left_as_cash'] == 0


def test_sales_trims_and_reinvestment():
    total = 10000
    holdings = [holding('S', 'SELL', 2000, total), holding('O', 'REDUCE', 2000, total, upside=-0.05),
                holding('Z', 'REDUCE', 4000, total, upside=0.4), holding('K', 'BUY MORE', 2000, total, score=0.5)]
    plan = allocate(holdings, [pick('K', 0.5, held=True)], 0)
    sales = {s['qualified_symbol']: s for s in plan['sales']}
    assert sales['S']['action'] == 'SELL' and sales['S']['amount'] == 2000
    assert sales['O']['action'] == 'TRIM' and sales['O']['shares'] == 100      # half of 200 shares
    assert sales['Z']['shares'] == 150 and 'Trim back to 25%' in sales['Z']['why']  # 4000 -> 2500
    assert plan['sale_proceeds'] == 4500
    (buy,) = plan['buys']  # K is held, so it is not bought twice as a new pick
    assert buy['action'] == 'BUY MORE' and buy['amount'] == 500  # 25% of 10000 minus its 2000
    kept = allocate(holdings, [], 0, reinvest=False)
    assert kept['available'] == 0 and kept['left_as_cash'] == 4500 - kept['invested']


def test_nothing_forced_and_non_usd_ignored():
    assert allocate([], [], 1000)['buys'] == [] and allocate([], [], 1000)['left_as_cash'] == 1000
    assert allocate([], [pick('A', 0.5, price=2000)], 1000)['buys'] == []  # cannot afford one share
    assert allocate([holding('G', 'SELL', 100, 100, currency='GBP')], [], 0)['sales'] == []
    assert allocate([], [pick('A', 0.5)], 100)['buys'] == []  # 25 USD is below the minimum purchase


@pytest.fixture
def paths(prototype_fixture, tmp_path):
    research, production = (Path(p) for p in prototype_fixture)
    return research, production, tmp_path / 'prototype' / 'signallens-prototype.duckdb'


def test_monthly_api_returns_an_allocation(paths, monkeypatch):
    from app.config import Settings
    from app import main
    from app.prototype import api
    monkeypatch.setattr(store_module, '_now', lambda: DECISION + timedelta(days=1))
    settings = Settings(database_path=paths[1], research_database_path=paths[0], prototype_database_path=paths[2],
                        staging_mode=True, api_token='test-token', prototype_writes_enabled=True)
    monkeypatch.setattr(main, 'settings', settings); monkeypatch.setattr(api, 'get_settings', lambda: settings)
    api._CACHE.clear(); api._MARKET.clear()
    headers = {'Authorization': 'Bearer test-token'}
    with TestClient(main.app) as client:
        client.post('/api/v1/research/prototype/store/portfolio/trades', headers=headers,
                    json={'kind': 'buy', 'qualified_symbol': 'SYN05', 'shares': 10, 'price': 1, 'traded_on': '2026-09-01'})
        body = client.get('/api/v1/research/prototype/monthly', headers=headers,
                          params={'decision_at': DECISION.isoformat(), 'cash': 500}).json()
        plan = body['allocation']
        # The only holding is 100% of the portfolio: REDUCE for size, trimmed to 25%; no picks in the fixture.
        assert plan['new_cash'] == 500 and plan['sales'][0]['action'] == 'TRIM' and plan['buys'] == []
        assert plan['left_as_cash'] == pytest.approx(500 + plan['sale_proceeds'])
        assert client.get('/api/v1/research/prototype/monthly', headers=headers,
                          params={'decision_at': DECISION.isoformat(), 'cash': -1}).status_code == 422
