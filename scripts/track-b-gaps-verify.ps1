# Complete Windows PowerShell 5.1 operator verification. Stop concurrent DB writers.
# Run after checking out this PR's branch. This script never pulls or changes refs.
param(
  [string]$Repo = "C:\Users\Juan Estrada\Projects\SignalLens"
)
& {
  $ErrorActionPreference = "Stop"
  $Backend = Join-Path $Repo "backend"
  $Python = Join-Path $Repo ".venv\Scripts\python.exe"
  $Research = Join-Path $Backend "data\research\signallens-research.duckdb"
  $Production = Join-Path $Backend "data\signallens.duckdb"
  $Reports = Join-Path $Backend "data\research\reports"
  $Pushed = $false
  try {
    Push-Location $Backend
    $Pushed = $true
    $Verify = @'
import hashlib, json, pathlib, subprocess, sys

paths = {'research': pathlib.Path(sys.argv[1]), 'production': pathlib.Path(sys.argv[2])}
reports = pathlib.Path(sys.argv[3])
decisions = ('2026-10-04T21:30:00+00:00', '2026-10-05T00:30:00+00:00')
events = []
failed = False
before = {}

def emit(stage, reason, **counts):
    events.append({'stage': stage, 'reason_code': reason, 'counts': counts})

def fingerprint(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024*1024), b''):
            digest.update(block)
    return digest.hexdigest()

def save(path, payload):
    path.write_text(json.dumps(payload, sort_keys=True, separators=(',',':')), encoding='utf-8')

def verify(r, decision):
    assert r['command']=='track-b-identity-accounting-gap-diagnostic'
    assert r['decision_at']==decision
    assert r['read_only'] and r['metadata_only']
    assert not r['realized_outcome_values_read'] and not r['model_executed']
    assert not r['panel_persisted'] and not r['acquisition_authorized']
    assert not r['approved_eligibility'] and not r['preregistration_ready']
    assert not r['accounting_rules_changed'] and not r['missing_inputs_are_zero']
    assert r['contract_selected'] is None and r['sample_thresholds'] is None
    assert r['estimated_provider_requests']==r['validation_credit']==0
    assert r['unresolved_requirement_count']==len(r['blockers'])==8
    assert all(b['state']=='unresolved' for b in r['blockers'])
    for key in ('scores','rankings','candidates','recommendations','allocations','selections',
                'paper_selections','vintages','prospective_vintages','validation_observations'):
        assert r[key]==[]
    n=len(json.dumps(r,sort_keys=True,separators=(',',':')).encode('utf-8'))
    assert n==r['compact_utf8_bytes']<=r['bounds']['maximum_compact_utf8_bytes']==131072
    identity=r['reconciliation']
    assert sum(identity['roster_counts'].values())==identity['comparable_roster_count']
    assert sum(identity['stored_partition'].values())==identity['stored_security_id_count']
    assert len(identity['roster_details'])==identity['comparable_roster_count']<=256
    assert not identity['approved_eligibility'] and not identity['historical_membership_certified']
    assert not identity['effective_identity_certified']
    matched=identity['roster_counts']['matched']
    for name in paths:
        fp=r['database_fingerprints'][name]
        assert fp['unchanged'] and fp['before']==fp['after']
        assert fp['before']['sha256']==before[name]
        for layer in r['databases'][name]['layers'].values():
            assert layer['population_denominator']==matched
            assert all(f['certified_formula_count']==0 for f in layer['families'].values())
            sample=layer['company_issue_samples']
            assert sample['total_count']==matched
            assert sample['returned_count']==len(sample['items'])<=sample['sample_limit']==10
            assert sample['truncated']==(sample['total_count']>sample['returned_count'])
        for counts in r['databases'][name]['raw_canonical_coverage'].values():
            values=[counts[k] for k in ('raw_and_canonical','raw_only','canonical_only','neither')]
            assert all(v is None for v in values) or sum(values)==matched
    completed=r['controlled_sec_retrievals']
    assert not completed.get('repeat_retrieval_recommended',False)
    return n

try:
    for name,path in paths.items():
        try:
            before[name]=fingerprint(path)
            emit(name+'.external.before','FINGERPRINT_OK')
        except Exception:
            failed=True
            emit(name+'.external.before','FINGERPRINT_READ_FAILED')
    tests=('test_track_b_gaps.py','test_track_b_history.py','test_track_b_history_market_stream.py',
           'test_track_b_history_diagnostic.py','test_track_b_panel.py',
           'test_financial_strength.py','test_liquidity_evidence.py','test_sec_liquidity_ingestion.py')
    result=subprocess.run([sys.executable,'-m','pytest', *('tests/'+t for t in tests), '-q'],
                          capture_output=True)
    if result.returncode:
        failed=True
        emit('offline_tests','OFFLINE_TESTS_FAILED')
    else:
        emit('offline_tests','OFFLINE_TESTS_PASSED')
    if len(before)==2 and result.returncode==0:
        reports.mkdir(parents=True,exist_ok=True)
        for index,decision in enumerate(decisions):
            stage='boundary_'+str(index)
            try:
                command=[sys.executable,'-m','app.investment_research_cli',
                    'track-b-identity-accounting-gap-diagnostic','--research-db',str(paths['research']),
                    '--production-db',str(paths['production']),'--decision-at',decision]
                result=subprocess.run(command,capture_output=True)
                if result.returncode:
                    raise RuntimeError('diagnostic failed')
                r=json.loads(result.stdout.decode('utf-8'))
                size=verify(r,decision)
                repeat=subprocess.run(command,capture_output=True)
                assert repeat.returncode==0 and repeat.stdout==result.stdout
                save(reports/('track-b-gaps-'+str(index)+'.json'),r)
                identity=r['reconciliation']
                emit(stage,'DIAGNOSTIC_VERIFIED',compact_utf8_bytes=size,
                     roster_count=identity['comparable_roster_count'],
                     stored_security_count=identity['stored_security_id_count'],
                     matched=identity['roster_counts']['matched'],
                     ambiguous=identity['roster_counts']['ambiguous'],
                     unmatched=identity['roster_counts']['unmatched'],
                     outside_perimeter=identity['outside_perimeter_count'],
                     outside_roster_unresolved=identity['stored_partition']['outside_roster_perimeter_unresolved'])
            except Exception:
                failed=True
                emit(stage,'DIAGNOSTIC_VERIFICATION_FAILED')
except Exception:
    failed=True
    emit('verification','VERIFICATION_FAILED')
finally:
    for name,path in paths.items():
        try:
            after=fingerprint(path)
            if name not in before:
                failed=True
                emit(name+'.external.after','FINGERPRINT_BASELINE_UNAVAILABLE')
            elif after!=before[name]:
                failed=True
                emit(name+'.external.after','FINGERPRINT_CHANGED')
            else:
                emit(name+'.external.after','FINGERPRINT_UNCHANGED')
        except Exception:
            failed=True
            emit(name+'.external.after','FINGERPRINT_READ_FAILED')
    try:
        reports.mkdir(parents=True,exist_ok=True)
        save(reports/'track-b-gaps-verification.json',{'events':events,'failed':failed})
    except Exception:
        failed=True
        emit('reports','REPORT_SAVE_FAILED')
print(json.dumps({'events':events,'failed':failed},sort_keys=True,separators=(',',':')))
sys.exit(int(failed))
'@
    & $Python -X utf8 -c $Verify $Research $Production $Reports 2>$null
    if ($LASTEXITCODE -ne 0) { throw "TRACK_B_GAPS_VERIFICATION_FAILED" }
  } finally {
    if ($Pushed) { Pop-Location }
  }
}
