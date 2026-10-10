"""Suggested allocation of new money (and optionally sale proceeds) for one month.

Sales first: SELL sells the whole position; REDUCE because of overvaluation
sells half; REDUCE because of size trims back to the buying limit. Money then
goes to BUY MORE holdings and new picks not yet held, in proportion to their
ranking score, without any position ending above the buying limit of the
portfolio after the trades. Money that has nowhere to go stays as cash rather
than being forced into weaker names. New names are only opened while the
portfolio stays within its maximum number of holdings (a cap, not a target):
when slots are short, the highest-scoring picks get them. Shares are whole, or
fractional to four decimals when the broker allows it (Trading 212 does), so a
small monthly amount can still buy a high-priced stock. USD positions only,
because the picks are US-listed. Suggestions only: nothing is executed.

RULES are the values the backtest was registered with and stay fixed for it; the
live app passes its own (`rules`): the operator's position and top-three limits,
minimum trade, and no automatic trim of a position that grew past its limit
(that is a review, not a sale). Live, sale proceeds are not spent until the
sale is recorded (`reinvest=False`): buys use confirmed cash only.
"""
import math

from .decisions import RULES as DECISION_RULES

RULES = {
    'position_limit': DECISION_RULES['maximum_position_weight_for_buying'],
    'reduce_overvalued_fraction': 0.5,
    'minimum_purchase_usd': 50.0,
    'fraction_decimals': 4,
    'top3_limit': None,          # combined weight of the three largest positions; None = no limit
    'trim_oversized': True,      # sell a position back to the limit when it grew past it
}


def _round(shares, fractional, rounding):
    """Shares rounded with `rounding` (math.floor or math.ceil) to a whole share or to the fraction step."""
    step = 10 ** RULES['fraction_decimals'] if fractional else 1
    nudge = -1e-9 if rounding is math.ceil else 1e-9  # keep float noise from crossing a step
    return rounding(shares * step + nudge) / step


def _sales(holdings, fractional, R=RULES):
    sales = []
    for h in holdings:
        if h['currency'] != 'USD' or not h.get('price') or h['decision'] not in ('SELL', 'REDUCE'): continue
        close, shares = h['price']['close'], h['shares']
        if h['decision'] == 'SELL':
            sell, why = shares, 'Sell the whole position.'
        elif (h['evidence'].get('upside') or 0) < DECISION_RULES['reduce_below_upside']:
            sell, why = _round(shares * R['reduce_overvalued_fraction'], fractional, math.floor), 'Sell half: the price is above the middle-case value.'
        elif not R['trim_oversized']:
            continue
        else:
            total = h['market_value'] / h['weight']
            excess = h['market_value'] - R['position_limit'] * total
            sell, why = _round(excess / close, fractional, math.ceil), f'Trim back to {R["position_limit"]:.0%} of the portfolio.'
        sell = min(shares, max(0, sell))
        if sell > 0:
            sales.append({'action': 'SELL' if sell == shares else 'TRIM', 'qualified_symbol': h['qualified_symbol'], 'security_id': h['security_id'],
                          'company_name': h['company_name'], 'shares': sell, 'price': close, 'amount': sell * close, 'why': why})
    return sales


def _top3_cut(value, targets, planned, total, limit):
    """Reduce planned buys so the three largest positions after the trades stay within
    `limit` of the portfolio; money cut stays as cash. Returns the names cut."""
    after = dict(value)
    for t in targets: after[t['security_id']] = t['current'] + planned[t['security_id']]
    cut = set()
    for _ in range(100):
        top = sorted(after.items(), key=lambda kv: -kv[1])[:3]
        excess = sum(v for _, v in top) - limit * total
        if excess <= 1e-9: break
        name = next((k for k, _ in top if planned.get(k, 0) > 1e-9), None)
        if name is None: break  # the largest holdings are over the limit on their own: nothing to cut
        less = min(planned[name], excess)
        planned[name] -= less; after[name] -= less; cut.add(name)
    return cut


def allocate(holdings, picks, cash, *, reinvest=True, max_holdings=None, fractional=False, rules=None):
    """`holdings` and `picks` as produced for the monthly view; `cash` in USD."""
    R = RULES | (rules or {})
    cash = max(0.0, float(cash or 0))
    sales = _sales(holdings, fractional, R)
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
    room = {t['security_id']: max(0.0, R['position_limit'] * total - t['current']) for t in targets}
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
    top3_cut = _top3_cut(value, targets, planned, total, R['top3_limit']) if R['top3_limit'] else set()
    buys = []
    for t in targets:
        shares = _round(planned[t['security_id']] / t['price'], fractional, math.floor)
        amount = shares * t['price']
        if amount < R['minimum_purchase_usd']: continue
        buys.append({k: t[k] for k in ('action', 'security_id', 'qualified_symbol', 'company_name', 'price', 'score')}
                    | {'shares': shares, 'amount': amount, 'weight_after': (t['current'] + amount) / total if total else None,
                       'why': ('Add: still undervalued with room to grow.' if t['action'] == 'BUY MORE' else 'Open: one of this month\'s top picks.')})
    spent = sum(b['amount'] for b in buys)
    return {'max_holdings': max_holdings, 'holdings_after': kept + sum(b['action'] == 'NEW BUY' for b in buys),
            'skipped_no_slot': [{k: t[k] for k in ('qualified_symbol', 'security_id', 'company_name')} for t in no_slot],
            'new_cash': cash, 'reinvest_sales': reinvest, 'sale_proceeds': proceeds, 'available': available,
            'sales': sales, 'buys': buys, 'invested': spent, 'left_as_cash': available - spent if reinvest else cash - spent + proceeds,
            'portfolio_after': total, 'rules': R, 'fractional': fractional,
            'top3_limited': sorted({t['qualified_symbol'] for t in targets if t['security_id'] in top3_cut}),
            'awaiting_proceeds': 0.0 if reinvest else proceeds,
            'method': 'Sales first; then money split by ranking score, no position above the limit after the trades'
                      + (f', the three largest within {R["top3_limit"]:.0%}' if R['top3_limit'] else '') + '; '
                      + ('fractional shares; ' if fractional else 'whole shares; ') + 'the rest stays as cash. USD positions only.',
            'label': 'Suggested orders for you to review and place yourself. Nothing is executed.'}
