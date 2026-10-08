"""One-page analyst brief assembled from the company's other sections.

Every point is restated from stored evidence or from the fixed rules in the
financial, valuation and event sections. Counterarguments are also fixed rules:
they challenge the case for the company rather than support it. Nothing here is
generated text, a forecast or a recommendation, and the brief never affects the
shortlist. The operator's own thesis stays separate.
"""
from datetime import datetime, timedelta

LABELS = {'price_to_earnings': 'P/E', 'price_to_sales': 'P/S', 'price_to_free_cash_flow': 'P/FCF', 'price_to_book': 'P/B'}
STALE_EVENTS_DAYS = 14


def _money(value):
    return f'${value / 1e9:.2f}B' if abs(value) >= 1e9 else f'${value / 1e6:,.0f}M'


def analyst_brief(c, decision, *, result):
    calc, size, industry = c.get('calculation') or {}, c.get('size'), c.get('industry') or {}
    fin, val, events = c.get('financials') or {}, c.get('valuation') or {}, c.get('events') or {}
    history = val.get('history') or {}
    observations = fin.get('observations', [])
    years = fin.get('years', [])
    latest = years[-1]['calculated'] if years else {}
    momentum = calc.get('momentum_return')
    sections, counter, missing = [], [], []

    what = []
    if industry.get('sic_description'): what.append(f'SIC {industry["sic"]}: {industry["sic_description"]}.')
    if size: what.append(f'Market cap about {_money(size["market_cap_usd"])} ({size["shares_outstanding"] / 1e6:,.1f}M shares x ${size["close"]:.2f}).')
    what += [f'Sector note: {n}' for n in c.get('sector_notes', [])]
    sections.append({'title': 'What it is', 'points': what})

    why = []
    if momentum is not None:
        why.append(f'Price moved {momentum:+.1%} over the last 126 trading sessions ({calc.get("start_session")} to {calc.get("end_session")}).')
    why.append('It is one of this month\'s qualifying results (positive momentum).' if result else
               'It passed every eligibility check and is in the proposed membership, but is not a qualifying result this month.')
    sections.append({'title': 'Why it is on the list', 'points': why})

    health = [f'Strength: {o["text"]}' for o in observations if o['kind'] == 'strength'][:3]
    health += [f'Weakness: {o["text"]}' for o in observations if o['kind'] == 'weakness'][:3]
    sections.append({'title': 'Financial health', 'points': health or ['No rule-based strengths or weaknesses were found.']})

    value = []
    multiples = val.get('multiples', {})
    if multiples:
        value.append('Current: ' + ', '.join(f'{LABELS[k]} {v:.1f}' for k, v in multiples.items()) + '.')
    if val.get('free_cash_flow_yield') is not None: value.append(f'Free-cash-flow yield {val["free_cash_flow_yield"]:.1%}.')
    value += [cmp['text'] for cmp in history.get('comparisons', [])]
    scenarios = val.get('scenarios') or {}
    if scenarios.get('available'):
        shown = [f"{x['case']} ${x['value_per_share']:,.2f} ({x['vs_price']:+.0%})" if x['value_per_share'] is not None else f"{x['case']} n/a" for x in scenarios['cases']]
        value.append(f"Scenario values per share from its own past {scenarios['measure']} and {scenarios['multiple_name']}: " + ', '.join(shown) + f" vs price ${scenarios['price']:,.2f}.")
    sections.append({'title': 'Valuation', 'points': value or ['No meaningful valuation multiples.']})

    catalysts = []
    if events.get('results_timing'): catalysts.append(events['results_timing']['text'])
    catalysts += [f['text'] for f in events.get('flags', []) if f['kind'] == 'catalyst']
    sections.append({'title': 'Catalysts and timing', 'points': catalysts or ['No catalyst found in stored filings.']})

    risks = [f['text'] for f in events.get('flags', []) if f['kind'] == 'risk']
    risks += [r for r in c.get('risks', []) if 'ticker reuse' in r or 'share counts' in r]
    sections.append({'title': 'Risks from filings and data', 'points': risks or ['No filing-based risk flag.']})

    # Counterarguments: rules that argue against the case.
    loss = latest.get('operating_margin') is not None and latest['operating_margin'] < 0
    burn = latest.get('free_cash_flow') is not None and latest['free_cash_flow'] < 0
    if momentum is not None and momentum > 0.3 and (loss or burn):
        counter.append(f'The price is up {momentum:.0%} while the latest year shows '
                       + ' and '.join(x for x, flag in (('an operating loss', loss), ('negative free cash flow', burn)) if flag)
                       + '; the move may be running ahead of the business.')
    below = [cmp for cmp in history.get('comparisons', []) if cmp['position'] == 'below']
    above = [cmp for cmp in history.get('comparisons', []) if cmp['position'] == 'above']
    if below:
        growth = latest.get('revenue_growth')
        counter.append('Multiples below the company\'s own range (' + ', '.join(LABELS[b['multiple']] for b in below) + ') can mean the market expects '
                       'earnings to fall' + (f'; latest revenue growth was {growth:+.1%}.' if growth is not None else '.'))
    if above:
        counter.append('Valuation is above its own history (' + ', '.join(LABELS[a['multiple']] for a in above) + '): an improvement may already be in the price.')
    middle = next((x for x in (val.get('scenarios') or {}).get('cases', []) if x['case'] == 'middle'), None)
    if middle and middle['vs_price'] is not None and middle['vs_price'] < 0:
        counter.append(f"The middle scenario values the shares {-middle['vs_price']:.0%} below today's price: on its own history the price already assumes better-than-typical results.")
    if momentum is not None and momentum < -0.15 and below:
        counter.append(f'The price has fallen {-momentum:.0%} over 126 sessions; cheap shares can keep getting cheaper.')
    if any(o['area'] == 'dilution' and o['kind'] == 'weakness' for o in observations):
        counter.append('A rising share count means each share owns less of any future profit.')
    if latest.get('cash_conversion') is not None and latest['cash_conversion'] < 0.7:
        counter.append('Earnings were not fully backed by operating cash flow in the latest year.')
    if c.get('sector_notes'):
        counter.append('Standard ratios may mislead for this business model (see the sector note).')

    missing += [o['text'] for o in observations if o['kind'] == 'gap']
    missing += [f'{m[0].upper()}{m[1:]}: not available.' for m in fin.get('not_available', [])]
    missing += [f'Valuation: {m}.' for m in val.get('not_meaningful', [])]
    if history.get('unavailable'): missing.append(f'Valuation history: {history["unavailable"]}')
    latest_event = events.get('latest_known_at')
    if latest_event and isinstance(latest_event, datetime) and decision - latest_event > timedelta(days=STALE_EVENTS_DAYS):
        missing.append(f'Filing events were last retrieved {latest_event.date().isoformat()}; newer filings are not included.')
    missing += ['What the company sells and its competitive position are not stored; describe them in your thesis.',
                'No comparison with peer companies yet.',
                'Scenario ranges assume the past is representative; they ignore debt, cyclicality and structural change.']
    return {'label': 'Rule-based summary of stored evidence. Interpretation, not a forecast or a recommendation; your thesis is separate.',
            'sections': sections, 'counterarguments': counter or ['No rule-based counterargument was triggered; look for one yourself.'],
            'missing_evidence': missing}
