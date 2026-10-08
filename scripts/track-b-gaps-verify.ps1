# Complete Windows PowerShell 5.1 operator verification. Stop concurrent DB writers.
# Run after checking out this PR's branch. This script never pulls or changes refs.
param(
  [string]$Repo = "C:\Users\Juan Estrada\Projects\SignalLens",
  [switch]$SkipOfflineTests,
  [switch]$InspectOnly
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
skip_tests = len(sys.argv) > 4 and sys.argv[4] == '1'
inspect_only = len(sys.argv) > 5 and sys.argv[5] == '1'
decisions = ('2026-10-04T21:30:00+00:00', '2026-10-05T00:30:00+00:00')
events = []
failed = False
before = {}
active = 'setup'
PARSE_FAILED = object()
PUBLIC_CODES = frozenset(('TRACK_B_GAP_DIAGNOSTIC_FAILED', 'TRACK_B_HISTORY_INVENTORY_FAILED',
    'INVESTMENT_RESEARCH_NOT_READY', 'INVESTMENT_RESEARCH_INTERNAL_ERROR'))

def emit(stage, reason, **counts):
    # Stage/reason strings come only from fixed call sites and assertion markers.
    # Never serialize exception text, records, file names, stderr or report values.
    events.append({'stage': stage, 'reason_code': reason, 'counts': counts})

def step(name):
    global active
    active = name

def fingerprint(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024*1024), b''):
            digest.update(block)
    return digest.hexdigest()

def save(path, payload):
    path.write_text(json.dumps(payload, sort_keys=True, separators=(',',':')), encoding='utf-8')

class SchemaFailure(Exception):
    pass

def require(value, kind):
    if type(value) is not kind:
        raise SchemaFailure()

def schema(r):
    step('root_schema')
    require(r, dict)
    for key in ('command','decision_at'):
        require(r[key], str)
    for key in ('read_only','metadata_only','realized_outcome_values_read','model_executed',
        'panel_persisted','acquisition_authorized','approved_eligibility','preregistration_ready',
        'accounting_rules_changed','missing_inputs_are_zero'):
        require(r[key], bool)
    for key in ('estimated_provider_requests','validation_credit','unresolved_requirement_count','compact_utf8_bytes'):
        require(r[key], int)
    for key in ('scores','rankings','candidates','recommendations','allocations','selections',
        'paper_selections','vintages','prospective_vintages','validation_observations','blockers'):
        require(r[key], list)
    step('blockers_schema')
    for blocker in r['blockers']:
        require(blocker, dict)
        require(blocker['state'], str)
    step('bounds_schema')
    require(r['bounds'], dict)
    require(r['bounds']['maximum_compact_utf8_bytes'], int)
    step('identity_schema')
    identity = r['reconciliation']
    require(identity, dict)
    for key in ('comparable_roster_count','stored_security_id_count','outside_perimeter_count'):
        require(identity[key], int)
    require(identity['roster_counts'], dict)
    for key in ('matched','ambiguous','unmatched'):
        require(identity['roster_counts'][key], int)
    require(identity['stored_partition'], dict)
    for key in ('matched_roster','ambiguous_roster','outside_perimeter_by_current_classification',
                'outside_roster_perimeter_unresolved'):
        require(identity['stored_partition'][key], int)
    require(identity['roster_details'], list)
    for key in ('approved_eligibility','historical_membership_certified','effective_identity_certified'):
        require(identity[key], bool)
    step('fingerprints_schema')
    require(r['database_fingerprints'], dict)
    for name in paths:
        fp = r['database_fingerprints'][name]
        require(fp, dict)
        require(fp['unchanged'], bool)
        for direction in ('before','after'):
            require(fp[direction], dict)
            require(fp[direction]['sha256'], str)
    step('accounting_schema')
    require(r['databases'], dict)
    for name in paths:
        database = r['databases'][name]
        require(database, dict)
        require(database['layers'], dict)
        for key in ('raw_sec','canonical'):
            layer = database['layers'][key]
            require(layer, dict)
            require(layer['population_denominator'], int)
            require(layer['families'], dict)
            for family in ('value','financial_strength'):
                require(layer['families'][family], dict)
                require(layer['families'][family]['certified_formula_count'], int)
            sample = layer['company_issue_samples']
            require(sample, dict)
            require(sample['items'], list)
            for field in ('total_count','returned_count','sample_limit'):
                require(sample[field], int)
            require(sample['truncated'], bool)
        step('coverage_schema')
        require(database['raw_canonical_coverage'], dict)
        for counts in database['raw_canonical_coverage'].values():
            require(counts, dict)
            values = [counts[k] for k in ('raw_and_canonical','raw_only','canonical_only','neither')]
            if not (all(v is None for v in values) or all(type(v) is int for v in values)):
                raise SchemaFailure()
        step('accounting_schema')
    step('completion_schema')
    require(r['controlled_sec_retrievals'], dict)
    if 'repeat_retrieval_recommended' in r['controlled_sec_retrievals']:
        require(r['controlled_sec_retrievals']['repeat_retrieval_recommended'], bool)
