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


def _latest(rows, stamp, revision='latest'):
    """Latest (or first-reported) visible revision for one exact period; None if
    that revision has conflicting values."""
    public = (max if revision == 'latest' else min)(stamp(r['public_at']) for r in rows)
    latest = [r for r in rows if stamp(r['public_at']) == public]
    values = {float(r['value']) for r in latest}
    if len(values) != 1: return None
    r = max(latest, key=lambda r: (stamp(r['retrieved_at']), r['fact_key']))
    return {'value': float(r['value']), 'concept': r['concept'], 'accession': r['accession_number'], 'form': r['form'],
            'known_at': max(stamp(r['public_at']), stamp(r['retrieved_at']))}


def annual_brief(sec, facts, decision, *, stamp, finite, revision='latest', known=None):
    """Series for up to YEARS fiscal years plus rule-based observations."""
    wanted = set(REVENUE) | {c for cs in DURATIONS.values() for c in cs} | {c for cs in INSTANTS.values() for c in cs}
    rows = [r for r in facts if str(r.get('security_id')) == sec['security_id'] and r.get('concept') in wanted
            and r.get('taxonomy') == 'us-gaap' and r.get('form') in ANNUAL_FORMS and finite(r.get('value'))
            and r.get('cik') and str(r['cik']).lstrip('0') == str(sec.get('cik') or '').lstrip('0')
            and stamp(r.get('public_at')) and stamp(r.get('retrieved_at'))
            and stamp(r['public_at']) <= decision and stamp(r['retrieved_at']) <= (known or decision) and _day(r.get('period_end'))]
    periods = defaultdict(list)
    for r in rows:
        start, end = _day(r.get('period_start')), _day(r['period_end'])
        if end > decision.date(): continue
        expected_unit = 'USD/shares' if r['concept'] == 'EarningsPerShareDiluted' else 'shares' if r['concept'] == 'WeightedAverageNumberOfDilutedSharesOutstanding' else 'USD'
        if r.get('unit') != expected_unit: continue
        if start is None: periods[(r['concept'], None, end)].append(r)
        elif ANNUAL_DAYS[0] <= (end - start).days <= ANNUAL_DAYS[1]: periods[(r['concept'], start, end)].append(r)
    chosen = {key: _latest(group, stamp, revision) for key, group in periods.items()}
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
    roster, keeping the report bounded. The latest year alone is kept for thesis checks."""
    return {'years': [], 'fiscal_years_available': len(brief['years']), 'observations': brief['observations'],
            'latest_year': brief['years'][-1] if brief['years'] else None,
            'method': brief['method'], 'rules': brief['rules'], 'not_available': brief['not_available'],
            'tables_omitted': 'Yearly tables are shown for eligible companies only.'}


def valuation(size, brief):
    """Current multiples: market cap (latest cover-page shares x decision-session
    close) against the last full fiscal year. No verdict: what counts as cheap
    depends on the sector and the company's own history (not yet assessed)."""
    if not size or not brief or not brief.get('years'): return None
    last = brief['years'][-1]
    v, c = {k: x['value'] for k, x in last['values'].items()}, last['calculated']
    cap = size['market_cap_usd']
    out = {'market_cap_usd': cap, 'fiscal_year_end': last['fiscal_year_end'], 'multiples': {}, 'not_meaningful': [],
           'basis': 'Market cap at the decision session against the last full fiscal year; the two dates differ.'}
    def multiple(name, value, label):
        if value is None: return
        if value > 0: out['multiples'][name] = cap / value
        else: out['not_meaningful'].append(f'{label} is zero or negative')
    multiple('price_to_earnings', v.get('net_income'), 'Net income')
    multiple('price_to_sales', v.get('revenue'), 'Revenue')
    multiple('price_to_free_cash_flow', c.get('free_cash_flow'), 'Free cash flow')
    multiple('price_to_book', v.get('equity'), "Shareholders' equity")
    if 'net_income' in v: out['earnings_yield'] = v['net_income'] / cap
    if 'free_cash_flow' in c: out['free_cash_flow_yield'] = c['free_cash_flow'] / cap
    return out


HISTORY_MULTIPLES = ('price_to_earnings', 'price_to_sales', 'price_to_free_cash_flow', 'price_to_book')
LABELS = {'price_to_earnings': 'P/E', 'price_to_sales': 'P/S', 'price_to_free_cash_flow': 'P/FCF', 'price_to_book': 'P/B'}


