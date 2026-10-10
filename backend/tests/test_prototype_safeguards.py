"""Portfolio safeguards: confirmed cash only, your limits, no forced trims,
duplicate submissions, dividends and fees, broker reconciliation, plan stamps (offline)."""
from datetime import date
from pathlib import Path

import pytest

from app.prototype.alerts import cash_message
from app.prototype.allocation import allocate
from app.prototype.cash import ledger, reconcile
from app.prototype.store import PrototypeStore, StoreError

TODAY = date(2026, 10, 9)
LIVE = {'position_limit': 0.15, 'top3_limit': 0.40, 'minimum_purchase_usd': 25.0, 'trim_oversized': False}


@pytest.fixture
def store(tmp_path):
    return PrototypeStore(tmp_path / 'prototype' / 'signallens-prototype.duckdb')


def holding(symbol, decision, value, total, *, price=10.0, upside=0.3, score=0.3):
    return {'qualified_symbol': symbol, 'security_id': symbol.lower(), 'company_name': symbol, 'currency': 'USD',
            'shares': value / price, 'price': {'close': price}, 'market_value': value, 'weight': value / total,
            'decision': decision, 'evidence': {'upside': upside, 'score': score}}


def pick(symbol, score, price=10.0):
    return {'security_id': symbol.lower(), 'qualified_symbol': symbol, 'company_name': symbol, 'price': price, 'score': score, 'held': False}


def test_buys_use_confirmed_cash_only_and_proposed_sales_wait():
    holdings = [holding('S', 'SELL', 500, 1000), holding('K', 'HOLD', 500, 1000, score=None)]
    plan = allocate(holdings, [pick('A', 0.5)], 100, reinvest=False, rules=LIVE)
    assert plan['sales'][0]['action'] == 'SELL' and plan['awaiting_proceeds'] == 500
    assert sum(b['amount'] for b in plan['buys']) <= 100  # never the 500 from a sale not yet made


def test_your_limits_apply_and_a_grown_position_is_never_trimmed_for_size():
    grown = [holding('G', 'REDUCE', 800, 1000, score=None)]  # REDUCE for size, as the backtest's rules decide it
    assert allocate(grown, [], 0, rules=LIVE)['sales'] == []
    assert allocate(grown, [], 0)['sales'][0]['action'] == 'TRIM'  # the backtest's frozen rule still trims
    plan = allocate([], [pick('A', 0.6), pick('B', 0.4)], 1000, rules=LIVE)
    assert all(b['amount'] <= 0.15 * 1000 + 1e-9 for b in plan['buys'])


def test_the_three_largest_stay_within_the_combined_limit():
    holdings = [holding('X', 'HOLD', 140, 1000, score=None), holding('Y', 'HOLD', 140, 1000, score=None),
                holding('Z', 'BUY MORE', 100, 1000)] + [holding(f'O{i}', 'HOLD', 62, 1000, score=None) for i in range(10)]
    plan = allocate(holdings, [], 0 + 100, rules=LIVE | {'minimum_purchase_usd': 1.0})
    # Z may grow to 15% alone, but X + Y + Z may not pass 40% of 1100: 440 - 280 = 160 at most for Z.
    z = sum(b['amount'] for b in plan['buys'] if b['qualified_symbol'] == 'Z')
    assert 140 + 140 + 100 + z <= 0.40 * plan['portfolio_after'] + 10 and plan['top3_limited'] == ['Z']


def test_the_same_submission_twice_records_once(store):
    first = store.record_trade('buy', 'LZ.US', 10, 5.9, '2026-10-01', account_amount=45, today=TODAY, request_key='abc-1')
    again = store.record_trade('buy', 'LZ.US', 10, 5.9, '2026-10-01', account_amount=45, today=TODAY, request_key='abc-1')
    assert first == again and len(store.trades()) == 1
    assert store.record_cash('deposit', 200, '2026-10-01', today=TODAY, request_key='d1') == \
        store.record_cash('deposit', 200, '2026-10-01', today=TODAY, request_key='d1')
    assert len(store.cash_movements()) == 1
    store.record_cash('deposit', 200, '2026-10-01', today=TODAY)  # no key: a genuine second deposit is allowed
    assert len(store.cash_movements()) == 2
    with pytest.raises(StoreError): store.record_cash('deposit', 1, '2026-10-01', today=TODAY, request_key='bad key!')


