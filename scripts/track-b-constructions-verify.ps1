# Proposed feasibility only. Windows PowerShell 5.1. Stop concurrent DB writers.
# No pull, branch switch, schema initialization, provider calls or derived evidence.
param(
  [string]$Repo = "C:\Users\Juan Estrada\Projects\SignalLens",
  [string]$ResearchDb = "",
  [string]$ProductionDb = "",
  [string]$Python = "",
  [string]$Reports = "",
  [switch]$SkipOfflineTests
)
& {
  $ErrorActionPreference = "Stop"
  if (-not $ResearchDb) { $ResearchDb = Join-Path $Repo "backend\data\research\signallens-research.duckdb" }
  if (-not $ProductionDb) { $ProductionDb = Join-Path $Repo "backend\data\signallens.duckdb" }
  if (-not $Python) { $Python = Join-Path $Repo ".venv\Scripts\python.exe" }
  if (-not $Reports) { $Reports = Join-Path $Repo "backend\data\research\reports" }
  $PreviousEncoding = $OutputEncoding
  $Pushed = $false
  try {
    $OutputEncoding = New-Object System.Text.UTF8Encoding($false)
    Push-Location (Join-Path $Repo "backend")
    $Pushed = $true
    $Verify = @'
import hashlib, json, os, pathlib, subprocess, sys

MAX_BYTES=131072
BOUNDARIES=('2026-10-04T21:30:00+00:00','2026-10-05T00:30:00+00:00')
BLOCKERS=('accounting_contract_unapproved','historical_universe_and_identity_unproven',
    'sample_policy_and_minima_unapproved','aligned_history_and_staleness_unproven',
    'outcomes_and_benchmark_unapproved','execution_costs_unapproved',
    'temporal_splits_and_inference_unapproved','registration_and_holdout_controls_unlocked')
ARRAYS=('scores','rankings','candidates','recommendations','allocations','selections',
    'paper_selections','vintages','prospective_vintages','validation_observations')

class VerificationFailure(Exception): pass

def fingerprint(path):
    digest=hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):digest.update(block)
    return {'sha256':digest.hexdigest(),'bytes':path.stat().st_size}

def save(path,payload):
    # UTF-8 without BOM; reports only, never database or canonical evidence writes.
    encoded=json.dumps(payload,sort_keys=True,separators=(',',':')).encode('utf-8')
    temporary=path.with_name(path.name+'.tmp')
    temporary.write_bytes(encoded)
    os.replace(str(temporary),str(path))

def require(condition):
    if not condition:raise VerificationFailure()

def counts(value):
    require(isinstance(value,dict))
    require(all(type(v) is int and v>=0 for v in value.values()))

def validate(r,decision,before):
    require(r['command']=='track-b-construction-feasibility' and r['status']=='proposed_not_authorized')
    require(r['decision_at']==decision and r['read_only'] is True and r['metadata_shapes_certify_nothing'] is True)
    require(r['value_signatures_inspected'] is True and r['numeric_source_columns_inspected'] is True)
    for k in ('amounts_returned','derived_amounts_computed','accounting_rules_changed','aliases_activated',
              'consumers_changed','derived_evidence_persisted','panel_persisted','model_executed',
              'realized_outcome_values_read','repeat_retrieval_recommended','preregistration_ready',
              'approved_eligibility','assessment_output_is_historical_evidence'):
        require(r[k] is False)
    require(r['future_persisted_revision_available_at'] is None)
    require(r['bounds']=={'rows_per_table':500000,'total_rows':500000,'cell_characters':1024,
        'rows_per_period_group':50000,'work_per_layer':50000,'report_bytes':131072,
        'sql_memory':'128MB','sql_threads':1,'disk_spill_allowed':False})
    require(r['provider_requests']==r['validation_credit']==0)
    require(r['contract_selected'] is None and r['sample_thresholds'] is None)
    require(r['unresolved_requirement_count']==8)
    require(r['blockers']==[{'code':k,'state':'unresolved'} for k in BLOCKERS])
    for k in ARRAYS:require(r[k]==[])
    from datetime import datetime
    execution=datetime.fromisoformat(r['assessment_executed_at'])
    require(execution.tzinfo is not None and execution>=datetime.fromisoformat(decision))
    identity=r['reconciliation'];denominator=identity['roster_counts']['matched']
    require(sum(identity['roster_counts'].values())==identity['comparable_roster_count'])
    require(sum(identity['stored_partition'].values())==identity['stored_security_id_count'])
    require(identity['approved_eligibility'] is False and identity['effective_identity_certified'] is False
            and identity['historical_membership_certified'] is False)
    require(set(r['database_fingerprints'])==set(before))
    for name,fp in before.items():
        require(r['database_fingerprints'][name]=={'before':fp,'after':fp,'unchanged':True})
        require(set(r['databases'][name])=={'raw_sec','canonical'})
        for layer in r['databases'][name].values():
            if layer['state']=='unsupported_schema':
                require(layer['counts'] is None and layer['reason']=='unknown_not_zero');continue
            require(layer['population_denominator']==denominator and layer['certified_formula_count']==0)
            for key in ('row_counts','visible_rows_by_exact_concept','missing_or_unproven_metadata',
                        'revision_and_shape_diagnostics','flows','borrowing','cash'):counts(layer[key])
            row=layer['row_counts']
            require(row['stored_relevant_rows']==sum(row[k] for k in ('visibility_unproven_rows','post_decision_rows','future_period_rows','visible_relevant_rows')))
            require(sum(layer['visible_rows_by_exact_concept'].values())==row['visible_relevant_rows'])
            cash=layer['cash']
            require(cash['visible_exact_source_cash_rows']==cash['compatible_direct_cash_candidate_rows']+cash['unproven_or_incompatible_direct_cash_rows'])
            debt=layer['borrowing']
            require(debt['borrowing_instant_groups']==debt['unknown_overlap_groups']+debt['proven_overlapping_groups']+debt['proven_disjoint_groups'])
            require(debt['borrowing_instant_groups']==debt['unknown_completeness_groups']+debt['proven_complete_groups']+debt['proven_incomplete_groups'])
            require(layer['work_units']<=r['bounds']['work_per_layer'])
    payload=json.dumps(r,sort_keys=True,separators=(',',':')).encode('utf-8')
    require(len(payload)==r['compact_utf8_bytes']<=MAX_BYTES)

def verify(paths,reports,skip_tests=False):
    events=[];before={};after={};pending=[];failed=False
    def emit(stage,reason):events.append({'stage':stage,'reason_code':reason})
    try:
        for name,path in paths.items():
            try:before[name]=fingerprint(path);emit(name+'.before','HASH_OK')
            except Exception:failed=True;emit(name+'.before','HASH_FAILED')
        tests_ok=True
        if not skip_tests:
            try:
                result=subprocess.run([sys.executable,'-m','pytest','tests/test_track_b_accounting_construction_proposal.py',
                    'tests/test_track_b_construction_feasibility.py','tests/test_track_b_construction_verification.py','-q'],capture_output=True)
                tests_ok=result.returncode==0
                emit('tests','PASSED' if tests_ok else 'FAILED')
            except Exception:tests_ok=False;emit('tests','LAUNCH_FAILED')
        else:emit('tests','SKIPPED')
        failed=failed or not tests_ok
        if not failed:
            for index,decision in enumerate(BOUNDARIES):
                stage='boundary_'+str(index)
                try:
                    command=[sys.executable,'-m','app.track_b_construction_feasibility',
                        '--research-db',str(paths['research']),'--production-db',str(paths['production']),
                        '--decision-at',decision]
                    result=subprocess.run(command,capture_output=True)
                    if result.returncode:emit(stage,'COMMAND_FAILED');failed=True;continue
                    if len(result.stdout)>MAX_BYTES+1:emit(stage,'OUTPUT_LIMIT');failed=True;continue
                    try:r=json.loads(result.stdout.decode('utf-8'))
                    except Exception:emit(stage,'UTF8_OR_JSON_FAILED');failed=True;continue
                    try:validate(r,decision,before)
                    except Exception:emit(stage,'INVARIANT_FAILED');failed=True;continue
                    repeat=subprocess.run(command,capture_output=True)
                    if repeat.returncode:emit(stage,'REPEAT_FAILED');failed=True;continue
                    if len(repeat.stdout)>MAX_BYTES+1:emit(stage,'OUTPUT_LIMIT');failed=True;continue
                    try:rr=json.loads(repeat.stdout.decode('utf-8'));validate(rr,decision,before)
                    except Exception:emit(stage,'REPEAT_INVARIANT_FAILED');failed=True;continue
                    # Execution time changes; it must never be erased from saved reports.
                    a=dict(r);b=dict(rr);a.pop('assessment_executed_at');b.pop('assessment_executed_at')
                    a.pop('compact_utf8_bytes');b.pop('compact_utf8_bytes')
                    if a!=b:emit(stage,'COUNT_REPEAT_MISMATCH');failed=True;continue
                    pending.append((reports/('track-b-constructions-'+str(index)+'.json'),r))
                    emit(stage,'COUNTS_VERIFIED')
                except Exception:failed=True;emit(stage,'STAGE_FAILED')
    except Exception:failed=True;emit('verification','INTERNAL_FAILED')
    finally:
        # Always attempt both after hashes independently, including launch/parse/test failures.
        for name,path in paths.items():
            try:
                after[name]=fingerprint(path)
                if name not in before or after[name]!=before[name]:failed=True;emit(name+'.after','HASH_CHANGED_OR_BASELINE_MISSING')
                else:emit(name+'.after','HASH_UNCHANGED')
            except Exception:failed=True;emit(name+'.after','HASH_FAILED')
        # Never publish a verified diagnostic if either external hash/check failed.
        try:
            reports.mkdir(parents=True,exist_ok=True)
            if not failed:
                for path,r in pending:save(path,r)
        except Exception:failed=True;emit('reports','SAVE_FAILED')
        summary={'events':events,'failed':failed,'status':'proposed_not_authorized',
            'database_fingerprints':{name:{'before':before.get(name),'after':after.get(name),
                'unchanged':name in before and name in after and before[name]==after[name]} for name in paths},
            'provider_requests':0,'derived_evidence_persisted':False,'preregistration_ready':False}
        try:save(reports/'track-b-constructions-verification.json',summary)
        except Exception:failed=True;summary['failed']=True;emit('reports','SUMMARY_SAVE_FAILED')
    return summary

if __name__=='__main__':
    paths={'research':pathlib.Path(sys.argv[1]),'production':pathlib.Path(sys.argv[2])}
    summary=verify(paths,pathlib.Path(sys.argv[3]),len(sys.argv)>4 and sys.argv[4]=='1')
    # Fixed safe stages/codes only. Hashes/reports remain in saved UTF-8 files.
    print(json.dumps({'events':summary['events'],'failed':summary['failed']},sort_keys=True,separators=(',',':')))
    sys.exit(int(summary['failed']))
'@
    $Skip = if ($SkipOfflineTests) { '1' } else { '0' }
    $Verify | & $Python -X utf8 - $ResearchDb $ProductionDb $Reports $Skip 2>$null
    if ($LASTEXITCODE -ne 0) { throw 'TRACK_B_CONSTRUCTION_VERIFICATION_FAILED' }
  } finally {
    if ($Pushed) { Pop-Location }
    $OutputEncoding = $PreviousEncoding
  }
}
