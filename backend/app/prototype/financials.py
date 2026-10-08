"""Annual financial-health brief from stored SEC 10-K facts (US issuers only).

Only full fiscal years reported in 10-K/10-K/A filings are used: durations of
350-380 days and balance-sheet instants at those year ends. No quarterly, YTD or
TTM conversion and no accounting construction beyond the labelled ratios below.
For each concept and exact period the latest visible revision is used; a period
with conflicting values in that revision is withheld. Observations come from
fixed, documented rules (FINANCIAL_RULES) and are interpretation, not a score.
"""
from collections import defaultdict
from datetime import date, datetime

YEARS = 5
ANNUAL_DAYS = (350, 380)
ANNUAL_FORMS = ('10-K', '10-K/A')
# Revenue concepts in preference order; the concept used is shown per year.
REVENUE = ('RevenueFromContractWithCustomerExcludingAssessedTax', 'Revenues', 'SalesRevenueNet')
DURATIONS = {
    'operating_income': ('OperatingIncomeLoss',),
    'net_income': ('NetIncomeLoss',),
    'operating_cash_flow': ('NetCashProvidedByUsedInOperatingActivities',),
    'capital_expenditure': ('PaymentsToAcquirePropertyPlantAndEquipment',),
    'diluted_shares': ('WeightedAverageNumberOfDilutedSharesOutstanding',),
    'diluted_eps': ('EarningsPerShareDiluted',),
}
INSTANTS = {
    'assets': ('Assets',),
    'liabilities': ('Liabilities',),
    'equity': ('StockholdersEquity',),
    'cash': ('CashAndCashEquivalentsAtCarryingValue',),
    'current_assets': ('AssetsCurrent',),
    'current_liabilities': ('LiabilitiesCurrent',),
    'long_term_debt_current': ('LongTermDebtCurrent',),
    'long_term_debt_noncurrent': ('LongTermDebtNoncurrent',),
}
UNITS = {'diluted_shares': 'shares', 'diluted_eps': 'USD/shares'}
FINANCIAL_RULES = {
    'strong_revenue_growth_per_year': 0.10,
    'material_revenue_decline': -0.05,
    'healthy_operating_margin': 0.10,
    'strong_current_ratio': 1.5,
    'weak_current_ratio': 1.0,
    'high_liabilities_to_assets': 0.70,
    'dilution_per_year': 0.03,
    'buyback_per_year': -0.02,
    'cash_conversion_low': 0.7,
}


def _day(value):
    if isinstance(value, datetime): return value.date()
    return value if isinstance(value, date) else None


def _latest(rows, stamp):
    """Latest visible revision for one exact period; None if it conflicts."""
    public = max(stamp(r['public_at']) for r in rows)
    latest = [r for r in rows if stamp(r['public_at']) == public]
    values = {float(r['value']) for r in latest}
    if len(values) != 1: return None
    r = max(latest, key=lambda r: (stamp(r['retrieved_at']), r['fact_key']))
    return {'value': float(r['value']), 'concept': r['concept'], 'accession': r['accession_number'], 'form': r['form'],
            'known_at': max(stamp(r['public_at']), stamp(r['retrieved_at']))}


