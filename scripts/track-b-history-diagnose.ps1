# Windows PowerShell 5.1. Run after checkout of the diagnostic PR branch.
& {
  $ErrorActionPreference = "Stop"
  $Repo = "C:\Users\Juan Estrada\Projects\SignalLens"
  $Backend = Join-Path $Repo "backend"
  $Python = "..\.venv\Scripts\python.exe"
  $Research = "data\research\signallens-research.duckdb"
  $Production = "data\signallens.duckdb"
  $Decisions = @("2026-10-04T21:30:00+00:00", "2026-10-05T00:30:00+00:00")
  $Pushed = $false
  try {
    Push-Location $Backend
    $Pushed = $true
    $Before = @{}
    $Files = @{ research = $Research; production = $Production }
    foreach ($Name in @("research", "production")) {
      try { $Before[$Name] = (Get-FileHash -LiteralPath $Files[$Name] -Algorithm SHA256).Hash }
      catch { Write-Host ($Name + ".external.before FINGERPRINT_READ_FAILED") }
    }
    try {
      if ($Before.Count -eq 2) {
        for ($i = 0; $i -lt $Decisions.Count; $i++) {
          Write-Host ("boundary_" + $i + " DIAGNOSTIC_STARTED")
          # Native stderr can contain interpreter/OS paths: never print it.
          $Text = & $Python -m app.track_b_history_diagnostic --research-db $Research --production-db $Production --decision-at $Decisions[$i] 2>$null
          $Code = $LASTEXITCODE
          if ($Text) {
            $Safe = ($Text -join "`n") | ConvertFrom-Json
            if ($Safe.events.Count -gt 52) { throw "DIAGNOSTIC_OUTPUT_INVALID" }
            # Module emits only fixed identifiers, stable reason codes and counts.
            Write-Output ($Safe | ConvertTo-Json -Depth 6 -Compress)
          } else { Write-Host ("boundary_" + $i + " DIAGNOSTIC_FAILED") }
          if ($Code -ne 0) { Write-Host ("boundary_" + $i + " DIAGNOSTIC_NONZERO") }
        }
      }
    } finally {
      foreach ($Name in @("research", "production")) {
        try {
          $After = (Get-FileHash -LiteralPath $Files[$Name] -Algorithm SHA256).Hash
          if (-not $Before.ContainsKey($Name)) { Write-Host ($Name + ".external.after FINGERPRINT_BASELINE_UNAVAILABLE") }
          elseif ($Before[$Name] -cne $After) { Write-Host ($Name + ".external.after FINGERPRINT_CHANGED") }
          else { Write-Host ($Name + ".external.after FINGERPRINT_UNCHANGED") }
        } catch { Write-Host ($Name + ".external.after FINGERPRINT_READ_FAILED") }
      }
    }
  } catch { Write-Host "diagnostic DIAGNOSTIC_FAILED" }
  finally { if ($Pushed) { Pop-Location } }
}
