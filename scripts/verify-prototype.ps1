# PowerShell 5.1. Uses only new system-temporary fixtures; no operator paths.
# tests/test_research_observations.py imports POSIX-only `resource`; Linux CI runs it.
[CmdletBinding()]
param()
$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) { throw "Install backend dependencies into .venv first; see the prototype runbook." }
$PreviousTestPython = $env:SIGNALLENS_TEST_PYTHON
$env:SIGNALLENS_TEST_PYTHON = $Python
try {
    Push-Location (Join-Path $ProjectRoot "backend")
    try {
        & $Python -m pytest -q tests/test_prototype.py tests/test_prototype_store.py tests/test_prototype_financials.py tests/test_prototype_events.py tests/test_prototype_brief.py tests/test_price_segments.py tests/test_track_b_gaps.py tests/test_track_b_panel.py tests/test_company_research.py tests/test_prospective_us_shadow.py
        if ($LASTEXITCODE -ne 0) { throw "Offline prototype/backend regression tests failed." }
    } finally { Pop-Location }
    Push-Location (Join-Path $ProjectRoot "frontend")
    try {
        & npm.cmd test
        if ($LASTEXITCODE -ne 0) { throw "Frontend offline workflow tests failed." }
        & npm.cmd run lint
        if ($LASTEXITCODE -ne 0) { throw "Frontend lint failed." }
        & npm.cmd run build
        if ($LASTEXITCODE -ne 0) { throw "Frontend production build failed." }
    } finally { Pop-Location }
} finally { $env:SIGNALLENS_TEST_PYTHON = $PreviousTestPython }
Write-Host "Verified offline prototype; membership remains proposed/unfrozen and validation credit remains zero."
