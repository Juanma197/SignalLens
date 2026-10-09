"""Descriptive follow-up of a frozen snapshot at exact session checkpoints.

Read-only against the research database. Each checkpoint is the k-th derived US
session after the snapshot's decision session. Unreached checkpoints are
'pending'; a missing stored price is 'missing_price' (possibly a halt or
delisting) and is never filled. Returns use the currently stored adjusted-close
series for both endpoints, because a later retrieval may re-adjust history. This
is description only: validation credit is zero.
"""
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import duckdb

from ..model_readiness import fingerprint
from .service import CONFIG, MAX_FILE_BYTES, MAX_ROWS, NOTICE, PrototypeError, _sessions, day, finite

CHECKPOINTS = (21, 63, 126, 252)
# 252 sessions fit comfortably inside 400 calendar days.
READ_CALENDAR_DAYS = 400


def _read(path, start, now, end=None):
    with duckdb.connect(str(path), read_only=True, config={'memory_limit': '256MB', 'threads': 1, 'enable_external_access': False}) as db:
        if not db.execute("SELECT count(*) FROM information_schema.tables WHERE table_schema='main' AND table_name='global_price_observations'").fetchone()[0]:
            raise PrototypeError('PROTOTYPE_PRICE_TABLE_ABSENT')
        where = "WHERE exchange = 'US' AND trading_date BETWEEN ? AND ?"
        args = [start, end or start + timedelta(days=READ_CALENDAR_DAYS)]
        if db.execute(f'SELECT count(*) FROM global_price_observations {where}', args).fetchone()[0] > MAX_ROWS:
            raise PrototypeError('PROTOTYPE_ROW_LIMIT')
        cursor = db.execute(f'SELECT qualified_symbol, trading_date, exchange, adjusted_close, retrieved_at FROM global_price_observations {where}', args)
        names = [d[0] for d in cursor.description]
        return [dict(zip(names, r)) for r in cursor.fetchall()]


def track(snapshot, *, research_db, now=None):
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    members = [m for m in snapshot['members'] if m.get('calculation') and m.get('qualified_symbol')]
    base = {'notice': NOTICE, 'validation_credit': 0, 'checkpoints': list(CHECKPOINTS), 'as_of': now.isoformat(),
            'method': 'k-th derived US session after the decision session; adjusted close; no filling'}
    if not members:
        return dict(base, companies=[], comparison=[], latest_stored_session=None, databases_unchanged=True)
    start = min(date.fromisoformat(m['calculation']['end_session']) for m in members)
    path = Path(research_db)
    try:
        if not path.is_file() or path.stat().st_size > MAX_FILE_BYTES: raise PrototypeError('PROTOTYPE_DATABASE_UNAVAILABLE')
        before = fingerprint(path)
    except PrototypeError: raise
    except Exception: raise PrototypeError('PROTOTYPE_DATABASE_UNAVAILABLE') from None
    failure, rows = None, []
    try: rows = _read(path, start, now)
    except PrototypeError as exc: failure = exc
    except Exception: failure = PrototypeError('PROTOTYPE_EVIDENCE_READ_FAILED')
    if fingerprint(path) != before: raise PrototypeError('PROTOTYPE_DATABASE_CHANGED')
    if failure: raise failure
    sessions, calendar = _sessions({'global_price_observations': rows}, now)
    prices = {}
    for r in rows:
        retrieved = r.get('retrieved_at')
        if isinstance(retrieved, datetime):
            retrieved = retrieved.replace(tzinfo=timezone.utc) if retrieved.tzinfo is None else retrieved
            if retrieved > now: continue
        prices.setdefault((r['qualified_symbol'], day(r['trading_date'])), []).append(r['adjusted_close'])
    def price(symbol, d):
        values = prices.get((symbol, d), [])
        return float(values[0]) if len(values) == 1 and finite(values[0]) and float(values[0]) > 0 else None
    companies = []
    for m in members:
        end = date.fromisoformat(m['calculation']['end_session'])
        after = [d for d in sessions if d > end]
        current_base = price(m['qualified_symbol'], end)
        points = []
        for k in CHECKPOINTS:
            if len(after) < k:
                points.append({'sessions': k, 'status': 'pending', 'sessions_elapsed': len(after)}); continue
            d = after[k - 1]
            value = price(m['qualified_symbol'], d)
            if current_base is None:
                points.append({'sessions': k, 'session': d.isoformat(), 'status': 'missing_base_price'})
            elif value is None:
                points.append({'sessions': k, 'session': d.isoformat(), 'status': 'missing_price'})
            else:
                points.append({'sessions': k, 'session': d.isoformat(), 'status': 'available',
                               'adjusted_close': value, 'return': value / current_base - 1})
        companies.append({'security_id': m['security_id'], 'qualified_symbol': m['qualified_symbol'],
            'company_name': m.get('company_name'), 'result': m['security_id'] in snapshot['results'],
            'decision_session': end.isoformat(), 'snapshot_base_adjusted_close': m['calculation']['end_adjusted_close'],
            'current_base_adjusted_close': current_base, 'checkpoints': points})
    comparison = []
    for i, k in enumerate(CHECKPOINTS):
        def mean(group):
            values = [c['checkpoints'][i]['return'] for c in group if c['checkpoints'][i]['status'] == 'available']
            return {'available': len(values), 'of': len(group), 'mean_return': sum(values) / len(values) if values else None}
        comparison.append({'sessions': k, 'results': mean([c for c in companies if c['result']]), 'all_members': mean(companies)})
    return dict(base, companies=companies, comparison=comparison, session_calendar=calendar,
                latest_stored_session=sessions[-1].isoformat() if sessions else None, databases_unchanged=True,
                session_rule=CONFIG['session_calendar'])
