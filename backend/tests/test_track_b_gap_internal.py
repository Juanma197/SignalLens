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


@pytest.mark.parametrize('column',g.IDENTITY_KEYS)
def test_confirmed_operator_guard_each_possible_column(tmp_path,column,monkeypatch):
    """Reproduce confirmed table/guard/counts without guessing its column."""
    from test_track_b_gaps import create, add
    # Reproduce the pre-fix projection, not the fingerprinted replacement.
    monkeypatch.setattr(g,'_projection_expression',lambda table,column:f'"{column}"')
    paths=(tmp_path/'research.duckdb',tmp_path/'production.duckdb')
    with duckdb.connect(str(paths[1])): pass
    with duckdb.connect(str(paths[0])) as db:
        create(db,'sec_liquidity_runs')
        row={key:'bounded' for key in g.IDENTITY_KEYS}
        row[column]='PRIVATE'+('x'*1018)  # 1025 characters; actual bound unchanged.
        add(db,'sec_liquidity_runs',**row)
    result=traced(paths); check(result,'METADATA_CELL_LIMIT')
    assert result['events'][-1]['stage']=='internal'
    guard=next(e for e in result['events'] if e['reason_code']=='METADATA_CELL_LIMIT')
    assert guard['stage']=='research.sec_liquidity_runs.cell_count'
    assert guard['counts']=={'row_count':1,'projected_column_count':6,
        'oversized_row_count':1,'cell_character_limit':1024}
    details=[e for e in result['events'] if e['reason_code']=='METADATA_CELL_COLUMN_LIMIT']
    assert len(details)==1
    assert details[0]['stage']=='research.sec_liquidity_runs.cell_count.'+column
    assert details[0]['counts']['maximum_cell_characters']==1025
    assert sum(e['reason_code']=='FINGERPRINT_AFTER_OK' for e in result['events'])==2
    with pytest.raises(g.GapDiagnosticError): run(paths)


def test_optional_cell_detail_failure_keeps_original_guard(tmp_path,monkeypatch):
    paths=fixture(tmp_path); original=g._read
    class Proxy:
        def __init__(self,db): self.db=db
        def execute(self,sql,args=None):
            if 'FILTER (WHERE' in sql: raise duckdb.OutOfMemoryException('PRIVATE')
            return self.db.execute(sql,args) if args is not None else self.db.execute(sql)
    def read(db,table,database='research'):
        return original(Proxy(db),table,database)
    monkeypatch.setattr(g,'_read',read); monkeypatch.setattr(g,'MAX_CELL_CHARS',1)
    result=traced(paths); check(result,'METADATA_CELL_LIMIT')
    assert 'METADATA_CELL_DETAILS_UNAVAILABLE' in {e['reason_code'] for e in result['events']}


def test_cell_details_all_columns_no_values(monkeypatch):
    monkeypatch.setattr(g,'_projection_expression',lambda table,column:f'"{column}"')
    trace=d.Trace(); token=g._TRACE.set(trace)
    try:
        with duckdb.connect(':memory:') as db:
            columns=g.SOURCES['sec_liquidity_runs']
            db.execute('CREATE TABLE sec_liquidity_runs('+','.join(c+' VARCHAR' for c in columns)+')')
            db.execute('INSERT INTO sec_liquidity_runs VALUES ('+','.join('?' for c in columns)+')',
                       ['PRIVATE'+'x'*1018 for c in columns])
            with pytest.raises(g.GapDiagnosticError): g._read(db,'sec_liquidity_runs')
    finally: g._TRACE.reset(token)
    details=[e for e in trace.events if e['reason_code']=='METADATA_CELL_COLUMN_LIMIT']
    assert len(details)==6 and 'PRIVATE' not in json.dumps(trace.events)
    assert all(e['counts']['maximum_cell_characters']==1025 for e in details)


