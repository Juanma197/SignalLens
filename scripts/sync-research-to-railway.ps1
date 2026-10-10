# PowerShell 5.1. Publishes the local research database to the Railway backend
# (docs/railway-signallens.md step 4.1, docs/milestone-33-railway-research-sync.md):
# verified snapshot -> capacity check -> upload to /data/bootstrap -> remote plan ->
# your "yes" -> atomic apply (keeps a rollback copy) -> status -> remove the upload.
# The production database (/data/signallens.duckdb) is never written.
#   .\scripts\sync-research-to-railway.ps1
#   .\scripts\sync-research-to-railway.ps1 -SkipUpload   when the same snapshot is already uploaded
[CmdletBinding()]
param(
    [string]$Service = "SignalLens",
    [string]$KeyFile = (Join-Path $env:USERPROFILE ".ssh\railway_signallens_ed25519"),
    [switch]$SkipUpload
)
$ErrorActionPreference = "Stop"
$Project = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Project ".venv\Scripts\python.exe"
$Backend = Join-Path $Project "backend"
$Research = Join-Path $Backend "data\research\signallens-research.duckdb"
$Snapshot = Join-Path $Backend "data\research\backups\railway-upload.duckdb"
$Remote = @{ Candidate = "/data/bootstrap/signallens-research.duckdb"; Research = "/data/research/signallens-research.duckdb"
             Production = "/data/signallens.duckdb"; Bootstrap = "/data/bootstrap"; Backups = "/data/research/backups" }
function Stage([string]$Text) { Write-Host ""; Write-Host "== $Text" -ForegroundColor Cyan }

Stage "Preflight"
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) { throw "Missing $Python" }
if (-not (Test-Path -LiteralPath $Research -PathType Leaf)) { throw "Research database not found: $Research" }
if (-not (Test-Path -LiteralPath $KeyFile -PathType Leaf)) { throw "SSH key not found: $KeyFile (register one with: railway ssh keys)" }
if (Get-NetTCPConnection -LocalPort 8015, 3015 -State Listen -ErrorAction SilentlyContinue) {
    throw "The prototype is running (ports 8015/3015). Stop it so the database is not changing, then rerun."
}
# The SSH user for the service, from Railway's own config block (nothing is written).
$Block = & { $ErrorActionPreference = "Continue"; railway ssh config --service $Service --alias signallens-railway --dry-run 2>$null }
$User = ($Block | Select-String -Pattern '^\s*User\s+(\S+)').Matches | ForEach-Object { $_.Groups[1].Value } | Select-Object -First 1
if (-not $User) { throw "Could not read the SSH user from 'railway ssh config'. Is the Railway CLI logged in and linked?" }
$Target = "$User@ssh.railway.com"
$SshOptions = @("-i", $KeyFile, "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new", "-o", "ServerAliveInterval=30")

