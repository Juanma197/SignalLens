# PowerShell 5.1. Runs `railway config <args>` (plan / apply / pull) on Windows.
# Railway's TypeScript SDK checks the CLI version by starting `railway` without a
# shell, which fails on Windows (the npm `railway` is a .ps1/.cmd wrapper); it reads
# the executable from $env:_ instead when set.
#   .\scripts\railway-config.ps1 plan
#   .\scripts\railway-config.ps1 apply
param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Arguments)
$Exe = Get-ChildItem -Path (Join-Path $env:APPDATA "npm\node_modules\@railway\cli\bin\railway.exe") -ErrorAction SilentlyContinue
if (-not $Exe) { throw "railway.exe not found under $env:APPDATA\npm. Install the CLI with: npm install -g @railway/cli" }
$Project = Split-Path -Parent $PSScriptRoot
if (-not (Test-Path (Join-Path $Project "node_modules\railway"))) { Push-Location $Project; try { npm install --silent } finally { Pop-Location } }
$env:_ = $Exe.FullName
Push-Location $Project
try { & railway config @Arguments } finally { Pop-Location; Remove-Item Env:_ -ErrorAction SilentlyContinue }