@pytest.mark.parametrize('length', [1024,1025])
def test_cell_only_runner_no_other_tables_or_subprocesses(tmp_path,monkeypatch,capsys,length):
    from pathlib import Path
    import subprocess
    import sys
    from test_track_b_gaps import create, add
    paths=(tmp_path/'research.duckdb',tmp_path/'production.duckdb'); reports=tmp_path/'reports'
    reports.mkdir(); old=reports/'track-b-gaps-focused-summary.json'; old.write_text('existing capture')
    with duckdb.connect(str(paths[1])): pass
    with duckdb.connect(str(paths[0])) as db:
        create(db,'sec_liquidity_runs')
        row={key:'bounded' for key in g.IDENTITY_KEYS}; row['plan_id']='PRIVATE'+'x'*(length-7)
        add(db,'sec_liquidity_runs',**row)
        db.execute("CREATE VIEW sec_facts AS SELECT error('PRIVATE_DO_NOT_READ') AS secret")
    original=duckdb.connect; connections=[]
    def connect(path,**kwargs):
        connections.append((path,kwargs)); return original(path,**kwargs)
    monkeypatch.setattr(duckdb,'connect',connect)
    def forbidden(*args,**kwargs): raise AssertionError('no subprocess permitted')
    monkeypatch.setattr(subprocess,'run',forbidden)
    monkeypatch.setattr(sys,'argv',['-',*(str(p) for p in paths),str(reports),'1'])
    script=Path(__file__).resolve().parents[2]/'scripts/track-b-gaps-diagnose.ps1'
    source=script.read_text().split("$Diagnostic = @'\n",1)[1].split("\n'@",1)[0]
    with pytest.raises(SystemExit) as exit: exec(compile(source,str(script),'exec'),{})
    assert exit.value.code==int(length>1024)
    assert len(connections)==1 and connections[0][0]==str(paths[0])
    assert connections[0][1]=={'read_only':True,'config':h._sql_config()}
    output=capsys.readouterr().out; assert 'PRIVATE' not in output
    summary=json.loads((reports/'track-b-gaps-cell-lengths-summary.json').read_text(encoding='utf-8'))
    assert summary['cell_lengths_only'] and not summary['pytest_run']
    assert not summary['deterministic_repeat_run'] and not summary['operator_verification_successful']
    assert sum(e['reason_code']=='FINGERPRINT_UNCHANGED' for e in summary['events'])==2
    assert old.read_text()=='existing capture' and g._TRACE.get() is None
    if length>1024:
        detail=next(e for e in summary['events'] if e['reason_code']=='METADATA_CELL_COLUMN_LIMIT')
        assert detail['stage']=='cell_lengths.research.sec_liquidity_runs.cell_count.plan_id'
        assert detail['counts']['maximum_cell_characters']==length


def test_confirmed_11171_plan_capability_reproduction_and_fix(tmp_path,monkeypatch):
    from app.sec_liquidity_plan import _encode_identifier, _decode_identifier
    import hashlib
    paths=fixture(tmp_path)
    plan=next(p for n in range(8200,8350) if len(p:=_encode_identifier(D,
        {'issued_at':D.isoformat(),'synthetic':'x'*n}))==11171)
    assert _decode_identifier(plan)[0]==D
    with duckdb.connect(str(paths[0])) as db:
        # Exactly one run row with six projected identity columns.
        db.execute('DELETE FROM sec_liquidity_runs WHERE run_id<>?', ['run-s0'])
        for table in g.PLAN_ID_TABLES:
            db.execute('UPDATE '+table+' SET plan_id=? WHERE run_id=?',[plan,'run-s0'])
    before=[h.fingerprint(p) for p in paths]
    with monkeypatch.context() as old:
        old.setattr(g,'_projection_expression',lambda table,column:f'"{column}"')
        rejected=traced(paths); check(rejected,'METADATA_CELL_LIMIT')
        detail=next(e for e in rejected['events'] if e['reason_code']=='METADATA_CELL_COLUMN_LIMIT')
        assert detail['stage']=='research.sec_liquidity_runs.cell_count.plan_id'
        assert detail['counts']=={'row_count':1,'projected_column_count':6,
            'oversized_row_count':1,'cell_character_limit':1024,'maximum_cell_characters':11171}
    result=run(paths)
    assert result['controlled_sec_retrievals']['completed_current_count']==1
    assert result['controlled_sec_retrievals']['completed_by_boundary_count']==1
    assert result['unresolved_requirement_count']==8 and not result['preregistration_ready']
    assert result['bounds']['maximum_metadata_cell_characters']==1024
    assert [h.fingerprint(p) for p in paths]==before
    assert plan not in json.dumps(result)
    with duckdb.connect(str(paths[0]),read_only=True,config=h._sql_config()) as db:
        summary,rows=g._read(db,'sec_liquidity_runs')
    assert rows[0]['plan_id']=='sha256:'+hashlib.sha256(plan.encode('utf-8')).hexdigest()
    assert summary['identity_projection']['plan_id']=='sha256_full_stored_utf8_preserving_null_and_empty'
    # Difference beyond the old bound must break completion; no prefix matching.
    with duckdb.connect(str(paths[0])) as db:
        db.execute('UPDATE sec_liquidity_raw_provenance SET plan_id=? WHERE run_id=?',[plan+'z','run-s0'])
    assert run(paths)['controlled_sec_retrievals']['completed_current_count']==0


@pytest.mark.parametrize('value',[None,'','é'+'x'*11170])
def test_plan_projection_null_empty_unicode(value):
    import hashlib
    with duckdb.connect(':memory:') as db:
        db.execute('CREATE TABLE sec_liquidity_runs(plan_id VARCHAR)')
        db.execute('INSERT INTO sec_liquidity_runs VALUES (?)',[value])
        summary,rows=g._read(db,'sec_liquidity_runs')
    expected=value if value in (None,'') else 'sha256:'+hashlib.sha256(value.encode('utf-8')).hexdigest()
    assert rows[0]['plan_id']==expected


def test_other_identity_columns_still_fail_at_original_bound():
    with duckdb.connect(':memory:') as db:
        db.execute('CREATE TABLE sec_liquidity_runs(plan_id VARCHAR,run_id VARCHAR)')
        db.execute('INSERT INTO sec_liquidity_runs VALUES (?,?)',['x'*11171,'x'*1025])
        with pytest.raises(g.GapDiagnosticError): g._read(db,'sec_liquidity_runs')
