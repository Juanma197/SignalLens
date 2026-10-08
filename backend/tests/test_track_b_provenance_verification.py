"""Execute the checked-in PowerShell's exact Python stdin verifier offline."""
import json
from pathlib import Path
import pytest
from app import track_b_provenance_replay as r
from test_track_b_provenance_replay import databases,D


def runner():
    path=Path(__file__).parents[2]/'scripts/track-b-provenance-verify.ps1'
    source=path.read_text(encoding='utf-8')
    payload=source.split("$Verify = @'\n",1)[1].split("\n'@",1)[0]
    ns={'__name__':'offline_test'};exec(compile(payload,str(path),'exec'),ns)
    return ns,source


def simulate(paths,failure=None):
    def command(argv,cwd):
        if 'pytest' in argv:return (1 if failure=='tests' else 0),b'offline'
        if failure=='launch':raise OSError('SECRET')
        if failure=='parse':return 1,b'SECRET_NOT_JSON'
        report=r.run(research_db=paths[0],production_db=paths[1],decision_at=D.isoformat())
        if failure=='invariant':report['database_writes']=True
        if failure=='inner_hash':report['database_hashes']['research']['after']['sha256']='wrong'
        return (0 if report['execution_state']=='completed' else 1),r.encode(report)
    return command


def verify(tmp_path,ns,paths,skip=True):
    repo=tmp_path/'repo';(repo/'frontend').mkdir(parents=True,exist_ok=True)
    (repo/'frontend'/'next-env.d.ts').write_bytes(b'preserve existing frontend\r\n')
    return ns['verify'](repo,dict(zip(('research','production'),paths)),tmp_path/'reports',D.isoformat(),skip)


def test_verifier_saves_utf8_and_all_hashes(tmp_path):
    paths=databases(tmp_path);ns,source=runner();ns['execute']=simulate(paths)
    summary=verify(tmp_path,ns,paths)
    assert summary['execution_state']=='completed' and not summary['errors']
    assert all(v['unchanged'] for v in summary['database_hashes'].values()) and summary['frontend_next_env']['unchanged']
    for filename in ('track-b-provenance-replay.json','track-b-provenance-verification.json'):
        raw=(tmp_path/'reports'/filename).read_bytes()
        assert not raw.startswith(b'\xef\xbb\xbf') and json.loads(raw.decode('utf-8'))['status']=='proposed_not_authorized'
    assert '-X utf8 -' in source and 'ConvertFrom-Json -Depth' not in source


@pytest.mark.parametrize('failure',['launch','parse','invariant','inner_hash','tests'])
def test_failure_reports_saved_and_redacted(tmp_path,failure):
    paths=databases(tmp_path);ns,_=runner();ns['execute']=simulate(paths,failure)
    summary=verify(tmp_path,ns,paths,skip=failure!='tests')
    assert summary['execution_state']=='failed' and summary['errors']
    assert all(v['unchanged'] for v in summary['database_hashes'].values())
    for filename in ('track-b-provenance-replay.json','track-b-provenance-verification.json'):
        raw=(tmp_path/'reports'/filename).read_bytes();assert b'SECRET' not in raw
        assert json.loads(raw)['execution_state']=='failed'


def test_after_hash_fail_does_not_skip_production(tmp_path):
    paths=databases(tmp_path);ns,_=runner();ns['execute']=simulate(paths,'launch');original=ns['fingerprint'];calls=[]
    def instrument(path):
        if path in paths:
            calls.append(path)
            if len(calls)==3:raise OSError('SECRET')
        return original(path)
    ns['fingerprint']=instrument;summary=verify(tmp_path,ns,paths)
    assert calls==[paths[0],paths[1],paths[0],paths[1]]
    assert summary['execution_state']=='failed' and summary['database_hashes']['production']['unchanged']


def test_report_save_failure_does_not_skip_hash_cleanup(tmp_path):
    paths=databases(tmp_path);ns,_=runner();original=ns['save'];called=0
    def save(path,payload):
        nonlocal called
        called+=1
        if called==1:raise OSError('SECRET')
        return original(path,payload)
    ns['save']=save;ns['execute']=simulate(paths);summary=verify(tmp_path,ns,paths)
    assert summary['execution_state']=='failed'
    assert all(x['after'] is not None for x in summary['database_hashes'].values())
    assert (tmp_path/'reports'/'track-b-provenance-verification.json').exists()


def test_real_payload_process_and_utf8_reports(tmp_path):
    # Real CLI subprocess over synthetic files; skip nested pytest recursion.
    paths=databases(tmp_path);ns,_=runner();repo=Path(__file__).parents[2]
    summary=ns['verify'](repo,dict(zip(('research','production'),paths)),tmp_path/'reports',D.isoformat(),True)
    assert summary['execution_state']=='completed',summary
    assert summary['frontend_next_env']['unchanged']


def test_capture_is_bounded_and_command_failure_redacted():
    import sys
    ns,_=runner()
    with pytest.raises(RuntimeError,match='COMMAND_OUTPUT_LIMIT'):
        ns['execute']([sys.executable,'-c','print("x"*140000)'],Path.cwd())


def test_initial_save_failure_still_attempts_both_pre_post_hashes(tmp_path):
    paths=databases(tmp_path);ns,_=runner();original_hash=ns['fingerprint'];original_save=ns['save'];calls=[];saves=0
    def fingerprint(path):
        if path in paths:calls.append(path)
        return original_hash(path)
    def save(path,payload):
        nonlocal saves
        saves+=1
        if saves==1:raise OSError('SECRET')
        return original_save(path,payload)
    ns['fingerprint']=fingerprint;ns['save']=save;ns['execute']=simulate(paths)
    summary=verify(tmp_path,ns,paths)
    assert calls==[paths[0],paths[1],paths[0],paths[1]]
    assert summary['execution_state']=='failed' and (tmp_path/'reports'/'track-b-provenance-verification.json').exists()


def test_outer_powershell_handles_missing_python_and_stale_reports():
    _,source=runner()
    assert '$PythonCompleted = $false' in source and 'if (-not $PythonCompleted)' in source
    assert source.index('$Before[$Name] = Database-Fingerprint $Path') < source.index('$Pending =')
    assert 'track-b-provenance-cleanup.json' in source and '$OutputEncoding = $PreviousEncoding' in source


def test_metadata_refusal_preserves_safe_diagnostic_and_execution_failure_stage(tmp_path):
    import duckdb
    paths=databases(tmp_path)
    with duckdb.connect(str(paths[0])) as db:db.execute('UPDATE sec_facts SET frame=?',['SECRET_REJECTED_'+'x'*1100])
    ns,_=runner();ns['execute']=simulate(paths);summary=verify(tmp_path,ns,paths)
    assert summary['execution_state']=='failed' and summary['errors']==['REPLAY_EXECUTION']
    report=json.loads((tmp_path/'reports'/'track-b-provenance-replay.json').read_bytes())
    assert report['errors']==['METADATA_CELL_LIMIT']
    assert report['safe_read_diagnostic']['offending_columns'][0]['projected_column']=='frame'
    assert 'SECRET_REJECTED_' not in json.dumps(report)
