"""The cash pool, its pound amounts per trade and the editable portfolio settings (offline)."""
from datetime import date
from pathlib import Path

import duckdb
from fastapi.testclient import TestClient
import pytest

from app.model_readiness import fingerprint
from app.prototype.cash import implied_gbp_rate, ledger
from app.prototype.store import PrototypeStore, StoreError

TODAY = date(2026, 10, 9)


@pytest.fixture
def paths(prototype_fixture, tmp_path):
    research, production = (Path(p) for p in prototype_fixture)
    return research, production, tmp_path / 'prototype' / 'signallens-prototype.duckdb'


def movement(kind, amount, day, n=[0], voided=False):
    n[0] += 1
    return dict(movement_id=f'm{n[0]}', kind=kind, amount=amount, moved_on=day, note=None,
                recorded_at=f'2026-10-09T00:00:{n[0] % 60:02}+00:00', voided_at='x' if voided else None)


def trade(kind, shares, price, day, account_amount, currency='USD', n=[0]):
    n[0] += 1
    return dict(transaction_id=f't{n[0]}', kind=kind, qualified_symbol='SYN01.US', currency=currency, shares=shares, price=price,
                fees=0, traded_on=day, recorded_at=f'2026-10-09T00:01:{n[0] % 60:02}+00:00', account_amount=account_amount, voided_at=None)


def test_cash_pool_carries_over_and_recycles_sale_proceeds():
    movements = [movement('deposit', 35, '2026-08-20'), movement('deposit', 200, '2026-09-01'),
                 movement('deposit', 999, '2026-09-02', voided=True), movement('withdrawal', 10, '2026-09-03')]
    trades = [trade('buy', 2, 100, '2026-09-04', 150), trade('sell', 1, 140, '2026-09-15', 110), trade('buy', 1, 50, '2026-09-16', None)]
    pool = ledger(movements, trades)
    # 35 + 200 - 10 - 150 + 110; the voided deposit and the trade without pounds are left out.
    assert pool['balance'] == pytest.approx(185) and not pool['overdrawn']
    assert (pool['deposited'], pool['withdrawn'], pool['spent_on_buys'], pool['received_from_sales']) == (235, 10, 150, 110)
    assert [t['transaction_id'] for t in pool['uncounted_trades']] == [trades[2]['transaction_id']]
    assert pool['deposited_this_month'] == 200  # September, the latest entry's month
    assert pool['entries'][0]['balance'] == pytest.approx(185) and pool['entries'][-1]['balance'] == 35
    earlier = ledger(movements, trades, until=date(2026, 9, 10))
    assert earlier['balance'] == pytest.approx(75) and earlier['received_from_sales'] == 0
    assert ledger([], [trade('buy', 1, 100, '2026-09-04', 80)])['overdrawn']


def test_implied_rate_comes_from_the_latest_trade_with_pounds():
    trades = [trade('buy', 2, 100, '2026-09-04', 150), trade('buy', 1, 100, '2026-09-20', 79), trade('buy', 1, 100, '2026-09-30', None)]
    assert implied_gbp_rate(trades, 'USD') == {'rate': 0.79, 'observed_on': '2026-09-20', 'source': 'your_last_trade'}
    assert implied_gbp_rate(trades, 'USD', until=date(2026, 9, 10))['rate'] == 0.75
    assert implied_gbp_rate(trades, 'EUR') is None


def test_store_cash_movements_trade_pounds_and_settings(paths):
    store = PrototypeStore(paths[2], protected_paths=paths[:2])
    assert store.settings() == {'monthly_contribution': 200.0, 'max_holdings': 10, 'fractional_shares': True, 'currency': 'GBP',
                                'position_limit': 0.15, 'top3_limit': 0.40, 'minimum_trade': 25.0, 'is_default': True, 'recorded_at': None}
    assert store.cash_movements() == [] and not paths[2].exists()
    deposit = store.record_cash('deposit', 200, '2026-10-01', today=TODAY)
    store.record_cash('withdrawal', 20, '2026-10-02', note='Fees', today=TODAY)
    for bad in [dict(kind='gift'), dict(amount=0), dict(amount=-5), dict(moved_on='2026-10-10')]:
        with pytest.raises(StoreError):
            store.record_cash(**({'kind': 'deposit', 'amount': 1, 'moved_on': '2026-10-01', 'today': TODAY} | bad))
    store.void_cash(deposit, reason='Arrived on the 2nd')
    with pytest.raises(StoreError, match='PROTOTYPE_TRANSACTION_ALREADY_VOIDED'): store.void_cash(deposit)
    with pytest.raises(StoreError, match='PROTOTYPE_UNKNOWN_CASH_MOVEMENT'): store.void_cash('missing')
    assert {m['movement_id']: m['voided_at'] is not None for m in store.cash_movements()}[deposit]
    # A pound trade defaults to its own total; a dollar trade keeps what the broker reported, or nothing.
    store.record_trade('buy', 'VOD.LSE', 10, 0.7, '2026-10-01', fees=1, currency='GBP', today=TODAY)
    store.record_trade('buy', 'SYN01', 2, 100, '2026-10-01', account_amount=160.5, today=TODAY)
    store.record_trade('buy', 'SYN02', 1, 10, '2026-10-01', today=TODAY)
    with pytest.raises(StoreError, match='PROTOTYPE_INVALID_TRADE'):
        store.record_trade('buy', 'SYN03', 1, 10, '2026-10-01', account_amount=0, today=TODAY)
    amounts = {t['qualified_symbol']: t['account_amount'] for t in store.trades()}
    assert amounts == {'VOD.LSE': pytest.approx(8), 'SYN01.US': 160.5, 'SYN02.US': None}
    saved = store.save_settings(monthly_contribution=150, max_holdings=8)
    assert saved['monthly_contribution'] == 150 and saved['max_holdings'] == 8 and not saved['is_default'] and saved['fractional_shares']
    assert not store.save_settings(monthly_contribution=150, max_holdings=8, fractional_shares=False)['fractional_shares']
    for bad in [dict(monthly_contribution=-1), dict(monthly_contribution=float('inf')), dict(max_holdings=0), dict(max_holdings=2.5)]:
        with pytest.raises(StoreError, match='PROTOTYPE_INVALID_SETTINGS'):
            store.save_settings(**({'monthly_contribution': 200, 'max_holdings': 10} | bad))
    store.save_settings(monthly_contribution=0, max_holdings=10)  # skipping contributions is allowed
    assert store.settings()['monthly_contribution'] == 0
    with duckdb.connect(str(paths[2]), read_only=True) as db:
        assert db.execute('SELECT count(*) FROM portfolio_settings').fetchone()[0] == 3  # history kept


