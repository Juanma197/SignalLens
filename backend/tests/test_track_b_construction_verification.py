"""Exercise the exact Python stdin payload in the PowerShell 5.1 verifier."""
from datetime import datetime
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import track_b_construction_feasibility as f
from test_track_b_construction_feasibility import fixture


def runner():
    path=Path(__file__).parents[2]/'scripts/track-b-constructions-verify.ps1'
    source=path.read_text(encoding='utf-8')
    payload=source.split("$Verify = @'\n",1)[1].split("\n'@",1)[0]
    namespace={'__name__':'offline_verifier_test'}
    exec(compile(payload,str(path),'exec'),namespace)
    return namespace,source


def simulated_command(paths,fail=None):
    def run(command,**kwargs):
        if 'pytest' in command:
            return SimpleNamespace(returncode=0,stdout=b'passed',stderr=b'')
        if fail=='launch':raise OSError('SECRET_EXCEPTION')
        if fail=='command':return SimpleNamespace(returncode=1,stdout=b'',stderr=b'SECRET_ERROR')
        if fail=='parse':return SimpleNamespace(returncode=0,stdout=b'\xffSECRET_PAYLOAD',stderr=b'')
        decision=command[command.index('--decision-at')+1]
        r=f.assess(research_db=paths[0],production_db=paths[1],decision_at=decision)
        if fail=='invariant':r['model_executed']=True
        if fail=='repeat' and getattr(run,'called',False):r['validation_credit']=9
        if fail=='count_repeat' and getattr(run,'called',False):
            r['databases']['research']['raw_sec']['flows']['compatible_ocf_ttm_candidates']+=1
            f.compact(r)
        run.called=True
        return SimpleNamespace(returncode=0,stdout=json.dumps(r,sort_keys=True,separators=(',',':')).encode()+b'\n',stderr=b'')
    return run


def test_verifier_saves_utf8_reports_after_both_hash_checks(tmp_path,monkeypatch):
    paths=fixture(tmp_path);ns,source=runner();reports=tmp_path/'reports'
    monkeypatch.setattr(ns['subprocess'],'run',simulated_command(paths))
    summary=ns['verify'](dict(zip(('research','production'),paths)),reports,True)
    assert not summary['failed']
    assert all(v['unchanged'] for v in summary['database_fingerprints'].values())
    for filename in ('track-b-constructions-0.json','track-b-constructions-1.json','track-b-constructions-verification.json'):
        data=(reports/filename).read_bytes()
        assert not data.startswith(b'\xef\xbb\xbf')
        r=json.loads(data.decode('utf-8'))
        assert r['status']=='proposed_not_authorized'
    assert '-X utf8 -' in source and 'finally {' in source
    assert 'ConvertFrom-Json -Depth' not in source and '??' not in source


@pytest.mark.parametrize('failure,reason',[('launch','STAGE_FAILED'),('command','COMMAND_FAILED'),
    ('parse','UTF8_OR_JSON_FAILED'),('invariant','INVARIANT_FAILED'),('repeat','REPEAT_INVARIANT_FAILED'),('count_repeat','COUNT_REPEAT_MISMATCH')])
def test_verifier_failure_no_verified_reports_hashes_still_checked(tmp_path,monkeypatch,failure,reason):
    paths=fixture(tmp_path);ns,_=runner();reports=tmp_path/'reports'
    monkeypatch.setattr(ns['subprocess'],'run',simulated_command(paths,failure))
    summary=ns['verify'](dict(zip(('research','production'),paths)),reports,True)
    assert summary['failed'] and reason in [e['reason_code'] for e in summary['events']]
    assert all(v['unchanged'] for v in summary['database_fingerprints'].values())
    assert not (reports/'track-b-constructions-0.json').exists()
    assert 'SECRET' not in json.dumps(summary)
    assert (reports/'track-b-constructions-verification.json').exists()


def test_verifier_both_after_hashes_attempted_on_hash_error(tmp_path,monkeypatch):
    paths=fixture(tmp_path);ns,_=runner();reports=tmp_path/'reports';original=ns['fingerprint'];calls=[]
    def hash_file(path):
        calls.append(path)
        if len(calls)==3:raise OSError('SECRET')
        return original(path)
    ns['fingerprint']=hash_file
    monkeypatch.setattr(ns['subprocess'],'run',simulated_command(paths,'command'))
    r=ns['verify'](dict(zip(('research','production'),paths)),reports,True)
    assert r['failed'] and calls==[paths[0],paths[1],paths[0],paths[1]]
    assert r['database_fingerprints']['production']['unchanged']


def test_verifier_changed_hash_and_save_failure(tmp_path,monkeypatch):
    paths=fixture(tmp_path);ns,_=runner();original=ns['fingerprint'];calls=[]
    def changed(path):
        calls.append(path);result=original(path)
        if len(calls)==3:result['sha256']='0'*64
        return result
    ns['fingerprint']=changed
    monkeypatch.setattr(ns['subprocess'],'run',simulated_command(paths))
    reports=tmp_path/'reports'
    r=ns['verify'](dict(zip(('research','production'),paths)),reports,True)
    assert r['failed'] and not (reports/'track-b-constructions-0.json').exists()
    ns,_=runner();ns['save']=lambda *args: (_ for _ in ()).throw(OSError('SECRET'))
    r=ns['verify'](dict(zip(('research','production'),paths)),reports,True)
    assert r['failed'] and 'SUMMARY_SAVE_FAILED' in [e['reason_code'] for e in r['events']]


def test_offline_test_failure_still_checks_hashes(tmp_path,monkeypatch):
    paths=fixture(tmp_path);ns,_=runner()
    def fail_tests(command,**kwargs):
        assert 'pytest' in command
        return SimpleNamespace(returncode=1,stdout=b'SECRET_TEST_LOG',stderr=b'')
    monkeypatch.setattr(ns['subprocess'],'run',fail_tests)
    result=ns['verify'](dict(zip(('research','production'),paths)),tmp_path/'reports',False)
    assert result['failed'] and all(v['unchanged'] for v in result['database_fingerprints'].values())
    assert 'SECRET' not in json.dumps(result)
