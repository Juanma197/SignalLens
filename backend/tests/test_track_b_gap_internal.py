"""Synthetic failures only; never opens operator files."""
import json
import duckdb
import pytest
from app import track_b_gaps as g, track_b_history as h
from app import track_b_gap_diagnostic as d
from test_track_b_gaps import fixture, D, run


def traced(paths):
    return d.diagnose(research_db=paths[0], production_db=paths[1], decision_at=D)


def check(result, reason):
    assert reason in {e['reason_code'] for e in result['events']}
    assert not result['diagnostic_completed'] and not result['operator_verification_successful']
    assert result['public_error_code']=='TRACK_B_GAP_DIAGNOSTIC_FAILED'
    encoded=json.dumps(result)
    assert 'PRIVATE' not in encoded and 'POISON' not in encoded and '.duckdb' not in encoded
    assert len(encoded.encode()) <= d.MAX_BYTES
    for event in result['events']:
        assert event['stage'] in d.STAGES and event['reason_code'] in d.REASONS
        assert all(k in d.COUNT_KEYS and type(v) is int and v>=0 for k,v in event['counts'].items())
    assert g._TRACE.get() is None


@pytest.mark.parametrize('bound,reason', [('MAX_ROWS','METADATA_ROW_LIMIT'),
    ('MAX_CELL_CHARS','METADATA_CELL_LIMIT'),('MAX_ROSTER','ROSTER_LIMIT'),
    ('MAX_PERIOD_WORK','PERIOD_STATE_LIMIT')])
def test_bounds(tmp_path,monkeypatch,bound,reason):
    paths=fixture(tmp_path)
    monkeypatch.setattr(g,bound,1)  # Offline fault injection; deployed limits unchanged.
    result=traced(paths); check(result,reason)
    assert next(e for e in result['events'] if e['reason_code']==reason)['counts']


def test_roster_schema(tmp_path):
    paths=fixture(tmp_path)
    with duckdb.connect(str(paths[0])) as db:
        db.execute('ALTER TABLE security_classification_evidence DROP COLUMN security_id')
    check(traced(paths),'ROSTER_SCHEMA_UNSUPPORTED')


@pytest.mark.parametrize('fault,reason', [('before','FINGERPRINT_BASELINE_UNAVAILABLE'),
    ('after','FINGERPRINT_AFTER_UNAVAILABLE'),('changed','FINGERPRINT_CHANGED')])
def test_fingerprints(tmp_path,monkeypatch,fault,reason):
    paths=fixture(tmp_path); original=h.fingerprint; calls=[]
    def fingerprint(path):
        calls.append(path)
        if len(calls)==(1 if fault=='before' else 3):
            if fault!='changed': raise OSError('PRIVATE')
            return dict(original(path),sha256='PRIVATE')
        return original(path)
    monkeypatch.setattr(h,'fingerprint',fingerprint)
    check(traced(paths),reason)
    assert calls==[paths[0],paths[1],paths[0],paths[1]]


def test_sql_memory_stage(tmp_path,monkeypatch):
    paths=fixture(tmp_path)
    def read(*args):
        g._mark('research.sec_facts.projection',row_count=12)
        raise duckdb.OutOfMemoryException('PRIVATE')
    monkeypatch.setattr(g,'_read',read)
    result=traced(paths); check(result,'SQL_MEMORY_LIMIT')
    event=next(e for e in result['events'] if e['reason_code']=='SQL_MEMORY_LIMIT')
    assert event['stage']=='research.sec_facts.projection' and event['counts']=={'row_count':12}


def test_count_mismatch():
    class Cursor:
        def execute(self,sql,args=None): self.sql=sql; return self
        def fetchone(self):
            if 'table_type' in self.sql: return ('BASE TABLE',)
            return (0 if ' WHERE ' in self.sql else 1,)
        def fetchall(self): return [('security_id',)]
        def fetchmany(self,n): return []
    trace=d.Trace(); token=g._TRACE.set(trace)
    try:
        with pytest.raises(g.GapDiagnosticError): g._read(Cursor(),'security_listings')
    finally: g._TRACE.reset(token)
    event=trace.events[-1]
    assert event['reason_code']=='METADATA_COUNT_MISMATCH'
    assert event['counts']['expected_row_count']==1 and event['counts']['decoded_row_count']==0


def test_success_unchanged_report(tmp_path):
    paths=fixture(tmp_path); before=run(paths); result=traced(paths)
    assert result['diagnostic_completed'] and not result['operator_verification_successful']
    assert result['public_error_code'] is None
    assert run(paths)==before and g._TRACE.get() is None
    assert len(result['events'])<=d.MAX_EVENTS


def test_multiple_failures(tmp_path,monkeypatch):
    paths=fixture(tmp_path); original=h.fingerprint; calls=[]
    def fp(path):
        calls.append(path)
        if len(calls)==3: raise OSError('PRIVATE')
        return original(path)
    monkeypatch.setattr(h,'fingerprint',fp); monkeypatch.setattr(g,'MAX_ROWS',1)
    result=traced(paths); check(result,'METADATA_ROW_LIMIT')
    assert 'FINGERPRINT_AFTER_UNAVAILABLE' in {e['reason_code'] for e in result['events']}


