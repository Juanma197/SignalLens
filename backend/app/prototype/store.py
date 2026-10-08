"""Separate append-only prototype store: watchlist events, notes and snapshots.

Nothing here opens the research or production databases for writing. There is
no update or delete path: watchlist removal is a new event, a note correction is
a new note, and a snapshot is immutable once recorded (verified by SHA-256 on
every read). Snapshots are never backfilled.
"""
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
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
"""


class StoreError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _now():
    return datetime.now(timezone.utc)


def _utc(value):
    return value.astimezone(timezone.utc).isoformat() if isinstance(value, datetime) else value


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
