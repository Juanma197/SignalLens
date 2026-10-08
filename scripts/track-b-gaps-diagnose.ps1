# Focused read-only diagnostics only. No pytest or verification repeat.
param([string]$Repo = 'C:\Users\Juan Estrada\Projects\SignalLens', [switch]$CellLengthsOnly)
& {
  $ErrorActionPreference = 'Stop'
  $Python = Join-Path $Repo '.venv\Scripts\python.exe'
  $Backend = Join-Path $Repo 'backend'
  $Research = Join-Path $Backend 'data\research\signallens-research.duckdb'
  $Production = Join-Path $Backend 'data\signallens.duckdb'
  $Reports = Join-Path $Backend 'data\research\reports'
  $PreviousEncoding = $OutputEncoding
  $Pushed = $false
  try {
    $OutputEncoding = New-Object System.Text.UTF8Encoding($false)
    Push-Location $Backend
    $Pushed = $true
    $Diagnostic = @'
import hashlib, json, pathlib, subprocess, sys
from app.track_b_gap_diagnostic import STAGES, REASONS, COUNT_KEYS, MAX_EVENTS, MAX_BYTES, PUBLIC_CODES

paths = {'research': pathlib.Path(sys.argv[1]), 'production': pathlib.Path(sys.argv[2])}
reports = pathlib.Path(sys.argv[3])
cell_lengths_only = len(sys.argv)>4 and sys.argv[4]=='1'
decisions = ('2026-10-04T21:30:00+00:00', '2026-10-05T00:30:00+00:00')
events, before = [], {}
failed = False

def emit(stage, reason, **counts):
    events.append({'stage':stage, 'reason_code':reason, 'counts':counts})

def fingerprint(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024), b''):
            digest.update(block)
    return digest.hexdigest()

def save(path, value):
    path.write_text(json.dumps(value,sort_keys=True,separators=(',',':')),encoding='utf-8')

def execute(args, prefix):
    # File redirection retains failed output without echoing private text.
    out, err = reports/(prefix+'.stdout.txt'), reports/(prefix+'.stderr.txt')
    with out.open('wb') as stdout, err.open('wb') as stderr:
        process = subprocess.run([sys.executable,'-X','utf8','-m']+args,
            stdout=stdout,stderr=stderr,check=False)
    return process.returncode, out, err

def read_bounded(path, limit):
    if path.stat().st_size > limit:
        raise ValueError()
    return path.read_bytes().decode('utf-8')

try:
    reports.mkdir(parents=True,exist_ok=True)
    for name,path in paths.items():
        try:
            before[name]=fingerprint(path)
            emit(name+'.external.before','FINGERPRINT_OK')
        except Exception:
            failed=True
            emit(name+'.external.before','FINGERPRINT_READ_FAILED')
    if len(before)==2 and cell_lengths_only:
        from app import track_b_gaps as g
        from app.track_b_gap_diagnostic import Trace
        from app.investment_research import public_error_code
        import duckdb
        trace=Trace()
        token=g._TRACE.set(trace)
        try:
            g.h.validate_paths(paths['research'],paths['production'])
            g._mark('research.connect')
            with duckdb.connect(str(paths['research']),read_only=True,config=g.h._sql_config()) as db:
                g._read(db,'sec_liquidity_runs','research')
        except Exception as exc:
            failed=True
            code=public_error_code(exc)
            emit('cell_lengths.public',code if code in PUBLIC_CODES else 'INVESTMENT_RESEARCH_INTERNAL_ERROR')
        finally:
            g._TRACE.reset(token)
        for event in trace.events:
            emit('cell_lengths.'+event['stage'],event['reason_code'],**event['counts'])
    elif len(before)==2:
        for index,decision in enumerate(decisions):
            stage='boundary_'+str(index)
            args=['--research-db',str(paths['research']),'--production-db',str(paths['production']),
                  '--decision-at',decision]
            try:
                code,out,err=execute(['app.investment_research_cli',
                    'track-b-identity-accounting-gap-diagnostic']+args,'track-b-gaps-focused-'+str(index)+'-public')
                emit(stage+'.command','COMMAND_COMPLETED' if code==0 else 'COMMAND_FAILED',
                    exit_code=code,stdout_bytes=out.stat().st_size,stderr_bytes=err.stat().st_size)
                if code!=0: failed=True
                try:
                    public=json.loads(read_bounded(err,4096))['error']['code']
                    if public in PUBLIC_CODES: emit(stage+'.public',public)
                except Exception:
                    if code!=0: emit(stage+'.public','PUBLIC_ERROR_UNPARSED')
            except Exception:
                failed=True
                emit(stage+'.command','COMMAND_LAUNCH_OR_CAPTURE_FAILED')
            try:
                code,out,err=execute(['app.track_b_gap_diagnostic']+args,
                    'track-b-gaps-focused-'+str(index)+'-internal')
                emit(stage+'.internal.command','COMMAND_COMPLETED' if code==0 else 'COMMAND_FAILED',
                    exit_code=code,stdout_bytes=out.stat().st_size,stderr_bytes=err.stat().st_size)
                if code!=0: failed=True
            except Exception:
                failed=True
                emit(stage+'.internal.command','COMMAND_LAUNCH_OR_CAPTURE_FAILED')
                continue
            try:
                text=read_bounded(out,MAX_BYTES+1)  # JSON budget plus print newline.
            except Exception:
                failed=True
                emit(stage+'.internal.output','OUTPUT_LIMIT_OR_UTF8_FAILED')
                continue
            try:
                report=json.loads(text)
            except Exception:
                failed=True
                emit(stage+'.internal.json','JSON_PARSE_FAILED')
                continue
            try:
                assert report['command']=='track-b-gap-internal-diagnostic'
                assert report['read_only'] is True and report['metadata_only'] is True
                assert report['operator_verification_successful'] is False
                assert type(report['diagnostic_completed']) is bool
                assert report['public_error_code'] is None or report['public_error_code'] in PUBLIC_CODES
                assert type(report['events']) is list and len(report['events'])<=MAX_EVENTS
                for event in report['events']:
                    assert set(event)=={'stage','reason_code','counts'}
                    assert event['stage'] in STAGES and event['reason_code'] in REASONS
                    assert type(event['counts']) is dict
                    assert all(k in COUNT_KEYS and type(v) is int and 0<=v<=2**63-1
                        for k,v in event['counts'].items())
            except Exception:
                failed=True
                emit(stage+'.internal.schema','SAFE_SCHEMA_FAILED')
                continue
            save(reports/('track-b-gaps-focused-'+str(index)+'-internal.json'),report)
            for event in report['events']:
                emit(stage+'.'+event['stage'],event['reason_code'],**event['counts'])
            if not report['diagnostic_completed']: failed=True
except Exception:
    failed=True
    emit('focused','FOCUSED_SETUP_OR_SAVE_FAILED')
finally:
    # Always attempt BOTH hashes independently, including after command failure.
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
    summary={'events':events,'diagnostic_failed':failed,'operator_verification_successful':False,
        'pytest_run':False,'deterministic_repeat_run':False,'cell_lengths_only':cell_lengths_only}
    try:
        save(reports/('track-b-gaps-cell-lengths-summary.json' if cell_lengths_only
                     else 'track-b-gaps-focused-summary.json'),summary)
    except Exception:
        failed=True
        summary['diagnostic_failed']=True
        emit('reports','SUMMARY_SAVE_FAILED')
print(json.dumps(summary,sort_keys=True,separators=(',',':')))
sys.exit(int(failed))
'@
    $Cells = if ($CellLengthsOnly) { '1' } else { '0' }
    $Diagnostic | & $Python -X utf8 - $Research $Production $Reports $Cells 2>$null
    if ($LASTEXITCODE -ne 0) { throw 'TRACK_B_FOCUSED_DIAGNOSTIC_FAILED' }
  } finally {
    if ($Pushed) { Pop-Location }
    $OutputEncoding = $PreviousEncoding
  }
}
