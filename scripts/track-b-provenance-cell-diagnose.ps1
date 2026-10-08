# Focused original-commit diagnostic. No pytest, provider calls or DB writes.
& {
    $ErrorActionPreference = "Stop"
    $Repo = "C:\Users\Juan Estrada\Projects\SignalLens"
    $Python = Join-Path $Repo ".venv\Scripts\python.exe"
    $Research = Join-Path $Repo "backend\data\research\signallens-research.duckdb"
    $Production = Join-Path $Repo "backend\data\signallens.duckdb"
    $Reports = Join-Path $Repo "backend\data\research\reports"
    $Report = Join-Path $Reports "track-b-provenance-cell-diagnostic.json"
    $PreviousEncoding = $OutputEncoding
    $Utf8 = New-Object System.Text.UTF8Encoding($false)
    $Before = @{}
    $After = @{}
    $Errors = New-Object System.Collections.Generic.List[string]
    $ExitCode = 1
    function Get-DatabaseFingerprint([string]$Path) {
        $Item = Get-Item -LiteralPath $Path -ErrorAction Stop
        if ($Item.PSIsContainer) { throw "Not a file" }
        return @{sha256=(Get-FileHash -LiteralPath $Path -Algorithm SHA256 -ErrorAction Stop).Hash.ToLowerInvariant(); bytes=$Item.Length}
    }
    try {
        foreach ($Name in @("research", "production")) {
            try {
                $Path = if ($Name -eq "research") { $Research } else { $Production }
                $Before[$Name] = Get-DatabaseFingerprint $Path
            } catch { $Errors.Add("PRE_HASH_" + $Name.ToUpperInvariant()) }
        }
        New-Item -ItemType Directory -Path $Reports -Force | Out-Null
        [System.IO.File]::WriteAllText($Report, '{"execution_state":"failed","errors":["NOT_COMPLETED"],"operator_replay_succeeded":false}', $Utf8)
        if ($Errors.Count -gt 0) { throw "Baseline hashes unavailable" }
        $OutputEncoding = $Utf8
        $Diagnostic = @'
import hashlib, json, os, pathlib, sys

# Exact ordered sec_facts projection of failed commit 43450e7; not payload fields.
FIELDS = tuple(dict.fromkeys((
 'fact_key','security_id','cik','taxonomy','concept','unit','currency','scale',
 'period_start','period_end','fiscal_year','fiscal_period','frame','form',
 'accession_number','filed_date','public_at','retrieved_at','is_amendment','is_revision',
 'source_endpoint','parser_contract_version','operation_type','operation_contract_version',
 'concept_contract_hash','ingestion_run_id','ingestion_plan_id',
 'fiscal_year_start','fiscal_quarter','fiscal_quarter_end_1','fiscal_quarter_end_2',
 'fiscal_quarter_end_3','fiscal_quarter_end_4','fiscal_calendar_source','duration_kind',
 'context_scope','context_dimensions','accounting_basis','context_source',
 'revision_set_id','revision_status','revision_source','source_decimals','precision_source',
 'component_members','borrowing_universe_members','borrowing_scope_source',
 'fiscal_metadata_available_at','context_metadata_available_at','revision_metadata_available_at',
 'precision_metadata_available_at','borrowing_metadata_available_at')))
BLOCKERS = (
 'accounting_contract_unapproved','historical_universe_and_identity_unproven',
 'sample_policy_and_minima_unapproved','aligned_history_and_staleness_unproven',
 'outcomes_and_benchmark_unapproved','execution_costs_unapproved',
 'temporal_splits_and_inference_unapproved','registration_and_holdout_controls_unlocked')
MAX_ROWS=500000
CELL_LIMIT=1024
SAMPLES=8
REPORT_LIMIT=131072


def fingerprint(path):
    digest=hashlib.sha256();size=0
    with path.open('rb') as stream:
        for block in iter(lambda:stream.read(1048576),b''):
            digest.update(block);size+=len(block)
    return {'sha256':digest.hexdigest(),'bytes':size}


def inspect(db):
    table=db.execute("SELECT table_type FROM information_schema.tables WHERE table_catalog=current_database() AND table_schema='main' AND table_name='sec_facts'").fetchone()
    if table is None:return {'state':'absent_evidence','offending_columns':[]}
    if table[0]!='BASE TABLE':raise RuntimeError('UNSUPPORTED_TABLE')
    count=db.execute('SELECT count(*) FROM sec_facts').fetchone()[0]
    if count>MAX_ROWS:raise RuntimeError('METADATA_ROW_LIMIT')
    columns={row[0] for row in db.execute("SELECT column_name FROM information_schema.columns WHERE table_catalog=current_database() AND table_schema='main' AND table_name='sec_facts'").fetchall()}
    selected=[field for field in FIELDS if field in columns]
    findings=[]
    for field in selected:
        # Only aggregate counts and exact cell lengths escape SQL. No rejected text.
        predicate='length(CAST("'+field+'" AS VARCHAR))>'+str(CELL_LIMIT)
        stats=db.execute('SELECT count(*),min(length(CAST("'+field+'" AS VARCHAR))),max(length(CAST("'+field+'" AS VARCHAR))) FROM sec_facts WHERE '+predicate).fetchone()
        if not stats[0]:continue
        lengths=db.execute('SELECT length(CAST("'+field+'" AS VARCHAR)),octet_length(encode(CAST("'+field+'" AS VARCHAR))) FROM sec_facts WHERE '+predicate+' ORDER BY 1 DESC,2 DESC LIMIT 8').fetchall()
        findings.append({'read_stage':'research.sec_facts','table':'sec_facts',
            'projected_column':field,'rejected_cell_count':stats[0],
            'min_characters':stats[1],'max_characters':stats[2],
            'cell_lengths':[{'characters':chars,'utf8_bytes':size} for chars,size in lengths],
            'sample_truncated':stats[0]>SAMPLES,'rejected_values_returned':False})
    return {'state':'diagnosed','table_rows':count,'projected_columns':selected,
        'offending_columns':findings,'cell_limit_reproduced':bool(findings),
        'payload_json_projected':False,'values_returned':False}


def diagnose(paths,reports):
    report={'command':'track-b-provenance-cell-diagnostic','status':'proposed_not_authorized',
        'execution_state':'failed','failed_operator_commit':'43450e7309569fa11bc073ee797cbf63181bdc86',
        'expected_read_stage':'research.sec_facts','expected_table':'sec_facts',
        'operator_replay_succeeded':False,'recovery_performed':False,'offline_tests':'skipped',
        'provider_requests':0,'database_writes':False,'model_outputs':[],
        'blockers':[{'code':code,'state':'unresolved'} for code in BLOCKERS],
        'unresolved_requirement_count':8,'errors':[],
        'bounds':{'table_rows':MAX_ROWS,'metadata_cell_characters':CELL_LIMIT,
                  'length_samples_per_column':SAMPLES,'report_bytes':REPORT_LIMIT}}
    before={};after={};stage='PRE_HASH'
    try:
        for name,path in paths.items():
            try:before[name]=fingerprint(path)
            except Exception:report['errors'].append('PRE_HASH_'+name.upper())
        if len(before)!=2:raise RuntimeError('PRE_HASH_FAILED')
        stage='PATH_VALIDATION'
        for path in paths.values():
            if path.is_symlink() or not path.is_file() or path.stat().st_nlink!=1:raise RuntimeError('PATH_INVALID')
        if paths['research'].resolve()==paths['production'].resolve():raise RuntimeError('DATABASES_NOT_DISTINCT')
        stage='READ_RESEARCH_SEC_FACTS_LENGTHS'
        import duckdb
        config={'memory_limit':'128MB','threads':'1','temp_directory':'','max_temp_directory_size':'0B'}
        with duckdb.connect(str(paths['research']),read_only=True,config=config) as db:
            db.execute('SET enable_external_access=false')
            report['diagnostic']=inspect(db)
        report['execution_state']='completed'
    except Exception:
        report['errors'].append(stage)
    finally:
        for name,path in paths.items():
            try:after[name]=fingerprint(path)
            except Exception:report['errors'].append('POST_HASH_'+name.upper())
        report['database_hashes']={}
        for name in paths:
            unchanged=name in before and name in after and before[name]==after[name]
            report['database_hashes'][name]={'before':before.get(name),'after':after.get(name),'unchanged':unchanged}
            if not unchanged:report['errors'].append('HASH_NOT_VERIFIED_'+name.upper())
        if report['errors']:report['execution_state']='failed'
        encoded=json.dumps(report,sort_keys=True,separators=(',',':')).encode('utf-8')
        if len(encoded)>REPORT_LIMIT:raise RuntimeError('REPORT_SIZE_LIMIT')
        reports.mkdir(parents=True,exist_ok=True)
        target=reports/'track-b-provenance-cell-diagnostic.json'
        temporary=target.with_name(target.name+'.tmp');temporary.write_bytes(encoded)
        os.replace(str(temporary),str(target))
    return report


if __name__=='__main__':
    result=diagnose({'research':pathlib.Path(sys.argv[1]),'production':pathlib.Path(sys.argv[2])},pathlib.Path(sys.argv[3]))
    print(str(pathlib.Path(sys.argv[3])/'track-b-provenance-cell-diagnostic.json'))
    sys.exit(0 if result['execution_state']=='completed' else 1)
'@
        $Diagnostic | & $Python -X utf8 - $Research $Production $Reports
        $ExitCode = $LASTEXITCODE
        if ($ExitCode -ne 0) { $Errors.Add("PYTHON_DIAGNOSTIC_FAILED") }
    } catch { $Errors.Add("POWERSHELL_DIAGNOSTIC_FAILED") }
    finally {
        foreach ($Name in @("research", "production")) {
            try {
                $Path = if ($Name -eq "research") { $Research } else { $Production }
                $After[$Name] = Get-DatabaseFingerprint $Path
            } catch { $Errors.Add("POST_HASH_" + $Name.ToUpperInvariant()) }
        }
        $Hashes = @{}
        foreach ($Name in @("research", "production")) {
            $Same = $Before.ContainsKey($Name) -and $After.ContainsKey($Name)
            if ($Same) { $Same = ($Before[$Name].sha256 -eq $After[$Name].sha256) -and ($Before[$Name].bytes -eq $After[$Name].bytes) }
            $Hashes[$Name] = @{before=$Before[$Name]; after=$After[$Name]; unchanged=$Same}
            if (-not $Same) { $Errors.Add("HASH_NOT_VERIFIED_" + $Name.ToUpperInvariant()) }
        }
        $Cleanup = @{execution_state=$(if ($ExitCode -eq 0 -and $Errors.Count -eq 0) { "completed" } else { "failed" }); database_hashes=$Hashes; errors=@($Errors.ToArray())}
        try {
            New-Item -ItemType Directory -Path $Reports -Force | Out-Null
            $Saved = Get-Content -LiteralPath $Report -Raw -Encoding UTF8 | ConvertFrom-Json
            $Saved | Add-Member -NotePropertyName powershell_cleanup -NotePropertyValue $Cleanup -Force
            if ($Cleanup.execution_state -eq "failed") { $Saved.execution_state = "failed" }
            [System.IO.File]::WriteAllText($Report, ($Saved | ConvertTo-Json -Depth 14), $Utf8)
        } catch { $Errors.Add("REPORT_SAVE_FAILED"); Write-Warning "Could not save diagnostic cleanup report." }
        $OutputEncoding = $PreviousEncoding
    }
    if ($ExitCode -ne 0 -or $Errors.Count -gt 0) { throw "Diagnostic failed; inspect track-b-provenance-cell-diagnostic.json." }
}
