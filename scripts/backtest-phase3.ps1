# PowerShell 5.1. Backtest phase 3 end to end (docs/backtest-phase3.md): finishes
# verifying delisted companies, asks before the price download, then prices, SEC
# facts, classification, filing events, the 2019-2022 replay, its measurement
# against VT and the selection experiments. Every stage is resumable: rerun the
# script and finished work is skipped. Outputs: backend\data\research\reports\phase3-<time>-*.json
#   .\scripts\backtest-phase3.ps1
#   .\scripts\backtest-phase3.ps1 -SkipDiscover     when discovery is already complete
[CmdletBinding()]
param([switch]$SkipDiscover, [int]$MaxRounds = 5)
$ErrorActionPreference = "Stop"
$Project = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Project ".venv\Scripts\python.exe"
$Backend = Join-Path $Project "backend"
$R = "data\research\signallens-research.duckdb"; $P = "data\signallens.duckdb"; $Replay = "data\research\backtest\replay.duckdb"
$Reports = Join-Path $Backend "data\research\reports"
$Stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$Utf8 = New-Object System.Text.UTF8Encoding -ArgumentList $false
function Stage([string]$Text) { Write-Host ""; Write-Host "== $Text" -ForegroundColor Cyan }

# Runs one Python module from backend\, saves its output and returns it (parsed when JSON).
function Invoke-Step([string]$Name, [string[]]$Arguments, [switch]$Text) {
    $Output = Join-Path $Reports "phase3-$Stamp-$Name.$(if ($Text) { 'txt' } else { 'json' })"
    Push-Location $Backend
    try { $Lines = & $Python -X utf8 @Arguments; $Exit = $LASTEXITCODE } finally { Pop-Location }
    [System.IO.File]::WriteAllText($Output, ($Lines -join [Environment]::NewLine), $Utf8)
    if ($Exit -ne 0) { throw "$Name failed (exit $Exit). Output: $Output. Rerun this script to continue." }
    if ($Text) { return $Lines }
    return (($Lines -join "`n") | ConvertFrom-Json)  # progress lines go to stderr, not here
}
function Show-Counts($Result) {
    $c = $Result.counts
    Write-Host ("  candidates {0}, verified {1}, priced {2}, ready {3}, excluded {4}" -f `
        [int]$c.candidate, [int]$c.mapped, [int]$c.priced, [int]$c.ready, [int]$c.excluded)
}

Stage "Preflight"
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) { throw "Missing $Python" }
if (Get-NetTCPConnection -LocalPort 8015, 3015 -State Listen -ErrorAction SilentlyContinue) {
    throw "The prototype is running (ports 8015/3015). Stop it, then rerun."
}
$Busy = Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -match '-m app\.' }
if ($Busy) { throw "Another SignalLens job is running (PID $($Busy[0].ProcessId)): wait for it to finish; only one process can write the database." }
[void](New-Item -ItemType Directory -Force -Path $Reports, (Join-Path $Backend "data\research\backtest"))
if (-not $env:SIGNALLENS_EODHD_API_TOKEN) {
    $Secure = Read-Host "EODHD API token (hidden)" -AsSecureString
    $env:SIGNALLENS_EODHD_API_TOKEN = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($Secure))
}
if (-not $env:SIGNALLENS_SEC_USER_AGENT) {
    $env:SIGNALLENS_SEC_USER_AGENT = Read-Host "SEC contact for request headers, e.g. 'SignalLens research Jane Doe jane@example.com'"
}
Write-Host "OK. Reports go to $Reports"

try {
    $Paths = @("--research-db", $R, "--production-db", $P)
    if (-not $SkipDiscover) {
        Stage "Finish verifying delisted companies against SEC"
        for ($Round = 1; $Round -le $MaxRounds; $Round++) {
            $Found = Invoke-Step "discover-$Round" (@("-m", "app.delisted", "discover", "--verify-only") + $Paths)
            Show-Counts $Found
            if (-not $Found.stop_reason) { break }
            Write-Host "  stopped at the SEC request budget; continuing"
        }
        if ($Found.stop_reason) { throw "Verification still incomplete after $MaxRounds rounds; rerun the script." }
    }
    $State = Invoke-Step "status" (@("-m", "app.delisted", "status") + $Paths)
    $Pending = [int]$State.counts.mapped
    Write-Host ("Verified delisted companies waiting for prices: {0} (about {1} EODHD requests, {2:N0} minutes at 60 a minute)." -f $Pending, (2 * $Pending), (2 * $Pending / 60))
    if ($Pending -gt 0) {
        $Answer = Read-Host "Download their prices now? Type yes"
        if ($Answer -ne "yes") { Write-Host "Stopped before downloading. Rerun with -SkipDiscover to continue."; exit 0 }
        Stage "Prices of delisted companies"
        for ($Round = 1; $Round -le $MaxRounds; $Round++) {
            $Priced = Invoke-Step "prices-$Round" (@("-m", "app.delisted", "prices") + $Paths)
            Show-Counts $Priced
            if (-not $Priced.stop_reason) { break }
            Write-Host "  stopped ($($Priced.stop_reason)); continuing"
        }
        if ($Priced.stop_reason) { throw "Prices stopped at $($Priced.stop_reason); rerun with -SkipDiscover tomorrow if it is the daily EODHD limit." }
    }

    Stage "SEC facts of delisted companies"
    for ($Round = 1; $Round -le 3; $Round++) {
        $Facts = Invoke-Step "sec-$Round" (@("-m", "app.delisted", "sec") + $Paths)
        Show-Counts $Facts
        if ([int]$Facts.counts.priced -eq 0 -or -not $Facts.ingestion.stop_reason) { break }
    }
    Write-Host ("  last delisting years: {0}" -f (($Facts.last_sessions_by_year.PSObject.Properties | ForEach-Object { "$($_.Name): $($_.Value)" }) -join ", "))

    Stage "Classification (stage 5, about 3 minutes)"
    $Now = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ss+00:00")
    $Mat = Invoke-Step "materialize" (@("-m", "app.investment_research_cli", "materialize-stored-investment-evidence") + $Paths + @("--decision-at", $Now, "--authorization", "I AUTHORIZE RESEARCH-ONLY INVESTMENT EVIDENCE MATERIALIZATION"))
    Write-Host "  failures: $($Mat.failures)"

    Stage "SEC filing events (stage 6)"
    $Events = Invoke-Step "sec-events" (@("-m", "app.sec_events_cli", "ingest-sec-events") + $Paths + @("--authorization", "I AUTHORIZE RESEARCH-ONLY SEC EVENT INGESTION", "--max-requests", "6000", "--runtime-seconds", "14400"))
    Write-Host "  status $($Events.status), requests $($Events.requests)"

    Stage "Replay 2019-2022 with delisted companies (about 45 minutes)"
    $Run = Invoke-Step "replay" @("-m", "app.prototype.replay", "run", "--replay-db", $Replay, "--note", "phase 3: with delisted companies")
    Write-Host "  months $($Run.completed) of $($Run.months), failed $($Run.failed)"

    Stage "Measurement against random picks, VT and SPY"
    $M = Invoke-Step "measure" @("-m", "app.prototype.measure", "--replay-db", $Replay)
    $S = $M.policy.performance; $G = $M.global_index.performance
    Write-Host ("  strategy GBP {0:N0} ({1:P1}/yr time-weighted, worst drop {2:P0}); VT GBP {3:N0} ({4:P1}/yr)" -f $S.final_value_gbp, $S.time_weighted_annual, $S.max_drawdown, $G.final_value_gbp, $G.time_weighted_annual)
    Write-Host ("  12m skill vs random {0:P1} (interval {1:P1} to {2:P1}); delisted company-months {3}" -f `
        $M.picks.'12m'.skill_vs_eligible.mean, $M.picks.'12m'.skill_vs_eligible.low, $M.picks.'12m'.skill_vs_eligible.high, $M.delisted_company_months)
    Write-Host "  verdict: $($M.criteria.verdict)"

    Stage "Selection experiments"
    Invoke-Step "experiments" @("-m", "app.prototype.experiments", "--replay-db", $Replay) -Text | ForEach-Object { Write-Host "  $_" }
    Write-Host ""
    Write-Host "Done. Send Claude the files phase3-$Stamp-measure.json and phase3-$Stamp-experiments.txt." -ForegroundColor Green
}
finally {
    Remove-Item Env:SIGNALLENS_EODHD_API_TOKEN -ErrorAction SilentlyContinue
}
