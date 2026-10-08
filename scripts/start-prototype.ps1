# PowerShell 5.1. Starts the read-only prototype UI on 127.0.0.1:3015 (API on 8015).
#   .\scripts\start-prototype.ps1            -> new synthetic temporary databases
#   .\scripts\start-prototype.ps1 -Operator  -> existing operator databases, read-only
# Staging mode blocks every non-GET request; the scheduler is disabled. Both
# database files are SHA-256 fingerprinted before start and after stop.
[CmdletBinding()]
param(
    [switch]$Operator,
    [string]$ResearchDb,
    [string]$ProductionDb
)
$ErrorActionPreference = "Stop"
$Project = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Project ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) { throw "Install backend dependencies into .venv first." }
if (-not (Test-Path -LiteralPath (Join-Path $Project "frontend\node_modules\.bin\next.cmd"))) { throw "Run 'npm.cmd ci' in frontend first." }
foreach ($Port in @(8015, 3015)) {
    if (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) {
        throw "Port $Port is already in use; stop that service yourself first."
    }
}
$Work = Join-Path ([System.IO.Path]::GetTempPath()) ("SignalLens Prototype " + [guid]::NewGuid().ToString("N"))
if ($Operator) {
    if (-not $ResearchDb) { $ResearchDb = Join-Path $Project "backend\data\research\signallens-research.duckdb" }
    if (-not $ProductionDb) { $ProductionDb = Join-Path $Project "backend\data\signallens.duckdb" }
    foreach ($Path in @($ResearchDb, $ProductionDb)) {
        if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw "Database not found: $Path" }
    }
    [void](New-Item -ItemType Directory -Path $Work)
    $Cutoff = "2026-10-02T12:00:00Z"
} else {
    Push-Location (Join-Path $Project "backend")
    try {
        & $Python -m app.prototype.fixture --new-temporary-directory $Work
        if ($LASTEXITCODE -ne 0) { throw "Synthetic fixture creation failed." }
    } finally { Pop-Location }
    $ResearchDb = Join-Path $Work "synthetic-research.duckdb"
    $ProductionDb = Join-Path $Work "synthetic-production.duckdb"
    $Cutoff = "2026-10-01T00:00:00Z"
}
Write-Host "Fingerprinting databases (large files take a few seconds)..."
$ResearchBefore = (Get-FileHash -Algorithm SHA256 -LiteralPath $ResearchDb).Hash
$ProductionBefore = (Get-FileHash -Algorithm SHA256 -LiteralPath $ProductionDb).Hash

$Token = [guid]::NewGuid().ToString("N") + [guid]::NewGuid().ToString("N")
# Login: a persistent (User/Machine) environment variable pair, else a pair in
# frontend\.env.local, else a one-time login for this run. Session values are
# ignored: an earlier run in the same window must not supply a stale login.
# A configured password is never printed.
function Get-PersistentVariable([string]$Name) {
    $Value = [Environment]::GetEnvironmentVariable($Name, "User")
    if (-not $Value) { $Value = [Environment]::GetEnvironmentVariable($Name, "Machine") }
    return $Value
}
$Username = Get-PersistentVariable "SIGNALLENS_DASHBOARD_USERNAME"
$Password = Get-PersistentVariable "SIGNALLENS_DASHBOARD_PASSWORD"
$LoginSource = "Windows user environment variables"
if (-not $Username -or -not $Password) {
    $Username = $null; $Password = $null
    $EnvLocal = Join-Path $Project "frontend\.env.local"
    if (Test-Path -LiteralPath $EnvLocal -PathType Leaf) {
        foreach ($Line in Get-Content -LiteralPath $EnvLocal) {
            if ($Line -match '^\s*SIGNALLENS_DASHBOARD_USERNAME\s*=\s*(.*?)\s*$') { $Username = $Matches[1].Trim('"', "'") }
            if ($Line -match '^\s*SIGNALLENS_DASHBOARD_PASSWORD\s*=\s*(.*?)\s*$') { $Password = $Matches[1].Trim('"', "'") }
        }
    }
    $LoginSource = "frontend\.env.local"
}
if (-not $Username -or -not $Password) {
    $Username = "prototype"
    $Password = [guid]::NewGuid().ToString("N").Substring(0, 16)
    $LoginSource = $null
}
# Every variable set below is restored when the script ends, so nothing from this
# run (including the login) remains in the calling PowerShell window.
$SessionNames = @("SIGNALLENS_ENVIRONMENT", "SIGNALLENS_STAGING_MODE", "SIGNALLENS_SCHEDULER_ENABLED",
    "SIGNALLENS_DATABASE_PATH", "SIGNALLENS_RESEARCH_DATABASE_PATH", "SIGNALLENS_PERSISTENT_VOLUME_PATH",
    "SIGNALLENS_API_TOKEN", "SIGNALLENS_API_URL", "NEXT_PUBLIC_API_URL", "SIGNALLENS_ALLOWED_ORIGINS",
    "SIGNALLENS_DASHBOARD_USERNAME", "SIGNALLENS_DASHBOARD_PASSWORD")
