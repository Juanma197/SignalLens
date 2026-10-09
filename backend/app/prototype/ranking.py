"""Monthly undervaluation ranking: at most three candidates, never forced.

Every rule is fixed and shown with its evidence. Upside is the middle scenario
(median profit x median own multiple) against the decision-session close. A
company is excluded when the range cannot be computed, when the upside is below
the minimum, or when it shows a value-trap sign: cheap because the business is
getting worse. Conviction rises with evidence that the discount is real (a
margin of safety, steady profits, cheap against its own history, no recovery
needed, full coverage); risk rises with each weakness. Score = capped upside x
conviction factor x risk factor. Unvalidated arithmetic, not advice: nothing here
has been tested against later returns.
"""
from .financials import FINANCIAL_RULES, VOLATILE_VARIATION

RULES = {
    'minimum_upside': 0.15,
    'strong_upside': 0.30,
    'upside_cap_for_score': 1.0,
    'sharp_fall_126_sessions': -0.30,
    'material_revenue_decline': FINANCIAL_RULES['material_revenue_decline'],
    'volatile_variation': VOLATILE_VARIATION,
    'conviction_levels': {'high': 4, 'medium': 2},     # points out of 5
    'risk_levels': {'high': 4, 'medium': 2},           # points, open-ended
    'conviction_factor': {'high': 1.0, 'medium': 0.75, 'low': 0.5},
    'risk_factor': {'low': 1.0, 'medium': 0.85, 'high': 0.6},
    'maximum_picks': 3,
}

TRAP_TEXT = {
    'operating_loss_latest_year': 'Operating loss in the latest fiscal year.',
    'net_loss_latest_year': 'Net loss in the latest fiscal year.',
    'negative_free_cash_flow_latest_year': 'Negative free cash flow in the latest fiscal year.',
    'revenue_falling_materially': 'Revenue fell materially in the latest fiscal year.',
    'negative_equity': "Shareholders' equity is negative.",
}


def _traps(latest):
    v = {k: x['value'] for k, x in latest['values'].items()}
    c = latest['calculated']
    out = []
    if c.get('operating_margin') is not None and c['operating_margin'] < 0: out.append('operating_loss_latest_year')
    if v.get('net_income') is not None and v['net_income'] < 0: out.append('net_loss_latest_year')
    if c.get('free_cash_flow') is not None and c['free_cash_flow'] < 0: out.append('negative_free_cash_flow_latest_year')
    if c.get('revenue_growth') is not None and c['revenue_growth'] <= RULES['material_revenue_decline']: out.append('revenue_falling_materially')
    if v.get('equity') is not None and v['equity'] < 0: out.append('negative_equity')
    return out


def _level(points, levels, top, middle, bottom):
    return top if points >= levels['high'] else middle if points >= levels['medium'] else bottom


