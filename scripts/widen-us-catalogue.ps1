# PowerShell 5.1. Widens the US catalogue to every listing near the size band
# (docs/us-catalogue-widening.md), one stage at a time, stopping on any failure.
# Every stage is resumable: rerunning this script continues where it stopped.
#   .\scripts\widen-us-catalogue.ps1                  full run (asks before changing the catalogue)
#   .\scripts\widen-us-catalogue.ps1 -SkipBackup      when a pre-widening backup already exists
[CmdletBinding()]
param(
    [int]$UsSecurities = 2500,
    [switch]$SkipBackup,
    [switch]$SkipEvents
)
$ErrorActionPreference = "Stop"
$Project = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Project ".venv\Scripts\python.exe"
$Backend = Join-Path $Project "backend"
$Research = Join-Path $Backend "data\research\signallens-research.duckdb"
$Production = Join-Path $Backend "data\signallens.duckdb"
$Reports = Join-Path $Backend "data\research\reports"
$Stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$Utf8 = New-Object System.Text.UTF8Encoding -ArgumentList $false
function Stage([string]$Text) { Write-Host ""; Write-Host "== $Text" -ForegroundColor Cyan }

# Runs one Python module from backend\, saves its output and returns it parsed.
function Invoke-Step([string]$Name, [string[]]$Arguments, [int[]]$Ok = @(0)) {
    $Output = Join-Path $Reports "widen-$Stamp-$Name.json"
    Push-Location $Backend
    try { $Lines = & $Python -X utf8 @Arguments; $Exit = $LASTEXITCODE } finally { Pop-Location }
    [System.IO.File]::WriteAllText($Output, ($Lines -join [Environment]::NewLine), $Utf8)
    if ($Ok -notcontains $Exit) { throw "$Name failed (exit $Exit). Output: $Output. Rerun this script to continue." }
    return (($Lines -join "`n") | ConvertFrom-Json)
}

Stage "Preflight"
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) { throw "Missing $Python" }
foreach ($Path in @($Research, $Production)) { if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw "Database not found: $Path" } }
if (Get-NetTCPConnection -LocalPort 8015, 3015 -State Listen -ErrorAction SilentlyContinue) {
    throw "The prototype is running (ports 8015/3015). Press Enter in its window to stop it, then rerun."
}
[void](New-Item -ItemType Directory -Force -Path $Reports)
Write-Host "OK. Reports go to $Reports"

if (-not $SkipBackup) {
    Stage "Verified backup of the research database"
    $Size = (Get-Item -LiteralPath $Research).Length
    if ((Get-PSDrive -Name ($Research.Substring(0, 1))).Free -lt 3 * $Size) { throw "Not enough free disk for a backup plus growth." }
    $BackupDir = Join-Path $Backend "data\research\backups"
    [void](New-Item -ItemType Directory -Force -Path $BackupDir)
    $Backup = Join-Path $BackupDir "signallens-research-pre-widening-$Stamp.duckdb"
    Copy-Item -LiteralPath $Research -Destination $Backup
    if ((Get-FileHash -Algorithm SHA256 -LiteralPath $Research).Hash -ne (Get-FileHash -Algorithm SHA256 -LiteralPath $Backup).Hash) { throw "Backup hash mismatch." }
    Write-Host "OK: $Backup (to undo everything, copy it back over the research database)"
}