$SavedSession = @{}
foreach ($Name in $SessionNames) { $SavedSession[$Name] = [Environment]::GetEnvironmentVariable($Name, "Process") }
$env:SIGNALLENS_ENVIRONMENT = "development"
$env:SIGNALLENS_STAGING_MODE = "true"
$env:SIGNALLENS_SCHEDULER_ENABLED = "false"
$env:SIGNALLENS_DATABASE_PATH = $ProductionDb
$env:SIGNALLENS_RESEARCH_DATABASE_PATH = $ResearchDb
$env:SIGNALLENS_PERSISTENT_VOLUME_PATH = $Work
$env:SIGNALLENS_API_TOKEN = $Token
$env:SIGNALLENS_API_URL = "http://127.0.0.1:8015"
$env:NEXT_PUBLIC_API_URL = "http://127.0.0.1:8015"
$env:SIGNALLENS_ALLOWED_ORIGINS = "http://localhost:3015,http://127.0.0.1:3015"
$env:SIGNALLENS_DASHBOARD_USERNAME = $Username
$env:SIGNALLENS_DASHBOARD_PASSWORD = $Password
$WebHeaders = @{ Authorization = "Basic " + [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes("${Username}:$Password")) }
$Api = $null
$Web = $null
try {
    $Api = Start-Process -FilePath $Python -ArgumentList "-m uvicorn app.main:app --host 127.0.0.1 --port 8015" -WorkingDirectory (Join-Path $Project "backend") -RedirectStandardOutput (Join-Path $Work "api.log") -RedirectStandardError (Join-Path $Work "api-error.log") -PassThru -WindowStyle Hidden
    $Web = Start-Process -FilePath $env:ComSpec -ArgumentList '/d /s /c "npm.cmd run dev -- --hostname 127.0.0.1 --port 3015"' -WorkingDirectory (Join-Path $Project "frontend") -RedirectStandardOutput (Join-Path $Work "web.log") -RedirectStandardError (Join-Path $Work "web-error.log") -PassThru -WindowStyle Hidden
    $Ready = $false
    for ($Attempt = 0; $Attempt -lt 60; $Attempt++) {
        if ($Api.HasExited -or $Web.HasExited) { throw "A service exited; inspect logs in $Work" }
        try {
            $Health = Invoke-RestMethod -Uri "http://127.0.0.1:8015/api/v1/health" -TimeoutSec 2
            $Page = Invoke-WebRequest -UseBasicParsing -Headers $WebHeaders -Uri "http://127.0.0.1:3015/prototype" -TimeoutSec 10
            if ($Health.status -eq "ok" -and $Page.StatusCode -eq 200) { $Ready = $true; break }
        } catch { Start-Sleep -Seconds 1 }
    }
    if (-not $Ready) { throw "Startup timed out; inspect logs in $Work" }
    Write-Host ""
    if ($Operator) { Write-Host "OPERATOR DATABASES, READ ONLY." -ForegroundColor Yellow } else { Write-Host "SYNTHETIC FIXTURE ONLY." -ForegroundColor Yellow }
    Write-Host "Open:     http://127.0.0.1:3015/prototype?decision_at=$Cutoff"
    if ($LoginSource) { Write-Host "Login:    your configured dashboard username and password (from $LoginSource)" }
    else { Write-Host "Login:    $Username / $Password  (one-time; set SIGNALLENS_DASHBOARD_USERNAME/PASSWORD to use your own)" }
    Write-Host "Cutoff:   $Cutoff  (paste it into the form; each new cutoff takes ~10 s on operator data)"
    [void](Read-Host "Press Enter to stop the two services started by this script")
} finally {
    foreach ($Process in @($Web, $Api)) {
        if ($null -ne $Process -and -not $Process.HasExited) { & taskkill.exe /PID $Process.Id /T /F | Out-Null }
    }
    foreach ($Name in $SessionNames) { [Environment]::SetEnvironmentVariable($Name, $SavedSession[$Name], "Process") }
    $ResearchAfter = (Get-FileHash -Algorithm SHA256 -LiteralPath $ResearchDb).Hash
    $ProductionAfter = (Get-FileHash -Algorithm SHA256 -LiteralPath $ProductionDb).Hash
    if ($ResearchAfter -ne $ResearchBefore -or $ProductionAfter -ne $ProductionBefore) {
        throw "A database fingerprint changed while the prototype ran. Logs: $Work"
    }
    Write-Host "Stopped. Both database hashes unchanged. Logs: $Work"
}
