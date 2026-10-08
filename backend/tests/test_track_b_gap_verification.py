"""Verifier-only reproductions. No database connections, providers or test reruns."""
import ast
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

from app.financial_strength import _within_contract

SCRIPT = Path(__file__).resolve().parents[2]/'scripts'/'track-b-gaps-verify.ps1'


def source():
    return SCRIPT.read_text(encoding='utf-8').split("$Verify = @'\n",1)[1].split("\n'@",1)[0]


def minimal_report(paths, decision):
    flags=('read_only','metadata_only','realized_outcome_values_read','model_executed',
        'panel_persisted','acquisition_authorized','approved_eligibility','preregistration_ready',
        'accounting_rules_changed','missing_inputs_are_zero')
    report={key:key in ('read_only','metadata_only') for key in flags}
    report.update(command='track-b-identity-accounting-gap-diagnostic',decision_at=decision,
        contract_selected=None,sample_thresholds=None,estimated_provider_requests=0,validation_credit=0,
        unresolved_requirement_count=8,blockers=[{'state':'unresolved'} for _ in range(8)],
        bounds={'maximum_compact_utf8_bytes':131072},controlled_sec_retrievals={'repeat_retrieval_recommended':False})
    for key in ('scores','rankings','candidates','recommendations','allocations','selections',
                'paper_selections','vintages','prospective_vintages','validation_observations'):
        report[key]=[]
    report['reconciliation']={
        'comparable_roster_count':0,'stored_security_id_count':0,'outside_perimeter_count':0,
        'roster_counts':dict(matched=0,ambiguous=0,unmatched=0),
        'stored_partition':dict(matched_roster=0,ambiguous_roster=0,
            outside_perimeter_by_current_classification=0,outside_roster_perimeter_unresolved=0),
        'roster_details':[],'approved_eligibility':False,'historical_membership_certified':False,
        'effective_identity_certified':False}
    report['database_fingerprints']={}
    report['databases']={}
    for name,path in paths.items():
        fp={'bytes':path.stat().st_size,'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
        report['database_fingerprints'][name]={'unchanged':True,'before':fp,'after':copy.deepcopy(fp)}
        layer={'population_denominator':0,
            'families':{f:{'certified_formula_count':0} for f in ('value','financial_strength')},
            'company_issue_samples':dict(items=[],total_count=0,returned_count=0,sample_limit=10,truncated=False)}
        report['databases'][name]={'layers':{l:copy.deepcopy(layer) for l in ('raw_sec','canonical')},
            'raw_canonical_coverage':{'current_debt':dict(raw_and_canonical=0,raw_only=0,canonical_only=0,neither=0)}}
    return _within_contract(report,131072)


def execute(tmp_path, monkeypatch, scenario='success', inspect=False):
    paths={name:tmp_path/(name+'.duckdb') for name in ('research','production')}
    for path in paths.values(): path.write_bytes(b'SYNTHETIC_HASH_FIXTURE')
    reports=tmp_path/'reports'; reports.mkdir()
    if inspect:
        for index,decision in enumerate(('2026-10-04T21:30:00+00:00','2026-10-05T00:30:00+00:00')):
            r=minimal_report(paths,decision)
            if scenario=='invariant': r['model_executed']=True
            (reports/f'track-b-gaps-{index}.json').write_text(json.dumps(r),encoding='utf-8')
    monkeypatch.setattr(sys,'argv',['verify',str(paths['research']),str(paths['production']),str(reports),'1','1' if inspect else '0'])
    calls=[]
    def command_run(command, **kwargs):
        calls.append(command)
        assert command[2]!='pytest', 'Focused verification must never rerun tests'
        if inspect: raise AssertionError('Inspection must not invoke diagnostic command')
        r=minimal_report(paths,command[-1])
        if scenario=='invariant': r['model_executed']=True
        if scenario=='schema': r['databases']['research']['layers']['raw_sec']['population_denominator']='PRIVATE_STRING'
        if scenario=='output': r['compact_utf8_bytes']=-1
        if scenario=='repeat' and len(calls)%2==0:
            r['nonce']='PRIVATE_REPEAT_CONTENT'
        if scenario in ('command','repeat_command') and (scenario=='command' or len(calls)%2==0):
            stderr=json.dumps({'status':'failed','error':{'code':'TRACK_B_GAP_DIAGNOSTIC_FAILED','message':'PRIVATE_MESSAGE'}}).encode()
            return subprocess.CompletedProcess(command,1,stdout=b'',stderr=stderr)
        if scenario=='launch': raise OSError('PRIVATE_OS_PATH')
        payload=json.dumps(r,sort_keys=True).encode('utf-8')
        if scenario=='json': payload=b'PRIVATE_INVALID_JSON'
        if scenario=='utf8': payload=b'\xffPRIVATE_INVALID_UTF8'
        if scenario=='null': payload=b'null'
        return subprocess.CompletedProcess(command,0,stdout=payload,stderr=b'')
    monkeypatch.setattr(subprocess,'run',command_run)
    original=Path.write_text
    def write(path,*args,**kwargs):
        if scenario=='save' and path.name in ('track-b-gaps-0.json','track-b-gaps-1.json'):
            raise OSError('PRIVATE_SAVE_PATH')
        if scenario=='capture_save' and path.name.endswith('.stdout.txt'):
            raise OSError('PRIVATE_CAPTURE_PATH')
        return original(path,*args,**kwargs)
    monkeypatch.setattr(Path,'write_text',write)
    before=[p.read_bytes() for p in paths.values()]
    with pytest.raises(SystemExit) as result:
        exec(compile(source(),str(SCRIPT),'exec'),{})
    summary=json.loads((reports/('track-b-gaps-inspection.json' if inspect else 'track-b-gaps-verification.json')).read_text())
    assert [p.read_bytes() for p in paths.values()]==before
    assert sum(e['reason_code']=='FINGERPRINT_UNCHANGED' for e in summary['events'])==2
    assert len(summary['events'])<=64
    assert len(json.dumps(summary).encode())<16384
    return summary, calls, reports, result.value.code


@pytest.mark.parametrize('scenario,expected_stage,expected_reason',[
    ('command','boundary_0.command','COMMAND_NONZERO'),
    ('launch','boundary_0.command','COMMAND_LAUNCH_FAILED'),
    ('json','boundary_0.parse','JSON_PARSE_FAILED'),
    ('utf8','boundary_0.parse','UTF8_DECODE_FAILED'),
    ('null','boundary_0.schema.root_schema','REPORT_SCHEMA_INVALID'),
    ('schema','boundary_0.schema.accounting_schema','REPORT_SCHEMA_INVALID'),
    ('invariant','boundary_0.validate.no_outcomes_or_model','REPORT_INVARIANT_FAILED'),
    ('output','boundary_0.validate.output_limits','OUTPUT_LIMIT_OR_SIZE_FAILED'),
    ('repeat','boundary_0.repeat','DETERMINISTIC_REPEAT_MISMATCH'),
    ('repeat_command','boundary_0.repeat.command','COMMAND_NONZERO'),
    ('save','boundary_0.save','REPORT_SAVE_FAILED'),
    ('capture_save','boundary_0.command.capture','CAPTURE_SAVE_FAILED'),
])
def test_safe_stage_specific_reproductions(tmp_path,monkeypatch,capsys,scenario,expected_stage,expected_reason):
    summary,calls,reports,code=execute(tmp_path,monkeypatch,scenario)
    assert code==1 and summary['failed']
    assert any(e['stage']==expected_stage and e['reason_code']==expected_reason for e in summary['events'])
    printed=capsys.readouterr().out
    assert 'PRIVATE_' not in printed and str(tmp_path) not in printed
    assert 'DIAGNOSTIC_VERIFICATION_FAILED' not in printed
    if scenario not in ('launch','capture_save'):
        assert (reports/'track-b-gaps-0.stdout.txt').exists()
        assert (reports/'track-b-gaps-0.stderr.txt').exists()
    if scenario=='command':
        assert any(e['reason_code']=='TRACK_B_GAP_DIAGNOSTIC_FAILED' for e in summary['events'])
        assert 'PRIVATE_MESSAGE' in (reports/'track-b-gaps-0.stderr.txt').read_text()


def test_focused_success_saves_utf8_without_test_suite(tmp_path,monkeypatch):
    summary,calls,reports,code=execute(tmp_path,monkeypatch)
    assert code==0 and not summary['failed'] and len(calls)==4
    assert any(e['reason_code']=='OFFLINE_TESTS_SKIPPED' for e in summary['events'])
    for index in range(2):
        content=(reports/f'track-b-gaps-{index}.json').read_bytes()
        assert not content.startswith(b'\xef\xbb\xbf')
        assert len(content)==json.loads(content)['compact_utf8_bytes']<=131072


@pytest.mark.parametrize('scenario',['success','invariant'])
def test_saved_inspection_never_runs_commands_or_tests(tmp_path,monkeypatch,scenario):
    summary,calls,_,code=execute(tmp_path,monkeypatch,scenario,inspect=True)
    assert not calls
    assert code==int(scenario=='invariant')
    assert any(e['reason_code']=='SAVED_REPORT_BASELINE_CONTEXT' for e in summary['events'])


def test_original_failure_envelope_cannot_identify_actual_operator_cause():
    # Exact defect in c058f72's boundary handler: all exception classes collapse
    # to this one code. This reproduces the confirmed loss of failure evidence,
    # not an assertion about which exception occurred on the operator's files.
    for exc in (RuntimeError(),UnicodeDecodeError('utf-8',b'\xff',0,1,'bad'),
                json.JSONDecodeError('bad','',0),KeyError(),AssertionError(),TypeError()):
        events=[]
        try:
            raise exc
        except Exception:
            events.append({'stage':'boundary_0','reason_code':'DIAGNOSTIC_VERIFICATION_FAILED'})
        assert events==[{'stage':'boundary_0','reason_code':'DIAGNOSTIC_VERIFICATION_FAILED'}]


def test_original_assertion_contract_is_preserved():
    tree=ast.parse(source())
    verify=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='verify')
    assertions=sorted((n for n in ast.walk(verify) if isinstance(n,ast.Assert)),key=lambda n:n.lineno)
    assert len(assertions)==27
    # Limits and the exact chained relations are unchanged, not relaxed.
    expressions=[ast.unparse(n.test) for n in assertions]
    assert "n == r['compact_utf8_bytes'] <= r['bounds']['maximum_compact_utf8_bytes'] == 131072" in expressions
    assert "len(identity['roster_details']) == identity['comparable_roster_count'] <= 256" in expressions
    assert "sample['returned_count'] == len(sample['items']) <= sample['sample_limit'] == 10" in expressions
    assert "all((v is None for v in values)) or sum(values) == matched" in expressions
