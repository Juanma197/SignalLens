"""The SignalLens cash pool: uninvested pounds in the brokerage account.

Built only from what the operator confirms: deposits and withdrawals they
record, and the pounds each recorded trade actually cost or paid (the broker
converts dollars automatically, so the pound amount comes from its
confirmation). Cash carries over from month to month and sale proceeds return
to it. A trade without a pound amount is listed as uncounted rather than
converted at a guessed rate. Pure functions; nothing is read or written here.
"""
from .store import ACCOUNT_CURRENCY

TOLERANCE = 0.005


def ledger(movements, trades, *, until=None):
    """Cash balance from non-voided movements and trades dated on or before `until` (a date)."""
    day = until.isoformat() if until else None
    entries, uncounted = [], []
    for m in movements:
        if m.get('voided_at') or (day and str(m['moved_on']) > day): continue
        sign = 1 if m['kind'] == 'deposit' else -1
        entries.append({'on': str(m['moved_on']), 'recorded_at': str(m['recorded_at']), 'kind': m['kind'],
                        'amount': sign * float(m['amount']), 'movement_id': m['movement_id'], 'note': m.get('note')})
    for t in trades:
        if t.get('voided_at') or (day and str(t['traded_on']) > day): continue
        if t.get('account_amount') is None:
            uncounted.append({k: t[k] for k in ('transaction_id', 'kind', 'qualified_symbol', 'traded_on', 'currency')})
            continue
        sign = -1 if t['kind'] == 'buy' else 1
        entries.append({'on': str(t['traded_on']), 'recorded_at': str(t['recorded_at']), 'kind': t['kind'],
                        'amount': sign * float(t['account_amount']), 'transaction_id': t['transaction_id'],
                        'qualified_symbol': t['qualified_symbol']})
    entries.sort(key=lambda e: (e['on'], e['recorded_at']))
    balance = 0.0
    for e in entries:
        balance += e['amount']
        e['balance'] = balance
    month = (day or (entries[-1]['on'] if entries else ''))[:7]
    def total(kind): return sum(abs(e['amount']) for e in entries if e['kind'] == kind)
    return {'currency': ACCOUNT_CURRENCY, 'balance': balance, 'overdrawn': balance < -TOLERANCE,
            'deposited': total('deposit'), 'withdrawn': total('withdrawal'),
            'spent_on_buys': total('buy'), 'received_from_sales': total('sell'),
            'deposited_this_month': sum(e['amount'] for e in entries if e['kind'] == 'deposit' and e['on'][:7] == month),
            'uncounted_trades': uncounted, 'entries': entries[::-1],
            'method': 'Confirmed deposits minus withdrawals, minus the pounds each buy cost, plus the pounds each sale paid '
                      '(fees and currency conversion included, as your broker reported them).'}


def implied_gbp_rate(trades, currency, *, until=None):
    """Pounds per unit of `currency` implied by the latest recorded trade with a pound amount,
    fees and the broker's conversion included. A fallback when no stored rate exists."""
    day = until.isoformat() if until else None
    usable = [t for t in trades if t['currency'] == currency and t.get('account_amount') and not t.get('voided_at')
              and (not day or str(t['traded_on']) <= day) and float(t['shares']) * float(t['price']) > 0]
    if not usable: return None
    t = max(usable, key=lambda t: (str(t['traded_on']), str(t['recorded_at'])))
    return {'rate': float(t['account_amount']) / (float(t['shares']) * float(t['price'])), 'observed_on': str(t['traded_on']),
            'source': 'your_last_trade'}
