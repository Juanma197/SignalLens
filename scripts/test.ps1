$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$PythonExe = Join-Path $ProjectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path $PythonExe)) {
    throw "Run .\start.ps1 once to install dependencies."
}

Push-Location (Join-Path $ProjectRoot "backend")
try { & $PythonExe -m pytest }
finally { Pop-Location }

Push-Location (Join-Path $ProjectRoot "frontend")
try {
    npm run lint
    npm run build
}
finally { Pop-Location }

