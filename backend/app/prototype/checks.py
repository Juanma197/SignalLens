"""Thesis checks: the operator's own conditions plus automatic warning signs,
re-evaluated against the stored evidence at any cutoff.

A thesis can break while the price holds up, so nothing here looks at the
operator's purchase price. Each condition compares one metric from the latest
visible full fiscal year (or the current valuation) with a threshold the
operator chose. Missing evidence is 'unknown', never assumed to pass.
Automatic checks always run: value-trap signs break the thesis; filing risk
flags and a vanished discount are warnings.
"""
from datetime import date
import math

from .ranking import RULES, TRAP_TEXT, _traps

MAX_CHECKS = 20
MAX_NOTE = 500
STALE_FISCAL_YEAR_DAYS = 550
COMPARATORS = ('at_least', 'at_most')


def _calc(name):
    return lambda c, year: (year or {}).get('calculated', {}).get(name)


def _value(name):
    return lambda c, year: ((year or {}).get('values', {}).get(name) or {}).get('value')


def _upside(c, year):
    scenarios = (c.get('valuation') or {}).get('scenarios') or {}
    middle = next((x for x in scenarios.get('cases', []) if x['case'] == 'middle'), None)
    return middle['vs_price'] if scenarios.get('available') and middle else None


# name: (label, unit, source, extractor). Units: fraction (shown as %), ratio, usd, usd_per_share.
METRICS = {
    'revenue_growth': ('Revenue growth', 'fraction', 'fiscal_year', _calc('revenue_growth')),
    'operating_margin': ('Operating margin', 'fraction', 'fiscal_year', _calc('operating_margin')),
    'net_margin': ('Net margin', 'fraction', 'fiscal_year', _calc('net_margin')),
    'free_cash_flow_margin': ('Free cash flow margin', 'fraction', 'fiscal_year', _calc('free_cash_flow_margin')),
    'free_cash_flow': ('Free cash flow', 'usd', 'fiscal_year', _calc('free_cash_flow')),
    'net_income': ('Net income', 'usd', 'fiscal_year', _value('net_income')),
    'cash_conversion': ('Operating cash flow / net income', 'ratio', 'fiscal_year', _calc('cash_conversion')),
    'current_ratio': ('Current ratio', 'ratio', 'fiscal_year', _calc('current_ratio')),
    'liabilities_to_assets': ('Liabilities / assets', 'fraction', 'fiscal_year', _calc('liabilities_to_assets')),
    'diluted_share_change': ('Diluted share count change', 'fraction', 'fiscal_year', _calc('diluted_share_change')),
    'middle_case_upside': ('Middle-case upside vs price', 'fraction', 'valuation', _upside),
    'price_change_126': ('Price change over 126 sessions', 'fraction', 'price', lambda c, y: (c.get('calculation') or {}).get('momentum_return')),
    'close': ('Latest close', 'usd_per_share', 'price', lambda c, y: (c.get('size') or {}).get('close')),
}


def catalogue():
    return [{'metric': k, 'label': v[0], 'unit': v[1], 'source': v[2]} for k, v in METRICS.items()]


class CheckError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def validate(checks):
    """Normalised list of {metric, comparator, threshold, note}; raises CheckError."""
    if not isinstance(checks, list) or len(checks) > MAX_CHECKS: raise CheckError('PROTOTYPE_INVALID_CHECKS')
    out = []
    for item in checks:
        if not isinstance(item, dict) or item.get('metric') not in METRICS or item.get('comparator') not in COMPARATORS:
            raise CheckError('PROTOTYPE_INVALID_CHECKS')
        try: threshold = float(item.get('threshold'))
        except (TypeError, ValueError): raise CheckError('PROTOTYPE_INVALID_CHECKS') from None
        note = str(item.get('note') or '').strip() or None
        if not math.isfinite(threshold) or (note and len(note) > MAX_NOTE): raise CheckError('PROTOTYPE_INVALID_CHECKS')
        out.append({'metric': item['metric'], 'comparator': item['comparator'], 'threshold': threshold, 'note': note})
    return out


def _latest_year(c):
    financials = c.get('financials') or {}
    years = financials.get('years') or []
    return years[-1] if years else financials.get('latest_year')


def evaluate(c, checks, decision_day):
    """Evaluate one company's checks and automatic signs at the report's cutoff."""
    year = _latest_year(c)
    fiscal_end = year and date.fromisoformat(str(year['fiscal_year_end'])[:10])
    stale = fiscal_end is not None and (decision_day - fiscal_end).days > STALE_FISCAL_YEAR_DAYS
    results = []
    for check in checks:
        label, unit, source, extract = METRICS[check['metric']]
        value = None if source == 'fiscal_year' and (year is None or stale) else extract(c, year)
        if value is None:
            status = 'unknown'
            reason = ('Figures are older than 550 days.' if stale else 'No visible value at this cutoff.')
        else:
            ok = value >= check['threshold'] if check['comparator'] == 'at_least' else value <= check['threshold']
            status, reason = ('holds' if ok else 'broken'), None
        results.append(check | {'label': label, 'unit': unit, 'source': source, 'value': value, 'status': status, 'reason': reason,
                                'fiscal_year_end': str(fiscal_end) if source == 'fiscal_year' and fiscal_end else None})
    automatic = []
    if year and not stale:
        automatic += [{'kind': 'value_trap', 'severity': 'broken', 'text': TRAP_TEXT[t]} for t in _traps(year)]
    elif year is None:
        automatic.append({'kind': 'coverage', 'severity': 'unknown', 'text': 'No full-year figures are visible at this cutoff.'})
    else:
        automatic.append({'kind': 'coverage', 'severity': 'unknown', 'text': f'Latest full-year figures end {fiscal_end}, more than 550 days ago.'})
    automatic += [{'kind': 'filing_risk', 'severity': 'warning', 'text': f['text']}
                  for f in ((c.get('events') or {}).get('flags') or []) if f['kind'] == 'risk']
    upside = _upside(c, year)
    if upside is not None and upside < RULES['minimum_upside']:
        automatic.append({'kind': 'valuation', 'severity': 'warning',
                          'text': f'Middle-case upside is {upside:.0%}, below the {RULES["minimum_upside"]:.0%} minimum: no longer clearly undervalued.'})
    severities = [r['status'] for r in results] + [a['severity'] for a in automatic]
    overall = ('broken' if 'broken' in severities else 'warning' if 'warning' in severities
               else 'unknown' if 'unknown' in severities else 'intact')
    return {'security_id': c['security_id'], 'qualified_symbol': c.get('qualified_symbol'), 'company_name': c.get('company_name'),
            'eligible': c.get('eligible'), 'fiscal_year_end': str(fiscal_end) if fiscal_end else None, 'overall': overall,
            'checks': results, 'automatic': automatic, 'has_own_checks': bool(checks)}