def annual_brief(sec, facts, decision, *, stamp, finite):
    """Series for up to YEARS fiscal years plus rule-based observations."""
    wanted = set(REVENUE) | {c for cs in DURATIONS.values() for c in cs} | {c for cs in INSTANTS.values() for c in cs}
    rows = [r for r in facts if str(r.get('security_id')) == sec['security_id'] and r.get('concept') in wanted
            and r.get('taxonomy') == 'us-gaap' and r.get('form') in ANNUAL_FORMS and finite(r.get('value'))
            and r.get('cik') and str(r['cik']).lstrip('0') == str(sec.get('cik') or '').lstrip('0')
            and stamp(r.get('public_at')) and stamp(r.get('retrieved_at'))
            and max(stamp(r['public_at']), stamp(r['retrieved_at'])) <= decision and _day(r.get('period_end'))]
    periods = defaultdict(list)
    for r in rows:
        start, end = _day(r.get('period_start')), _day(r['period_end'])
        if end > decision.date(): continue
        expected_unit = 'USD/shares' if r['concept'] == 'EarningsPerShareDiluted' else 'shares' if r['concept'] == 'WeightedAverageNumberOfDilutedSharesOutstanding' else 'USD'
        if r.get('unit') != expected_unit: continue
        if start is None: periods[(r['concept'], None, end)].append(r)
        elif ANNUAL_DAYS[0] <= (end - start).days <= ANNUAL_DAYS[1]: periods[(r['concept'], start, end)].append(r)
    chosen = {key: _latest(group, stamp) for key, group in periods.items()}
    # Fiscal years are the ends of annual net income or revenue periods.
    ends = sorted({end for (concept, start, end), v in chosen.items() if v and start
                   and concept in REVENUE + ('NetIncomeLoss',)}, reverse=True)[:YEARS]
    def annual(concepts, end):
        for concept in concepts:
            found = [v for (c, start, e), v in chosen.items() if v and c == concept and start and e == end]
            if len(found) == 1: return found[0]
            if len(found) > 1: return None  # two different annual periods ending together
        return None
    def instant(concepts, end):
        for concept in concepts:
            v = chosen.get((concept, None, end))
            if v: return v
        return None
    years = []
    for end in sorted(ends):
        year = {'fiscal_year_end': end, 'values': {}}
        for field, concepts in [('revenue', REVENUE), *DURATIONS.items()]:
            v = annual(concepts, end)
            if v: year['values'][field] = v
        for field, concepts in INSTANTS.items():
            v = instant(concepts, end)
            if v: year['values'][field] = v
        years.append(year)
    for i, year in enumerate(years):
        v = {k: x['value'] for k, x in year['values'].items()}
        prev = {k: x['value'] for k, x in years[i - 1]['values'].items()} if i else {}
        calc = {}
        def ratio(name, num, den, positive_den=True):
            if num in v and den in v and (v[den] > 0 if positive_den else v[den] != 0): calc[name] = v[num] / v[den]
        ratio('operating_margin', 'operating_income', 'revenue')
        ratio('net_margin', 'net_income', 'revenue')
        ratio('current_ratio', 'current_assets', 'current_liabilities')
        ratio('liabilities_to_assets', 'liabilities', 'assets')
        if 'operating_cash_flow' in v and 'capital_expenditure' in v:
            calc['free_cash_flow'] = v['operating_cash_flow'] - v['capital_expenditure']
            if v.get('revenue', 0) > 0: calc['free_cash_flow_margin'] = calc['free_cash_flow'] / v['revenue']
        if v.get('net_income', 0) > 0 and 'operating_cash_flow' in v: calc['cash_conversion'] = v['operating_cash_flow'] / v['net_income']
        if prev.get('revenue', 0) > 0 and 'revenue' in v and year['values']['revenue']['concept'] == years[i - 1]['values']['revenue']['concept']:
            calc['revenue_growth'] = v['revenue'] / prev['revenue'] - 1
        if prev.get('diluted_shares', 0) > 0 and 'diluted_shares' in v: calc['diluted_share_change'] = v['diluted_shares'] / prev['diluted_shares'] - 1
        year['calculated'] = calc
    return {'years': years, 'observations': _observations(years),
            'method': 'Annual 10-K facts only; latest visible revision per exact period; ratios are labelled calculations.',
            'rules': FINANCIAL_RULES,
            'not_available': ['interest coverage (no interest-expense facts stored)', 'total debt (borrowing components are not combined)',
                              'quarterly or trailing-twelve-month figures (not constructed)']}


def _consecutive(points):
    """Latest run of fiscal years without a gap; a missing year (often a merger,
    spin-off or change of reporting entity) ends the comparison."""
    run = points[-1:]
    for point in reversed(points[:-1]):
        if 300 <= (run[0][0] - point[0]).days <= 430: run.insert(0, point)
        else: break
    return run