def test_dividends_interest_and_fees_move_the_cash_pool_and_can_be_voided(store):
    store.record_cash('deposit', 200, '2026-10-01', today=TODAY)
    store.record_adjustment('dividend', 3.2, '2026-10-02', qualified_symbol='WU.US', today=TODAY, request_key='div1')
    store.record_adjustment('dividend', 3.2, '2026-10-02', qualified_symbol='WU.US', today=TODAY, request_key='div1')
    store.record_adjustment('fee', 1.5, '2026-10-03', today=TODAY)
    fee = store.record_adjustment('interest', 0.4, '2026-10-04', today=TODAY)
    pool = ledger(store.cash_movements(), [], store.cash_adjustments())
    assert pool['balance'] == pytest.approx(200 + 3.2 - 1.5 + 0.4) and pool['dividends'] == pytest.approx(3.2) and pool['fees'] == 1.5
    store.void_adjustment(fee, reason='Entered twice')
    assert ledger(store.cash_movements(), [], store.cash_adjustments())['balance'] == pytest.approx(201.7)
    with pytest.raises(StoreError): store.record_adjustment('bonus', 1, '2026-10-02', today=TODAY)


def test_a_broker_mismatch_blocks_buys_until_the_balances_agree(store):
    store.record_cash('deposit', 200, '2026-10-01', today=TODAY)
    def check(): return reconcile(store.cash_movements(), [], store.cash_adjustments(), store.broker_balances(), today=TODAY)
    assert check()['status'] == 'unchecked' and not check()['blocks_buys']
    store.record_broker_balance(203.20, '2026-10-05', today=TODAY)
    mismatch = check()
    assert mismatch['status'] == 'mismatch' and mismatch['blocks_buys'] and mismatch['difference'] == pytest.approx(3.2)
    store.record_adjustment('dividend', 3.2, '2026-10-02', today=TODAY)  # the missing entry
    assert check()['status'] == 'matched'
    assert reconcile(store.cash_movements(), [], store.cash_adjustments(), store.broker_balances(), today=date(2026, 12, 1))['status'] == 'stale'


def test_saved_limits_are_validated_and_kept(store):
    saved = store.save_settings(monthly_contribution=200, max_holdings=10, position_limit=0.2, top3_limit=0.5, minimum_trade=30)
    assert (saved['position_limit'], saved['top3_limit'], saved['minimum_trade']) == (0.2, 0.5, 30.0)
    assert store.save_settings(monthly_contribution=250, max_holdings=10)['position_limit'] == 0.2  # left out: kept
    for bad in ({'position_limit': 0.5, 'top3_limit': 0.4}, {'position_limit': 0.01}, {'minimum_trade': -1}):
        with pytest.raises(StoreError): store.save_settings(monthly_contribution=200, max_holdings=10, **bad)


def test_telegram_names_unfunded_picks_proceeds_and_the_plan_it_assumes():
    account = {'available': 100.0, 'awaiting_proceeds': 375.0, 'reconciliation': {'status': 'matched', 'blocks_buys': False}}
    plan = {'buys': [{'action': 'NEW BUY', 'qualified_symbol': 'LZ.US', 'amount_gbp': 60.0, 'shares': 13.5, 'price': 5.92, 'why': 'Open.'}],
            'unfunded_picks': [{'qualified_symbol': 'WU.US', 'rank': 3}], 'account': account}
    monthly = {'allocation': plan, 'plan': {'version': 'ab12cd34', 'assumes': {'cash_pool': 100.0, 'prices_through': '2026-10-09'}}}
    _, text = cash_message(monthly, 'daily')
    assert 'Watch WU.US (pick #3): insufficient available cash' in text
    assert 'About £375 more after your suggested sales are recorded' in text
    assert 'Plan ab12cd34: assumes £100.00 cash and prices to 2026-10-09. Out of date once you record' in text
    held = {'allocation': {'buys': [], 'held_back_buys': [{}], 'account': account | {'reconciliation': {
        'status': 'mismatch', 'blocks_buys': True, 'difference': 3.2, 'as_of': '2026-10-05', 'message': 'Your broker showed £203.20.'}}}}
    fingerprint, warning = cash_message(held, 'daily')
    assert warning.startswith('Buys held back: Your broker showed £203.20.') and '3.2' in fingerprint
