"""Separate prototype store, immutable snapshots and descriptive tracking (offline)."""
from datetime import datetime, timedelta, timezone

import duckdb
from fastapi.testclient import TestClient
import pandas as pd
import pytest

from app.model_readiness import fingerprint
from app.prototype import service, store as store_module
from app.prototype.fixture import DECISION, create_fixture
from app.prototype.store import PrototypeStore, StoreError
from app.prototype.tracking import track


@pytest.fixture
def paths(tmp_path):
    research, production = create_fixture(tmp_path / 'new-synthetic-fixture')
    return research, production, tmp_path / 'prototype' / 'signallens-prototype.duckdb'


def report(paths):
    return service.assess(research_db=paths[0], production_db=paths[1], decision_at=DECISION)


def test_store_must_be_separate_from_protected_databases(paths):
    with pytest.raises(StoreError, match='PROTOTYPE_STORE_PATH_NOT_SEPARATE'):
        PrototypeStore(paths[0], protected_paths=paths[:2])


def test_watchlist_and_notes_are_append_only_events(paths):
    store = PrototypeStore(paths[2], protected_paths=paths[:2])
    assert store.watchlist() == [] and store.notes() == []  # reads never create the file
    assert not paths[2].exists()
    store.watch('synthetic-01', 'add', qualified_symbol='SYN01.US', company_name='Synthetic company 01')
    store.watch('synthetic-02', 'add')
    store.watch('synthetic-01', 'remove')
    assert [i['security_id'] for i in store.watchlist()] == ['synthetic-02']
    store.watch('synthetic-01', 'add')
    assert {i['security_id'] for i in store.watchlist()} == {'synthetic-01', 'synthetic-02'}
    store.add_note('synthetic-01', 'First thought.')
    store.add_note('synthetic-01', 'Correction: second thought.')
    assert [n['body'] for n in store.notes('synthetic-01')] == ['Correction: second thought.', 'First thought.']
    with duckdb.connect(str(paths[2]), read_only=True) as db:
        assert db.execute('SELECT count(*) FROM watchlist_events').fetchone()[0] == 4
    for bad in ('', ' ', 'x' * 4001):
        with pytest.raises(StoreError, match='PROTOTYPE_INVALID_NOTE'): store.add_note('synthetic-01', bad)
    with pytest.raises(StoreError, match='PROTOTYPE_INVALID_SECURITY_ID'): store.watch('', 'add')


def test_snapshot_is_frozen_once_per_month_and_never_backfilled(paths):
    before = [fingerprint(p) for p in paths[:2]]
    r = report(paths)
    store = PrototypeStore(paths[2], protected_paths=paths[:2])
    with pytest.raises(StoreError, match='PROTOTYPE_SNAPSHOT_BACKFILL_REFUSED'):
        store.create_snapshot(r, now=DECISION + timedelta(days=15))
    with pytest.raises(StoreError, match='PROTOTYPE_FUTURE_CUTOFF'):
        store.create_snapshot(r, now=DECISION - timedelta(hours=1))
    s = store.create_snapshot(r, now=DECISION + timedelta(days=2))
    assert s['month'] == '2026-10' and s['integrity_verified'] and s['synthetic_fixture']
    assert s['proposed_membership'] == r['proposed_membership'] and s['results'] == r['results']
    assert len(s['members']) == 15 and all(m['calculation'] for m in s['members'])
    with pytest.raises(StoreError, match='PROTOTYPE_SNAPSHOT_MONTH_EXISTS'):
        store.create_snapshot(r, now=DECISION + timedelta(days=3))
    assert [x['snapshot_id'] for x in store.snapshots()] == [s['snapshot_id']]
    with duckdb.connect(str(paths[2])) as db:
        db.execute("UPDATE monthly_snapshots SET report_json = replace(report_json, 'synthetic-', 'tampered-')")
    with pytest.raises(StoreError, match='PROTOTYPE_SNAPSHOT_INTEGRITY_FAILED'): store.snapshot(s['snapshot_id'])
    assert [fingerprint(p) for p in paths[:2]] == before


def _add_sessions(path, count, skip_symbol=None, skip_index=None):
    """Append `count` later business-day sessions; prices rise 1% per session."""
    dates = list(pd.bdate_range(start='2026-10-01', periods=count).date)
    with duckdb.connect(str(path)) as db:
        last = dict(db.execute("SELECT qualified_symbol, adjusted_close FROM global_price_observations WHERE trading_date = DATE '2026-09-30'").fetchall())
        rows = [(s, d, 'US', 'USD', v, v + 1, v - 1, v, v, 1000, 'available', 'offline-synthetic', datetime(2026, 11, 30))
                for s, base in last.items() for j, d in enumerate(dates, 1) for v in [float(base) * (1 + 0.01 * j)]
                if not (s == skip_symbol and j == skip_index)]
        db.executemany('INSERT INTO global_price_observations VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)', rows)