def _multiples(cap, values, calculated):
    out = {}
    for name, denominator in (('price_to_earnings', values.get('net_income')), ('price_to_sales', values.get('revenue')),
                              ('price_to_free_cash_flow', calculated.get('free_cash_flow')), ('price_to_book', values.get('equity'))):
        if denominator is not None and denominator > 0: out[name] = cap / denominator
    return out


def valuation_history(first_reported, prices, current_close):
    """Multiples at each fiscal year end on one basis: the unadjusted close on the
    last session on or before the year end x that year's weighted diluted shares,
    with the figures as first reported in that year's 10-K (so a later split or
    restatement is not paired with an old price). The current row uses the same
    basis with today's close and the latest year's shares. Approximate: weighted
    average shares differ from shares outstanding on any one day."""
    rows = []
    for year in first_reported.get('years', []):
        end = year['fiscal_year_end']
        values = {k: x['value'] for k, x in year['values'].items()}
        price = prices.get(end)
        if not price or not values.get('diluted_shares'): continue
        cap = price['close'] * values['diluted_shares']
        rows.append({'fiscal_year_end': end, 'price_session': price['session'], 'close': price['close'],
                     'diluted_shares': values['diluted_shares'], 'approximate_market_cap_usd': cap,
                     'multiples': _multiples(cap, values, year['calculated'])})
    current = None
    if rows and current_close:
        latest = first_reported['years'][-1]
        values = {k: x['value'] for k, x in latest['values'].items()}
        if values.get('diluted_shares'):
            cap = current_close * values['diluted_shares']
            current = {'basis_fiscal_year_end': latest['fiscal_year_end'], 'close': current_close,
                       'multiples': _multiples(cap, values, latest['calculated'])}
    comparisons = []
    for name in HISTORY_MULTIPLES:
        past = sorted(r['multiples'][name] for r in rows if name in r['multiples'])
        if current and name in current['multiples'] and len(past) >= 3:
            median = past[len(past) // 2] if len(past) % 2 else (past[len(past) // 2 - 1] + past[len(past) // 2]) / 2
            now = current['multiples'][name]
            position = 'below' if now < past[0] else 'above' if now > past[-1] else 'within'
            comparisons.append({'multiple': name, 'current': now, 'historical_median': median, 'historical_low': past[0],
                                'historical_high': past[-1], 'years': len(past), 'position': position,
                                'text': f'{LABELS[name]} {now:.1f} vs its own {len(past)}-year range '
                                        f'{past[0]:.1f}-{past[-1]:.1f} (median {median:.1f}): {position} the range.'})
    unavailable = None
    if not first_reported.get('years'): unavailable = 'No full fiscal years are visible.'
    elif not rows: unavailable = 'No fiscal year has both a stored year-end price and a reported diluted share count.'
    elif not comparisons: unavailable = 'Fewer than three comparable years for any multiple.'
    return {'basis': 'Fiscal-year-end unadjusted close x weighted diluted shares, figures as first reported; approximate. '
                     'This share basis differs from the cover-page count used in the snapshot above, so current values differ slightly.',
            'years': rows, 'current': current, 'comparisons': comparisons, 'unavailable': unavailable}


# Business models where the standard ratios mislead. Shown as context, never used.
SECTOR_NOTES = (
    ((1311, 1389), 'Oil and gas: results follow commodity prices; depletion and reserve values matter more than one year of earnings.'),
    ((2833, 2836), 'Pharmaceuticals and biotech: value often rests on pipelines and approvals; losses and low revenue are common before launch.'),
    ((3570, 3579), 'Computer hardware: cyclical demand and inventory swings move margins.'),
    ((5500, 5599), 'Auto dealers: floor-plan financing for inventory is debt-like but may sit outside reported long-term debt.'),
    ((7350, 7359), 'Equipment rental and lease-to-own: purchases of lease assets can run through operating cash flow, inflating free cash flow.'),
    ((7370, 7379), 'Software and IT services: stock-based compensation is a real cost that free cash flow excludes.'),
    ((4400, 4499), 'Water transportation and cruise lines: heavy capital spending and debt; results swing with fuel and demand.'),
    ((8000, 8099), 'Health services: reimbursement rates and regulation drive margins.'),
)


def sector_notes(industry):
    if not industry or industry.get('sic') is None: return []
    return [note for (low, high), note in SECTOR_NOTES if low <= int(industry['sic']) <= high]


MAX_USABLE_MULTIPLE = 100
VOLATILE_VARIATION = 0.5


def scenario_ranges(brief, history, size):
    """Cautious / middle / optimistic value per share from the company's own past.

    Measure: free cash flow or net income, whichever has three or more years and
    three or more usable own multiples (0-100x; near-zero profits make larger ones
    meaningless) and is steadier: fewer zero or negative years, then the lower
    coefficient of variation. Only the profit level varies between cases (worst,
    median, best year), all at the median own multiple, so pessimism is not
    compounded; the lowest and highest own multiples are shown separately as a
    sensitivity. Values are per current cover-page share against the decision-
    session close. Arithmetic from history, not a target or forecast."""
    if not size or not brief or not history: return None
    years = brief.get('years', [])[-5:]
    hist = history.get('years', [])
    shares, price = size['shares_outstanding'], size['close']
    equity = years[-1]['values'].get('equity', {}).get('value') if years else None
    book = equity / shares if equity is not None and shares else None
    candidates = []
    for label, source, key, multiple, short in (('free cash flow', 'calculated', 'free_cash_flow', 'price_to_free_cash_flow', 'P/FCF'),
                                                ('net income', 'values', 'net_income', 'price_to_earnings', 'P/E')):
        values = [y[source][key] if source == 'calculated' else y[source][key]['value'] for y in years if key in y[source]]
        multiples = sorted(h['multiples'][multiple] for h in hist if 0 < h['multiples'].get(multiple, 0) <= MAX_USABLE_MULTIPLE)
        if len(values) < 3 or len(multiples) < 3: continue
        mean = sum(values) / len(values)
        spread = (sum((v - mean) ** 2 for v in values) / len(values)) ** 0.5
        variation = spread / abs(mean) if mean else float('inf')
        candidates.append(((sum(v <= 0 for v in values), variation), label, values, multiples, short, variation))
    if not candidates:
        return {'available': False, 'reason': 'Needs at least three years of free cash flow or net income and three usable own multiples (0-100x).',
                'book_value_per_share': book, 'price': price}
    _, label, values, multiples, short, variation = min(candidates, key=lambda c: c[0])
    def median(xs):
        xs = sorted(xs)
        return xs[len(xs) // 2] if len(xs) % 2 else (xs[len(xs) // 2 - 1] + xs[len(xs) // 2]) / 2
    multiple = median(multiples)
    cases = []
    for name, profit, which in (('cautious', min(values), 'worst'), ('middle', median(values), 'median'), ('optimistic', max(values), 'best')):
        explain = f'{which} {label} of the last {len(values)} years x median own {short}'
        if profit <= 0:
            cases.append({'case': name, 'assumption': explain, 'profit': profit, 'multiple': multiple, 'value_per_share': None,
                          'vs_price': None, 'note': f'{label.capitalize()} was zero or negative; no earnings-based value.'})
            continue
        value = profit * multiple / shares
        cases.append({'case': name, 'assumption': explain, 'profit': profit, 'multiple': multiple,
                      'value_per_share': value, 'vs_price': value / price - 1})
    middle_profit = median(values)
    sensitivity = None
    if middle_profit > 0:
        sensitivity = {'low_multiple': multiples[0], 'high_multiple': multiples[-1],
                       'low_value_per_share': middle_profit * multiples[0] / shares, 'high_value_per_share': middle_profit * multiples[-1] / shares}
    volatility_note = (f'{label.capitalize()} varied a lot from year to year (coefficient of variation {variation:.2f}); '
                       'treat this range as unreliable.') if variation >= VOLATILE_VARIATION else None
    return {'available': True, 'measure': label, 'multiple_name': short, 'years_used': len(values), 'multiples_used': len(multiples),
            'variation': variation, 'volatility_note': volatility_note, 'latest_profit': values[-1], 'median_profit': middle_profit,
            'cases': cases, 'multiple_sensitivity': sensitivity, 'price': price,
            'price_session': size['close_session'], 'book_value_per_share': book,
            'label': "Arithmetic from the company's own past results and multiples, assuming that past range is representative. Not a price target or a forecast."}
