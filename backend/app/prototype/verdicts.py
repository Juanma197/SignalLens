"""Plain-English answers for one company: is it cheap, is it a good business, is
it growing, is it risky, and what does the ranking do with it.

Nothing here decides anything new. Each answer translates a result the ranking
(`ranking.py`) or the financial observations (`financials.py`) already produced,
using their thresholds, and carries the figures behind it so the page can show
them on request. The rules are the same for every company, so they are stored
once on the ranking (RULES_TEXT). One presentation rule is stricter than the
ranking: the risk answer is green only when no warning sign is found, although
the ranking counts a single sign as low risk.

Levels: positive, mixed, negative, neutral (information only), unknown.
"""
from .financials import FINANCIAL_RULES
from .ranking import RULES


RULES_TEXT = {
    'cheap': (f'Middle scenario (usual profit x usual own multiple) against the price: at least {RULES["strong_upside"]:.0%} above '
              f'is cheap, {RULES["minimum_upside"]:.0%} to {RULES["strong_upside"]:.0%} a little cheap, below {RULES["minimum_upside"]:.0%} not cheap.'),
    'quality': (f'Healthy margin: operating margin of at least {FINANCIAL_RULES["healthy_operating_margin"]:.0%} and positive every year. '
                f'Cash: free cash flow positive every year; operating cash flow below {FINANCIAL_RULES["cash_conversion_low"]:.0%} '
                'of net income counts as profits not backed by cash.'),
    'growth': (f'Growing quickly: revenue up at least {FINANCIAL_RULES["strong_revenue_growth_per_year"]:.0%} a year over three or more years. '
               f'Shrinking: revenue down over that period, or down {-FINANCIAL_RULES["material_revenue_decline"]:.0%} or more in the latest year.'),
    'risk': (f'Warning signs: financial weaknesses, volatile profits, a price fall of {-RULES["sharp_fall_126_sessions"]:.0%} '
             f'or more over 126 sessions, and risk flags in recent filings. Green only with none; {RULES["risk_levels"]["high"]} or more is red. '
             "The ranking's own risk level counts a single sign as low."),
    'action': ('A pick needs at least the minimum upside, no value-trap sign, and neither low conviction nor high risk; '
               f'the best {RULES["maximum_picks"]} by score are picks, never more.'),
}
METHOD = ('Translations of the ranking and financial observations; the rule behind each answer is in `verdict_rules`. '
          'No rule here changes the ranking.')


def _answer(question, level, headline, because=(), figures=()):
    return {'question': question, 'level': level, 'headline': headline, 'because': [b for b in because if b],
            'figures': [f for f in figures if f['value'] is not None]}


def _figure(label, value, unit):
    return {'label': label, 'value': value, 'unit': unit}


def _latest(c):
    years = (c.get('financials') or {}).get('years') or []
    if years: return years[-1]['calculated']
    return ((c.get('financials') or {}).get('latest_year') or {}).get('calculated') or {}


def _observations(c, *areas):
    return [o for o in (c.get('financials') or {}).get('observations') or [] if o.get('area') in areas]


def cheap(c, a):
    v = c.get('valuation') or {}
    multiples = v.get('multiples') or {}
    figures = [_figure('P/E', multiples.get('price_to_earnings'), 'multiple'),
               _figure('P/FCF', multiples.get('price_to_free_cash_flow'), 'multiple'),
               _figure('Free cash flow yield', v.get('free_cash_flow_yield'), 'percent')]
    history = [x['text'] for x in (v.get('history') or {}).get('comparisons') or [] if x.get('text')]
    minimum, strong = RULES['minimum_upside'], RULES['strong_upside']
    if not a or a.get('upside') is None:
        reason = (a or {}).get('reasons', ['No valuation range is available.'])[0]
        return _answer('cheap', 'unknown', "Can't tell whether it's cheap.", [reason], figures)
    upside, price, middle, cautious = a['upside'], a['price'], a['middle_value_per_share'], a['cautious_vs_price']
    figures = [_figure('Price', price, 'per_share_usd'), _figure('Middle-case value', middle, 'per_share_usd'),
               _figure('Middle case vs price', upside, 'percent'), _figure('Cautious case vs price', cautious, 'percent')] + figures
    worth = f'on its usual profits and valuation it would be worth about ${middle:,.2f} a share against ${price:,.2f} today'
    if upside >= strong: level, headline = 'positive', f'Looks cheap: {worth}.'
    elif upside >= minimum: level, headline = 'mixed', f'Looks a little cheap: {worth}.'
    elif upside >= 0: level, headline = 'negative', f'Not cheap: {worth}.'
    else: level, headline = 'negative', f'Looks expensive: {worth}.'
    margin = None if cautious is None else (
        'Even a cautious case is at or above the price.' if cautious >= 0 else 'A cautious case is below the price.')
    return _answer('cheap', level, headline, [margin, *history], figures)


PROFIT = {'strength': 'Consistently profitable with healthy margins', 'neutral': 'Profitable, but margins are thin or uneven',
          'weakness': 'Losing money'}
CASH = {'strength': 'generates cash every year', 'neutral': 'cash generation has been uneven',
        'weakness': 'profits are not backed by cash'}


def _kind(observations):
    kinds = {o['kind'] for o in observations}
    return next((k for k in ('weakness', 'strength', 'neutral') if k in kinds), None)