def test_tracking_reports_exact_checkpoints_pending_and_missing_prices(paths):
    store = PrototypeStore(paths[2], protected_paths=paths[:2])
    s = store.snapshot(store.create_snapshot(report(paths), now=DECISION + timedelta(days=1))['snapshot_id'])
    first = track(s, research_db=paths[0], now=datetime(2026, 12, 1, tzinfo=timezone.utc))
    assert all(p['status'] == 'pending' and p['sessions_elapsed'] == 0 for c in first['companies'] for p in c['checkpoints'])
    assert first['validation_credit'] == 0 and first['databases_unchanged']
    missing = s['members'][0]['qualified_symbol']
    _add_sessions(paths[0], 21, skip_symbol=missing, skip_index=21)
    before = fingerprint(paths[0])
    t = track(s, research_db=paths[0], now=datetime(2026, 12, 1, tzinfo=timezone.utc))
    assert fingerprint(paths[0]) == before
    by = {c['qualified_symbol']: c for c in t['companies']}
    assert by[missing]['checkpoints'][0]['status'] == 'missing_price'
    other = next(c for sym, c in by.items() if sym != missing)
    p21 = other['checkpoints'][0]
    assert p21['status'] == 'available' and p21['session'] == '2026-10-29' and p21['return'] == pytest.approx(0.21)
    assert other['checkpoints'][1] == {'sessions': 63, 'status': 'pending', 'sessions_elapsed': 21}
    assert t['comparison'][0]['all_members'] == {'available': 14, 'of': 15, 'mean_return': pytest.approx(0.21)}
    # Sessions completing after `now` are not counted.
    early = track(s, research_db=paths[0], now=datetime(2026, 10, 29, 21, tzinfo=timezone.utc))
    assert early['companies'][1]['checkpoints'][0]['status'] == 'pending'


def test_api_writes_only_to_the_store_and_only_when_enabled(paths, monkeypatch):
    from app.config import Settings
    from app import main
    from app.prototype import api
    monkeypatch.setattr(store_module, '_now', lambda: DECISION + timedelta(days=1))
    headers = {'Authorization': 'Bearer test-token'}
    before = [fingerprint(p) for p in paths[:2]]
    for enabled in (False, True):
        settings = Settings(database_path=paths[1], research_database_path=paths[0], prototype_database_path=paths[2],
                            staging_mode=True, api_token='test-token', prototype_writes_enabled=enabled)
        monkeypatch.setattr(main, 'settings', settings); monkeypatch.setattr(api, 'get_settings', lambda: settings)
        api._CACHE.clear(); api._TRACKING.clear()
        with TestClient(main.app) as client:
            add = client.post('/api/v1/research/prototype/store/watchlist', headers=headers, json={'security_id': 'synthetic-03', 'action': 'add'})
            if not enabled:
                assert add.status_code == 409 and add.json()['detail']['code'] == 'staging_read_only'
                assert client.get('/api/v1/research/prototype/store/watchlist', headers=headers).json() == {'items': [], 'notes': []}
                continue
            assert add.status_code == 200 and add.json()['items'][0]['security_id'] == 'synthetic-03'
            assert client.post('/api/v1/research/prototype/store/notes', headers=headers, json={'security_id': 'synthetic-03', 'body': 'Why it moved.'}).status_code == 200
            notes = client.get('/api/v1/research/prototype/store/notes/synthetic-03', headers=headers).json()
            assert notes['watched'] and notes['notes'][0]['body'] == 'Why it moved.'
            created = client.post('/api/v1/research/prototype/store/snapshots', headers=headers, json={'decision_at': DECISION.isoformat()})
            assert created.status_code == 200, created.text
            sid = created.json()['snapshot_id']
            assert client.post('/api/v1/research/prototype/store/snapshots', headers=headers, json={'decision_at': DECISION.isoformat()}).json()['detail']['code'] == 'PROTOTYPE_SNAPSHOT_MONTH_EXISTS'
            view = client.get(f'/api/v1/research/prototype/store/snapshots/{sid}', headers=headers).json()
            assert view['snapshot']['integrity_verified'] and view['tracking']['validation_credit'] == 0
            assert client.get('/api/v1/research/prototype/store/snapshots/unknown', headers=headers).status_code == 404
            # The staging guard still refuses every other write.
            assert client.post('/api/v1/research/prototype/roster', headers=headers).status_code == 409
    assert [fingerprint(p) for p in paths[:2]] == before