def test_redaction_and_bound(monkeypatch):
    def fake(**kwargs):
        for i in range(100):
            g._mark('PRIVATE',row_count=i,matched_count=True,roster_count=-1,private='PRIVATE')
            g._event('PRIVATE')
        raise RuntimeError('PRIVATE')
    monkeypatch.setattr(g,'diagnose',fake)
    result=d.diagnose()
    assert len(result['events'])==d.MAX_EVENTS
    assert result['events'][-1]['reason_code']=='EVENT_BOUND_EXCEEDED'
    assert 'PRIVATE' not in json.dumps(result)
    assert result['events'][0]['counts']=={'row_count':0}


def test_other_public_code(tmp_path,monkeypatch):
    paths=fixture(tmp_path); monkeypatch.setattr(g,'MAXIMUM_BYTES',1)
    result=traced(paths)
    assert result['public_error_code']=='INVESTMENT_RESEARCH_NOT_READY'
    assert 'REPORT_BYTE_LIMIT' in {e['reason_code'] for e in result['events']}


@pytest.mark.parametrize('mode', ['safe_failure','bad_json','bad_schema','too_large','launch_failure'])
def test_focused_stdin_runner_failure_cleanup(tmp_path,monkeypatch,capsys,mode):
    from pathlib import Path
    import subprocess
    import sys
    script=Path(__file__).resolve().parents[2]/'scripts/track-b-gaps-diagnose.ps1'
    source=script.read_text().split("$Diagnostic = @'\n",1)[1].split("\n'@",1)[0]
    assert '$Diagnostic | & $Python -X utf8 - ' in script.read_text()
    assert 'pytest' not in source.replace("'pytest_run'",'')
    paths=[tmp_path/'research',tmp_path/'production']; reports=tmp_path/'reports'
    for path in paths: path.write_bytes(b'offline synthetic fingerprint')
    monkeypatch.setattr(sys,'argv',['-',*(str(p) for p in paths),str(reports)])
    calls=[]
    def execute(args,stdout,stderr,check):
        calls.append(args)
        if mode=='launch_failure': raise OSError('PRIVATE')
        if 'app.investment_research_cli' in args:
            stderr.write(b'{"error":{"code":"TRACK_B_GAP_DIAGNOSTIC_FAILED"}}')
        else:
            report={'command':'track-b-gap-internal-diagnostic','read_only':True,'metadata_only':True,
                'operator_verification_successful':False,'diagnostic_completed':False,
                'public_error_code':'TRACK_B_GAP_DIAGNOSTIC_FAILED','events':[
                    {'stage':'research.sec_facts.row_count','reason_code':'METADATA_ROW_LIMIT',
                     'counts':{'row_count':500001,'row_limit':500000}}]}
            if mode=='bad_schema': report['events'][0]['stage']='PRIVATE'
            stdout.write(b'PRIVATE' if mode=='bad_json' else b'x'*(d.MAX_BYTES+2)
                if mode=='too_large' else json.dumps(report).encode())
        return subprocess.CompletedProcess(args,1)
    monkeypatch.setattr(subprocess,'run',execute)
    with pytest.raises(SystemExit) as exit: exec(compile(source,str(script),'exec'),{})
    assert exit.value.code==1 and len(calls)==4
    output=capsys.readouterr().out
    assert 'PRIVATE' not in output
    summary=json.loads((reports/'track-b-gaps-focused-summary.json').read_text(encoding='utf-8'))
    assert summary['operator_verification_successful'] is False
    assert not summary['pytest_run'] and not summary['deterministic_repeat_run']
    assert sum(e['reason_code']=='FINGERPRINT_UNCHANGED' for e in summary['events'])==2
    expected={'safe_failure':'METADATA_ROW_LIMIT','bad_json':'JSON_PARSE_FAILED',
        'bad_schema':'SAFE_SCHEMA_FAILED','too_large':'OUTPUT_LIMIT_OR_UTF8_FAILED',
        'launch_failure':'COMMAND_LAUNCH_OR_CAPTURE_FAILED'}[mode]
    assert expected in {e['reason_code'] for e in summary['events']}


def test_public_gap_failure_stays_redacted(tmp_path):
    import subprocess
    import sys
    paths=fixture(tmp_path)
    with duckdb.connect(str(paths[0])) as db:
        db.execute('ALTER TABLE security_classification_evidence DROP COLUMN security_id')
    result=subprocess.run([sys.executable,'-m','app.investment_research_cli',
        'track-b-identity-accounting-gap-diagnostic','--research-db',str(paths[0]),
        '--production-db',str(paths[1]),'--decision-at',D.isoformat()],capture_output=True)
    assert result.returncode==1 and result.stdout==b''
    assert json.loads(result.stderr)['error']['code']=='TRACK_B_GAP_DIAGNOSTIC_FAILED'
    assert b'Traceback' not in result.stderr and str(tmp_path).encode() not in result.stderr
    assert b'ROSTER_SCHEMA_UNSUPPORTED' not in result.stderr


def test_chain_failure_keeps_other_public_contract(tmp_path,monkeypatch):
    paths=fixture(tmp_path); monkeypatch.setattr(h,'MAX_CHAIN_WORK',1)
    result=traced(paths)
    assert result['public_error_code']=='TRACK_B_HISTORY_INVENTORY_FAILED'
    assert 'CHAIN_WORK_LIMIT' in {e['reason_code'] for e in result['events']}