def verify(r, decision):
    step('command')
    assert r['command']=='track-b-identity-accounting-gap-diagnostic'
    step('decision_boundary')
    assert r['decision_at']==decision
    step('read_only_metadata')
    assert r['read_only'] and r['metadata_only']
    step('no_outcomes_or_model')
    assert not r['realized_outcome_values_read'] and not r['model_executed']
    step('no_persistence_or_acquisition')
    assert not r['panel_persisted'] and not r['acquisition_authorized']
    step('no_eligibility_or_readiness')
    assert not r['approved_eligibility'] and not r['preregistration_ready']
    step('unchanged_accounting')
    assert not r['accounting_rules_changed'] and not r['missing_inputs_are_zero']
    step('unselected_contracts')
    assert r['contract_selected'] is None and r['sample_thresholds'] is None
    step('zero_requests_and_credit')
    assert r['estimated_provider_requests']==r['validation_credit']==0
    step('eight_blockers')
    assert r['unresolved_requirement_count']==len(r['blockers'])==8
    step('unresolved_blockers')
    assert all(b['state']=='unresolved' for b in r['blockers'])
    for key in ('scores','rankings','candidates','recommendations','allocations','selections',
                'paper_selections','vintages','prospective_vintages','validation_observations'):
        step('empty_outputs')
        assert r[key]==[]
    n=len(json.dumps(r,sort_keys=True,separators=(',',':')).encode('utf-8'))
    step('output_limits')
    assert n==r['compact_utf8_bytes']<=r['bounds']['maximum_compact_utf8_bytes']==131072
    identity=r['reconciliation']
    step('roster_partition')
    assert sum(identity['roster_counts'].values())==identity['comparable_roster_count']
    step('stored_partition')
    assert sum(identity['stored_partition'].values())==identity['stored_security_id_count']
    step('roster_detail_bound')
    assert len(identity['roster_details'])==identity['comparable_roster_count']<=256
    step('unapproved_identity')
    assert not identity['approved_eligibility'] and not identity['historical_membership_certified']
    step('uncertified_effective_identity')
    assert not identity['effective_identity_certified']
    matched=identity['roster_counts']['matched']
    for name in paths:
        fp=r['database_fingerprints'][name]
        step('internal_fingerprints')
        assert fp['unchanged'] and fp['before']==fp['after']
        step('external_fingerprint_match')
        assert fp['before']['sha256']==before[name]
        for layer in r['databases'][name]['layers'].values():
            step('layer_denominator')
            assert layer['population_denominator']==matched
            step('uncertified_formulas')
            assert all(f['certified_formula_count']==0 for f in layer['families'].values())
            sample=layer['company_issue_samples']
            step('sample_population')
            assert sample['total_count']==matched
            step('sample_return_bound')
            assert sample['returned_count']==len(sample['items'])<=sample['sample_limit']==10
            step('sample_truncation')
            assert sample['truncated']==(sample['total_count']>sample['returned_count'])
        for counts in r['databases'][name]['raw_canonical_coverage'].values():
            values=[counts[k] for k in ('raw_and_canonical','raw_only','canonical_only','neither')]
            step('coverage_partition')
            assert all(v is None for v in values) or sum(values)==matched
    completed=r['controlled_sec_retrievals']
    step('no_repeat_recommendation')
    assert not completed.get('repeat_retrieval_recommended',False)
    return n
