"""Undervaluation ranking rules: exclusions, conviction, risk, ordering and picks (offline)."""
from app.prototype.ranking import RULES, assess_company, value_ranking


def company(sid, *, middle=0.5, cautious=0.05, optimistic=0.9, variation=0.2, latest=110, median=100, years=5,
            positions=('below',), observations=(), latest_year=None, momentum=0.0, risk_flags=0, eligible=True):
    def case(name, vs):
        return {'case': name, 'vs_price': vs, 'value_per_share': None if vs is None else 10 * (1 + vs)}
    year = latest_year or {'values': {'net_income': {'value': 5}, 'equity': {'value': 50}},
                           'calculated': {'operating_margin': 0.12, 'free_cash_flow': 4, 'revenue_growth': 0.03}}
    return {'security_id': sid, 'qualified_symbol': f'{sid.upper()}.US', 'company_name': sid, 'eligible': eligible,
            'calculation': {'momentum_return': momentum},
            'financials': {'years': [year], 'observations': [{'kind': k, 'text': t} for k, t in observations]},
            'events': {'flags': [{'kind': 'risk', 'text': f'flag {i}'} for i in range(risk_flags)]},
            'valuation': {'history': {'comparisons': [{'position': p} for p in positions]},
                          'scenarios': {'available': True, 'measure': 'net income', 'variation': variation, 'years_used': years,
                                        'latest_profit': latest, 'median_profit': median, 'price': 10, 'price_session': '2026-10-08',
                                        'cases': [case('cautious', cautious), case('middle', middle), case('optimistic', optimistic)]}}}


def test_strong_buy_needs_high_conviction_low_risk_and_strong_upside():
    a = assess_company(company('a'))
    assert a['status'] == 'candidate' and a['recommendation'] == 'strong_buy'
    assert a['conviction'] == 'high' and a['conviction_points'] == 5 and a['risk'] == 'low' and a['score'] == 0.5
    b = assess_company(company('b', cautious=-0.1, latest=90))  # 3 of 5 points
    assert b['recommendation'] == 'buy' and b['conviction'] == 'medium'
    assert 'The middle case needs profits to recover to their median.' in b['conviction_against']
    assert b['score'] == 0.5 * RULES['conviction_factor']['medium']


def test_value_traps_are_excluded_even_when_cheap():
    trap = {'values': {'net_income': {'value': -1}, 'equity': {'value': -2}},
            'calculated': {'operating_margin': -0.05, 'free_cash_flow': -3, 'revenue_growth': -0.2}}
    a = assess_company(company('a', middle=2.0, latest_year=trap))
    assert a['status'] == 'value_trap' and len(a['reasons']) == 5
    assert assess_company(company('b', latest_year={'values': {}, 'calculated': {'revenue_growth': -0.05}}))['status'] == 'value_trap'
    assert assess_company(company('c', latest_year={'values': {}, 'calculated': {'revenue_growth': -0.04}}))['status'] == 'candidate'


def test_not_undervalued_watch_and_not_assessable():
    assert assess_company(company('a', middle=0.1))['status'] == 'not_undervalued'
    low = assess_company(company('b', cautious=-0.2, variation=0.8, positions=('above',), latest=50, years=3))
    assert low['status'] == 'watch' and low['conviction'] == 'low'
    risky = assess_company(company('c', observations=[('weakness', 'w1'), ('weakness', 'w2')], momentum=-0.4, risk_flags=1))
    assert risky['risk'] == 'high' and risky['status'] == 'watch' and any('fell 40%' in r for r in risky['risks'])
    missing = company('d'); missing['valuation']['scenarios'] = {'available': False, 'reason': 'Needs three years.'}
    assert assess_company(missing) == {'security_id': 'd', 'qualified_symbol': 'D.US', 'company_name': 'd',
                                       'status': 'not_assessable', 'reasons': ['Needs three years.']}
    no_middle = company('e', middle=None)
    assert assess_company(no_middle)['status'] == 'not_assessable'


def test_ranking_orders_by_score_caps_upside_and_never_forces_picks():
    ranking = value_ranking([company('a', middle=0.4), company('b', middle=5.0), company('c', middle=0.8),
                             company('d', middle=0.6), company('x', middle=0.1), company('z', eligible=False)])
    assert ranking['population'] == 5
    # b's 500% upside is capped at 100% for scoring, so it still ranks first, then c, then d.
    assert ranking['picks'] == ['b', 'c', 'd']
    assert [a['rank'] for a in ranking['companies']] == [1, 2, 3, None, None]
    assert ranking['companies'][0]['score'] == RULES['upside_cap_for_score']
    assert ranking['companies'][-1]['status'] == 'not_undervalued'
    only_one = value_ranking([company('a'), company('b', middle=0.05)])
    assert only_one['picks'] == ['a']
    assert value_ranking([])['picks'] == []
