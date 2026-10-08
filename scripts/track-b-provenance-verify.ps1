# Proposed read-only Step 1/2 pilot. Windows PowerShell 5.1; no checkout/provider calls.
# Run with concurrent database writers stopped; hashes do not replace a snapshot.
param(
  [string]$Repo = "C:\Users\Juan Estrada\Projects\SignalLens",
  [string]$ResearchDb = "",
  [string]$ProductionDb = "",
  [string]$Python = "",
  [string]$Reports = "",
  [string]$DecisionAt = "2026-10-05T00:30:00+00:00",
  [ValidateSet("metadata-patterns", "flow-focused")]
  [string]$SelectionMode = "metadata-patterns",
  [switch]$SkipOfflineTests
)
& {
  $ErrorActionPreference = "Stop"
  if (-not $ResearchDb) { $ResearchDb = Join-Path $Repo "backend\data\research\signallens-research.duckdb" }
  if (-not $ProductionDb) { $ProductionDb = Join-Path $Repo "backend\data\signallens.duckdb" }
  if (-not $Python) { $Python = Join-Path $Repo ".venv\Scripts\python.exe" }
  if (-not $Reports) { $Reports = Join-Path $Repo "backend\data\research\reports" }
  $Before = @{}
  $After = @{}
  $Errors = New-Object System.Collections.Generic.List[string]
  $ExitCode = 1
  $PythonCompleted = $false
  $PreviousEncoding = $OutputEncoding
  $Utf8 = New-Object System.Text.UTF8Encoding($false)
  function Database-Fingerprint([string]$Path) {
    $File = Get-Item -LiteralPath $Path -ErrorAction Stop
    if ($File.PSIsContainer) { throw "Not a file" }
    return @{sha256=(Get-FileHash -LiteralPath $Path -Algorithm SHA256 -ErrorAction Stop).Hash.ToLowerInvariant(); bytes=$File.Length}
  }
  try {
    # Independently attempt both pre hashes, even if the first one fails.
    foreach ($Name in @("research", "production")) {
      try {
        $Path = if ($Name -eq "research") { $ResearchDb } else { $ProductionDb }
        $Before[$Name] = Database-Fingerprint $Path
      } catch { $Errors.Add("PRE_HASH_" + $Name.ToUpperInvariant()) }
    }
    if ($Errors.Count -gt 0) { throw "Baseline not verified" }
    New-Item -ItemType Directory -Path $Reports -Force | Out-Null
    $Pending = @{command="track-b-provenance-startup"; status="proposed_not_authorized"; execution_state="failed"; errors=@("NOT_COMPLETED")}
    foreach ($Filename in @("track-b-provenance-replay.json", "track-b-provenance-verification.json")) {
      [System.IO.File]::WriteAllText((Join-Path $Reports $Filename), ($Pending | ConvertTo-Json -Depth 12), $Utf8)
    }
    $OutputEncoding = $Utf8
    $Verify = @'
import hashlib, json, os, pathlib, subprocess, sys, threading

LIMIT=131072
BLOCKERS=('accounting_contract_unapproved','historical_universe_and_identity_unproven',
 'sample_policy_and_minima_unapproved','aligned_history_and_staleness_unproven',
 'outcomes_and_benchmark_unapproved','execution_costs_unapproved',
 'temporal_splits_and_inference_unapproved','registration_and_holdout_controls_unlocked')

def fingerprint(path):
    h=hashlib.sha256();size=0
    with path.open('rb') as stream:
        for block in iter(lambda:stream.read(1048576),b''):h.update(block);size+=len(block)
    return {'sha256':h.hexdigest(),'bytes':size}

def save(path,report):
    encoded=json.dumps(report,sort_keys=True,separators=(',',':'),ensure_ascii=True).encode('utf-8')
    if len(encoded)>LIMIT:raise RuntimeError('REPORT_LIMIT')
    temporary=path.with_name(path.name+'.tmp')
    temporary.write_bytes(encoded);os.replace(str(temporary),str(path))

def execute(argv,cwd):
    process=subprocess.Popen(argv,cwd=str(cwd),stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
    chunks=[];size=0;overflow=False
    def capture():
        nonlocal size,overflow
        while True:
            block=process.stdout.read(4096)
            if not block:break
            size+=len(block)
            if size>LIMIT:
                overflow=True;process.kill();break
            chunks.append(block)
    reader=threading.Thread(target=capture,daemon=True);reader.start()
    try:code=process.wait(timeout=180)
    finally:
        if process.poll() is None:process.kill();process.wait()
        reader.join(timeout=5);process.stdout.close()
    if reader.is_alive() or overflow:raise RuntimeError('COMMAND_OUTPUT_LIMIT')
    return code,b''.join(chunks)

def verify(repo,paths,reports,decision,skip,selection="metadata-patterns"):
    repo=pathlib.Path(repo);paths={k:pathlib.Path(v) for k,v in paths.items()};reports=pathlib.Path(reports)
    result={'command':'track-b-provenance-replay','status':'proposed_not_authorized','execution_state':'failed',
     'errors':['NOT_RUN'],'blockers':[{'code':k,'state':'unresolved'} for k in BLOCKERS],
     'unresolved_requirement_count':8,'provider_requests':0,'model_outputs':[],'recovered_fields_persisted':False}
    verification={'command':'track-b-provenance-verification','status':'proposed_not_authorized','execution_state':'failed',
     'errors':[],'offline_tests':'skipped' if skip else 'not_run','blockers':result['blockers'],
     'unresolved_requirement_count':8}
    before={};after={};next_before=None;next_after=None;next_path=repo/'frontend'/'next-env.d.ts'
    stage='PRE_HASH'
    try:
        for name,path in paths.items():
            try:before[name]=fingerprint(path)
            except Exception:verification['errors'].append('PRE_HASH_'+name.upper())
        if len(before)!=2:raise RuntimeError('PRE_HASH_FAILED')
        stage='INITIAL_REPORT_SAVE'
        reports.mkdir(parents=True,exist_ok=True)
        save(reports/'track-b-provenance-replay.json',result)
        stage='FRONTEND_BASELINE'
        next_before=fingerprint(next_path) if next_path.exists() else {'exists':False}
        if not skip:
            stage='OFFLINE_TESTS'
            code,_=execute([sys.executable,'-m','pytest','tests/test_track_b_provenance_replay.py',
                'tests/test_track_b_provenance_verification.py','tests/test_track_b_provenance_cell_diagnostic.py','tests/test_track_b_provenance_plan_identity.py','tests/test_track_b_provenance_flow.py','-q'],repo/'backend')
            verification['offline_tests']='passed' if code==0 else 'failed'
            if code:raise RuntimeError('TESTS_FAILED')
        stage='REPLAY_COMMAND'
        code,output=execute([sys.executable,'-m','app.track_b_provenance_replay','--research-db',str(paths['research']),
            '--production-db',str(paths['production']),'--decision-at',decision,'--selection-mode',selection],repo/'backend')
        stage='REPLAY_REPORT_VALIDATION'
        candidate=json.loads(output.decode('utf-8'))
        if candidate.get('selection_mode')!=selection:raise RuntimeError('SELECTION_MODE_MISMATCH')
        if candidate['command']!='track-b-provenance-replay' or candidate['status']!='proposed_not_authorized':raise RuntimeError('REPORT_IDENTITY')
        if candidate['blockers']!=result['blockers'] or candidate['unresolved_requirement_count']!=8:raise RuntimeError('BLOCKERS_CHANGED')
        if candidate['provider_requests']!=0 or candidate['model_outputs']!=[]:raise RuntimeError('FORBIDDEN_OUTPUT')
        for key in ('financial_values_compared','derived_calculations','database_writes','aliases_activated',
                    'consumers_changed','recovered_fields_persisted','constructions_approved'):
            if candidate[key] is not False:raise RuntimeError('FORBIDDEN_ACTION')
        result=candidate
        stage='REPLAY_EXECUTION'
        if code or candidate['execution_state']!='completed':raise RuntimeError('REPLAY_FAILED')
        for name in paths:
            record=candidate['database_hashes'][name]
            if record['before']!=before[name] or record['after']!=before[name] or record['unchanged'] is not True:raise RuntimeError('INNER_HASH_MISMATCH')
        verification['execution_state']='completed'
    except Exception:
        verification['errors'].append(stage)
    finally:
        # Neither the research hash nor its exception can suppress the production check.
        for name,path in paths.items():
            try:after[name]=fingerprint(path)
            except Exception:verification['errors'].append('POST_HASH_'+name.upper())
        verification['database_hashes']={}
        for name in paths:
            unchanged=name in before and name in after and before[name]==after[name]
            verification['database_hashes'][name]={'before':before.get(name),'after':after.get(name),'unchanged':unchanged}
            if not unchanged:verification['errors'].append('HASH_NOT_VERIFIED_'+name.upper())
        try:
            next_after=fingerprint(next_path) if next_path.exists() else {'exists':False}
            if next_before is None or next_before!=next_after:verification['errors'].append('FRONTEND_NOT_VERIFIED')
        except Exception:verification['errors'].append('FRONTEND_POST_CHECK_FAILED')
        verification['frontend_next_env']={'before':next_before,'after':next_after,'unchanged':next_before is not None and next_before==next_after}
        if verification['errors']:verification['execution_state']='failed'
        if verification['execution_state']=='failed':
            result['execution_state']='failed'
            result['verifier_errors']=verification['errors']
            if len(json.dumps(result,separators=(',',':')).encode())>LIMIT:
                result={'command':'track-b-provenance-replay','status':'proposed_not_authorized','execution_state':'failed',
                    'errors':['VERIFIER_REPORT_LIMIT'],'verifier_errors':verification['errors'],'blockers':verification['blockers'],
                    'unresolved_requirement_count':8,'database_hashes':verification['database_hashes']}
        for filename,payload in [('track-b-provenance-replay.json',result),('track-b-provenance-verification.json',verification)]:
            try:save(reports/filename,payload)
            except Exception:verification['errors'].append('REPORT_SAVE_FAILED')
        print('track-b-provenance-replay.json; track-b-provenance-verification.json')
    return verification

if __name__=='__main__':
    summary=verify(sys.argv[1],{'research':sys.argv[2],'production':sys.argv[3]},sys.argv[4],sys.argv[5],sys.argv[6]=='True',sys.argv[7])
    sys.exit(0 if summary['execution_state']=='completed' and not summary['errors'] else 1)

'@
    $Verify | & $Python -X utf8 - $Repo $ResearchDb $ProductionDb $Reports $DecisionAt $SkipOfflineTests.IsPresent $SelectionMode
    $ExitCode = $LASTEXITCODE
    $PythonCompleted = $true
    if ($ExitCode -ne 0) { $Errors.Add("PYTHON_VERIFIER_FAILED") }
  } catch { $Errors.Add("POWERSHELL_EXECUTION_FAILED") }
  finally {
    foreach ($Name in @("research", "production")) {
      try {
        $Path = if ($Name -eq "research") { $ResearchDb } else { $ProductionDb }
        $After[$Name] = Database-Fingerprint $Path
      } catch { $Errors.Add("POST_HASH_" + $Name.ToUpperInvariant()) }
    }
    $Hashes = @{}
    foreach ($Name in @("research", "production")) {
      $Unchanged = $Before.ContainsKey($Name) -and $After.ContainsKey($Name)
      if ($Unchanged) { $Unchanged = ($Before[$Name].sha256 -eq $After[$Name].sha256) -and ($Before[$Name].bytes -eq $After[$Name].bytes) }
      $Hashes[$Name] = @{before=$Before[$Name]; after=$After[$Name]; unchanged=$Unchanged}
      if (-not $Unchanged) { $Errors.Add("HASH_NOT_VERIFIED_" + $Name.ToUpperInvariant()) }
    }
    $Summary = @{command="track-b-provenance-powershell-cleanup"; status="proposed_not_authorized";
      execution_state= $(if ($ExitCode -eq 0 -and $Errors.Count -eq 0) { "completed" } else { "failed" });
      database_hashes=$Hashes; errors=@($Errors.ToArray()); unresolved_requirement_count=8;
      blockers=@(@{code="accounting_contract_unapproved";state="unresolved"},
        @{code="historical_universe_and_identity_unproven";state="unresolved"},
        @{code="sample_policy_and_minima_unapproved";state="unresolved"},
        @{code="aligned_history_and_staleness_unproven";state="unresolved"},
        @{code="outcomes_and_benchmark_unapproved";state="unresolved"},
        @{code="execution_costs_unapproved";state="unresolved"},
        @{code="temporal_splits_and_inference_unapproved";state="unresolved"},
        @{code="registration_and_holdout_controls_unlocked";state="unresolved"})}
    try {
      New-Item -ItemType Directory -Path $Reports -Force | Out-Null
      [System.IO.File]::WriteAllText((Join-Path $Reports "track-b-provenance-cleanup.json"), ($Summary | ConvertTo-Json -Depth 12), $Utf8)
      if (-not $PythonCompleted) {
        [System.IO.File]::WriteAllText((Join-Path $Reports "track-b-provenance-verification.json"), ($Summary | ConvertTo-Json -Depth 12), $Utf8)
        [System.IO.File]::WriteAllText((Join-Path $Reports "track-b-provenance-replay.json"), ($Summary | ConvertTo-Json -Depth 12), $Utf8)
      }
    } catch { $Errors.Add("REPORT_SAVE_FAILED"); Write-Warning "Could not save UTF-8 failure report to the reports directory." }
    $OutputEncoding = $PreviousEncoding
  }
  if ($ExitCode -ne 0 -or $Errors.Count -gt 0) { throw "Proposed provenance verifier failed; inspect UTF-8 replay, verification and cleanup reports." }
}