def capture(stage, command, prefix):
    try:
        result = subprocess.run(command, capture_output=True)
    except Exception:
        emit(stage, 'COMMAND_LAUNCH_FAILED')
        return None
    try:
        for suffix, data in (('.stdout.txt',result.stdout),('.stderr.txt',result.stderr)):
            pathlib.Path(str(prefix)+suffix).write_text(data.decode('utf-8',errors='replace'),encoding='utf-8')
    except Exception:
        emit(stage+'.capture', 'CAPTURE_SAVE_FAILED')
        return None
    emit(stage, 'COMMAND_CAPTURED', exit_code=result.returncode,
         stdout_bytes=len(result.stdout), stderr_bytes=len(result.stderr))
    if result.returncode:
        emit(stage, 'COMMAND_NONZERO')
        try:
            # Parse only the small public error envelope. Never print its message.
            if len(result.stderr)>4096:
                raise ValueError()
            error=json.loads(result.stderr.decode('utf-8'))
            code=error.get('error',{}).get('code')
            emit(stage+'.public_error', code if code in PUBLIC_CODES else 'PUBLIC_ERROR_UNRECOGNIZED')
        except Exception:
            emit(stage+'.public_error', 'PUBLIC_ERROR_PARSE_FAILED')
        return None
    return result

def parse(stage, payload, saved=False):
    try:
        text=payload.decode('utf-8-sig' if saved else 'utf-8')
    except UnicodeDecodeError:
        emit(stage, 'UTF8_DECODE_FAILED')
        return PARSE_FAILED
    try:
        return json.loads(text)
    except (ValueError,RecursionError):
        emit(stage, 'JSON_PARSE_FAILED')
        return PARSE_FAILED

def validate(stage, report, decision, saved=False):
    global before
    original_before=before
    try:
        schema(report)
        if saved:
            # Inspect an old report against its own recorded baseline, not a
            # later DB state. External current hashes still run independently.
            before={name:report['database_fingerprints'][name]['before']['sha256'] for name in paths}
            emit(stage+'.input', 'SAVED_REPORT_BASELINE_CONTEXT',
                 hashes_matching_current=sum(before[name]==original_before.get(name) for name in paths))
        size=verify(report,decision)
        emit(stage+'.validate', 'REPORT_ASSERTIONS_PASSED',compact_utf8_bytes=size)
        return True
    except AssertionError:
        emit(stage+'.validate.'+active,
             'OUTPUT_LIMIT_OR_SIZE_FAILED' if active=='output_limits' else 'REPORT_INVARIANT_FAILED')
    except (SchemaFailure,KeyError,TypeError,AttributeError,ValueError):
        emit(stage+'.schema.'+active, 'REPORT_SCHEMA_INVALID')
    except Exception:
        emit(stage+'.validate', 'VALIDATOR_INTERNAL_FAILED')
    finally:
        before=original_before
    return False