def test_settings_saved_before_the_fractional_choice_default_to_fractional(paths):
    paths[2].parent.mkdir(parents=True)
    with duckdb.connect(str(paths[2])) as db:
        db.execute("""CREATE TABLE portfolio_settings(setting_id VARCHAR PRIMARY KEY, monthly_contribution DOUBLE NOT NULL,
                      max_holdings INTEGER NOT NULL, recorded_at TIMESTAMPTZ NOT NULL)""")
        db.execute("INSERT INTO portfolio_settings VALUES ('old', 250, 8, now())")
    store = PrototypeStore(paths[2], protected_paths=paths[:2])
    assert store.settings()['fractional_shares'] is True and store.settings()['max_holdings'] == 8
    assert store.save_settings(monthly_contribution=250, max_holdings=8, fractional_shares=False)['fractional_shares'] is False


def test_a_store_from_before_cash_tracking_still_reads(paths):
    paths[2].parent.mkdir(parents=True)
    with duckdb.connect(str(paths[2])) as db:
        db.execute("""CREATE TABLE portfolio_transactions(
          transaction_id VARCHAR PRIMARY KEY, kind VARCHAR NOT NULL, qualified_symbol VARCHAR, company_name VARCHAR, shares DOUBLE,
          price DOUBLE, fees DOUBLE, currency VARCHAR, traded_on DATE, note VARCHAR, voids_transaction_id VARCHAR, recorded_at TIMESTAMPTZ NOT NULL)""")
        db.execute("INSERT INTO portfolio_transactions VALUES ('old', 'buy', 'SYN01.US', NULL, 1, 10, 0, 'USD', '2026-09-01', NULL, NULL, now())")
    store = PrototypeStore(paths[2], protected_paths=paths[:2])
    before = fingerprint(paths[2])
    assert store.trades()[0]['account_amount'] is None and fingerprint(paths[2]) == before  # reading never migrates
    store.record_trade('buy', 'SYN01', 1, 10, '2026-09-02', account_amount=8, today=TODAY)
    assert sorted(str(t['account_amount']) for t in store.trades()) == ['8.0', 'None']


def test_portfolio_api_cash_and_settings(paths, monkeypatch):
    from app.config import Settings
    from app import main
    from app.prototype import api
    settings = Settings(database_path=paths[1], research_database_path=paths[0], prototype_database_path=paths[2],
                        staging_mode=True, api_token='test-token', prototype_writes_enabled=True)
    monkeypatch.setattr(main, 'settings', settings); monkeypatch.setattr(api, 'get_settings', lambda: settings)
    api._MARKET.clear()
    headers = {'Authorization': 'Bearer test-token'}
    root = '/api/v1/research/prototype/store/portfolio'
    with TestClient(main.app) as client:
        body = client.get(root, headers=headers).json()
        assert body['cash']['balance'] == 0 and body['settings']['is_default'] and body['account_currency'] == 'GBP'
        body = client.post(f'{root}/cash', headers=headers, json={'kind': 'deposit', 'amount': 200, 'moved_on': '2026-09-01'}).json()
        assert body['cash']['balance'] == 200
        body = client.post(f'{root}/trades', headers=headers, json={'kind': 'buy', 'qualified_symbol': 'SYN01', 'shares': 1, 'price': 100,
                                                                    'traded_on': '2026-09-02', 'account_amount': 79}).json()
        assert body['cash']['balance'] == 121 and body['transactions'][0]['account_amount'] == 79
        movement_id = body['cash']['entries'][-1]['movement_id']
        assert client.post(f'{root}/cash/voids', headers=headers, json={'movement_id': movement_id}).json()['cash']['balance'] == -79
        assert client.post(f'{root}/cash/voids', headers=headers, json={'movement_id': 'nope'}).status_code == 404
        assert client.post(f'{root}/cash', headers=headers, json={'kind': 'gift', 'amount': 1, 'moved_on': '2026-09-01'}).status_code == 422
        body = client.post(f'{root}/settings', headers=headers, json={'monthly_contribution': 300, 'max_holdings': 6, 'fractional_shares': False}).json()
        assert body['settings']['monthly_contribution'] == 300 and body['settings']['max_holdings'] == 6
        assert body['settings']['fractional_shares'] is False
        assert client.post(f'{root}/settings', headers=headers, json={'monthly_contribution': 300, 'max_holdings': 0}).status_code == 422