def assess_company(c):
    """One company's upside, value-trap check, conviction, risk and score, with reasons."""
    base = {'security_id': c['security_id'], 'qualified_symbol': c['qualified_symbol'], 'company_name': c.get('company_name')}
    scenarios = (c.get('valuation') or {}).get('scenarios') or {}
    years = (c.get('financials') or {}).get('years') or []
    if not scenarios.get('available') or not years:
        return base | {'status': 'not_assessable', 'reasons': [scenarios.get('reason') or 'No scenario range is available.']}
    cases = {x['case']: x for x in scenarios['cases']}
    upside = cases['middle']['vs_price']
    if upside is None:
        return base | {'status': 'not_assessable', 'reasons': [cases['middle'].get('note') or 'The middle case has no value.']}
    cautious = cases['cautious']['vs_price']
    traps = _traps(years[-1])
    comparisons = ((c.get('valuation') or {}).get('history') or {}).get('comparisons') or []
    below = [x for x in comparisons if x['position'] == 'below']
    above = [x for x in comparisons if x['position'] == 'above']
    observations = (c.get('financials') or {}).get('observations') or []
    weaknesses = [o['text'] for o in observations if o['kind'] == 'weakness']
    gaps = [o['text'] for o in observations if o['kind'] == 'gap']
    volatile = scenarios['variation'] >= RULES['volatile_variation']
    recovery_needed = scenarios['latest_profit'] < scenarios['median_profit']

    conviction_for, conviction_against = [], []
    def point(ok, yes, no):
        (conviction_for if ok else conviction_against).append(yes if ok else no)
        return int(ok)
    conviction_points = sum((
        point(cautious is not None and cautious >= 0, 'Even the cautious case is at or above the price (margin of safety).',
              'The cautious case is below the price.'),
        point(not volatile, f'{scenarios["measure"].capitalize()} has been steady.',
              f'{scenarios["measure"].capitalize()} swings a lot, so the range is unreliable.'),
        point(bool(below) and not above, f'Cheap against its own history on {len(below)} multiple(s), expensive on none.',
              'Not cheap against its own history on every comparable multiple.' if comparisons else 'Too little history to compare multiples.'),
        point(not recovery_needed, 'Latest profit is at or above its median: no recovery is needed for the middle case.',
              'The middle case needs profits to recover to their median.'),
        point(scenarios['years_used'] >= 4 and not gaps, f'{scenarios["years_used"]} years of figures with no coverage gaps.',
              f'Gap in the figures: {gaps[0]}' if gaps else f'Only {scenarios["years_used"]} years of figures.'),
    ))
    conviction = _level(conviction_points, RULES['conviction_levels'], 'high', 'medium', 'low')

    risks = list(weaknesses)
    if volatile: risks.append('Profits are volatile.')
    momentum = (c.get('calculation') or {}).get('momentum_return')
    if momentum is not None and momentum <= RULES['sharp_fall_126_sessions']:
        risks.append(f'The price fell {-momentum:.0%} over 126 sessions; the market may know something the figures do not show yet.')
    risks += [f['text'] for f in ((c.get('events') or {}).get('flags') or []) if f['kind'] == 'risk']
    risk = _level(len(risks), RULES['risk_levels'], 'high', 'medium', 'low')

    score = min(upside, RULES['upside_cap_for_score']) * RULES['conviction_factor'][conviction] * RULES['risk_factor'][risk]
    out = base | {'upside': upside, 'cautious_vs_price': cautious, 'optimistic_vs_price': cases['optimistic']['vs_price'],
                  'measure': scenarios['measure'], 'price': scenarios['price'], 'price_session': scenarios.get('price_session'),
                  'middle_value_per_share': cases['middle']['value_per_share'],
                  'conviction': conviction, 'conviction_points': conviction_points, 'conviction_for': conviction_for,
                  'conviction_against': conviction_against, 'risk': risk, 'risks': risks, 'score': score,
                  'value_traps': [TRAP_TEXT[t] for t in traps]}
    if traps:
        return out | {'status': 'value_trap', 'reasons': [TRAP_TEXT[t] for t in traps]}
    if upside < RULES['minimum_upside']:
        return out | {'status': 'not_undervalued', 'reasons': [f'Middle-case upside {upside:.0%} is below the {RULES["minimum_upside"]:.0%} minimum.']}
    if conviction == 'low' or risk == 'high':
        return out | {'status': 'watch', 'reasons': ['Cheap, but conviction is low.' if conviction == 'low' else 'Cheap, but risk is high.']}
    strong = upside >= RULES['strong_upside'] and conviction == 'high' and risk == 'low'
    return out | {'status': 'candidate', 'recommendation': 'strong_buy' if strong else 'buy', 'reasons': []}


def value_ranking(companies):
    """Rank eligible companies; the top three candidates (or fewer) are the month's picks."""
    assessed = [assess_company(c) for c in companies if c.get('eligible')]
    order = {'candidate': 0, 'watch': 1, 'not_undervalued': 2, 'value_trap': 3, 'not_assessable': 4}
    assessed.sort(key=lambda a: (order[a['status']], -a.get('score', float('-inf')), a['security_id']))
    candidates = [a for a in assessed if a['status'] == 'candidate']
    picks = [a['security_id'] for a in candidates[:RULES['maximum_picks']]]
    for a in assessed:
        a['rank'] = picks.index(a['security_id']) + 1 if a['security_id'] in picks else None
    return {'population': len(assessed), 'picks': picks, 'companies': assessed, 'rules': RULES,
            'method': 'Upside = middle scenario vs price. Excluded: no range, value-trap sign, or upside below the minimum. '
                      'Score = min(upside, cap) x conviction factor x risk factor. At most three picks; fewer when fewer qualify.',
            'label': 'Unvalidated ranking arithmetic from stored figures. Not investment advice and not tested against later returns.'}
