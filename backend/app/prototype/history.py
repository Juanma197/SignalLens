"""Your money against the same money in VALL.

The pre-registered pass/fail benchmark is the same pounds, on the same days,
put into a global index fund (VALL). Every recorded deposit buys the fund on its
day and every withdrawal sells it, at that day's stored close and pound/dollar
rate; the result is valued today and compared with your holdings plus cash.
VT, stored in dollars, stands in for VALL (same index family, GBP-listed VALL is
not stored); fund costs are not deducted. Pure functions; nothing is read here.
"""
from datetime import date, timedelta

FUND, FUND_LABEL = 'VT.US', 'Global stocks (VT, standing in for VALL)'
MAX_GAP_DAYS = 7  # how stale a price or rate may be on a cash-flow day


def _on_or_before(series, day):
    """(value, date) of the latest entry on or before `day`, within MAX_GAP_DAYS."""
    for back in range(MAX_GAP_DAYS + 1):
        d = day - timedelta(days=back)
        if d in series: return series[d], d
    return None, None


def money_weighted(flows, final_value, today):
    """Annual rate r with sum(flow x (1+r)^(years to today)) = final value: the
    return on the money while it was invested. `flows` are (date, pounds in > 0
    or out < 0). None when it cannot be pinned down."""
    if not flows or final_value is None: return None
    def gap(r): return sum(a * (1 + r) ** ((today - d).days / 365.25) for d, a in flows) - final_value
    lo, hi = -0.99, 10.0
    if gap(lo) * gap(hi) > 0: return None
    for _ in range(200):
        mid = (lo + hi) / 2
        if gap(lo) * gap(mid) <= 0: hi = mid
        else: lo = mid
    return (lo + hi) / 2


def versus_fund(movements, *, fund, rates, your_value, today, your_value_complete=True):
    """`movements` are the cash ledger's deposits and withdrawals (pounds), `fund`
    {date: adjusted close in dollars}, `rates` {date: pounds per dollar}, and
    `your_value` your holdings plus cash in pounds today (None when unknown)."""
    flows, rows, unpriced, units = [], [], [], 0.0
    for m in sorted((m for m in movements if not m.get('voided_at')), key=lambda m: str(m['moved_on'])):
        day = date.fromisoformat(str(m['moved_on'])[:10])
        amount = float(m['amount']) * (1 if m['kind'] == 'deposit' else -1)
        price, price_on = _on_or_before(fund, day)
        rate, _ = _on_or_before(rates, day)
        flows.append((day, amount))
        if price is None or rate is None:
            unpriced.append({'on': day.isoformat(), 'amount': amount, 'missing': 'fund price' if price is None else 'pound/dollar rate'})
            continue
        bought = amount / (rate * price)
        units += bought
        rows.append({'on': day.isoformat(), 'amount': amount, 'fund_price': price, 'price_on': price_on.isoformat(),
                     'gbp_per_usd': rate, 'units': bought})
    price_now, price_now_on = _on_or_before(fund, today)
    rate_now, _ = _on_or_before(rates, today)
    fund_value = units * price_now * rate_now if price_now is not None and rate_now is not None and not unpriced else None
    net = sum(a for _, a in flows)
    complete = fund_value is not None and your_value is not None and your_value_complete
    result = {'fund': FUND, 'fund_label': FUND_LABEL, 'as_of': today.isoformat(), 'price_on': price_now_on and price_now_on.isoformat(),
              'net_deposited': net, 'your_value': your_value, 'fund_value': fund_value,
              'difference': your_value - fund_value if complete else None,
              'your_money_weighted': money_weighted(flows, your_value, today) if your_value is not None else None,
              'fund_money_weighted': money_weighted(flows, fund_value, today) if fund_value is not None else None,
              'complete': complete, 'flows': rows[::-1], 'unpriced_flows': unpriced,
              'method': 'Each recorded deposit buys the fund on its day and each withdrawal sells it, at that day\'s stored close '
                        'and pound/dollar rate; valued today. Your side is your holdings at the latest stored close plus your cash pool. '
                        'Money-weighted returns are annual rates on the money while it was invested.',
              'caveats': ['VT (dollars) stands in for VALL; its pound returns differ slightly and fund costs are not deducted.',
                          'Trades recorded without a pound amount are not in the cash pool, so your side may be incomplete.']}
    if not flows: result['note'] = 'No deposits are recorded yet. Record deposits on the Portfolio page to compare.'
    return result
