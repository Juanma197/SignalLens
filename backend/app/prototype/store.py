"""Separate append-only prototype store: watchlist events, notes, theses,
snapshots, the operator's own trades, cash movements and portfolio settings.

Nothing here opens the research or production databases for writing. There is
no update or delete path: watchlist removal is a new event, a note correction is
a new note, a mistaken trade or cash movement is voided by a new row, a changed
setting is a new settings row (the latest wins), and a snapshot is immutable
once recorded (verified by SHA-256 on every read). Snapshots are never backfilled.
The one exception is index-fund prices for the scorecard benchmark: market data,
rewritten on refresh like the research database's prices.
"""
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import uuid

import duckdb

SCHEMA_VERSION = 1
MAX_NOTE_CHARS = 4000
MAX_ID_CHARS = 128
SNAPSHOT_MAX_LAG_DAYS = 14
# Structured thesis sections, written by the operator. The app never fills them.
THESIS_SECTIONS = ('business', 'financial_health', 'why_cheap', 'catalysts', 'downside',
                   'invalidation', 'assumptions')
THESIS_STATUSES = ('researching', 'active', 'rejected')
TRADE_KINDS = ('buy', 'sell')
CURRENCIES = ('USD', 'GBP', 'EUR', 'CAD', 'CHF', 'JPY', 'AUD')
MAX_TRADE_VALUE = 1e12
EARLIEST_TRADE = date(1970, 1, 1)
# The brokerage account's cash is held in pounds; a trade in another currency
# records what it actually cost or paid in pounds after the broker's conversion.
ACCOUNT_CURRENCY = 'GBP'
CASH_KINDS = ('deposit', 'withdrawal')
MAX_CONTRIBUTION = 1e6
MAX_HOLDINGS_LIMIT = 30
# Defaults until the operator saves their own; the contribution is expected to change.
DEFAULT_SETTINGS = {'monthly_contribution': 200.0, 'max_holdings': 10}
# EODHD-style symbol: ticker plus exchange suffix, e.g. AAPL.US or BRK-B.US.
SYMBOL = re.compile(r'^[A-Z0-9][A-Z0-9.\-]{0,19}\.[A-Z]{2,6}$')
SCHEMA = """
CREATE TABLE IF NOT EXISTS prototype_schema(version INTEGER NOT NULL, created_at TIMESTAMPTZ NOT NULL);
CREATE TABLE IF NOT EXISTS watchlist_events(
  event_id VARCHAR PRIMARY KEY, security_id VARCHAR NOT NULL, qualified_symbol VARCHAR,
  company_name VARCHAR, action VARCHAR NOT NULL CHECK(action IN ('add', 'remove')),
  recorded_at TIMESTAMPTZ NOT NULL);
CREATE TABLE IF NOT EXISTS research_notes(
  note_id VARCHAR PRIMARY KEY, security_id VARCHAR NOT NULL, body VARCHAR NOT NULL,
  recorded_at TIMESTAMPTZ NOT NULL);
CREATE TABLE IF NOT EXISTS research_theses(
  thesis_id VARCHAR PRIMARY KEY, security_id VARCHAR NOT NULL, status VARCHAR NOT NULL,
  business VARCHAR, financial_health VARCHAR, why_cheap VARCHAR, catalysts VARCHAR,
  downside VARCHAR, invalidation VARCHAR, assumptions VARCHAR, recorded_at TIMESTAMPTZ NOT NULL);
CREATE TABLE IF NOT EXISTS monthly_snapshots(
  snapshot_id VARCHAR PRIMARY KEY, month VARCHAR NOT NULL UNIQUE, decision_at TIMESTAMPTZ NOT NULL,
  created_at TIMESTAMPTZ NOT NULL, version VARCHAR NOT NULL, configuration_hash VARCHAR NOT NULL,
  synthetic_fixture BOOLEAN NOT NULL, report_sha256 VARCHAR NOT NULL, report_json VARCHAR NOT NULL);
CREATE TABLE IF NOT EXISTS alert_events(
  event_id VARCHAR PRIMARY KEY, sent_at TIMESTAMPTZ NOT NULL, kind VARCHAR NOT NULL CHECK(kind IN ('baseline','change','summary')),
  qualified_symbol VARCHAR, decision VARCHAR, thesis VARCHAR, message VARCHAR NOT NULL, delivered BOOLEAN NOT NULL);
CREATE TABLE IF NOT EXISTS benchmark_prices(
  qualified_symbol VARCHAR NOT NULL, trading_date DATE NOT NULL, close DOUBLE NOT NULL, adjusted_close DOUBLE NOT NULL,
  retrieved_at TIMESTAMPTZ NOT NULL, source VARCHAR NOT NULL, PRIMARY KEY (qualified_symbol, trading_date));
CREATE TABLE IF NOT EXISTS monthly_decision_records(
  record_id VARCHAR PRIMARY KEY, month VARCHAR NOT NULL UNIQUE, decision_at TIMESTAMPTZ NOT NULL,
  created_at TIMESTAMPTZ NOT NULL, record_sha256 VARCHAR NOT NULL, record_json VARCHAR NOT NULL);
CREATE TABLE IF NOT EXISTS thesis_check_sets(
  set_id VARCHAR PRIMARY KEY, security_id VARCHAR NOT NULL, checks_json VARCHAR NOT NULL,
  recorded_at TIMESTAMPTZ NOT NULL);
CREATE TABLE IF NOT EXISTS portfolio_transactions(
  transaction_id VARCHAR PRIMARY KEY, kind VARCHAR NOT NULL CHECK(kind IN ('buy', 'sell', 'void')),
  qualified_symbol VARCHAR, company_name VARCHAR, shares DOUBLE, price DOUBLE, fees DOUBLE,
  currency VARCHAR, traded_on DATE, note VARCHAR, voids_transaction_id VARCHAR,
  recorded_at TIMESTAMPTZ NOT NULL);
ALTER TABLE portfolio_transactions ADD COLUMN IF NOT EXISTS account_amount DOUBLE;
CREATE TABLE IF NOT EXISTS cash_movements(
  movement_id VARCHAR PRIMARY KEY, kind VARCHAR NOT NULL CHECK(kind IN ('deposit', 'withdrawal', 'void')),
  amount DOUBLE, moved_on DATE, note VARCHAR, voids_movement_id VARCHAR, recorded_at TIMESTAMPTZ NOT NULL);
CREATE TABLE IF NOT EXISTS portfolio_settings(
  setting_id VARCHAR PRIMARY KEY, monthly_contribution DOUBLE NOT NULL, max_holdings INTEGER NOT NULL,
  recorded_at TIMESTAMPTZ NOT NULL);
"""