try:
    for name,path in paths.items():
        try:
            before[name]=fingerprint(path)
            emit(name+'.external.before','FINGERPRINT_OK')
        except Exception:
            failed=True
            emit(name+'.external.before','FINGERPRINT_READ_FAILED')
    tests_ok=True
    if skip_tests or inspect_only:
        emit('offline_tests','OFFLINE_TESTS_SKIPPED')
    else:
        tests=('test_track_b_gap_verification.py','test_track_b_gaps.py','test_track_b_history.py','test_track_b_history_market_stream.py',
               'test_track_b_history_diagnostic.py','test_track_b_panel.py',
               'test_financial_strength.py','test_liquidity_evidence.py','test_sec_liquidity_ingestion.py')
        try:
            result=subprocess.run([sys.executable,'-m','pytest', *('tests/'+t for t in tests), '-q'],capture_output=True)
            tests_ok=result.returncode==0
            emit('offline_tests','OFFLINE_TESTS_PASSED' if tests_ok else 'OFFLINE_TESTS_FAILED')
        except Exception:
            tests_ok=False
            emit('offline_tests','OFFLINE_TESTS_LAUNCH_FAILED')
        failed=failed or not tests_ok
    if len(before)==2 and tests_ok:
        reports.mkdir(parents=True,exist_ok=True)
        for index,decision in enumerate(decisions):
            stage='boundary_'+str(index)
            try:
                target=reports/('track-b-gaps-'+str(index)+'.json')
                if inspect_only:
                    if not target.exists():
                        failed=True
                        emit(stage+'.input','SAVED_REPORT_MISSING')
                        continue
                    payload=target.read_bytes()
                    emit(stage+'.input','SAVED_REPORT_FOUND',bytes=len(payload))
                else:
                    command=[sys.executable,'-m','app.investment_research_cli',
                        'track-b-identity-accounting-gap-diagnostic','--research-db',str(paths['research']),
                        '--production-db',str(paths['production']),'--decision-at',decision]
                    result=capture(stage+'.command',command,reports/('track-b-gaps-'+str(index)))
                    if result is None:
                        failed=True
                        continue
                    payload=result.stdout
                report=parse(stage+'.parse',payload,saved=inspect_only)
                if report is PARSE_FAILED:
                    failed=True
                    continue
                if not validate(stage,report,decision,saved=inspect_only):
                    failed=True
                    continue
                if inspect_only:
                    emit(stage,'SAVED_REPORT_INSPECTED')
                    continue
                repeat=capture(stage+'.repeat.command',command,reports/('track-b-gaps-'+str(index)+'.repeat'))
                if repeat is None:
                    failed=True
                    continue
                if repeat.stdout!=result.stdout:
                    failed=True
                    emit(stage+'.repeat','DETERMINISTIC_REPEAT_MISMATCH',
                         first_stdout_bytes=len(result.stdout),repeat_stdout_bytes=len(repeat.stdout))
                    continue
                try:
                    save(target,report)
                except Exception:
                    failed=True
                    emit(stage+'.save','REPORT_SAVE_FAILED')
                    continue
                emit(stage,'DIAGNOSTIC_VERIFIED',compact_utf8_bytes=report['compact_utf8_bytes'])
            except Exception:
                failed=True
                emit(stage,'BOUNDARY_INTERNAL_FAILED')
except Exception:
    failed=True
    emit('verification','VERIFICATION_INTERNAL_FAILED')
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
        filename='track-b-gaps-inspection.json' if inspect_only else 'track-b-gaps-verification.json'
        save(reports/filename,{'events':events,'failed':failed})
    except Exception:
        failed=True
        emit('reports','REPORT_SAVE_FAILED')
print(json.dumps({'events':events,'failed':failed},sort_keys=True,separators=(',',':')))
sys.exit(int(failed))
'@
    $Skip = if ($SkipOfflineTests) { "1" } else { "0" }
    $Inspect = if ($InspectOnly) { "1" } else { "0" }
    & $Python -X utf8 -c $Verify $Research $Production $Reports $Skip $Inspect 2>$null
    if ($LASTEXITCODE -ne 0) { throw "TRACK_B_GAPS_VERIFICATION_FAILED" }
  } finally {
    if ($Pushed) { Pop-Location }
  }
}
