"""Holdings computed from the operator's own recorded trades, valued at stored closes.

Positions use the average-cost method: a buy adds shares and cost (fees
included); a sell removes shares at the current average cost and books the
difference as realised profit after fees. Market values use the latest stored
unadjusted close available at the valuation time. A holding without a stored
price keeps its cost basis but has no market value, weight or unrealised
result; nothing is estimated. Read-only against the research database.
"""
from datetime import timezone
import math
from pathlib import Path

import duckdb

from ..model_readiness import fingerprint
from .service import MAX_FILE_BYTES, PrototypeError

SHARE_TOLERANCE = 1e-9
MAX_SYMBOLS = 200


def _ordered(trades):
    return sorted(trades, key=lambda t: (str(t['traded_on']), str(t['recorded_at']), t['transaction_id']))


def positions_from(trades):
    """Open and closed positions from non-voided buy/sell rows; raises on an oversell."""
    book = {}
    for t in _ordered(trades):
        key = (t['qualified_symbol'], t['currency'])
        p = book.setdefault(key, {'qualified_symbol': t['qualified_symbol'], 'currency': t['currency'],
            'company_name': None, 'shares': 0.0, 'cost_basis': 0.0, 'realised_profit': 0.0, 'fees': 0.0,
            'first_traded_on': str(t['traded_on']), 'last_traded_on': None, 'trades': 0})
        p['company_name'] = t.get('company_name') or p['company_name']
        shares, price, fees = float(t['shares']), float(t['price']), float(t.get('fees') or 0)
        if t['kind'] == 'buy':
            p['shares'] += shares
            p['cost_basis'] += shares * price + fees
        else:
            if shares > p['shares'] + SHARE_TOLERANCE:
                raise PrototypeError('PROTOTYPE_SELL_EXCEEDS_HOLDING')
            average = p['cost_basis'] / p['shares']
            p['realised_profit'] += shares * (price - average) - fees
            p['shares'] -= shares
            p['cost_basis'] -= shares * average
            if p['shares'] <= SHARE_TOLERANCE: p['shares'], p['cost_basis'] = 0.0, 0.0
        p['fees'] += fees
        p['last_traded_on'] = str(t['traded_on'])
        p['trades'] += 1
    for p in book.values():
        p['average_cost'] = p['cost_basis'] / p['shares'] if p['shares'] > SHARE_TOLERANCE else None
    open_ = [p for p in book.values() if p['shares'] > SHARE_TOLERANCE]
    closed = [p for p in book.values() if p['shares'] <= SHARE_TOLERANCE]
    return open_, closed


def _read_only(research_db, reader):
    """Run `reader(db)` on a read-only connection; the file must be unchanged afterwards."""
    path = Path(research_db)
    try:
        if not path.is_file() or path.stat().st_size > MAX_FILE_BYTES: raise PrototypeError('PROTOTYPE_DATABASE_UNAVAILABLE')
        before = fingerprint(path)
    except PrototypeError: raise
    except Exception: raise PrototypeError('PROTOTYPE_DATABASE_UNAVAILABLE') from None
    failure, result = None, None
    try:
        with duckdb.connect(str(path), read_only=True, config={'memory_limit': '256MB', 'threads': 1, 'enable_external_access': False}) as db:
            result = reader(db)
    except PrototypeError as exc: failure = exc
    except Exception: failure = PrototypeError('PROTOTYPE_EVIDENCE_READ_FAILED')
    if fingerprint(path) != before: raise PrototypeError('PROTOTYPE_DATABASE_CHANGED')
    if failure: raise failure
    return result


def read_market(research_db, symbols, now):
    """Latest stored close and listing identity per symbol, at or before `now`."""
    symbols = sorted(set(symbols))[:MAX_SYMBOLS]
    if not symbols: return {}
    marks = ', '.join('?' for _ in symbols)
    naive_now = now.astimezone(timezone.utc).replace(tzinfo=None)

    def reader(db):
        market = {}
        for symbol, trading_date, close, retrieved in db.execute(f"""
            SELECT qualified_symbol, trading_date, close, retrieved_at FROM (
              SELECT *, row_number() OVER (PARTITION BY qualified_symbol ORDER BY trading_date DESC, retrieved_at DESC) AS n
              FROM global_price_observations
              WHERE qualified_symbol IN ({marks}) AND status = 'available' AND trading_date <= ?
                AND retrieved_at <= ?) WHERE n = 1""",
                [*symbols, naive_now.date(), naive_now]).fetchall():
            if close is not None and math.isfinite(float(close)) and float(close) > 0:
                market.setdefault(symbol, {})['price'] = {'close': float(close), 'trading_date': str(trading_date)}
        for sid, symbol, name in db.execute(f"""SELECT security_id, qualified_symbol, company_name
                FROM security_listings WHERE qualified_symbol IN ({marks})""", symbols).fetchall():
            market.setdefault(symbol, {}).setdefault('listings', []).append({'security_id': str(sid), 'company_name': name})
        return market
    return _read_only(research_db, reader)