Stage "Credentials (kept in this window only, removed at the end)"
$Secure = Read-Host "EODHD API token (hidden)" -AsSecureString
$Agent = Read-Host "SEC contact for request headers, e.g. 'SignalLens research Jane Doe jane@example.com'"
if ($Agent -notmatch "@") { throw "SEC asks for a contact email in the header." }
try {
    $env:SIGNALLENS_EODHD_API_TOKEN = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($Secure))
    $env:SIGNALLENS_SEC_USER_AGENT = $Agent
    $Db = @("--research-db", $Research, "--production-db", $Production)

    Stage "1/6 Market-cap pre-screen (4 SEC + 1 EODHD requests)"
    $Screen = Invoke-Step "prescreen" (@("-m", "app.us_size_prescreen") + $Db)
    Write-Host "Estimates: $($Screen.estimates). Under 300M: $($Screen.by_size.under_300m); 300M-10B: $($Screen.by_size.'300m_to_10b'); over 10B: $($Screen.by_size.over_10b)"

    Stage "2/6 Catalogue"
    $Dry = Invoke-Step "catalogue-dry-run" (@("-m", "app.eodhd_ingestion_cli", "dry-run") + $Db + @("--us-securities", "$UsSecurities"))
    if ($Dry.status -ne "validated") { throw "Catalogue dry run did not validate: $($Dry.error)" }
    Write-Host "Would select US: $($Dry.selected_by_region.US); other regions: LSE $($Dry.selected_by_region.LSE), TO $($Dry.selected_by_region.TO), XETRA $($Dry.selected_by_region.XETRA), PA $($Dry.selected_by_region.PA)"
    Write-Host "US exclusions: outside band $($Dry.exclusions_by_reason.excluded_outside_size_band); size unknown $($Dry.exclusions_by_reason.excluded_size_unknown)"
    if ((Read-Host "Replace the active catalogue with this selection? Type yes") -ne "yes") { Write-Host "Stopped; nothing changed."; return }
    $Cat = Invoke-Step "catalogue" (@("-m", "app.eodhd_ingestion_cli", "ingest-catalogue") + $Db + @("--us-securities", "$UsSecurities"))
    if ($Cat.status -ne "validated") { throw "Catalogue did not validate." }
    Write-Host "Active catalogue now has $($Cat.accepted) listings."

    Stage "3/6 Ten years of prices and dividends for new listings (EODHD, 60/min)"
    $Limits = @("--daily-request-budget", "6000", "--requests-per-minute", "60", "--maximum-runtime-seconds", "14400")
    $Prices = Invoke-Step "prices" (@("-m", "app.eodhd_ingestion_cli", "ingest-prices") + $Db + $Limits)
    $Round = 1
    while ($Prices.status -eq "partial_checkpointed" -and $Round -lt 5) {
        Write-Host "Stopped at a limit with $($Prices.pending) pending ($($Prices.stop_reason)); continuing..."
        $Round++; $Prices = Invoke-Step "prices-resume-$Round" (@("-m", "app.eodhd_ingestion_cli", "resume") + $Db + $Limits)
    }
    Write-Host "Prices: $($Prices.status); completed $($Prices.completed), failed $($Prices.failed), pending $($Prices.pending)"
    if ($Prices.status -eq "partial_checkpointed") { throw "Prices still pending (daily budget?). Rerun this script with -SkipBackup tomorrow." }

    Stage "4/6 SEC fundamentals, industry codes and share counts (2 requests per company)"
    $SecArgs = @("--authorization", "I AUTHORIZE RESEARCH-ONLY SEC INGESTION", "--max-requests", "6000", "--runtime-seconds", "14400", "--max-response-bytes", "10000000")
    $Sec = Invoke-Step "sec-fundamentals" (@("-m", "app.sec_ingestion_cli", "ingest-sec-fundamentals") + $Db + $SecArgs)
    $Round = 1
    while ($Sec.status -eq "stopped" -and $Round -lt 5) {
        $Round++; $Sec = Invoke-Step "sec-fundamentals-$Round" (@("-m", "app.sec_ingestion_cli", "ingest-sec-fundamentals") + $Db + $SecArgs)
    }
    $Retry = Invoke-Step "sec-retry" (@("-m", "app.sec_ingestion_cli", "retry-sec-failures") + $Db + $SecArgs)
    Write-Host "SEC fundamentals: $($Sec.status); retry selected $($Retry.selected)"

    Stage "5/6 Classification and coverage evidence"
    $Now = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ss+00:00")
    $Mat = Invoke-Step "materialize" (@("-m", "app.investment_research_cli", "materialize-stored-investment-evidence") + $Db + @("--decision-at", $Now,
        "--authorization", "I AUTHORIZE RESEARCH-ONLY INVESTMENT EVIDENCE MATERIALIZATION"))
    Write-Host "Classification: $($Mat.status)"
    if (-not $SkipEvents) {
        Stage "6/6 SEC filing events (risk flags; a failure here does not undo anything)"
        try {
            $Events = Invoke-Step "sec-events" (@("-m", "app.sec_events_cli", "ingest-sec-events") + $Db + @("--authorization", "I AUTHORIZE RESEARCH-ONLY SEC EVENT INGESTION", "--max-requests", "6000", "--runtime-seconds", "14400"))
            Write-Host "SEC events: $($Events.status)"
        } catch { Write-Host "SEC events did not finish: $_. The rest is done; rerun later with -SkipBackup." -ForegroundColor Yellow }
    }
} finally {
    Remove-Item Env:SIGNALLENS_EODHD_API_TOKEN -ErrorAction SilentlyContinue
    Remove-Item Env:SIGNALLENS_SEC_USER_AGENT -ErrorAction SilentlyContinue
    Remove-Variable Secure -ErrorAction SilentlyContinue
}

Stage "Result: read-only roster at the current time"
$Cutoff = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:00Z")
# Exit 2 means results were withheld by a blocker; the counts are still reported.
$Roster = Invoke-Step "roster" @("-m", "app.prototype.cli", "--research-db", $Research, "--production-db", $Production, "--decision-at", $Cutoff) @(0, 2)
$Picks = $Roster.value_ranking.picks.Count
Write-Host "Cutoff $Cutoff | eligible $($Roster.eligible_count) | value-ranking population $($Roster.value_ranking.population) | picks $Picks" -ForegroundColor Green
Write-Host "Done. Start the prototype (.\scripts\start-prototype.ps1 -Operator) and open This month with the current time."
