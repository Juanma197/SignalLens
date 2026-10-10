"""Suggested allocation: sales, score-proportional buys, position limit, whole shares, cash left (offline)."""
from datetime import timedelta
from pathlib import Path

import duckdb
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


def test_fractional_shares_buy_expensive_picks_and_halve_exactly():
    # 25% of 1000 is 250: a 2,000-dollar share cannot be bought whole, but 0.125 of one can.
    assert allocate([], [pick('A', 0.5, price=2000)], 1000)['buys'] == []
    plan = allocate([], [pick('A', 0.5, price=2000)], 1000, fractional=True)
    (buy,) = plan['buys']
    assert buy['shares'] == 0.125 and buy['amount'] == 250 and plan['fractional'] and 'fractional shares' in plan['method']
    # Rounded down to four decimals, never up: 250 / 3000 = 0.08333... -> 0.0833.
    (buy,) = allocate([], [pick('A', 0.5, price=3000)], 1000, fractional=True)['buys']
    assert buy['shares'] == 0.0833 and buy['amount'] == pytest.approx(249.9)
    holdings = [holding('O', 'REDUCE', 30, 100, upside=-0.05), holding('Z', 'REDUCE', 45, 100, upside=0.4)]
    whole = {s['qualified_symbol']: s['shares'] for s in allocate(holdings, [], 0)['sales']}
    part = {s['qualified_symbol']: s['shares'] for s in allocate(holdings, [], 0, fractional=True)['sales']}
    assert whole['O'] == 1 and part['O'] == 1.5       # half of 3 shares
    # Z holds 4.5 shares worth 45; trimming to 25% of 100 sells 20 / 10 = 2 shares either way.
    assert whole['Z'] == 2 and part['Z'] == 2


def test_new_names_only_while_holdings_stay_within_the_maximum():
    holdings = [holding(f'H{i}', 'HOLD', 1000, 9000, score=None) for i in range(8)] + [holding('S', 'SELL', 1000, 9000)]
    picks = [pick('A', 0.2), pick('B', 0.5), pick('C', 0.3)]
    plan = allocate(holdings, picks, 3000, max_holdings=10)
    # Selling S leaves 8 holdings, so two slots: the two best picks get them, A waits.
    assert sorted(b['qualified_symbol'] for b in plan['buys']) == ['B', 'C']
    assert [s['qualified_symbol'] for s in plan['skipped_no_slot']] == ['A'] and plan['holdings_after'] == 10
    full = allocate(holdings[:8] + [holding('K', 'BUY MORE', 1000, 9000, score=0.4)] + [holding('H9', 'HOLD', 1000, 9000, score=None)],
                    picks, 3000, max_holdings=10)
    # Ten holdings already: no new names, but adding to an existing holding is still allowed.
    assert [b['action'] for b in full['buys']] == ['BUY MORE'] and len(full['skipped_no_slot']) == 3
    assert len(allocate(holdings, picks, 3000)['buys']) == 3  # no maximum given


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
    with duckdb.connect(str(paths[0])) as db:
        # 0.8 pounds per dollar, known before the cutoff.
        db.execute("INSERT INTO global_fx_observations VALUES ('USD', 'GBP', ?, 0.8, 'test', ?, ?)",
                   [DECISION.date() - timedelta(days=1), DECISION.replace(tzinfo=None) - timedelta(hours=5), DECISION.replace(tzinfo=None) - timedelta(hours=5)])
    with TestClient(main.app) as client:
        client.post('/api/v1/research/prototype/store/portfolio/trades', headers=headers,
                    json={'kind': 'buy', 'qualified_symbol': 'SYN05', 'shares': 10, 'price': 1, 'traded_on': '2026-09-01', 'account_amount': 8})
        client.post('/api/v1/research/prototype/store/portfolio/cash', headers=headers,
                    json={'kind': 'deposit', 'amount': 300, 'moved_on': '2026-09-01'})
        client.post('/api/v1/research/prototype/store/portfolio/settings', headers=headers,
                    json={'monthly_contribution': 250, 'max_holdings': 10})
        body = client.get('/api/v1/research/prototype/monthly', headers=headers,
                          params={'decision_at': DECISION.isoformat(), 'include_contribution': True}).json()
        plan, account = body['allocation'], body['allocation']['account']
        # Cash pool 300 - 8 = 292 pounds, plus the planned 250 = 542 pounds = 677.50 dollars at 0.8.
        assert account['cash_pool'] == pytest.approx(292) and account['contribution_included'] == 250
        assert account['available'] == pytest.approx(542) and account['gbp_per_usd']['source'] == 'stored'
        assert plan['new_cash'] == pytest.approx(677.5)
        # The only holding is 100% of the portfolio: above your limit it is a review, never a forced trim; no picks in the fixture.
        assert plan['sales'] == [] and plan['buys'] == [] and plan['awaiting_proceeds'] == 0
        assert account['left_as_cash'] == pytest.approx(542) and account['reconciliation']['status'] == 'unchecked'
        assert body['plan']['assumes']['cash_pool'] == pytest.approx(292) and len(body['plan']['version']) == 8
        without = client.get('/api/v1/research/prototype/monthly', headers=headers,
                             params={'decision_at': DECISION.isoformat()}).json()['allocation']['account']
        assert without['contribution_included'] == 0 and without['available'] == pytest.approx(292)