def read_gbp_rate(research_db, currency, now, *, tolerance_days=7):
    """Stored pounds per one unit of `currency` known at `now`, or None when there is no recent rate."""
    naive_now = now.astimezone(timezone.utc).replace(tzinfo=None)

    def reader(db):
        if not db.execute("SELECT count(*) FROM information_schema.tables WHERE table_name = 'global_fx_observations'").fetchone()[0]:
            return None
        row = db.execute("""SELECT rate, observed_on FROM global_fx_observations
            WHERE base_currency = ? AND quote_currency = 'GBP' AND observed_on <= ? AND available_at <= ?
            ORDER BY observed_on DESC, available_at DESC LIMIT 1""", [currency, naive_now.date(), naive_now]).fetchone()
        if row is None or (naive_now.date() - row[1]).days > tolerance_days: return None
        rate = float(row[0])
        return {'rate': rate, 'observed_on': str(row[1]), 'source': 'stored'} if math.isfinite(rate) and rate > 0 else None
    return _read_only(research_db, reader)


def read_gbp_rate_series(research_db, currency, since, now):
    """Stored pounds per one unit of `currency`, {observed_on: rate}, from `since` to `now`, in one read."""
    naive_now = now.astimezone(timezone.utc).replace(tzinfo=None)

    def reader(db):
        if not db.execute("SELECT count(*) FROM information_schema.tables WHERE table_name = 'global_fx_observations'").fetchone()[0]:
            return {}
        rows = db.execute("""SELECT observed_on, rate FROM global_fx_observations WHERE base_currency = ? AND quote_currency = 'GBP'
            AND observed_on BETWEEN ? AND ? AND available_at <= ? ORDER BY observed_on, available_at""",
            [currency, since, naive_now.date(), naive_now]).fetchall()
        return {d: float(r) for d, r in rows if math.isfinite(float(r)) and float(r) > 0}  # the latest available revision wins
    return _read_only(research_db, reader)


def valuation(trades, market, *, as_of):
    """Open positions with market values, closed positions, and per-currency totals."""
    open_, closed = positions_from(trades)
    for p in open_:
        info = market.get(p['qualified_symbol'], {})
        listings = info.get('listings', [])
        # An ambiguous symbol is never resolved to one company.
        p['security_id'] = listings[0]['security_id'] if len(listings) == 1 else None
        p['listed_name'] = listings[0]['company_name'] if len(listings) == 1 else None
        price = info.get('price')
        p['price'] = price
        p['market_value'] = p['shares'] * price['close'] if price else None
        p['unrealised_profit'] = p['market_value'] - p['cost_basis'] if price else None
        p['unrealised_return'] = p['unrealised_profit'] / p['cost_basis'] if price and p['cost_basis'] > 0 else None
    totals = []
    for currency in sorted({p['currency'] for p in open_ + closed}):
        held = [p for p in open_ if p['currency'] == currency]
        priced = [p for p in held if p['market_value'] is not None]
        value = sum(p['market_value'] for p in priced)
        for p in priced: p['weight'] = p['market_value'] / value if value > 0 else None
        for p in held:
            if p['market_value'] is None: p['weight'] = None
        priced_cost = sum(p['cost_basis'] for p in priced)
        totals.append({'currency': currency, 'positions': len(held), 'priced_positions': len(priced),
            'cost_basis': sum(p['cost_basis'] for p in held), 'priced_cost_basis': priced_cost,
            'market_value': value if priced else None,
            'unrealised_profit': value - priced_cost if priced else None,
            'unrealised_return': (value - priced_cost) / priced_cost if priced and priced_cost > 0 else None,
            'realised_profit': sum(p['realised_profit'] for p in open_ + closed if p['currency'] == currency)})
    open_.sort(key=lambda p: (p['currency'], -(p['market_value'] if p['market_value'] is not None else -1), p['qualified_symbol']))
    closed.sort(key=lambda p: p['last_traded_on'], reverse=True)
    return {'as_of': as_of.isoformat(), 'positions': open_, 'closed_positions': closed, 'totals': totals,
            'method': 'average cost; fees added to cost on buys and deducted from proceeds on sells; '
                      'latest stored unadjusted close; unpriced holdings are never estimated'}