def quality(c):
    profit, cash = _observations(c, 'profitability'), _observations(c, 'cash generation')
    p, f = _kind(profit), _kind(cash)
    calc = _latest(c)
    figures = [_figure('Operating margin', calc.get('operating_margin'), 'percent'),
               _figure('Net margin', calc.get('net_margin'), 'percent'),
               _figure('Free cash flow', calc.get('free_cash_flow'), 'usd'),
               _figure('Cash conversion', calc.get('cash_conversion'), 'ratio')]
    because = [o['text'] for o in profit + cash if o['kind'] != 'gap']
    if p is None and f is None:
        return _answer('quality', 'unknown', "Can't judge business quality: profit and cash figures are missing.",
                       [o['text'] for o in profit + cash], figures)
    if f == 'weakness' and any(o['kind'] == 'weakness' and 'Free cash flow' in o['text'] for o in cash):
        cash_text = 'burned cash in the latest year'
    else:
        cash_text = CASH.get(f, 'cash generation is unknown')
    profit_text = PROFIT.get(p, 'Profitability is unknown')
    if p == 'neutral' and calc.get('operating_margin') is None:  # judged on net income alone
        profit_text = 'Profitable in the latest year (margins unavailable)'
    headline = f'{profit_text}; {cash_text}.'
    level = 'negative' if 'weakness' in (p, f) else 'positive' if p == f == 'strength' else 'mixed'
    return _answer('quality', level, headline, because, figures)


def growth(c):
    observations = _observations(c, 'growth')
    calc = _latest(c)
    figures = [_figure('Revenue growth, latest year', calc.get('revenue_growth'), 'percent')]
    kind = _kind(observations)
    headline = {'strength': 'Growing quickly.', 'neutral': 'Growing, but not fast.', 'weakness': 'Shrinking.'}.get(kind)
    if headline is None:
        return _answer('growth', 'unknown', "Can't judge growth from the figures.", [o['text'] for o in observations], figures)
    level = {'strength': 'positive', 'neutral': 'mixed', 'weakness': 'negative'}[kind]
    return _answer('growth', level, headline, [o['text'] for o in observations if o['kind'] != 'gap'], figures)


def risk(c, a):
    if a and 'risks' in a:
        signs = a['risks']
    else:  # not assessable: the same sources the ranking would use, without the price-fall check
        signs = [o['text'] for o in (c.get('financials') or {}).get('observations') or [] if o['kind'] == 'weakness']
        signs += [f['text'] for f in (c.get('events') or {}).get('flags') or [] if f['kind'] == 'risk']
    calc = _latest(c)
    figures = [_figure('Warning signs', len(signs), 'count'),
               _figure('Price change, 126 sessions', (c.get('calculation') or {}).get('momentum_return'), 'percent'),
               _figure('Current ratio', calc.get('current_ratio'), 'ratio'),
               _figure('Liabilities to assets', calc.get('liabilities_to_assets'), 'percent'),
               _figure('Diluted share change, latest year', calc.get('diluted_share_change'), 'percent')]
    high = RULES['risk_levels']['high']
    if not signs:
        return _answer('risk', 'positive', 'No warning signs found.',
                       ['That is not the same as safe: only annual figures, the price trend and recent filings are checked.'], figures)
    level = 'negative' if len(signs) >= high else 'mixed'
    headline = 'One warning sign.' if len(signs) == 1 else f'{len(signs)} warning signs.'
    return _answer('risk', level, headline, signs, figures)


def action(a):
    if not a: return _answer('action', 'unknown', 'Not assessed.')
    status, reasons = a['status'], a.get('reasons') or []
    figures = [_figure('Score', a.get('score'), 'ratio')]
    conviction = a.get('conviction')
    if status == 'candidate':
        strength = 'strong' if a.get('recommendation') == 'strong_buy' else 'qualifying'
        headline = (f'Pick #{a["rank"]} this month' if a.get('rank') else 'Qualifies, but outside the top three') + \
                   f': {strength} case, {conviction} conviction.'
        return _answer('action', 'positive', headline, a.get('conviction_for') or [], figures)
    if status == 'watch': return _answer('action', 'mixed', f'Watch: {reasons[0][0].lower()}{reasons[0][1:]}', a.get('conviction_against') or [], figures)
    if status == 'value_trap': return _answer('action', 'negative', 'Avoid for now: cheap, but the business is getting worse.', reasons, figures)
    if status == 'not_undervalued': return _answer('action', 'neutral', 'Pass: not cheap enough to be a pick.', reasons, figures)
    return _answer('action', 'unknown', "Can't assess this company yet.", reasons)


def plain_verdicts(c, a=None):
    """The four answers plus the ranking's action for one company; `a` is its
    `assess_company` result, None when it was not ranked."""
    return {'action': action(a), 'answers': [cheap(c, a), quality(c), growth(c), risk(c, a)]}


def attach(companies, ranking):
    """Add `verdicts` to every eligible company, from its ranking result; the rules,
    the same for every company, go on the ranking once."""
    ranking['verdict_rules'], ranking['verdict_method'] = RULES_TEXT, METHOD
    assessed = {a['security_id']: a for a in ranking['companies']}
    for c in companies:
        if c.get('eligible'): c['verdicts'] = plain_verdicts(c, assessed.get(c['security_id']))
