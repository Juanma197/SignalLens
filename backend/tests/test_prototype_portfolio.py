"""Operator trade ledger, average-cost positions and stored-price valuation (offline)."""
from datetime import date, datetime, timezone
from pathlib import Path

import duckdb
from fastapi.testclient import TestClient
import pytest

from app.model_readiness import fingerprint
from app.prototype.portfolio import positions_from, read_market, valuation
from app.prototype.service import PrototypeError
from app.prototype.store import PrototypeStore, StoreError, symbol

TODAY = date(2026, 10, 9)
NOW = datetime(2026, 10, 9, 12, tzinfo=timezone.utc)


@pytest.fixture
def paths(prototype_fixture, tmp_path):
    research, production = (Path(p) for p in prototype_fixture)
    return research, production, tmp_path / 'prototype' / 'signallens-prototype.duckdb'


def trade(kind, shares, price, day, fees=0.0, sym='SYN01.US', n=[0]):
    n[0] += 1
    return dict(transaction_id=f'{n[0]:04}', kind=kind, qualified_symbol=sym, currency='USD', shares=shares,
                price=price, fees=fees, traded_on=day, recorded_at=f'2026-10-09T00:00:{n[0] % 60:02}+00:00')


def test_average_cost_realised_profit_and_full_exit():
    open_, closed = positions_from([
        trade('buy', 10, 100, '2026-01-02', fees=10),   # cost 1010
        trade('buy', 10, 120, '2026-02-02'),            # cost 2210, 20 shares, average 110.5
        trade('sell', 5, 130, '2026-03-02', fees=5),    # realised 5 * (130 - 110.5) - 5 = 92.5
    ])
    (p,) = open_
    assert closed == [] and p['shares'] == 15
    assert p['average_cost'] == pytest.approx(110.5) and p['cost_basis'] == pytest.approx(1657.5)
    assert p['realised_profit'] == pytest.approx(92.5) and p['fees'] == 15
    open_, closed = positions_from([trade('buy', 4, 50, '2026-01-02'), trade('sell', 4, 40, '2026-02-02')])
    assert open_ == [] and closed[0]['realised_profit'] == pytest.approx(-40) and closed[0]['average_cost'] is None
    # Order is by trade date, not entry order.
    with pytest.raises(PrototypeError, match='PROTOTYPE_SELL_EXCEEDS_HOLDING'):
        positions_from([trade('buy', 4, 50, '2026-03-02'), trade('sell', 4, 40, '2026-02-02')])


def test_symbols_are_normalised_and_validated():
    assert symbol(' aapl ') == 'AAPL.US' and symbol('BRK-B.US') == 'BRK-B.US' and symbol('vod.lse') == 'VOD.LSE'
    for bad in ('', '$$$', 'A' * 30, 'AAPL US'):
        with pytest.raises(StoreError, match='PROTOTYPE_INVALID_SYMBOL'): symbol(bad)


def test_trades_are_append_only_and_voids_keep_history(paths):
    store = PrototypeStore(paths[2], protected_paths=paths[:2])
    assert store.transactions() == [] and not paths[2].exists()
    buy = store.record_trade('buy', 'syn01', 10, 100, '2026-09-01', fees=1, today=TODAY)
    with pytest.raises(StoreError, match='PROTOTYPE_SELL_EXCEEDS_HOLDING'):
        store.record_trade('sell', 'SYN01.US', 11, 100, '2026-09-02', today=TODAY)
    with pytest.raises(StoreError, match='PROTOTYPE_SELL_EXCEEDS_HOLDING'):
        store.record_trade('sell', 'SYN01.US', 5, 100, '2026-08-31', today=TODAY)  # before the buy
    sell = store.record_trade('sell', 'SYN01.US', 4, 110, '2026-09-03', today=TODAY)
    for bad in [dict(shares=0), dict(shares=-1), dict(price=float('nan')), dict(fees=-1)]:
        with pytest.raises(StoreError, match='PROTOTYPE_INVALID_TRADE'):
            store.record_trade('buy', 'SYN02', **({'shares': 1, 'price': 1, 'traded_on': '2026-09-01', 'today': TODAY} | bad))
    with pytest.raises(StoreError, match='PROTOTYPE_INVALID_TRADE_DATE'):
        store.record_trade('buy', 'SYN02', 1, 1, '2026-10-10', today=TODAY)
    with pytest.raises(StoreError, match='PROTOTYPE_INVALID_CURRENCY'):
        store.record_trade('buy', 'SYN02', 1, 1, '2026-09-01', currency='XXX', today=TODAY)
    # Voiding the buy would leave the sell overselling, so it is refused until the sell is voided.
    with pytest.raises(StoreError, match='PROTOTYPE_SELL_EXCEEDS_HOLDING'): store.void_trade(buy)
    store.void_trade(sell, reason='Entered twice.')
    with pytest.raises(StoreError, match='PROTOTYPE_TRANSACTION_ALREADY_VOIDED'): store.void_trade(sell)
    with pytest.raises(StoreError, match='PROTOTYPE_UNKNOWN_TRANSACTION'): store.void_trade('missing')
    rows = {t['transaction_id']: t for t in store.transactions()}
    assert rows[sell]['void_reason'] == 'Entered twice.' and rows[sell]['voided_at'] and rows[buy]['voided_at'] is None
    assert [t['transaction_id'] for t in store.trades()] == [buy]
    with duckdb.connect(str(paths[2]), read_only=True) as db:
        assert db.execute('SELECT count(*) FROM portfolio_transactions').fetchone()[0] == 3


