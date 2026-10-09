"""Analyst brief: sections restate evidence; counterarguments are fixed rules."""
from datetime import datetime, timezone

from app.prototype.brief import analyst_brief

DECISION = datetime(2026, 10, 8, tzinfo=timezone.utc)


def company(momentum, operating_margin, free_cash_flow, comparisons=(), observations=(), flags=(), notes=(), scenarios=None):
    return {'calculation': {'momentum_return': momentum, 'start_session': '2026-04-08', 'end_session': '2026-10-07'},
            'size': {'market_cap_usd': 2e9, 'shares_outstanding': 2e7, 'close': 100.0},
            'industry': {'sic': 3560, 'sic_description': 'General Industrial Machinery'}, 'sector_notes': list(notes), 'risks': [],
            'financials': {'years': [{'calculated': {'operating_margin': operating_margin, 'free_cash_flow': free_cash_flow, 'revenue_growth': -0.02}}],
                           'observations': list(observations), 'not_available': ['interest coverage (no interest-expense facts stored)']},
            'valuation': {'multiples': {'price_to_earnings': 9.0}, 'not_meaningful': [], 'history': {'comparisons': list(comparisons)}, 'scenarios': scenarios},
            'events': {'flags': list(flags), 'results_timing': None, 'latest_known_at': datetime(2026, 9, 1, tzinfo=timezone.utc)}}


def test_momentum_without_profits_is_challenged():
    b = analyst_brief(company(0.9, -0.01, -5e6), DECISION, result=True)
    assert any('up 90%' in c and 'operating loss and negative free cash flow' in c for c in b['counterarguments'])
    assert b['sections'][1]['points'][1].startswith("It is one of this month's qualifying results")


def test_cheap_versus_history_and_falling_price_are_challenged():
    below = {'multiple': 'price_to_earnings', 'position': 'below', 'text': 'P/E 9.0 vs its own 5-year range 12.0-16.0 (median 14.0): below the range.'}
    b = analyst_brief(company(-0.25, 0.1, 5e6, comparisons=[below]), DECISION, result=False)
    text = ' '.join(b['counterarguments'])
    assert 'market expects earnings to fall; latest revenue growth was -2.0%' in text and 'keep getting cheaper' in text
    assert below['text'] in b['sections'][3]['points']


def test_flags_notes_dilution_and_missing_evidence():
    flag = {'kind': 'risk', 'category': 'capital_raise', 'count': 1, 'text': 'A capital raise was filed.'}
    dilution = {'kind': 'weakness', 'area': 'dilution', 'text': 'Diluted share count rose 8.0% a year.'}
    b = analyst_brief(company(0.1, 0.1, 5e6, flags=[flag], observations=[dilution], notes=['Oil and gas: commodity prices.']), DECISION, result=False)
    assert 'A capital raise was filed.' in b['sections'][5]['points']
    assert any('owns less' in c for c in b['counterarguments']) and any('sector note' in c for c in b['counterarguments'])
    assert 'Interest coverage (no interest-expense facts stored): not available.' in b['missing_evidence']
    assert any('last retrieved 2026-09-01' in m for m in b['missing_evidence'])


def test_no_triggered_counterargument_says_so():
    b = analyst_brief(company(0.1, 0.1, 5e6), DECISION, result=False)
    assert b['counterarguments'] == ['No rule-based counterargument was triggered; look for one yourself.']


def test_scenarios_appear_in_valuation_and_an_expensive_middle_case_is_challenged():
    scenarios = {'available': True, 'measure': 'free cash flow', 'multiple_name': 'P/FCF', 'price': 100.0, 'years_used': 5,
                 'latest_profit': 5.0, 'median_profit': 8.0, 'volatility_note': 'Free cash flow varied a lot; treat this range as unreliable.',
                 'cases': [{'case': 'cautious', 'value_per_share': 40.0, 'vs_price': -0.6}, {'case': 'middle', 'value_per_share': 80.0, 'vs_price': -0.2},
                           {'case': 'optimistic', 'value_per_share': None, 'vs_price': None}]}
    b = analyst_brief(company(0.1, 0.1, 5e6, scenarios=scenarios), DECISION, result=False)
    assert any('cautious $40.00 (-60%), middle $80.00 (-20%), optimistic n/a vs price $100.00' in p for p in b['sections'][3]['points'])
    assert any('20% below today' in c for c in b['counterarguments'])
    assert any('assumes a recovery that has not happened yet' in c for c in b['counterarguments'])
    assert 'Free cash flow varied a lot; treat this range as unreliable.' in b['counterarguments']