def _observations(years):
    """Rule-based strengths, weaknesses and gaps, each naming its evidence years."""
    R, out = FINANCIAL_RULES, []
    def add(kind, area, text, used):
        out.append({'kind': kind, 'area': area, 'text': text, 'fiscal_years': [y.isoformat() for y in used]})
    if not years:
        add('gap', 'coverage', 'No full-year 10-K figures are visible at this cutoff.', []); return out
    ends = [y['fiscal_year_end'] for y in years]
    last = years[-1]; lv, lc = last['values'], last['calculated']
    # Revenue
    revs = _consecutive([(y['fiscal_year_end'], y['values']['revenue']['value']) for y in years if 'revenue' in y['values']])
    concepts = {y['values']['revenue']['concept'] for y in years if 'revenue' in y['values']}
    if len(revs) >= 3 and revs[0][1] > 0 and revs[-1][1] > 0 and len(concepts) == 1:
        n = (revs[-1][0] - revs[0][0]).days / 365.25
        cagr = (revs[-1][1] / revs[0][1]) ** (1 / n) - 1 if n > 0 else None
        if cagr is not None:
            kind = 'strength' if cagr >= R['strong_revenue_growth_per_year'] else 'weakness' if cagr < 0 else 'neutral'
            add(kind, 'growth', (f'Revenue grew {cagr:.1%} a year over {n:.1f} years.' if cagr >= 0 else f'Revenue shrank {-cagr:.1%} a year over {n:.1f} years.'), [d for d, _ in revs])
    elif len(concepts) > 1:
        add('gap', 'growth', 'Revenue is reported under different concepts across years; growth is not compared.', [d for d, _ in revs])
    if 'revenue_growth' in lc and lc['revenue_growth'] <= R['material_revenue_decline']:
        add('weakness', 'growth', f'Revenue fell {-lc["revenue_growth"]:.1%} in the latest fiscal year.', ends[-2:])
    if not revs: add('gap', 'growth', 'No annual revenue is reported (possibly pre-revenue).', [])
    # Profitability
    margins = [(y['fiscal_year_end'], y['calculated']['operating_margin']) for y in years if 'operating_margin' in y['calculated']]
    if margins:
        positive = sum(m > 0 for _, m in margins)
        latest = margins[-1][1]
        kind = 'strength' if positive == len(margins) and latest >= R['healthy_operating_margin'] else 'weakness' if latest < 0 else 'neutral'
        add(kind, 'profitability', f'Operating margin was positive in {positive} of {len(margins)} years; latest {latest:.1%}.', [d for d, _ in margins])
    elif 'net_income' in lv:
        add('weakness' if lv['net_income']['value'] < 0 else 'neutral', 'profitability',
            f'Latest annual net income {lv["net_income"]["value"] / 1e6:,.0f}M USD (no operating margin available).', ends[-1:])
    # Cash generation
    fcf = [(y['fiscal_year_end'], y['calculated']['free_cash_flow']) for y in years if 'free_cash_flow' in y['calculated']]
    if fcf:
        positive = sum(f > 0 for _, f in fcf)
        kind = 'strength' if positive == len(fcf) else 'weakness' if fcf[-1][1] < 0 else 'neutral'
        add(kind, 'cash generation', f'Free cash flow (operating cash flow minus capex) was positive in {positive} of {len(fcf)} years; latest {fcf[-1][1] / 1e6:,.0f}M USD.', [d for d, _ in fcf])
    else:
        add('gap', 'cash generation', 'Free cash flow cannot be calculated: annual operating cash flow or capex is missing.', [])
    if 'cash_conversion' in lc and lc['cash_conversion'] < R['cash_conversion_low']:
        add('weakness', 'cash generation', f'Operating cash flow was {lc["cash_conversion"]:.0%} of net income in the latest year (earnings not fully backed by cash).', ends[-1:])
    # Balance sheet
    if 'current_ratio' in lc:
        cr = lc['current_ratio']
        kind = 'strength' if cr >= R['strong_current_ratio'] else 'weakness' if cr < R['weak_current_ratio'] else 'neutral'
        add(kind, 'liquidity', f'Current ratio {cr:.2f} at the latest fiscal year end.', ends[-1:])
    if 'liabilities_to_assets' in lc:
        la = lc['liabilities_to_assets']
        add('weakness' if la >= R['high_liabilities_to_assets'] else 'neutral', 'leverage',
            f'Liabilities were {la:.0%} of assets at the latest fiscal year end.', ends[-1:])
    if 'equity' in lv and lv['equity']['value'] < 0:
        add('weakness', 'leverage', 'Shareholders\' equity is negative at the latest fiscal year end.', ends[-1:])
    debt = [lv[k]['value'] for k in ('long_term_debt_current', 'long_term_debt_noncurrent') if k in lv]
    if debt:
        add('neutral', 'leverage', f'Reported long-term debt components total {sum(debt) / 1e6:,.0f}M USD (other borrowings may exist).', ends[-1:])
    # Dilution
    shares = _consecutive([(y['fiscal_year_end'], y['values']['diluted_shares']['value']) for y in years if 'diluted_shares' in y['values']])
    if len(shares) >= 2 and shares[0][1] > 0:
        n = (shares[-1][0] - shares[0][0]).days / 365.25
        per_year = (shares[-1][1] / shares[0][1]) ** (1 / n) - 1 if n > 0 else 0
        if per_year >= R['dilution_per_year']:
            add('weakness', 'dilution', f'Diluted share count rose {per_year:.1%} a year over {n:.1f} years.', [d for d, _ in shares])
        elif per_year <= R['buyback_per_year']:
            add('strength', 'dilution', f'Diluted share count fell {-per_year:.1%} a year over {n:.1f} years (buybacks).', [d for d, _ in shares])
    if len(years) < 3:
        add('gap', 'coverage', f'Only {len(years)} full fiscal year(s) are visible; trends are limited.', ends)
    return out


def summary_only(brief):
    """Observations without the yearly tables, for companies outside the eligible
    roster, keeping the report bounded."""
    return {'years': [], 'fiscal_years_available': len(brief['years']), 'observations': brief['observations'],
            'method': brief['method'], 'rules': brief['rules'], 'not_available': brief['not_available'],
            'tables_omitted': 'Yearly tables are shown for eligible companies only.'}
