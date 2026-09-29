$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$BackendPath = Join-Path $ProjectRoot "backend"
$FrontendPath = Join-Path $ProjectRoot "frontend"
$VenvPath = Join-Path $ProjectRoot ".venv"
$EnvPath = Join-Path $ProjectRoot ".env"

function Get-PortOwner([int]$Port) {
    $Connection = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $Connection) { return $null }
    $Process = Get-CimInstance Win32_Process -Filter "ProcessId=$($Connection.OwningProcess)" -ErrorAction SilentlyContinue
    [PSCustomObject]@{ Pid = $Connection.OwningProcess; CommandLine = [string]$Process.CommandLine }
}

function Assert-AvailablePort([int]$Port, [string]$ExpectedFragment) {
    $Owner = Get-PortOwner $Port
    if (-not $Owner) { return $false }
    if ($Owner.CommandLine -notlike "*$ProjectRoot*" -or $Owner.CommandLine -notlike "*$ExpectedFragment*") {
        throw "Port $Port is occupied by PID $($Owner.Pid), which is not the current SignalLens service. Stop it explicitly; no process was killed."
    }
    Write-Host "Current SignalLens $ExpectedFragment already listens on port $Port (PID $($Owner.Pid))."
    return $true
}

function Wait-Url([string]$Url, [int]$Attempts = 30) {
    for ($Attempt = 1; $Attempt -le $Attempts; $Attempt++) {
        try {
            $Response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 2
            if ($Response.StatusCode -ge 200 -and $Response.StatusCode -lt 500) { return }
        } catch { Start-Sleep -Seconds 1 }
    }
    throw "SignalLens readiness check timed out for $Url. Inspect the service window for a redacted error."
}

$BackendRunning = Assert-AvailablePort 8000 "uvicorn"
$FrontendRunning = Assert-AvailablePort 3000 "next"

if (-not (Get-Command py -ErrorAction SilentlyContinue) -and -not (Get-Command python -ErrorAction SilentlyContinue)) {
    throw "Python 3.12 or newer is required."
}
if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
    throw "Node.js 20 or newer is required."
}

if (-not (Test-Path $EnvPath)) {
    Copy-Item (Join-Path $ProjectRoot ".env.example") $EnvPath
}

if (-not (Test-Path $VenvPath)) {
    if (Get-Command py -ErrorAction SilentlyContinue) { py -m venv $VenvPath }
    else { python -m venv $VenvPath }
}

$PythonExe = Join-Path $VenvPath "Scripts\python.exe"
& $PythonExe -m pip install -r (Join-Path $BackendPath "requirements.txt")

if (-not (Test-Path (Join-Path $FrontendPath "node_modules"))) {
    Push-Location $FrontendPath
    try { npm install }
    finally { Pop-Location }
}

$FrontendEnv = Join-Path $FrontendPath ".env.local"
if (-not (Test-Path $FrontendEnv)) {
    "NEXT_PUBLIC_API_URL=http://127.0.0.1:8000" | Set-Content $FrontendEnv
}

$BackendCommand = "Set-Location '$BackendPath'; & '$PythonExe' -m uvicorn app.main:app --host 127.0.0.1 --port 8000"
$FrontendCommand = "Set-Location '$FrontendPath'; npm run dev -- --hostname 127.0.0.1 --port 3000"

if (-not $BackendRunning) {
    Start-Process powershell -ArgumentList "-NoExit", "-Command", $BackendCommand | Out-Null
}

Wait-Url "http://127.0.0.1:8000/api/v1/health"

if (-not $FrontendRunning) {
    Start-Process powershell -ArgumentList "-NoExit", "-Command", $FrontendCommand | Out-Null
}
Wait-Url "http://127.0.0.1:3000/operations" 60

Write-Host "SignalLens is ready. Service processes run independently of this launcher."
Write-Host "Dashboard: http://127.0.0.1:3000/operations"
Write-Host "API docs:  http://127.0.0.1:8000/docs"

