"""Suggested allocation of new money (and optionally sale proceeds) for one month.

Sales first: SELL sells the whole position; REDUCE because of overvaluation
sells half; REDUCE because of size trims back to the buying limit. Money then
goes to BUY MORE holdings and new picks not yet held, in proportion to their
ranking score, without any position ending above the buying limit of the
portfolio after the trades. Money that has nowhere to go stays as cash rather
than being forced into weaker names. New names are only opened while the
portfolio stays within its maximum number of holdings (a cap, not a target):
when slots are short, the highest-scoring picks get them. Whole shares only; USD positions only,
because the picks are US-listed. Suggestions only: nothing is executed.
"""
import math

from .decisions import RULES as DECISION_RULES

RULES = {
    'position_limit': DECISION_RULES['maximum_position_weight_for_buying'],
    'reduce_overvalued_fraction': 0.5,
    'minimum_purchase_usd': 50.0,
}


def _sales(holdings):
    sales = []
    for h in holdings:
        if h['currency'] != 'USD' or not h.get('price') or h['decision'] not in ('SELL', 'REDUCE'): continue
        close, shares = h['price']['close'], h['shares']
        if h['decision'] == 'SELL':
            sell, why = shares, 'Sell the whole position.'
        elif (h['evidence'].get('upside') or 0) < DECISION_RULES['reduce_below_upside']:
            sell, why = math.floor(shares * RULES['reduce_overvalued_fraction']), 'Sell half: the price is above the middle-case value.'
        else:
            total = h['market_value'] / h['weight']
            excess = h['market_value'] - RULES['position_limit'] * total
            sell, why = math.ceil(excess / close), f'Trim back to {RULES["position_limit"]:.0%} of the portfolio.'
        sell = min(shares, max(0, sell))
        if sell > 0:
            sales.append({'action': 'SELL' if sell == shares else 'TRIM', 'qualified_symbol': h['qualified_symbol'], 'security_id': h['security_id'],
                          'company_name': h['company_name'], 'shares': sell, 'price': close, 'amount': sell * close, 'why': why})
    return sales


def allocate(holdings, picks, cash, *, reinvest=True, max_holdings=None):
    """`holdings` and `picks` as produced for the monthly view; `cash` in USD."""
    cash = max(0.0, float(cash or 0))
    sales = _sales(holdings)
    proceeds = sum(s['amount'] for s in sales)
    available = cash + (proceeds if reinvest else 0.0)
    value = {h['security_id'] or h['qualified_symbol']: h['market_value'] for h in holdings
             if h['currency'] == 'USD' and h['market_value'] is not None}
    for s in sales: value[s['security_id'] or s['qualified_symbol']] -= s['amount']
    # The portfolio after the sales, with all available money invested; unspent money stays in it as cash.
    total = sum(value.values()) + cash + proceeds
    targets = []
    for h in holdings:
        if h['decision'] == 'BUY MORE' and h['currency'] == 'USD' and h.get('price'):
            targets.append({'action': 'BUY MORE', 'security_id': h['security_id'], 'qualified_symbol': h['qualified_symbol'],
                            'company_name': h['company_name'], 'price': h['price']['close'], 'score': h['evidence'].get('score') or 0,
                            'current': value.get(h['security_id'] or h['qualified_symbol'], 0.0)})
    for p in picks:
        if not p.get('held') and p.get('price'):
            targets.append({'action': 'NEW BUY', 'security_id': p['security_id'], 'qualified_symbol': p['qualified_symbol'],
                            'company_name': p['company_name'], 'price': p['price'], 'score': p.get('score') or 0, 'current': 0.0})
    targets = [t for t in targets if t['score'] > 0]
    sold_out = {s['qualified_symbol'] for s in sales if s['action'] == 'SELL'}
    kept = sum(1 for h in holdings if h['qualified_symbol'] not in sold_out)
    no_slot = []
    if max_holdings is not None:
        new = sorted((t for t in targets if t['action'] == 'NEW BUY'), key=lambda t: -t['score'])
        no_slot = new[max(0, max_holdings - kept):]
        targets = [t for t in targets if t not in no_slot]
    room = {t['security_id']: max(0.0, RULES['position_limit'] * total - t['current']) for t in targets}
    planned = {t['security_id']: 0.0 for t in targets}
    remaining, active = available, [t for t in targets if room[t['security_id']] > 0]
    # Proportional to score; a capped name's excess is re-offered to the rest.
    while remaining > 1e-9 and active:
        weight = sum(t['score'] for t in active)
        capped = []
        for t in active:
            want = remaining * t['score'] / weight
            if planned[t['security_id']] + want >= room[t['security_id']]: capped.append(t)
        if not capped:
            for t in active: planned[t['security_id']] += remaining * t['score'] / weight
            remaining = 0.0
            break
        for t in capped:
            remaining -= room[t['security_id']] - planned[t['security_id']]
            planned[t['security_id']] = room[t['security_id']]
        active = [t for t in active if t not in capped]
    buys = []
    for t in targets:
        shares = math.floor(planned[t['security_id']] / t['price'])
        amount = shares * t['price']
        if amount < RULES['minimum_purchase_usd']: continue
        buys.append({k: t[k] for k in ('action', 'security_id', 'qualified_symbol', 'company_name', 'price', 'score')}
                    | {'shares': shares, 'amount': amount, 'weight_after': (t['current'] + amount) / total if total else None,
                       'why': ('Add: still undervalued with room to grow.' if t['action'] == 'BUY MORE' else 'Open: one of this month\'s top picks.')})
    spent = sum(b['amount'] for b in buys)
    return {'max_holdings': max_holdings, 'holdings_after': kept + sum(b['action'] == 'NEW BUY' for b in buys),
            'skipped_no_slot': [{k: t[k] for k in ('qualified_symbol', 'security_id', 'company_name')} for t in no_slot],
            'new_cash': cash, 'reinvest_sales': reinvest, 'sale_proceeds': proceeds, 'available': available,
            'sales': sales, 'buys': buys, 'invested': spent, 'left_as_cash': available - spent if reinvest else cash - spent + proceeds,
            'portfolio_after': total, 'rules': RULES,
            'method': 'Sales first; then money split by ranking score, no position above the limit after the trades; whole shares; '
                      'the rest stays as cash. USD positions only.',
            'label': 'Suggested orders for you to review and place yourself. Nothing is executed.'}