class StoreError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _now():
    return datetime.now(timezone.utc)


def _utc(value):
    return value.astimezone(timezone.utc).isoformat() if isinstance(value, datetime) else value


def symbol(value):
    """Normalise a typed ticker; a bare US ticker gets the .US suffix."""
    value = str(value or '').strip().upper()
    if value and not re.search(r'\.[A-Z]{2,6}$', value): value += '.US'
    if not SYMBOL.match(value): raise StoreError('PROTOTYPE_INVALID_SYMBOL')
    return value


def _amount(value, *, allow_zero=False):
    try: value = float(value)
    except (TypeError, ValueError): raise StoreError('PROTOTYPE_INVALID_TRADE') from None
    if not math.isfinite(value) or value < 0 or (value == 0 and not allow_zero) or value > MAX_TRADE_VALUE:
        raise StoreError('PROTOTYPE_INVALID_TRADE')
    return value


def _security_id(value):
    value = str(value or '').strip()
    if not 1 <= len(value) <= MAX_ID_CHARS: raise StoreError('PROTOTYPE_INVALID_SECURITY_ID')
    return value


class PrototypeStore:
    def __init__(self, path, *, protected_paths=()):
        self.path = Path(path)
        resolved = self.path.expanduser().resolve()
        if any(resolved == Path(p).expanduser().resolve() for p in protected_paths):
            raise StoreError('PROTOTYPE_STORE_PATH_NOT_SEPARATE')

    def _connect(self, write):
        if not write and not self.path.is_file(): return None
        if write:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        db = duckdb.connect(str(self.path), read_only=not write, config={'enable_external_access': False})
        if write:
            db.execute(SCHEMA)
            if not db.execute('SELECT count(*) FROM prototype_schema').fetchone()[0]:
                db.execute('INSERT INTO prototype_schema VALUES (?, ?)', [SCHEMA_VERSION, _now()])
        return db

    def _rows(self, sql, args=()):
        db = self._connect(False)
        if db is None: return []
        with db:
            cursor = db.execute(sql, list(args))
            names = [d[0] for d in cursor.description]
            return [{k: _utc(v) for k, v in zip(names, row)} for row in cursor.fetchall()]

    # Watchlist and notes -------------------------------------------------
    def watch(self, security_id, action, *, qualified_symbol=None, company_name=None):
        if action not in ('add', 'remove'): raise StoreError('PROTOTYPE_INVALID_WATCHLIST_ACTION')
        sid = _security_id(security_id)
        with self._connect(True) as db:
            db.execute('INSERT INTO watchlist_events VALUES (?, ?, ?, ?, ?, ?)',
                [uuid.uuid4().hex, sid, qualified_symbol, company_name, action, _now()])
        return self.watchlist()

    def watchlist(self):
        """Current members: companies whose latest event is 'add'."""
        return self._rows("""
            SELECT security_id, qualified_symbol, company_name, recorded_at AS added_at FROM (
              SELECT *, row_number() OVER (PARTITION BY security_id ORDER BY recorded_at DESC, event_id DESC) AS n
              FROM watchlist_events) WHERE n = 1 AND action = 'add' ORDER BY recorded_at DESC""")

    def add_note(self, security_id, body):
        sid = _security_id(security_id)
        body = str(body or '').strip()
        if not body or len(body) > MAX_NOTE_CHARS: raise StoreError('PROTOTYPE_INVALID_NOTE')
        with self._connect(True) as db:
            db.execute('INSERT INTO research_notes VALUES (?, ?, ?, ?)', [uuid.uuid4().hex, sid, body, _now()])
        return self.notes(sid)

    def notes(self, security_id=None):
        if security_id is None:
            return self._rows('SELECT * FROM research_notes ORDER BY recorded_at DESC')
        return self._rows('SELECT * FROM research_notes WHERE security_id = ? ORDER BY recorded_at DESC',
                          [_security_id(security_id)])

    # Theses ----------------------------------------------------------------
    def add_thesis(self, security_id, status, sections):
        """Record a new thesis version; earlier versions are kept unchanged."""
        sid = _security_id(security_id)
        if status not in THESIS_STATUSES: raise StoreError('PROTOTYPE_INVALID_THESIS_STATUS')
        if set(sections) - set(THESIS_SECTIONS): raise StoreError('PROTOTYPE_INVALID_THESIS_SECTION')
        values = [str(sections.get(k) or '').strip() or None for k in THESIS_SECTIONS]
        if not any(values) or any(v and len(v) > MAX_NOTE_CHARS for v in values):
            raise StoreError('PROTOTYPE_INVALID_THESIS')
        with self._connect(True) as db:
            db.execute(f'INSERT INTO research_theses (thesis_id, security_id, status, {", ".join(THESIS_SECTIONS)}, recorded_at) '
                       f'VALUES ({", ".join("?" for _ in range(len(THESIS_SECTIONS) + 4))})',
                       [uuid.uuid4().hex, sid, status, *values, _now()])
        return self.theses(sid)

    def theses(self, security_id=None):
        """Thesis versions, newest first (all companies when no ID is given)."""
        if not self.path.is_file(): return []
        with duckdb.connect(str(self.path), read_only=True) as db:
            if not db.execute("SELECT count(*) FROM information_schema.tables WHERE table_name='research_theses'").fetchone()[0]:
                return []  # a store created before theses existed
        if security_id is None:
            return self._rows('SELECT * FROM research_theses ORDER BY recorded_at DESC, thesis_id DESC')
        return self._rows('SELECT * FROM research_theses WHERE security_id = ? ORDER BY recorded_at DESC, thesis_id DESC',
                          [_security_id(security_id)])

    # Thesis checks -----------------------------------------------------------
    def _has_table(self, name):
        if not self.path.is_file(): return False
        with duckdb.connect(str(self.path), read_only=True) as db:
            return bool(db.execute("SELECT count(*) FROM information_schema.tables WHERE table_name = ?", [name]).fetchone()[0])

    def add_check_set(self, security_id, checks):
        """Record the full set of conditions as a new version (an empty set clears them)."""
        from .checks import CheckError, validate
        sid = _security_id(security_id)
        try: checks = validate(checks)
        except CheckError as exc: raise StoreError(exc.code) from None
        with self._connect(True) as db:
            db.execute('INSERT INTO thesis_check_sets VALUES (?, ?, ?, ?)',
                       [uuid.uuid4().hex, sid, json.dumps(checks, sort_keys=True), _now()])
        return self.check_sets(sid)

    def check_sets(self, security_id=None):
        """Check-set versions, newest first, with the checks decoded."""
        if not self._has_table('thesis_check_sets'): return []
        where, args = ('WHERE security_id = ?', [_security_id(security_id)]) if security_id is not None else ('', [])
        rows = self._rows(f'SELECT * FROM thesis_check_sets {where} ORDER BY recorded_at DESC, set_id DESC', args)
        for row in rows: row['checks'] = json.loads(row.pop('checks_json'))
        return rows

    def current_checks(self):
        """Latest check set per company."""
        latest = {}
        for row in self.check_sets(): latest.setdefault(row['security_id'], row['checks'])
        return latest

    # Trades ------------------------------------------------------------------
    def _has_trades_table(self):
        if not self.path.is_file(): return False
        with duckdb.connect(str(self.path), read_only=True) as db:
            return bool(db.execute("SELECT count(*) FROM information_schema.tables WHERE table_name='portfolio_transactions'").fetchone()[0])

    def transactions(self):
        """Every buy and sell, newest trade first, each marked if later voided."""
        if not self._has_trades_table(): return []
        # A store written before cash tracking has no pounds column until its next write.
        account = 't.account_amount' if self._has_column('portfolio_transactions', 'account_amount') else 'NULL'
        return self._rows(f"""
            SELECT t.transaction_id, t.kind, t.qualified_symbol, t.company_name, t.shares, t.price, t.fees,
                   t.currency, CAST(t.traded_on AS VARCHAR) AS traded_on, t.note, t.recorded_at, {account} AS account_amount,
                   v.recorded_at AS voided_at, v.note AS void_reason
            FROM portfolio_transactions t
            LEFT JOIN portfolio_transactions v ON v.kind = 'void' AND v.voids_transaction_id = t.transaction_id
            WHERE t.kind IN ('buy', 'sell') ORDER BY t.traded_on DESC, t.recorded_at DESC, t.transaction_id DESC""")

    def trades(self):
        """Buys and sells that have not been voided."""
        return [t for t in self.transactions() if t['voided_at'] is None]

    @staticmethod
    def _check(trades):
        from .portfolio import positions_from
        from .service import PrototypeError
        try: positions_from(trades)
        except PrototypeError as exc: raise StoreError(exc.code) from None

    def record_trade(self, kind, qualified_symbol, shares, price, traded_on, *, fees=0, currency='USD',
                     company_name=None, note=None, account_amount=None, today=None):
        """Record one buy or sell. A sell may never exceed the shares held on its date.

        `account_amount` is the pounds that left (buy) or reached (sell) the cash
        pool, fees and conversion included. A pound trade defaults to its own
        total; a trade in another currency without it is kept out of the cash pool."""
        if kind not in TRADE_KINDS: raise StoreError('PROTOTYPE_INVALID_TRADE_KIND')
        sym = symbol(qualified_symbol)
        shares, price, fees = _amount(shares), _amount(price), _amount(fees, allow_zero=True)
        if shares * price > MAX_TRADE_VALUE: raise StoreError('PROTOTYPE_INVALID_TRADE')
        if currency not in CURRENCIES: raise StoreError('PROTOTYPE_INVALID_CURRENCY')
        try: day = traded_on if isinstance(traded_on, date) else date.fromisoformat(str(traded_on))
        except ValueError: raise StoreError('PROTOTYPE_INVALID_TRADE_DATE') from None
        if not EARLIEST_TRADE <= day <= (today or _now().date()): raise StoreError('PROTOTYPE_INVALID_TRADE_DATE')
        name = str(company_name or '').strip()[:256] or None
        note = str(note or '').strip() or None
        if note and len(note) > MAX_NOTE_CHARS: raise StoreError('PROTOTYPE_INVALID_NOTE')
        if account_amount is not None: account_amount = _amount(account_amount)
        elif currency == ACCOUNT_CURRENCY: account_amount = shares * price + fees if kind == 'buy' else max(0.0, shares * price - fees)
        now = _now()
        row = dict(transaction_id=uuid.uuid4().hex, kind=kind, qualified_symbol=sym, company_name=name, shares=shares,
                   price=price, fees=fees, currency=currency, traded_on=day.isoformat(), note=note, recorded_at=now.isoformat(),
                   account_amount=account_amount)
        # Older trades can be entered late, so the whole history is re-checked for an oversell.
        self._check(self.trades() + [row])
        with self._connect(True) as db:
            db.execute("""INSERT INTO portfolio_transactions (transaction_id, kind, qualified_symbol, company_name, shares, price,
                fees, currency, traded_on, note, recorded_at, account_amount) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                [row['transaction_id'], kind, sym, name, shares, price, fees, currency, day, note, now, account_amount])
        return row['transaction_id']

    def void_trade(self, transaction_id, *, reason=None):
        """Cancel a mistaken trade by recording a void row; the original stays visible."""
        target = next((t for t in self.transactions() if t['transaction_id'] == str(transaction_id)[:64]), None)
        if target is None: raise StoreError('PROTOTYPE_UNKNOWN_TRANSACTION')
        if target['voided_at'] is not None: raise StoreError('PROTOTYPE_TRANSACTION_ALREADY_VOIDED')
        reason = str(reason or '').strip() or None
        if reason and len(reason) > MAX_NOTE_CHARS: raise StoreError('PROTOTYPE_INVALID_NOTE')
        self._check([t for t in self.trades() if t['transaction_id'] != target['transaction_id']])
        with self._connect(True) as db:
            db.execute("INSERT INTO portfolio_transactions (transaction_id, kind, note, voids_transaction_id, recorded_at) VALUES (?, 'void', ?, ?, ?)",
                [uuid.uuid4().hex, reason, target['transaction_id'], _now()])

    # Cash pool ---------------------------------------------------------------
    def _has_column(self, table, column):
        if not self.path.is_file(): return False
        with duckdb.connect(str(self.path), read_only=True) as db:
            return bool(db.execute("SELECT count(*) FROM information_schema.columns WHERE table_name = ? AND column_name = ?",
                                   [table, column]).fetchone()[0])

    def cash_movements(self):
        """Every deposit and withdrawal (pounds), newest first, each marked if later voided."""
        if not self._has_table('cash_movements'): return []
        return self._rows("""
            SELECT m.movement_id, m.kind, m.amount, CAST(m.moved_on AS VARCHAR) AS moved_on, m.note, m.recorded_at,
                   v.recorded_at AS voided_at, v.note AS void_reason
            FROM cash_movements m
            LEFT JOIN cash_movements v ON v.kind = 'void' AND v.voids_movement_id = m.movement_id
            WHERE m.kind IN ('deposit', 'withdrawal') ORDER BY m.moved_on DESC, m.recorded_at DESC, m.movement_id DESC""")

    def record_cash(self, kind, amount, moved_on, *, note=None, today=None):
        """Record money confirmed as arrived in (deposit) or taken out of (withdrawal) the account."""
        if kind not in CASH_KINDS: raise StoreError('PROTOTYPE_INVALID_CASH_KIND')
        amount = _amount(amount)
        try: day = moved_on if isinstance(moved_on, date) else date.fromisoformat(str(moved_on))
        except ValueError: raise StoreError('PROTOTYPE_INVALID_TRADE_DATE') from None
        if not EARLIEST_TRADE <= day <= (today or _now().date()): raise StoreError('PROTOTYPE_INVALID_TRADE_DATE')
        note = str(note or '').strip() or None
        if note and len(note) > MAX_NOTE_CHARS: raise StoreError('PROTOTYPE_INVALID_NOTE')
        movement_id = uuid.uuid4().hex
        with self._connect(True) as db:
            db.execute('INSERT INTO cash_movements VALUES (?, ?, ?, ?, ?, NULL, ?)', [movement_id, kind, amount, day, note, _now()])
        return movement_id

    def void_cash(self, movement_id, *, reason=None):
        """Cancel a mistaken deposit or withdrawal; the original stays visible."""
        target = next((m for m in self.cash_movements() if m['movement_id'] == str(movement_id)[:64]), None)
        if target is None: raise StoreError('PROTOTYPE_UNKNOWN_CASH_MOVEMENT')
        if target['voided_at'] is not None: raise StoreError('PROTOTYPE_TRANSACTION_ALREADY_VOIDED')
        reason = str(reason or '').strip() or None
        if reason and len(reason) > MAX_NOTE_CHARS: raise StoreError('PROTOTYPE_INVALID_NOTE')
        with self._connect(True) as db:
            db.execute("INSERT INTO cash_movements (movement_id, kind, note, voids_movement_id, recorded_at) VALUES (?, 'void', ?, ?, ?)",
                       [uuid.uuid4().hex, reason, target['movement_id'], _now()])

    # Portfolio settings ------------------------------------------------------
    def settings(self):
        """The latest saved settings, or the defaults (marked `is_default`) before any are saved."""
        rows = self._rows('SELECT * FROM portfolio_settings ORDER BY recorded_at DESC, setting_id DESC LIMIT 1') \
            if self._has_table('portfolio_settings') else []
        if not rows: return DEFAULT_SETTINGS | {'currency': ACCOUNT_CURRENCY, 'is_default': True, 'recorded_at': None}
        row = rows[0]
        return {'monthly_contribution': row['monthly_contribution'], 'max_holdings': row['max_holdings'],
                'currency': ACCOUNT_CURRENCY, 'is_default': False, 'recorded_at': row['recorded_at']}

    def save_settings(self, *, monthly_contribution, max_holdings):
        """Save new settings as a new row; earlier values stay in the history."""
        try: contribution, limit = float(monthly_contribution), float(max_holdings)
        except (TypeError, ValueError): raise StoreError('PROTOTYPE_INVALID_SETTINGS') from None
        if not (math.isfinite(contribution) and 0 <= contribution <= MAX_CONTRIBUTION) \
                or not (limit.is_integer() and 1 <= limit <= MAX_HOLDINGS_LIMIT):
            raise StoreError('PROTOTYPE_INVALID_SETTINGS')
        with self._connect(True) as db:
            db.execute('INSERT INTO portfolio_settings VALUES (?, ?, ?, ?)', [uuid.uuid4().hex, contribution, int(limit), _now()])
        return self.settings()

    # Alerts ------------------------------------------------------------------
    def record_alert(self, kind, message, *, delivered, qualified_symbol=None, decision=None, thesis=None, now=None):
        with self._connect(True) as db:
            db.execute('INSERT INTO alert_events VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                       [uuid.uuid4().hex, now or _now(), kind, qualified_symbol, decision, thesis, str(message)[:8000], bool(delivered)])

    def alert_states(self):
        """{symbol: (decision, thesis)} last delivered for each holding."""
        if not self._has_table('alert_events'): return {}
        out = {}
        for r in self._rows("""SELECT qualified_symbol, decision, thesis FROM alert_events
                WHERE delivered AND kind IN ('baseline', 'change') AND qualified_symbol IS NOT NULL
                ORDER BY sent_at, event_id"""):
            out[r['qualified_symbol']] = (r['decision'], r['thesis'])
        return out

    def last_alert_at(self, kind):
        if not self._has_table('alert_events'): return None
        rows = self._rows('SELECT max(sent_at) AS at FROM alert_events WHERE delivered AND kind = ?', [kind])
        return rows[0]['at'] if rows and rows[0]['at'] else None

    def alert_events(self, limit=50):
        if not self._has_table('alert_events'): return []
        return self._rows('SELECT * FROM alert_events ORDER BY sent_at DESC, event_id DESC LIMIT ?', [int(limit)])

    # Benchmark fund prices ---------------------------------------------------
    def store_benchmark_prices(self, rows):
        """Insert or replace (symbol, date, close, adjusted_close, retrieved_at, source) rows."""
        if not rows: return
        with self._connect(True) as db:
            db.executemany('INSERT OR REPLACE INTO benchmark_prices VALUES (?, ?, ?, ?, ?, ?)', rows)

    def benchmark_latest_dates(self):
        if not self._has_table('benchmark_prices'): return {}
        return {r['qualified_symbol']: date.fromisoformat(str(r['latest'])[:10])
                for r in self._rows('SELECT qualified_symbol, max(trading_date) AS latest FROM benchmark_prices GROUP BY 1')}

    def benchmark_prices(self, now=None):
        """{symbol: {date: adjusted_close}} for prices retrieved by `now`."""
        if not self._has_table('benchmark_prices'): return {}
        out = {}
        for r in self._rows('SELECT qualified_symbol, trading_date, adjusted_close FROM benchmark_prices WHERE retrieved_at <= ?', [now or _now()]):
            out.setdefault(r['qualified_symbol'], {})[date.fromisoformat(str(r['trading_date'])[:10])] = float(r['adjusted_close'])
        return out

    # Monthly decision records ---------------------------------------------
    def create_decision_record(self, record, *, now=None):
        """Freeze one month's picks and decisions for later scoring. Same rules as
        snapshots: a recent cutoff only (no backfill) and one record per month."""
        now = now or _now()
        decision = datetime.fromisoformat(str(record['decision_at']).replace('Z', '+00:00')).astimezone(timezone.utc)
        if decision > now: raise StoreError('PROTOTYPE_FUTURE_CUTOFF')
        if now - decision > timedelta(days=SNAPSHOT_MAX_LAG_DAYS): raise StoreError('PROTOTYPE_RECORD_BACKFILL_REFUSED')
        month = decision.strftime('%Y-%m')
        encoded = json.dumps(record, sort_keys=True, separators=(',', ':'), allow_nan=False)
        digest = hashlib.sha256(encoded.encode('utf-8')).hexdigest()
        with self._connect(True) as db:
            if db.execute('SELECT count(*) FROM monthly_decision_records WHERE month = ?', [month]).fetchone()[0]:
                raise StoreError('PROTOTYPE_RECORD_MONTH_EXISTS')
            record_id = uuid.uuid4().hex
            db.execute('INSERT INTO monthly_decision_records VALUES (?, ?, ?, ?, ?, ?)', [record_id, month, decision, now, digest, encoded])
        return next(r for r in self.decision_records() if r['record_id'] == record_id)

    def decision_records(self):
        """Every record, oldest first, verified against its SHA-256 on each read."""
        if not self._has_table('monthly_decision_records'): return []
        out = []
        for row in self._rows('SELECT * FROM monthly_decision_records ORDER BY decision_at'):
            encoded = row.pop('record_json')
            if hashlib.sha256(encoded.encode('utf-8')).hexdigest() != row['record_sha256']:
                raise StoreError('PROTOTYPE_RECORD_INTEGRITY_FAILED')
            out.append(row | json.loads(encoded) | {'decision_at': row['decision_at'], 'integrity_verified': True})
        return out

    # Snapshots -------------------------------------------------------------
    def create_snapshot(self, report, *, now=None):
        """Freeze one assessed report as the month's snapshot. The cutoff must be
        recent (no backfill) and each month can be recorded once."""
        now = now or _now()
        decision = datetime.fromisoformat(str(report['decision_at']).replace('Z', '+00:00')).astimezone(timezone.utc)
        if decision > now: raise StoreError('PROTOTYPE_FUTURE_CUTOFF')
        if now - decision > timedelta(days=SNAPSHOT_MAX_LAG_DAYS): raise StoreError('PROTOTYPE_SNAPSHOT_BACKFILL_REFUSED')
        month = decision.strftime('%Y-%m')
        encoded = json.dumps(report, sort_keys=True, separators=(',', ':'), allow_nan=False)
        digest = hashlib.sha256(encoded.encode('utf-8')).hexdigest()
        with self._connect(True) as db:
            if db.execute('SELECT count(*) FROM monthly_snapshots WHERE month = ?', [month]).fetchone()[0]:
                raise StoreError('PROTOTYPE_SNAPSHOT_MONTH_EXISTS')
            snapshot_id = uuid.uuid4().hex
            db.execute('INSERT INTO monthly_snapshots VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
                [snapshot_id, month, decision, now, report['version'], report['configuration_hash'],
                 bool(report.get('synthetic_fixture')), digest, encoded])
        return self.snapshot(snapshot_id)

    def snapshots(self):
        return self._rows("""SELECT snapshot_id, month, decision_at, created_at, version, configuration_hash,
            synthetic_fixture, report_sha256 FROM monthly_snapshots ORDER BY decision_at DESC""")

    def snapshot(self, snapshot_id):
        rows = self._rows('SELECT * FROM monthly_snapshots WHERE snapshot_id = ?', [str(snapshot_id)[:64]])
        if not rows: raise StoreError('PROTOTYPE_UNKNOWN_SNAPSHOT')
        row = rows[0]
        encoded = row.pop('report_json')
        if hashlib.sha256(encoded.encode('utf-8')).hexdigest() != row['report_sha256']:
            raise StoreError('PROTOTYPE_SNAPSHOT_INTEGRITY_FAILED')
        report = json.loads(encoded)
        by_id = {c['security_id']: c for c in report['companies']}
        row.update(integrity_verified=True, notice=report['notice'], blockers=report['blockers'],
            eligible_count=report['eligible_count'], proposed_membership=report['proposed_membership'],
            results=report['results'], members=[{k: by_id[s].get(k) for k in ('security_id', 'qualified_symbol', 'company_name', 'calculation')}
                                                for s in report['proposed_membership']])
        return row