def test_valuation_uses_latest_stored_close_and_never_prices_unknown_symbols(paths):
    before = fingerprint(paths[0])
    market = read_market(paths[0], ['SYN01.US', 'NOPE.US'], NOW)
    assert fingerprint(paths[0]) == before
    with duckdb.connect(str(paths[0]), read_only=True) as db:
        close, day = db.execute("SELECT close, trading_date FROM global_price_observations WHERE qualified_symbol='SYN01.US' ORDER BY trading_date DESC LIMIT 1").fetchone()
    assert market['SYN01.US']['price'] == {'close': float(close), 'trading_date': str(day)}
    assert len(market['SYN01.US']['listings']) == 1 and 'NOPE.US' not in market
    # Prices retrieved after the valuation time are invisible.
    assert read_market(paths[0], ['SYN01.US'], datetime(2000, 1, 1, tzinfo=timezone.utc)) == {'SYN01.US': {'listings': market['SYN01.US']['listings']}}
    result = valuation([trade('buy', 10, 1, '2026-09-01'), trade('buy', 3, 20, '2026-09-01', sym='NOPE.US')], market, as_of=NOW)
    priced, unpriced = sorted(result['positions'], key=lambda p: p['market_value'] is None)
    assert priced['market_value'] == pytest.approx(10 * float(close)) and priced['weight'] == 1
    assert priced['security_id'] == market['SYN01.US']['listings'][0]['security_id']
    assert unpriced['market_value'] is None and unpriced['weight'] is None and unpriced['unrealised_profit'] is None
    (total,) = result['totals']
    assert total['positions'] == 2 and total['priced_positions'] == 1 and total['cost_basis'] == 70
    assert total['unrealised_profit'] == pytest.approx(10 * float(close) - 10)


def test_portfolio_api_writes_only_when_enabled(paths, monkeypatch):
    from app.config import Settings
    from app import main
    from app.prototype import api
    headers = {'Authorization': 'Bearer test-token'}
    before = [fingerprint(p) for p in paths[:2]]
    body = {'kind': 'buy', 'qualified_symbol': 'syn01', 'shares': 10, 'price': 5, 'traded_on': '2026-09-01'}
    for enabled in (False, True):
        settings = Settings(database_path=paths[1], research_database_path=paths[0], prototype_database_path=paths[2],
                            staging_mode=True, api_token='test-token', prototype_writes_enabled=enabled)
        monkeypatch.setattr(main, 'settings', settings); monkeypatch.setattr(api, 'get_settings', lambda: settings)
        api._MARKET.clear()
        with TestClient(main.app) as client:
            response = client.post('/api/v1/research/prototype/store/portfolio/trades', headers=headers, json=body)
            if not enabled:
                assert response.status_code == 409
                assert client.get('/api/v1/research/prototype/store/portfolio', headers=headers).json()['positions'] == []
                continue
            assert response.status_code == 200, response.text
            (position,) = response.json()['positions']
            assert position['qualified_symbol'] == 'SYN01.US' and position['price'] and position['security_id']
            oversell = client.post('/api/v1/research/prototype/store/portfolio/trades', headers=headers, json=body | {'kind': 'sell', 'shares': 11})
            assert oversell.json()['detail']['code'] == 'PROTOTYPE_SELL_EXCEEDS_HOLDING'
            assert client.post('/api/v1/research/prototype/store/portfolio/trades', headers=headers, json=body | {'kind': 'hold'}).status_code == 422
            tid = response.json()['transactions'][0]['transaction_id']
            voided = client.post('/api/v1/research/prototype/store/portfolio/voids', headers=headers, json={'transaction_id': tid})
            assert voided.status_code == 200 and voided.json()['positions'] == []
            assert client.post('/api/v1/research/prototype/store/portfolio/voids', headers=headers, json={'transaction_id': 'x'}).status_code == 404
    assert [fingerprint(p) for p in paths[:2]] == before
