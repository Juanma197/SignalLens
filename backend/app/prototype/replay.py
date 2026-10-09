"""Backtest phase 1: replay the prototype's monthly assessment at past dates.

See docs/backtest-preregistration.md. The live rules use only data SignalLens had
retrieved by the cutoff, so a past cutoff sees nothing. The replay runs the same
rules with a knowledge horizon of "now": data counts once it was public by the
cutoff (SEC filing day, trading session, dividend ex-date) and retrieved by the
run. Identity facts (classification, CIK mapping, industry code) are taken as
known today, and the catalogue holds today's listings only (survivors); both are
documented limitations, the second addressed in phase 3.

Each month is assessed at 23:59 UTC on its first weekday. Results go to a
separate replay database, never to the research or production database (both
are opened read-only and must be byte-identical afterwards). Months from 2023
onward are the pre-registered holdout: they run only with --holdout, and only
once per database.

    python -m app.prototype.replay run --replay-db PATH [--start 2019-01] [--end 2022-12] [--holdout]
    python -m app.prototype.replay list --replay-db PATH
    python -m app.prototype.replay split-check
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, time, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sys
import uuid

import duckdb

from ..model_readiness import fingerprint
from .checks import evaluate
from .service import CONFIG, MAX_FILE_BYTES, PrototypeError, _build, validate_paths

FIRST_MONTH = date(2019, 1, 1)       # five fiscal years and own valuation history first exist
HOLDOUT_START = date(2023, 1, 1)     # pre-registered: 2023-01 onwards is run once
CUTOFF_TIME = time(23, 59, tzinfo=timezone.utc)
SCHEMA = """
CREATE TABLE IF NOT EXISTS replay_runs(
  run_id VARCHAR PRIMARY KEY, created_at TIMESTAMPTZ NOT NULL, finished_at TIMESTAMPTZ, status VARCHAR NOT NULL,
  known_at TIMESTAMPTZ NOT NULL, first_month VARCHAR NOT NULL, last_month VARCHAR NOT NULL, holdout BOOLEAN NOT NULL,
  rules_version VARCHAR NOT NULL, configuration_hash VARCHAR NOT NULL, research_sha256 VARCHAR NOT NULL, note VARCHAR);
CREATE TABLE IF NOT EXISTS replay_months(
  run_id VARCHAR NOT NULL, decision_at TIMESTAMPTZ NOT NULL, status VARCHAR NOT NULL, error VARCHAR,
  eligible INTEGER, picks INTEGER, report_sha256 VARCHAR, month_json VARCHAR, PRIMARY KEY (run_id, decision_at));
"""


class ReplayError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def month_cutoffs(first: date, last: date) -> list[datetime]:
    """23:59 UTC on the first weekday of each month from `first` to `last` (inclusive months)."""
    out, month = [], date(first.year, first.month, 1)
    while month <= date(last.year, last.month, 1):
        day = month
        while day.weekday() >= 5: day += timedelta(days=1)
        out.append(datetime.combine(day, CUTOFF_TIME))
        month = date(month.year + month.month // 12, month.month % 12 + 1, 1)
    return out


def compact(report, decision):
    """The parts of one month's report that later measurement needs, without briefs or payload text."""
    ranking = report.get('value_ranking') or {'picks': [], 'companies': [], 'population': 0}
    by_id = {c['security_id']: c for c in report['companies']}
    eligible = []
    for c in report['companies']:
        if not c.get('eligible'): continue
        calc, size = c.get('calculation') or {}, c.get('size') or {}
        signs = evaluate(c, [], decision.date())
        eligible.append({'security_id': c['security_id'], 'qualified_symbol': c['qualified_symbol'], 'cik': c.get('cik'),
                         'close': calc.get('end_close'), 'close_session': calc.get('end_session'),
                         'market_cap_usd': size.get('market_cap_usd'), 'shares_filed': size.get('shares_filed'),
                         'thesis': signs['overall'], 'warnings': [w['text'] for w in signs['automatic'] if w['severity'] in ('broken', 'warning')]})
    return {'decision_at': decision, 'knowledge_horizon': report.get('knowledge_horizon'),
            'eligible_count': report['eligible_count'], 'blockers': report['blockers'],
            'withholding_counts': report['withholding_counts'], 'session_calendar': report.get('session_calendar'),
            'population': ranking['population'], 'picks': ranking['picks'], 'assessments': ranking['companies'],
            'eligible': eligible, 'industries': {sid: (by_id[sid].get('industry') or {}).get('sic') for sid in by_id if by_id[sid].get('eligible')}}


