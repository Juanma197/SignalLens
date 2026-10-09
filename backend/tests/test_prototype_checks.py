"""Thesis checks: operator conditions, automatic warning signs, versioned storage, API (offline)."""
from datetime import date, timedelta
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from app.model_readiness import fingerprint
from app.prototype import store as store_module
from app.prototype.checks import CheckError, evaluate, validate
from app.prototype.fixture import DECISION
from app.prototype.store import PrototypeStore, StoreError

DAY = date(2026, 10, 9)


def company(*, year_end='2025-12-31', calculated=None, values=None, flags=(), middle=0.4, eligible=True):
    year = {'fiscal_year_end': year_end, 'values': {k: {'value': v} for k, v in (values or {'net_income': 5, 'equity': 50}).items()},
            'calculated': calculated if calculated is not None else {'operating_margin': 0.12, 'free_cash_flow': 4, 'revenue_growth': 0.03}}
    return {'security_id': 'a', 'qualified_symbol': 'A.US', 'company_name': 'A', 'eligible': eligible,
            'calculation': {'momentum_return': -0.1}, 'size': {'close': 20.0},
            'financials': {'years': [year]} if eligible else {'years': [], 'latest_year': year},
            'events': {'flags': [{'kind': 'risk', 'text': t} for t in flags]},
            'valuation': {'scenarios': {'available': True, 'cases': [{'case': 'middle', 'vs_price': middle}]}}}


def check(metric, comparator, threshold):
    return {'metric': metric, 'comparator': comparator, 'threshold': threshold}


def test_conditions_hold_break_or_stay_unknown():
    r = evaluate(company(), [check('operating_margin', 'at_least', 0.10), check('revenue_growth', 'at_least', 0.05),
                             check('current_ratio', 'at_least', 1.0), check('close', 'at_most', 25)], DAY)
    assert [c['status'] for c in r['checks']] == ['holds', 'broken', 'unknown', 'holds']
    assert r['checks'][0]['fiscal_year_end'] == '2025-12-31' and r['checks'][3]['fiscal_year_end'] is None
    assert r['overall'] == 'broken'
    assert evaluate(company(), [check('operating_margin', 'at_least', 0.10)], DAY)['overall'] == 'intact'


def test_automatic_signs_break_or_warn_without_any_own_checks():
    trap = evaluate(company(calculated={'operating_margin': -0.02}, values={'net_income': -1}), [], DAY)
    assert trap['overall'] == 'broken' and not trap['has_own_checks']
    assert {a['text'] for a in trap['automatic']} >= {'Operating loss in the latest fiscal year.', 'Net loss in the latest fiscal year.'}
    warned = evaluate(company(flags=['Auditor change.'], middle=0.05), [], DAY)
    assert warned['overall'] == 'warning' and [a['kind'] for a in warned['automatic']] == ['filing_risk', 'valuation']
    # Non-eligible companies keep only their latest year, which is enough to check.
    assert evaluate(company(eligible=False, calculated={'revenue_growth': -0.2}), [], DAY)['overall'] == 'broken'


def test_stale_or_missing_figures_are_unknown_never_a_pass():
    stale = evaluate(company(year_end=str(DAY - timedelta(days=551))), [check('operating_margin', 'at_least', 0)], DAY)
    assert stale['checks'][0]['status'] == 'unknown' and stale['overall'] == 'unknown'
    assert 'more than 550 days' in stale['automatic'][0]['text']
    empty = company(); empty['financials'] = {'years': []}
    assert evaluate(empty, [], DAY)['overall'] == 'unknown'


def test_validation():
    assert validate([check('net_margin', 'at_most', '0.5') | {'note': ' why '}]) == [
        {'metric': 'net_margin', 'comparator': 'at_most', 'threshold': 0.5, 'note': 'why'}]
    for bad in ([check('made_up', 'at_least', 1)], [check('net_margin', 'equals', 1)], [check('net_margin', 'at_least', 'nan')],
                [check('net_margin', 'at_least', None)], [check('net_margin', 'at_least', 1)] * 21, 'x'):
        with pytest.raises(CheckError): validate(bad)


@pytest.fixture
def paths(prototype_fixture, tmp_path):
    research, production = (Path(p) for p in prototype_fixture)
    return research, production, tmp_path / 'prototype' / 'signallens-prototype.duckdb'


def test_check_sets_are_versioned(paths):
    store = PrototypeStore(paths[2], protected_paths=paths[:2])
    assert store.check_sets('x') == [] and store.current_checks() == {}
    store.add_check_set('x', [check('net_margin', 'at_least', 0.05)])
    store.add_check_set('x', [])
    versions = store.check_sets('x')
    assert [len(v['checks']) for v in versions] == [0, 1] and store.current_checks() == {'x': []}
    with pytest.raises(StoreError, match='PROTOTYPE_INVALID_CHECKS'): store.add_check_set('x', [check('nope', 'at_least', 1)])


def test_thesis_checks_api_covers_held_watched_and_checked_companies(paths, monkeypatch):
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
    with TestClient(main.app) as client:
        assert client.post(f'{base}/store/watchlist', headers=headers, json={'security_id': 'synthetic-03', 'action': 'add'}).status_code == 200
        trade = {'kind': 'buy', 'qualified_symbol': 'SYN05', 'shares': 1, 'price': 1, 'traded_on': '2026-09-01'}
        trade_response = client.post(f'{base}/store/portfolio/trades', headers=headers, json=trade | {'qualified_symbol': 'ZZZZ'})
        assert trade_response.status_code == 200, trade_response.text
        assert client.post(f'{base}/store/portfolio/trades', headers=headers, json=trade).status_code == 200
        saved = client.post(f'{base}/store/checks', headers=headers, json={'security_id': 'synthetic-07', 'checks': [
            check('price_change_126', 'at_least', -1), check('close', 'at_least', 1e9)]})
        assert saved.status_code == 200 and len(saved.json()['versions']) == 1
        assert client.post(f'{base}/store/checks', headers=headers, json={'security_id': 'synthetic-07', 'checks': [check('x', 'at_least', 1)]}).status_code == 409
        result = client.get(f'{base}/thesis-checks', headers=headers, params={'decision_at': DECISION.isoformat()}).json()
        by_id = {c['security_id']: c for c in result['companies']}
        assert set(by_id) == {'synthetic-03', 'synthetic-05', 'synthetic-07'}
        assert by_id['synthetic-03']['interest'] == ['watched'] and by_id['synthetic-05']['interest'] == ['held']
        # The fixture has no annual figures: unknown, never intact.
        assert by_id['synthetic-03']['overall'] == 'unknown'
        assert by_id['synthetic-07']['overall'] == 'broken' and [c['status'] for c in by_id['synthetic-07']['checks']] == ['holds', 'broken']
        assert result['companies'][0]['security_id'] == 'synthetic-07'  # broken first
        assert result['uncovered_holdings'] == ['ZZZZ.US']
        one = client.get(f'{base}/thesis-checks', headers=headers, params={'decision_at': DECISION.isoformat(), 'security_id': 'unknown-id'}).json()
        assert one['companies'][0]['overall'] == 'not_covered'
    assert [fingerprint(p) for p in paths[:2]] == before
