"""Monthly holding decisions: rule order, hold band, position limits and the API (offline)."""
from datetime import timedelta
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from app.model_readiness import fingerprint
from app.prototype import store as store_module
from app.prototype.decisions import decide
from app.prototype.fixture import DECISION


def checks(overall='intact', own=(), automatic=()):
    return {'overall': overall, 'checks': [{'label': 'Operating margin', 'comparator': 'at_least', 'threshold': 0.1, 'status': s} for s in own],
            'automatic': [{'severity': s, 'text': t} for s, t in automatic]}


def value(upside=0.4, status='candidate'):
    return {'upside': upside, 'status': status, 'conviction': 'medium', 'risk': 'low'}


def test_rule_order_and_reasons():
    position = {'weight': 0.10}
    assert decide(position, value(), None)['decision'] == 'REVIEW'
    assert decide(position, value(), checks('not_covered'))['decision'] == 'REVIEW'
    broken = decide(position, value(), checks('broken', own=['broken']))
    assert broken['decision'] == 'SELL' and 'Operating margin at least 0.1 no longer holds.' in broken['reasons']
    trap = decide(position, value(), checks('broken', automatic=[('broken', 'Net loss in the latest fiscal year.')]))
    assert trap['decision'] == 'SELL' and 'Net loss in the latest fiscal year.' in trap['reasons']
    assert decide(position, value(-0.25), checks())['decision'] == 'SELL'
    assert decide(position, value(-0.05), checks())['decision'] == 'REDUCE'
    assert decide({'weight': 0.40}, value(), checks())['decision'] == 'REDUCE'
    buy = decide(position, value(), checks())
    assert buy['decision'] == 'BUY MORE' and buy['evidence'] == {'upside': 0.4, 'weight': 0.1, 'thesis': 'intact',
                                                               'value_status': 'candidate', 'conviction': 'medium', 'risk': 'low', 'score': None}


def test_hold_band_and_blockers_prevent_churn():
    position = {'weight': 0.10}
    for upside in (0.0, 0.05, 0.14):  # between REDUCE and BUY MORE
        assert decide(position, value(upside), checks())['decision'] == 'HOLD'
    assert decide({'weight': 0.30}, value(), checks())['decision'] == 'HOLD'  # full but under the reduce limit
    watch = decide(position, value(status='watch'), checks())
    assert watch['decision'] == 'HOLD' and 'Conviction is too low or risk too high to add.' in watch['reasons']
    warned = decide(position, value(), checks('warning', automatic=[('warning', 'Auditor change.')]))
    assert warned['decision'] == 'HOLD' and 'Warnings: Auditor change.' in warned['reasons']
    unknown = decide(position, value(), checks('unknown', own=['unknown']))
    assert unknown['decision'] == 'HOLD' and any('cannot be checked' in r for r in unknown['reasons'])
    assert decide({'weight': None}, value(), checks())['decision'] == 'HOLD'
    assert decide(position, None, checks())['decision'] == 'HOLD'


@pytest.fixture
def paths(prototype_fixture, tmp_path):
    research, production = (Path(p) for p in prototype_fixture)
    return research, production, tmp_path / 'prototype' / 'signallens-prototype.duckdb'


def test_monthly_api_combines_picks_holdings_and_checks(paths, monkeypatch):
    from app.config import Settings
    from app import main
    from app.prototype import api
    monkeypatch.setattr(store_module, '_now', lambda: DECISION + timedelta(days=1))
    headers = {'Authorization': 'Bearer test-token'}
    before = [fingerprint(p) for p in paths[:2]]
    settings = Settings(database_path=paths[1], research_database_path=paths[0], prototype_database_path=paths[2],
                        staging_mode=True, api_token='test-token', prototype_writes_enabled=True)
    monkeypatch.setattr(main, 'settings', settings); monkeypatch.setattr(api, 'get_settings', lambda: settings)
    api._CACHE.clear(); api._MARKET.clear()
    base = '/api/v1/research/prototype'
    trade = {'kind': 'buy', 'shares': 10, 'price': 1, 'traded_on': '2026-09-01'}
    with TestClient(main.app) as client:
        for symbol in ('SYN05', 'SYN07', 'ZZZZ'):
            assert client.post(f'{base}/store/portfolio/trades', headers=headers, json=trade | {'qualified_symbol': symbol}).status_code == 200
        # Recorded after the cutoff: invisible to that month's decisions.
        assert client.post(f'{base}/store/portfolio/trades', headers=headers, json=trade | {'qualified_symbol': 'SYN09', 'traded_on': '2026-10-02'}).status_code == 200
        client.post(f'{base}/store/checks', headers=headers, json={'security_id': 'synthetic-07', 'checks': [
            {'metric': 'close', 'comparator': 'at_least', 'threshold': 1e9}]})
        result = client.get(f'{base}/monthly', headers=headers, params={'decision_at': DECISION.isoformat()})
        assert result.status_code == 200, result.text
        body = result.json()
        by_symbol = {h['qualified_symbol']: h for h in body['holdings']}
        assert set(by_symbol) == {'SYN05.US', 'SYN07.US', 'ZZZZ.US'}
        assert by_symbol['SYN07.US']['decision'] == 'SELL'          # its own condition broke
        assert by_symbol['ZZZZ.US']['decision'] == 'REVIEW'         # outside SignalLens data
        # About half the priced portfolio: above the concentration limit.
        assert by_symbol['SYN05.US']['decision'] == 'REDUCE' and 'above the 35% limit' in by_symbol['SYN05.US']['reasons'][0]
        assert [h['decision'] for h in body['holdings']] == ['SELL', 'REDUCE', 'REVIEW']
        assert body['counts'] == {'SELL': 1, 'REDUCE': 1, 'REVIEW': 1, 'BUY MORE': 0, 'HOLD': 0}
        assert body['picks'] == [] and body['totals'][0]['priced_positions'] == 2
    assert [fingerprint(p) for p in paths[:2]] == before
