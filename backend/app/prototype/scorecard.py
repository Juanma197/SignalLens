"""Scorecard: was each recorded monthly call right?

Every recorded month freezes its picks and holding decisions. Afterwards each is
measured from the last completed session at the decision cutoff to exactly
21/63/126/252 derived US sessions later, on stored adjusted closes (no filling;
missing prices are shown). The benchmark is the equal-weight average return of
every company assessed that month, so the question is "did the calls beat the
rest of the list?", not "did they beat the market" (no index prices are stored).

A call is right when it beat the benchmark (picks, BUY MORE, HOLD) or lagged it
(SELL, REDUCE: selling avoided the shortfall). REVIEW is not scored. Read-only
against the research database; description only, not validation.
"""
from datetime import datetime, time, timedelta, timezone
from pathlib import Path

from ..model_readiness import fingerprint
from .service import MAX_FILE_BYTES, PrototypeError, _sessions, day, finite
from .tracking import CHECKPOINTS, _read

GROUPS = {'pick': 'Top picks', 'BUY MORE': 'Buy more', 'HOLD': 'Hold', 'sell_or_reduce': 'Sell or reduce'}


def _group(item):
    if item['kind'] == 'pick': return 'pick'
    if item['decision'] in ('SELL', 'REDUCE'): return 'sell_or_reduce'
    return item['decision'] if item['decision'] in ('BUY MORE', 'HOLD') else None


def score(records, *, research_db, now=None):
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    base = {'checkpoints': list(CHECKPOINTS), 'as_of': now.isoformat(), 'groups': GROUPS,
            'benchmark': 'Equal-weight average of every company assessed that month (not a market index).',
            'rule': 'Picks, BUY MORE and HOLD are right when they beat the benchmark; SELL and REDUCE are right when they lagged it. REVIEW is not scored.'}
    if not records: return dict(base, months=[], summary=[])
    decisions = [datetime.fromisoformat(str(r['decision_at']).replace('Z', '+00:00')) for r in records]
    start = min(decisions).date() - timedelta(days=14)
    path = Path(research_db)
    try:
        if not path.is_file() or path.stat().st_size > MAX_FILE_BYTES: raise PrototypeError('PROTOTYPE_DATABASE_UNAVAILABLE')
        before = fingerprint(path)
    except PrototypeError: raise
    except Exception: raise PrototypeError('PROTOTYPE_DATABASE_UNAVAILABLE') from None
    failure, rows = None, []
    try: rows = _read(path, start, now, end=now.date())  # every month recorded so far, up to today
    except PrototypeError as exc: failure = exc
    except Exception: failure = PrototypeError('PROTOTYPE_EVIDENCE_READ_FAILED')
    if fingerprint(path) != before: raise PrototypeError('PROTOTYPE_DATABASE_CHANGED')
    if failure: raise failure
    sessions, _ = _sessions({'global_price_observations': rows}, now)
    prices = {}
    for r in rows:
        retrieved = r.get('retrieved_at')
        if isinstance(retrieved, datetime) and (retrieved.replace(tzinfo=timezone.utc) if retrieved.tzinfo is None else retrieved) > now: continue
        prices.setdefault((r['qualified_symbol'], day(r['trading_date'])), []).append(r['adjusted_close'])
    def price(symbol, d):
        values = prices.get((symbol, d), [])
        return float(values[0]) if len(values) == 1 and finite(values[0]) and float(values[0]) > 0 else None

    months, tallies = [], {}
    for record, decision in zip(records, decisions):
        completed = [s for s in sessions if datetime.combine(s, time(22), timezone.utc) <= decision]
        base_session = completed[-1] if completed else None
        after = [s for s in sessions if base_session and s > base_session]
        def returns(symbol):
            out = []
            for k in CHECKPOINTS:
                if base_session is None: out.append({'sessions': k, 'status': 'no_base_session'}); continue
                if len(after) < k: out.append({'sessions': k, 'status': 'pending', 'sessions_elapsed': len(after)}); continue
                start_price, end_price = price(symbol, base_session), price(symbol, after[k - 1])
                if start_price is None or end_price is None:
                    out.append({'sessions': k, 'status': 'missing_price', 'session': after[k - 1].isoformat()}); continue
                out.append({'sessions': k, 'status': 'available', 'session': after[k - 1].isoformat(), 'return': end_price / start_price - 1})
            return out
        bench = []
        member_returns = [returns(s) for s in record['benchmark_symbols']]
        for i, k in enumerate(CHECKPOINTS):
            values = [m[i]['return'] for m in member_returns if m[i]['status'] == 'available']
            bench.append({'sessions': k, 'return': sum(values) / len(values) if values else None, 'available': len(values), 'of': len(member_returns)})
        items = []
        for item in record['items']:
            group = _group(item)
            points = returns(item['qualified_symbol'])
            for i, p in enumerate(points):
                b = bench[i]['return']
                if p['status'] != 'available' or b is None or group is None: continue
                p['excess'] = p['return'] - b
                p['right'] = p['excess'] < 0 if group == 'sell_or_reduce' else p['excess'] > 0
                t = tallies.setdefault((group, p['sessions']), {'scored': 0, 'right': 0, 'excess': 0.0})
                t['scored'] += 1; t['right'] += p['right']; t['excess'] += p['excess']
            items.append(item | {'group': group, 'checkpoints': points})
        months.append({'record_id': record['record_id'], 'month': record['month'], 'decision_at': record['decision_at'],
                       'base_session': base_session.isoformat() if base_session else None, 'benchmark': bench, 'items': items})
    summary = []
    for group in GROUPS:
        for k in CHECKPOINTS:
            t = tallies.get((group, k), {'scored': 0, 'right': 0, 'excess': 0.0})
            summary.append({'group': group, 'sessions': k, 'scored': t['scored'], 'right': t['right'],
                            'hit_rate': t['right'] / t['scored'] if t['scored'] else None,
                            'mean_excess': t['excess'] / t['scored'] if t['scored'] else None})
    return dict(base, months=months, summary=summary, latest_stored_session=sessions[-1].isoformat() if sessions else None)


def record_from_monthly(monthly, report):
    """The frozen subset of one month's view that is later scored."""
    items = [{'kind': 'pick', 'security_id': p['security_id'], 'qualified_symbol': p['qualified_symbol'], 'company_name': p.get('company_name'),
              'rank': p.get('rank'), 'recommendation': p.get('recommendation'), 'upside': p.get('upside'), 'decision': None, 'reasons': []}
             for p in monthly['picks']]
    items += [{'kind': 'holding', 'security_id': h['security_id'], 'qualified_symbol': h['qualified_symbol'], 'company_name': h.get('company_name'),
               'rank': None, 'recommendation': None, 'upside': h['evidence'].get('upside'), 'decision': h['decision'], 'reasons': h['reasons'],
               'weight': h.get('weight')}
              for h in monthly['holdings']]
    assessed = {a['security_id'] for a in (report.get('value_ranking') or {}).get('companies', [])}
    benchmark = sorted(c['qualified_symbol'] for c in report['companies'] if c['security_id'] in assessed and c.get('qualified_symbol'))
    return {'decision_at': monthly['decision_at'], 'items': items, 'benchmark_symbols': benchmark,
            'allocation': monthly.get('allocation'), 'decision_rules': monthly['rules'], 'synthetic_fixture': monthly['synthetic_fixture']}
