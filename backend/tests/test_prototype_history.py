"""Your money against the same deposits in the VALL stand-in (offline)."""
from datetime import date, timedelta
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from app.prototype import store as store_module
from app.prototype.fixture import DECISION
from app.prototype.history import money_weighted, versus_fund

TODAY = date(2026, 10, 9)


def deposit(on, amount, kind='deposit', voided=None):
    return {'moved_on': on, 'amount': amount, 'kind': kind, 'voided_at': voided}


def test_each_deposit_buys_the_fund_on_its_day_at_that_days_rate():
    fund = {date(2026, 8, 3): 100.0, date(2026, 9, 1): 110.0, TODAY: 121.0}
    rates = {date(2026, 8, 3): 0.8, date(2026, 8, 31): 0.75, TODAY: 0.8}
    movements = [deposit('2026-08-03', 200), deposit('2026-09-01', 200), deposit('2026-09-02', 999, voided='x')]
    r = versus_fund(movements, fund=fund, rates=rates, your_value=520.0, today=TODAY)
    # 200 / (0.8 x 100) = 2.5 units; 200 / (0.75 [31 Aug, the latest rate] x 110) = 2.4242 units; today x 121 x 0.8.
    assert r['flows'][1]['units'] == pytest.approx(2.5) and r['flows'][0]['gbp_per_usd'] == 0.75
    assert r['fund_value'] == pytest.approx((2.5 + 200 / (0.75 * 110)) * 121 * 0.8)
    assert r['net_deposited'] == 400 and r['complete'] is True
    assert r['difference'] == pytest.approx(520 - r['fund_value'])
    assert r['your_money_weighted'] > r['fund_money_weighted'] > 0


def test_withdrawals_sell_and_missing_prices_make_the_comparison_incomplete():
    fund = {date(2026, 9, 1): 100.0, TODAY: 100.0}
    rates = {date(2026, 9, 1): 1.0, TODAY: 1.0}
    r = versus_fund([deposit('2026-09-01', 300), deposit('2026-09-01', 100, kind='withdrawal')], fund=fund, rates=rates,
                    your_value=200.0, today=TODAY)
    assert r['fund_value'] == pytest.approx(200) and r['net_deposited'] == 200 and r['difference'] == pytest.approx(0)
    gap = versus_fund([deposit('2026-06-01', 300)], fund=fund, rates=rates, your_value=300.0, today=TODAY)
    assert gap['fund_value'] is None and gap['complete'] is False and gap['difference'] is None
    assert gap['unpriced_flows'] == [{'on': '2026-06-01', 'amount': 300.0, 'missing': 'fund price'}]
    unsure = versus_fund([deposit('2026-09-01', 300)], fund=fund, rates=rates, your_value=300.0, today=TODAY, your_value_complete=False)
    assert unsure['difference'] is None and unsure['fund_value'] == pytest.approx(300)
    assert 'note' in versus_fund([], fund=fund, rates=rates, your_value=0.0, today=TODAY)


def test_money_weighted_return_matches_a_simple_year():
    assert money_weighted([(TODAY - timedelta(days=365), 100)], 110, TODAY) == pytest.approx(0.10, abs=1e-3)
    assert money_weighted([], 110, TODAY) is None and money_weighted([(TODAY, 100)], None, TODAY) is None


@pytest.fixture
def paths(prototype_fixture, tmp_path):
    research, production = (Path(p) for p in prototype_fixture)
    return research, production, tmp_path / 'prototype' / 'signallens-prototype.duckdb'


def test_endpoint_reports_without_fund_prices(paths, monkeypatch):
    from app.config import Settings
    from app import main
    from app.prototype import api
    monkeypatch.setattr(store_module, '_now', lambda: DECISION + timedelta(days=1))
    settings = Settings(database_path=paths[1], research_database_path=paths[0], prototype_database_path=paths[2],
                        staging_mode=True, api_token='t', prototype_writes_enabled=True)
    monkeypatch.setattr(main, 'settings', settings); monkeypatch.setattr(api, 'get_settings', lambda: settings)
    api._MARKET.clear()
    headers, base = {'Authorization': 'Bearer t'}, '/api/v1/research/prototype'
    with TestClient(main.app) as client:
        assert client.post(f'{base}/store/portfolio/cash', headers=headers, json={'kind': 'deposit', 'amount': 200, 'moved_on': '2026-09-30'}).status_code == 200
        body = client.get(f'{base}/history/versus-vall', headers=headers).json()
    assert body['net_deposited'] == 200 and body['fund_value'] is None and 'benchmarks' in body['note']
    assert body['unpriced_flows'][0]['missing'] == 'fund price'
