"""Offline operator-shaped persisted schemas; no operator access."""
import json
import subprocess
import sys

import duckdb
import pytest

from app.track_b_history_diagnostic import diagnose
import app.track_b_history as h
from test_track_b_history import fixture, DECISION


def check(paths):
    before = [p.read_bytes() for p in paths]
    result = diagnose(research_db=paths[0], production_db=paths[1], decision_at=DECISION)
    assert [p.read_bytes() for p in paths] == before
    assert len(result['events']) <= 52
    text = json.dumps(result)
    assert len(text.encode()) < 16384
    for secret in ('POISON', 'DO NOT READ', str(paths[0]), 'Traceback', 'sha256'):
        assert secret not in text
    assert all(set(e) == {'stage', 'reason_code', 'counts'} for e in result['events'])
    assert all(type(v) is int for e in result['events'] for v in e['counts'].values())
    return result['events']


def test_success_partial_and_incompatible_schema(tmp_path):
    paths = fixture(tmp_path)
    with duckdb.connect(str(paths[1])) as db:
        db.execute('CREATE TABLE canonical_factor_evidence(unknown VARCHAR)')
        db.execute("INSERT INTO canonical_factor_evidence VALUES ('POISON')")
        db.execute("CREATE VIEW global_corporate_actions AS SELECT error('POISON') AS value")
    events = check(paths)
    assert any(e['reason_code'] == 'SCHEMA_PARTIAL' for e in events)
    assert any(e['stage'] == 'production.canonical_factor_evidence' and
               e['reason_code'] == 'SCHEMA_INCOMPATIBLE' for e in events)
    assert 'row_count' not in next(e['counts'] for e in events
                                 if e['stage'] == 'production.global_corporate_actions')
    assert any(e['reason_code'] == 'INVENTORY_COMPLETED' for e in events)
    assert check(paths) == events


def test_real_table_cap_boundary(tmp_path):
    paths = fixture(tmp_path)
    with duckdb.connect(str(paths[1])) as db:
        # Persisted legacy price metadata with poison values; real default cap.
        db.execute('CREATE TABLE price_bars AS SELECT i::VARCHAR ticker, DATE \'2026-10-02\' trading_date, \'POISON\' AS "close" FROM range(500001) r(i)')
    events = check(paths)
    assert any(e['stage'] == 'production.price_bars' and
               e['reason_code'] == 'TABLE_ROW_WORK_LIMIT' and
               e['counts']['row_count'] == 500001 for e in events)
    assert events[-1]['reason_code'] == 'FINGERPRINT_UNCHANGED'


def test_chain_limit_is_distinct(tmp_path, monkeypatch):
    paths = fixture(tmp_path)
    monkeypatch.setattr(h, 'MAX_CHAIN_WORK', 1)
    events = check(paths)
    assert any(e['reason_code'] == 'PERIOD_CHAIN_WORK_LIMIT' for e in events)
    assert not any(e['reason_code'] == 'TABLE_ROW_WORK_LIMIT' for e in events)


def test_query_and_parsing_failures_distinct(tmp_path, monkeypatch):
    paths = fixture(tmp_path)
    with monkeypatch.context() as m:
        m.setattr(h, '_read', lambda *a: (_ for _ in ()).throw(RuntimeError('POISON')))
        assert any(e['reason_code'] == 'METADATA_QUERY_OR_DECODE_FAILED' for e in check(paths))
    with monkeypatch.context() as m:
        m.setattr(h, '_identity', lambda *a: (_ for _ in ()).throw(TypeError('POISON')))
        assert any(e['stage'] == 'research.identity' and
                   e['reason_code'] == 'METADATA_PARSING_FAILED' for e in check(paths))


def test_persisted_wrong_metadata_type_is_redacted(tmp_path):
    paths = fixture(tmp_path)
    with duckdb.connect(str(paths[0])) as db:
        db.execute("ALTER TABLE canonical_factor_evidence ALTER canonical_field TYPE STRUCT(secret VARCHAR) USING struct_pack(secret := 'POISON')")
    events = check(paths)
    assert any(e['stage'] == 'research.accounting' and
               e['reason_code'] == 'METADATA_PARSING_FAILED' for e in events)
    assert any(e['reason_code'] == 'INVENTORY_INTERNAL_FAILED' for e in events)


@pytest.mark.parametrize('failure', ['read', 'changed'])
def test_fingerprints_both_attempted_on_failure(tmp_path, monkeypatch, failure):
    paths = fixture(tmp_path)
    original = h.fingerprint
    calls = []
    def fingerprint(path):
        calls.append(path)
        if failure == 'read' and path == paths[0]: raise OSError('POISON')
        result = original(path)
        if failure == 'changed' and len(calls) == 3: result['sha256'] = 'POISON'
        return result
    monkeypatch.setattr(h, 'fingerprint', fingerprint)
    events = check(paths)
    assert calls == [paths[0], paths[1], paths[0], paths[1]]
    assert any(e['reason_code'] == ('FINGERPRINT_READ_FAILED' if failure == 'read'
                                  else 'FINGERPRINT_CHANGED') for e in events)
    assert h.fingerprint is fingerprint  # instrumentation restored


def test_count_mismatch_is_distinct(tmp_path, monkeypatch):
    paths = fixture(tmp_path)
    monkeypatch.setattr(h, '_read', lambda *a: (_ for _ in ()).throw(h.InventoryError('metadata count mismatch')))
    assert any(e['reason_code'] == 'METADATA_COUNT_MISMATCH' for e in check(paths))


def test_diagnostic_cli_and_public_error_remain_separate(tmp_path):
    paths = fixture(tmp_path)
    with duckdb.connect(str(paths[0])) as db:
        db.execute("ALTER TABLE canonical_factor_evidence ALTER canonical_field TYPE STRUCT(secret VARCHAR) USING struct_pack(secret := 'POISON')")
    options = ['--research-db', str(paths[0]), '--production-db', str(paths[1]),
               '--decision-at', DECISION.isoformat()]
    diagnostic = subprocess.run([sys.executable, '-m', 'app.track_b_history_diagnostic', *options], capture_output=True, text=True)
    assert diagnostic.returncode == 1 and not diagnostic.stderr
    assert 'POISON' not in diagnostic.stdout and str(tmp_path) not in diagnostic.stdout
    assert any(e['reason_code'] == 'METADATA_PARSING_FAILED' for e in json.loads(diagnostic.stdout)['events'])
    public = subprocess.run([sys.executable, '-m', 'app.investment_research_cli',
                             'track-b-historical-evidence-inventory', *options], capture_output=True, text=True)
    assert public.returncode == 1 and not public.stdout
    assert json.loads(public.stderr) == {'status': 'failed', 'error': {
        'code': 'INVESTMENT_RESEARCH_INTERNAL_ERROR',
        'message': 'investment research request failed; details redacted'}}
