"""Cheap daily price and dividend update from EODHD's whole-market files.

The full refresh asks for each listing separately (two requests per listing,
about 5,000 for a wide catalogue). Between full refreshes this step asks for one
day of the whole US market at a time: `eod-bulk-last-day/US?date=D` for prices
and `...&type=dividends` and `...&type=splits` for dividends and splits, three
requests per trading day. A listing that splits is downloaded again in full
(one more request): EODHD rebases its whole adjusted-close history on the split,
so the stored history and the new day would otherwise be on different bases.

Prices are validated exactly as in the full refresh (one-row `parse_eod`) and
stored for active-catalogue US listings only. A 'bulk_daily' checkpoint, which
the prototype accepts as dividend coverage, is written only when every weekday
since the last full refresh or bulk day has been processed: a missed day would
otherwise hide a dividend. Older gaps (more than MAX_GAP_DAYS) need a full
refresh. Writes the research database only; production is fingerprinted.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import duckdb

from .eodhd_ingestion import BudgetStop, EODHDClient, parse_eod, rebase_from, split_ratio
from .global_market_data import CorporateAction, GlobalMarketDataRepository, utc_naive
from .global_universe import GlobalUniverseRepository
from .model_readiness import fingerprint
from .sec_ingestion import validate_paths

STAGE = 'bulk_daily'
MAX_GAP_DAYS = 31
SCHEMA = """
CREATE TABLE IF NOT EXISTS eodhd_bulk_days(trading_date DATE PRIMARY KEY, price_rows INTEGER NOT NULL,
  dividend_rows INTEGER NOT NULL, retrieved_at TIMESTAMP NOT NULL);
ALTER TABLE eodhd_bulk_days ADD COLUMN IF NOT EXISTS split_rows INTEGER;
"""


def weekdays(start: date, end: date) -> list[date]:
    days, d = [], start
    while d <= end:
        if d.weekday() < 5: days.append(d)
        d += timedelta(days=1)
    return days


def covered_through(db, symbols: list[str]) -> date | None:
    """The last day whose dividends are known for every listing: the oldest latest
    full-refresh day across listings, extended by contiguous bulk days."""
    rows = dict(db.execute("""SELECT qualified_symbol, max(updated_at) FROM eodhd_ingestion_checkpoints
        WHERE status = 'completed' AND stage IN ('prices', 'refresh') GROUP BY 1""").fetchall())
    if not symbols or any(s not in rows for s in symbols): return None
    through = min(rows[s] for s in symbols).date()
    done = {r[0] for r in db.execute('SELECT trading_date FROM eodhd_bulk_days').fetchall()}
    for d in weekdays(through + timedelta(days=1), date.today() + timedelta(days=1)):
        if d not in done: break
        through = d
    return through


def update(*, research, production, client: EODHDClient, now: datetime | None = None) -> dict:
    validate_paths(research, production)
    production_before = fingerprint(production)
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    listings = [x for x in GlobalUniverseRepository(research).latest_items(as_of=now)[2] if x.exchange == 'US']
    by_code = {x.ticker.strip().upper(): x for x in listings}
    by_symbol = {x.qualified_symbol: x for x in listings}
    with duckdb.connect(str(research)) as db:
        db.execute(SCHEMA)
        start = covered_through(db, [x.qualified_symbol for x in listings])
    if start is None: return {'command': 'daily-prices', 'status': 'needs_full_refresh', 'reason': 'some listings have never been fully refreshed'}
    # Today's file is only complete after the US close; take days up to yesterday before 22:00 UTC.
    last = now.date() if now.hour >= 22 else now.date() - timedelta(days=1)
    days = weekdays(start + timedelta(days=1), last)
    if (last - start).days > MAX_GAP_DAYS:
        return {'command': 'daily-prices', 'status': 'needs_full_refresh', 'reason': f'last covered day {start} is more than {MAX_GAP_DAYS} days ago'}
    market = GlobalMarketDataRepository(research)
    processed, stop = [], None
    for day in days:
        try:
            price_payload = client.get('eod-bulk-last-day/US', {'date': day.isoformat()})
            dividend_payload = client.get('eod-bulk-last-day/US', {'date': day.isoformat(), 'type': 'dividends'})
            split_payload = client.get('eod-bulk-last-day/US', {'date': day.isoformat(), 'type': 'splits'})
        except BudgetStop as exc:
            stop = exc.reason; break
        prices, actions = [], []
        for row in price_payload if isinstance(price_payload, list) else []:
            listing = by_code.get(str(row.get('code', '')).strip().upper())
            if listing is None or str(row.get('date', ''))[:10] != day.isoformat(): continue
            try: prices += parse_eod([{k: row.get(k) for k in ('date', 'open', 'high', 'low', 'close', 'adjusted_close', 'volume')}], listing, now, day, day)
            except (ValueError, ArithmeticError): continue  # an invalid row is skipped, never stored
        for row in dividend_payload if isinstance(dividend_payload, list) else []:
            listing = by_code.get(str(row.get('code', '')).strip().upper())
            if listing is None or str(row.get('date', ''))[:10] != day.isoformat(): continue
            try: value = Decimal(str(row.get('dividend', row.get('value'))))
            except ArithmeticError: continue
            if value > 0: actions.append(CorporateAction(listing.qualified_symbol, day, 'cash_distribution', value, listing.currency, 'eodhd', now))
        dividends, splits = len(actions), []
        for row in split_payload if isinstance(split_payload, list) else []:
            listing = by_code.get(str(row.get('code', '')).strip().upper())
            if listing is None or str(row.get('date', ''))[:10] != day.isoformat(): continue
            try: splits.append(CorporateAction(listing.qualified_symbol, day, 'split', split_ratio(row.get('split', row.get('value'))), None, 'eodhd', now))
            except (ValueError, ArithmeticError): continue
        rebased = []
        try:
            for split in splits:
                with duckdb.connect(str(research), read_only=True) as db:
                    first = rebase_from(db, split.qualified_symbol, [split], day)
                if first:
                    since, listing = first, by_symbol[split.qualified_symbol]
                    history = parse_eod(client.get(f'eod/{split.qualified_symbol}', {'from': since.isoformat(), 'to': last.isoformat(), 'period': 'd'}), listing, now, since, last)
                    prices = [p for p in prices if p.qualified_symbol != split.qualified_symbol] + history
                    rebased.append(split.qualified_symbol)
        except BudgetStop as exc:
            stop = exc.reason; break
        except Exception:
            # Storing the day without the rebase would join two bases; stop and retry it next run.
            stop = 'split_rebase_failed'; break
        market.store(prices, actions + splits)
        with duckdb.connect(str(research)) as db:
            db.execute('INSERT OR REPLACE INTO eodhd_bulk_days VALUES (?, ?, ?, ?, ?)', [day, len(prices), dividends, utc_naive(now), len(splits)])
        processed.append({'date': day.isoformat(), 'prices': len(prices), 'dividends': dividends, 'splits': len(splits), 'rebased': rebased})
    if processed and stop is None:
        with duckdb.connect(str(research)) as db:
            db.executemany('INSERT OR REPLACE INTO eodhd_ingestion_checkpoints VALUES (?, ?, ?, ?, ?)',
                           [[STAGE, x.qualified_symbol, 'completed', None, utc_naive(datetime.now(timezone.utc))] for x in listings])
    if fingerprint(production) != production_before: raise RuntimeError('production database changed')
    return {'command': 'daily-prices', 'status': 'stopped' if stop else 'completed', 'stop_reason': stop,
            'covered_from': start.isoformat(), 'days': processed, 'requests': client.requests, 'production_unchanged': True}
