$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$BackendPath = Join-Path $ProjectRoot "backend"
$FrontendPath = Join-Path $ProjectRoot "frontend"
$VenvPath = Join-Path $ProjectRoot ".venv"
$EnvPath = Join-Path $ProjectRoot ".env"

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

$BackendCommand = "Set-Location '$BackendPath'; & '$PythonExe' -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000"
$FrontendCommand = "Set-Location '$FrontendPath'; npm run dev"

Start-Process powershell -ArgumentList "-NoExit", "-Command", $BackendCommand
Start-Process powershell -ArgumentList "-NoExit", "-Command", $FrontendCommand

Write-Host "SignalLens is starting."
Write-Host "Dashboard: http://localhost:3000"
Write-Host "API docs:  http://127.0.0.1:8000/docs"