# Runs one command in the backend container and returns its output lines.
function Remote([string]$Command) {
    $Lines = & ssh @SshOptions $Target $Command
    if ($LASTEXITCODE -ne 0) { throw "Remote command failed (exit $LASTEXITCODE): $Command`n$($Lines -join "`n")" }
    return $Lines
}
# Runs the sync CLI in the container (single-quoted arguments only) and parses its JSON.
function Sync([string]$Arguments) {
    $Lines = Remote "cd /app && python -m app.research_sync_cli $Arguments"
    return (($Lines -join "`n") | ConvertFrom-Json)
}
[void](Remote "echo connected")
Write-Host "OK: connected to $Service"

if (-not $SkipUpload) {
    Stage "Verified snapshot of the local research database"
    Copy-Item -LiteralPath $Research -Destination $Snapshot -Force
    if ((Get-FileHash -Algorithm SHA256 -LiteralPath $Research).Hash -ne (Get-FileHash -Algorithm SHA256 -LiteralPath $Snapshot).Hash) {
        throw "Snapshot hash mismatch: is something writing the research database?"
    }
    Push-Location $Backend
    try { $Verify = & $Python -X utf8 -m app.database_backup verify $Snapshot --profile research } finally { Pop-Location }
    if ($LASTEXITCODE -ne 0) { throw "The snapshot is not a valid research database: $Verify" }
}
if (-not (Test-Path -LiteralPath $Snapshot -PathType Leaf)) { throw "No snapshot at $Snapshot; run without -SkipUpload." }
$Sha = (Get-FileHash -Algorithm SHA256 -LiteralPath $Snapshot).Hash.ToLower()
$Bytes = (Get-Item -LiteralPath $Snapshot).Length
Write-Host ("Snapshot: {0:N2} GB, SHA-256 {1}" -f ($Bytes / 1GB), $Sha)

if (-not $SkipUpload) {
    Stage "Railway volume capacity"
    $Free = [int64]((Remote "df -B1 --output=avail /data | tail -1").Trim())
    $Active = [int64]((Remote "stat -c %s $($Remote.Research) 2>/dev/null || echo 0").Trim())
    # The upload, then a rollback copy of the active database and a temporary copy of the upload.
    $Needed = 2 * $Bytes + $Active + 64MB
    Write-Host ("Free {0:N2} GB, needed {1:N2} GB" -f ($Free / 1GB), ($Needed / 1GB))
    if ($Free -lt $Needed) {
        throw ("Not enough space on the Railway volume. Resize it (dashboard: service $Service -> Volume) to at least {0:N0} GB, then rerun." -f [math]::Ceiling(($Needed + 2GB) / 1GB))
    }

    Stage "Upload (a few minutes for ~2 GB)"
    [void](Remote "mkdir -p $($Remote.Bootstrap) $($Remote.Backups)")
    & scp @SshOptions $Snapshot "${Target}:$($Remote.Candidate)"
    if ($LASTEXITCODE -ne 0) { throw "Upload failed (exit $LASTEXITCODE); rerun to try again." }
}
$Uploaded = ((Remote "sha256sum $($Remote.Candidate)") -split '\s+')[0]
if ($Uploaded -ne $Sha) { throw "The uploaded file does not match the snapshot (remote $Uploaded); rerun without -SkipUpload." }
Write-Host "OK: upload verified"

Stage "Plan (read-only)"
$Paths = "--candidate '$($Remote.Candidate)' --research '$($Remote.Research)' --production '$($Remote.Production)' --bootstrap '$($Remote.Bootstrap)' --expected-sha256 '$Sha' --expected-byte-count '$Bytes'"
$Plan = Sync "plan-research-sync $Paths"
if ($Plan.status -ne "ready" -or -not $Plan.compatibility.compatible) { throw "Plan not ready: $($Plan | ConvertTo-Json -Depth 6)" }
$Evidence = $Plan.compatibility.evidence
Write-Host ("Compatible. Companies with briefs {0:N0}, SEC facts {1:N0}, SEC events {2:N0}, prices {3:N0}." -f `
    $Evidence.scored_company_brief_availability, $Evidence.sec_fundamental_observations, $Evidence.sec_events, $Evidence.price_observations)
Write-Host ("Railway's current research database: {0:N0} MB. Production database unchanged and never written." -f ($Plan.active_research.byte_count / 1MB))

$Answer = Read-Host "Replace Railway's research database with this snapshot? Research pages pause for about a minute. Type yes"
if ($Answer -ne "yes") { Write-Host "Stopped; nothing was replaced. The upload stays in $($Remote.Bootstrap) (rerun with -SkipUpload)."; exit 0 }

Stage "Apply"
$Applied = Sync "apply-research-sync $Paths --backup-dir '$($Remote.Backups)' --plan-id '$($Plan.plan_id)' --authorization 'I AUTHORIZE STAGING RESEARCH DATABASE REPLACEMENT'"
if ($Applied.status -ne "completed") { throw "Apply did not complete: $($Applied | ConvertTo-Json -Depth 6)" }
Write-Host "OK: replaced; the previous database is kept as a rollback copy"

Stage "Status"
$Status = Sync "research-sync-status --research '$($Remote.Research)' --production '$($Remote.Production)' --backup-dir '$($Remote.Backups)'"
$Problems = @()
if ($Status.active_research.sha256 -ne $Sha) { $Problems += "active database is not the snapshot" }
if (-not $Status.compatibility) { $Problems += "not compatible" }
if ($Status.lock -or $Status.maintenance) { $Problems += "lock or maintenance marker left behind" }
if (-not $Status.rollback_available) { $Problems += "no rollback copy" }
if (-not $Status.production.unchanged) { $Problems += "production database changed" }
if ($Problems) { throw "Status check failed: $($Problems -join '; '). The upload is kept for investigation." }
[void](Remote "rm -f $($Remote.Candidate)")
Write-Host "OK: all checks passed; upload removed. Open the website's shortlist: the first load of a date takes a minute or two." -ForegroundColor Green