def _encode(value):
    return json.dumps(value, default=lambda v: v.isoformat(), sort_keys=True, allow_nan=False)


def _check_months(first, last, holdout):
    if first < FIRST_MONTH: raise ReplayError('REPLAY_BEFORE_FIRST_MONTH')
    if last < first: raise ReplayError('REPLAY_EMPTY_RANGE')
    if holdout and first < HOLDOUT_START: raise ReplayError('REPLAY_HOLDOUT_MIXED_WITH_TUNING')
    if not holdout and last >= HOLDOUT_START: raise ReplayError('REPLAY_HOLDOUT_NOT_DECLARED')


def run(*, research_db, production_db, replay_db, first, last, holdout=False, known_at=None, target=15, note=None, progress=None):
    """Assess every month from `first` to `last` and store the compact results. Returns the run summary."""
    _check_months(first, last, holdout)
    known = (known_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    cutoffs = month_cutoffs(first, last)
    if cutoffs[-1] > known: raise ReplayError('REPLAY_FUTURE_CUTOFF')
    paths = [Path(research_db), Path(production_db)]
    out = Path(replay_db)
    if any(out.expanduser().resolve() == p.expanduser().resolve() for p in paths): raise ReplayError('REPLAY_DB_NOT_SEPARATE')
    try:
        validate_paths(*paths)
        if any(p.stat().st_size > MAX_FILE_BYTES for p in paths): raise PrototypeError('PROTOTYPE_DATABASE_SIZE_LIMIT')
        before = [fingerprint(p) for p in paths]
    except PrototypeError: raise
    except Exception: raise PrototypeError('PROTOTYPE_DATABASE_UNAVAILABLE') from None
    configuration = dict(CONFIG, target_members=target)
    configuration_hash = hashlib.sha256(json.dumps(configuration, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    out.parent.mkdir(parents=True, exist_ok=True)
    run_id = uuid.uuid4().hex
    with duckdb.connect(str(out)) as store:
        store.execute(SCHEMA)
        if holdout and store.execute("SELECT count(*) FROM replay_runs WHERE holdout AND status = 'completed'").fetchone()[0]:
            raise ReplayError('REPLAY_HOLDOUT_ALREADY_RUN')
        store.execute('INSERT INTO replay_runs VALUES (?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                      [run_id, datetime.now(timezone.utc), 'running', known, first.strftime('%Y-%m'), last.strftime('%Y-%m'), holdout,
                       CONFIG['version'], configuration_hash, before[0].sha256 or '', note])
    cache, done, failed = {}, 0, 0
    try:
        with duckdb.connect(str(paths[0]), read_only=True, config={'memory_limit': '1GB', 'threads': 2, 'enable_external_access': False}) as db:
            for cutoff in cutoffs:
                status, error, month = 'completed', None, None
                try:
                    report = json.loads(_encode(_build(db, cutoff, target, known=known, cache=cache)))
                    month = compact(report, cutoff)
                    digest = hashlib.sha256(_encode(report).encode()).hexdigest()
                except PrototypeError as exc:
                    status, error, digest = 'failed', exc.code, None
                with duckdb.connect(str(out)) as store:
                    store.execute('INSERT INTO replay_months VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                                  [run_id, cutoff, status, error, month and month['eligible_count'], month and len(month['picks']),
                                   digest, month and _encode(month)])
                done += status == 'completed'; failed += status == 'failed'
                if progress: progress({'decision_at': cutoff.isoformat(), 'status': status, 'error': error,
                                       'eligible': month and month['eligible_count'], 'picks': month and len(month['picks'])})
    finally:
        changed = before != [fingerprint(p) for p in paths]
        with duckdb.connect(str(out)) as store:
            store.execute('UPDATE replay_runs SET status = ?, finished_at = ? WHERE run_id = ?',
                          ['database_changed' if changed else ('completed' if done + failed == len(cutoffs) else 'interrupted'),
                           datetime.now(timezone.utc), run_id])
    if changed: raise PrototypeError('PROTOTYPE_DATABASE_CHANGED')
    return {'run_id': run_id, 'months': len(cutoffs), 'completed': done, 'failed': failed, 'holdout': holdout,
            'rules_version': CONFIG['version'], 'configuration_hash': configuration_hash, 'knowledge_horizon': known.isoformat()}


def runs(replay_db):
    path = Path(replay_db)
    if not path.is_file(): return []
    with duckdb.connect(str(path), read_only=True) as store:
        cursor = store.execute("""SELECT r.*, count(m.decision_at) AS months, sum(CASE WHEN m.status = 'completed' THEN 1 ELSE 0 END) AS completed
            FROM replay_runs r LEFT JOIN replay_months m USING (run_id) GROUP BY ALL ORDER BY r.created_at""")
        names = [d[0] for d in cursor.description]
        return [dict(zip(names, row)) for row in cursor.fetchall()]


SPLIT_RATIOS = (2, 3, 4, 5, 8, 10, 15, 20)


def split_check(research_db, *, examples=10):
    """Do stored closes jump at stock splits (unadjusted) or not (split-adjusted)?

    Looks for consecutive US sessions where the close changes by about a split ratio
    (1/2 ... 1/20 or 2x ... 20x, within 3%) while the adjusted close moves less
    than 15%. Unadjusted closes show many such days over ten years of a large
    universe; split-adjusted closes show almost none."""
    path = Path(research_db)
    before = fingerprint(path)
    ratios = ' OR '.join(f'abs(r - {k}) <= {k} * 0.03 OR abs(r - {1 / k:.6f}) <= {1 / k:.6f} * 0.03' for k in SPLIT_RATIOS)
    with duckdb.connect(str(path), read_only=True, config={'memory_limit': '1GB', 'threads': 2, 'enable_external_access': False}) as db:
        base = f"""WITH p AS (
              SELECT qualified_symbol, trading_date, close, adjusted_close,
                     close / lag(close) OVER w AS r, adjusted_close / lag(adjusted_close) OVER w AS a
              FROM global_price_observations WHERE exchange = 'US' AND status = 'available' AND close > 0 AND adjusted_close > 0
              WINDOW w AS (PARTITION BY qualified_symbol ORDER BY trading_date))
            SELECT qualified_symbol, trading_date, r, a FROM p WHERE r IS NOT NULL AND a BETWEEN 0.85 AND 1.15 AND ({ratios})"""
        symbols, sessions = db.execute("SELECT count(DISTINCT qualified_symbol), count(*) FROM global_price_observations WHERE exchange = 'US'").fetchone()
        count = db.execute(f'SELECT count(*) FROM ({base})').fetchone()[0]
        sample = db.execute(f'{base} ORDER BY trading_date DESC LIMIT ?', [examples]).fetchall()
    if fingerprint(path) != before: raise PrototypeError('PROTOTYPE_DATABASE_CHANGED')
    verdict = ('unadjusted: closes jump at splits, so historical market caps from as-reported share counts are consistent'
               if count >= max(5, symbols // 200) else
               'split-adjusted or no splits seen: historical close x as-reported shares is wrong across later splits')
    return {'us_symbols': symbols, 'us_price_rows': sessions, 'split_like_close_jumps': count, 'verdict': verdict,
            'examples': [{'symbol': s, 'date': str(d), 'close_ratio': round(r, 4), 'adjusted_ratio': round(a, 4)} for s, d, r, a in sample]}


def _month(value):
    try: return datetime.strptime(value, '%Y-%m').date()
    except ValueError: raise argparse.ArgumentTypeError('use YYYY-MM') from None


def main(argv=None):
    from ..config import get_settings
    parser = argparse.ArgumentParser(description='Backtest phase 1: replay the monthly assessment at past dates.')
    sub = parser.add_subparsers(dest='command', required=True)
    go = sub.add_parser('run', help='assess every month in the range and store the results')
    go.add_argument('--replay-db', required=True, type=Path)
    go.add_argument('--start', type=_month, default=FIRST_MONTH)
    go.add_argument('--end', type=_month, default=HOLDOUT_START - timedelta(days=1))
    go.add_argument('--holdout', action='store_true', help='run the pre-registered 2023+ holdout (once)')
    go.add_argument('--note')
    listing = sub.add_parser('list', help='show stored replay runs')
    listing.add_argument('--replay-db', required=True, type=Path)
    sub.add_parser('split-check', help='are stored closes adjusted for later splits?')
    args = parser.parse_args(argv)
    settings = get_settings()
    try:
        if args.command == 'split-check': result = split_check(settings.research_database_path)
        elif args.command == 'list': result = runs(args.replay_db)
        else:
            result = run(research_db=settings.research_database_path, production_db=settings.database_path, replay_db=args.replay_db,
                         first=args.start, last=args.end, holdout=args.holdout, note=args.note,
                         progress=lambda m: print(json.dumps(m), file=sys.stderr, flush=True))
    except (ReplayError, PrototypeError) as exc:
        print(json.dumps({'status': 'failed', 'error': exc.code}), file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
