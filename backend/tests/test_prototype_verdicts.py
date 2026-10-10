"""Plain-English verdicts: each answer follows the ranking and financial rules (offline)."""
from app.prototype.ranking import assess_company, value_ranking
from app.prototype.verdicts import RULES_TEXT, attach, plain_verdicts


def company(sid='a', *, middle=0.5, cautious=0.05, observations=(), momentum=0.0, risk_flags=0, latest=110):
    def case(name, vs):
        return {'case': name, 'vs_price': vs, 'value_per_share': None if vs is None else 10 * (1 + vs)}
    year = {'values': {'net_income': {'value': 5}, 'equity': {'value': 50}},
            'calculated': {'operating_margin': 0.12, 'free_cash_flow': 4e6, 'revenue_growth': 0.03, 'current_ratio': 1.8}}
    return {'security_id': sid, 'qualified_symbol': f'{sid.upper()}.US', 'company_name': sid, 'eligible': True,
            'calculation': {'momentum_return': momentum},
            'financials': {'years': [year], 'observations': [{'kind': k, 'area': area, 'text': t} for k, area, t in observations]},
            'events': {'flags': [{'kind': 'risk', 'text': f'flag {i}'} for i in range(risk_flags)]},
            'valuation': {'multiples': {'price_to_earnings': 8.5}, 'free_cash_flow_yield': 0.11,
                          'history': {'comparisons': [{'position': 'below', 'text': 'P/E is below its own 5-year range.'}]},
                          'scenarios': {'available': True, 'measure': 'net income', 'variation': 0.2, 'years_used': 5,
                                        'latest_profit': latest, 'median_profit': 100, 'price': 10, 'price_session': '2026-10-08',
                                        'cases': [case('cautious', cautious), case('middle', middle), case('optimistic', 0.9)]}}}


GOOD = (('strength', 'profitability', 'Operating margin was positive in 5 of 5 years; latest 12.0%.'),
        ('strength', 'cash generation', 'Free cash flow (operating cash flow minus capex) was positive in 5 of 5 years; latest 4M USD.'),
        ('neutral', 'growth', 'Revenue grew 3.0% a year over 4.0 years.'))


def answers(c):
    v = plain_verdicts(c, assess_company(c))
    return v['action'], {x['question']: x for x in v['answers']}


def test_a_strong_pick_reads_as_cheap_good_slow_growing_and_clean():
    act, a = answers(company(observations=GOOD))
    assert act['level'] == 'positive' and act['headline'] == 'Qualifies, but outside the top three: strong case, high conviction.'
    assert a['cheap']['level'] == 'positive' and a['cheap']['headline'].startswith('Looks cheap: ')
    assert '$15.00 a share against $10.00 today' in a['cheap']['headline']
    assert a['cheap']['because'] == ['Even a cautious case is at or above the price.', 'P/E is below its own 5-year range.']
    assert {f['label']: f['value'] for f in a['cheap']['figures']}['Middle case vs price'] == 0.5
    assert a['quality'] == a['quality'] | {'level': 'positive', 'headline': 'Consistently profitable with healthy margins; generates cash every year.'}
    assert a['growth']['level'] == 'mixed' and a['growth']['headline'] == 'Growing, but not fast.'
    assert a['risk']['level'] == 'positive' and 'not the same as safe' in a['risk']['because'][0]
    assert set(RULES_TEXT) == {*a, 'action'}


def test_upside_bands_follow_the_ranking_thresholds():
    assert answers(company(middle=0.2))[1]['cheap']['level'] == 'mixed'
    act, a = answers(company(middle=0.1))
    assert a['cheap']['headline'].startswith('Not cheap') and act['headline'] == 'Pass: not cheap enough to be a pick.'
    assert answers(company(middle=-0.2))[1]['cheap']['headline'].startswith('Looks expensive')


def test_one_warning_sign_is_never_green_although_the_ranking_calls_it_low_risk():
    c = company(observations=GOOD, momentum=-0.8)
    assert assess_company(c)['risk'] == 'low'
    risk = answers(c)[1]['risk']
    assert risk['level'] == 'mixed' and risk['headline'] == 'One warning sign.'
    assert risk['because'][0].startswith('The price fell 80% over 126 sessions')
    assert answers(company(observations=GOOD, risk_flags=4))[1]['risk']['level'] == 'negative'


def test_weak_business_value_trap_and_unassessable_company():
    weak = (('weakness', 'profitability', 'Operating margin was positive in 2 of 5 years; latest -3.0%.'),
            ('weakness', 'cash generation', 'Free cash flow (operating cash flow minus capex) was positive in 3 of 5 years; latest -2M USD.'),
            ('weakness', 'growth', 'Revenue fell 9.0% in the latest fiscal year.'))
    c = company(observations=weak)
    c['financials']['years'][0]['calculated']['operating_margin'] = -0.03
    act, a = answers(c)
    assert act['level'] == 'negative' and act['headline'].startswith('Avoid for now')
    assert a['quality']['headline'] == 'Losing money; burned cash in the latest year.' and a['quality']['level'] == 'negative'
    assert a['growth']['headline'] == 'Shrinking.'
    gone = company(middle=None)
    act, a = answers(gone)
    assert act['level'] == 'unknown' and a['cheap']['level'] == 'unknown'
    assert a['quality']['level'] == 'unknown' and a['growth']['level'] == 'unknown'


def test_attach_adds_verdicts_to_eligible_companies_with_pick_ranks():
    cs = [company('a', observations=GOOD), company('b', middle=0.1), company('c') | {'eligible': False}]
    ranking = value_ranking(cs)
    attach(cs, ranking)
    assert cs[0]['verdicts']['action']['headline'].startswith('Pick #1 this month')
    assert cs[1]['verdicts']['action']['level'] == 'neutral'
    assert 'verdicts' not in cs[2] and ranking['verdict_rules'] == RULES_TEXT


def test_profit_judged_on_net_income_alone_does_not_claim_thin_margins():
    c = company(observations=(('neutral', 'profitability', 'Latest annual net income 1,094M USD (no operating margin available).'),))
    del c['financials']['years'][0]['calculated']['operating_margin']
    quality = answers(c)[1]['quality']
    assert quality['headline'] == 'Profitable in the latest year (margins unavailable); cash generation is unknown.'
    assert quality['level'] == 'mixed'
