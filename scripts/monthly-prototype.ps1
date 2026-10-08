# PowerShell 5.1. Monthly prototype routine, one stage at a time with checks:
#   1 preflight  2 verified backup  3 refresh dry run  4 price refresh (EODHD)
#   5 read-only roster at the current cutoff, saved as a report
# Recording the snapshot stays a deliberate step in the UI afterwards.
#   .\scripts\monthly-prototype.ps1                 full routine
#   .\scripts\monthly-prototype.ps1 -SkipRefresh    stage 5 only (no backup, no requests)
[CmdletBinding()]
param(
    [switch]$SkipRefresh,
    [int]$DailyRequestBudget = 1100,
    [int]$RequestsPerMinute = 20
)
$ErrorActionPreference = "Stop"
$Project = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Project ".venv\Scripts\python.exe"
$Backend = Join-Path $Project "backend"
$Research = Join-Path $Backend "data\research\signallens-research.duckdb"
$Production = Join-Path $Backend "data\signallens.duckdb"
$Reports = Join-Path $Backend "data\research\reports"
$Stamp = Get-Date -Format "yyyyMMdd-HHmmss"
function Stage([string]$Text) { Write-Host ""; Write-Host "== $Text" -ForegroundColor Cyan }

Stage "1/5 Preflight"
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) { throw "Missing $Python" }
foreach ($Path in @($Research, $Production)) { if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw "Database not found: $Path" } }
if (Get-NetTCPConnection -LocalPort 8015, 3015 -State Listen -ErrorAction SilentlyContinue) {
    throw "The prototype is running (ports 8015/3015). Press Enter in its window to stop it, then rerun."
}
$Other = Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -like "*eodhd_ingestion_cli*" }
if ($Other) { throw "Another EODHD ingestion process is running (PID $($Other.ProcessId -join ', ')). Wait for it to finish." }
$ProductionBefore = (Get-FileHash -Algorithm SHA256 -LiteralPath $Production).Hash
Write-Host "OK: databases present, prototype stopped, no other ingestion running."

if (-not $SkipRefresh) {
    Stage "2/5 Verified backup of the research database"
    $Size = (Get-Item -LiteralPath $Research).Length
    $Free = (Get-PSDrive -Name ($Research.Substring(0, 1))).Free
    if ($Free -lt 2 * $Size) { throw "Not enough free disk for a backup: need $([math]::Round(2 * $Size / 1GB, 1)) GB" }
    $BackupDir = Join-Path $Backend "data\research\backups"
    [void](New-Item -ItemType Directory -Force -Path $BackupDir)
    $Backup = Join-Path $BackupDir "signallens-research-pre-refresh-$Stamp.duckdb"
    Copy-Item -LiteralPath $Research -Destination $Backup
    $ResearchHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $Research).Hash
    if ($ResearchHash -ne (Get-FileHash -Algorithm SHA256 -LiteralPath $Backup).Hash) { throw "Backup hash mismatch; nothing was refreshed." }
    Write-Host "OK: $Backup"

    Stage "3/5 Refresh dry run (no requests, no writes)"
    Push-Location $Backend
    try {
        $Dry = & $Python -m app.eodhd_ingestion_cli refresh --dry-run --research-db $Research --production-db $Production
        if ($LASTEXITCODE -ne 0) { throw "Dry run failed (exit $LASTEXITCODE)." }
    } finally { Pop-Location }
    $DryPlan = ($Dry -join "`n") | ConvertFrom-Json
    Write-Host "OK: $($DryPlan.provider_request_estimate) provider requests planned for $($DryPlan.eligible_securities) securities."
    if ($DryPlan.provider_request_estimate -gt $DailyRequestBudget) {
        Write-Host "The plan exceeds -DailyRequestBudget $DailyRequestBudget; the refresh will stop part-way. Rerun tomorrow to continue." -ForegroundColor Yellow
    }

    Stage "4/5 Price refresh (EODHD; about one minute per $RequestsPerMinute requests)"
    $Secure = Read-Host "EODHD API token (hidden)" -AsSecureString
    $Output = Join-Path $Reports "eodhd-refresh-$Stamp.json"
    [void](New-Item -ItemType Directory -Force -Path $Reports)
    Push-Location $Backend
    try {
        $env:SIGNALLENS_EODHD_API_TOKEN = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($Secure))
        $Result = & $Python -m app.eodhd_ingestion_cli refresh --research-db $Research --production-db $Production `
            --daily-request-budget $DailyRequestBudget --requests-per-minute $RequestsPerMinute --maximum-runtime-seconds 7200
        $Exit = $LASTEXITCODE
    } finally {
        Remove-Item Env:SIGNALLENS_EODHD_API_TOKEN -ErrorAction SilentlyContinue
        Remove-Variable Secure -ErrorAction SilentlyContinue
        Pop-Location
    }
    $Utf8 = New-Object System.Text.UTF8Encoding -ArgumentList $false
    [System.IO.File]::WriteAllText($Output, ($Result -join [Environment]::NewLine), $Utf8)
    if ($Exit -ne 0) { throw "Refresh failed (exit $Exit). Research backup: $Backup. Output: $Output" }
    $Refresh = ($Result -join "`n") | ConvertFrom-Json
    Write-Host "Refresh status: $($Refresh.status); completed $($Refresh.completed), failed $($Refresh.failed), pending $($Refresh.pending). Output: $Output"
    if ($Refresh.status -eq "partial_checkpointed") {
        Write-Host "Stopped at the request budget or runtime limit. Rerun this script tomorrow to continue; do not record a snapshot yet." -ForegroundColor Yellow
        return
    }
    if ((Get-FileHash -Algorithm SHA256 -LiteralPath $Production).Hash -ne $ProductionBefore) { throw "The production database changed during the refresh." }
}

Stage "5/5 Read-only roster at the current cutoff"
$Cutoff = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:00Z")
[void](New-Item -ItemType Directory -Force -Path $Reports)
$Report = Join-Path $Reports "prototype-roster-$Stamp.json"
Push-Location $Backend
try {
    $Roster = & $Python -X utf8 -m app.prototype.cli --research-db $Research --production-db $Production --decision-at $Cutoff
    $Exit = $LASTEXITCODE
} finally { Pop-Location }
$Utf8 = New-Object System.Text.UTF8Encoding -ArgumentList $false
[System.IO.File]::WriteAllText($Report, ($Roster -join [Environment]::NewLine), $Utf8)
if ($Exit -notin @(0, 2)) { throw "Roster read refused (exit $Exit); see $Report" }
$R = ($Roster -join "`n") | ConvertFrom-Json
$Names = @{}; foreach ($C in $R.companies) { $Names[$C.security_id] = "$($C.qualified_symbol) $($C.company_name)" }
Write-Host "Cutoff $Cutoff | eligible $($R.eligible_count) | proposed $($R.proposed_membership.Count) | results $($R.results.Count) | last session $($R.session_calendar.last_session)"
foreach ($Id in $R.results) { Write-Host "  result: $($Names[$Id])" }
if ($R.blockers.Count) { Write-Host "Blocked: $($R.blockers -join ', ')" -ForegroundColor Yellow }
if ((Get-FileHash -Algorithm SHA256 -LiteralPath $Production).Hash -ne $ProductionBefore) { throw "The production database changed." }
Write-Host ""
Write-Host "Next: .\scripts\start-prototype.ps1 -Operator, review the shortlist at $Cutoff,"
Write-Host "then record this month's snapshot on the Snapshots page with the same cutoff (within 14 days)."
