# First slice: unfrozen 15-company research prototype

The `/prototype` shortlist and `/prototype/company/<durable-id>` detail pages
use a separate `research-prototype` namespace, version `momentum-prototype-1.0.0`.
Every result is **UNVALIDATED RESEARCH PROTOTYPE — ZERO VALIDATION CREDIT**.
This is a 126-session price-behaviour baseline, with no scoring weights. It does
not infer undervaluation, quality, predicted growth or investment conviction.

## One workflow

Enter an explicit ISO evidence cutoff including a timezone. Review the actual
eligible roster, the deterministic membership proposal, every withheld identity
and its exact blockers. Inspect zero-to-three qualifying results, then follow a
result to the calculation, source citations, identity/action evidence, risks and
missing financial fields. There is no approval, freeze, write or provider button.
All reads are authenticated through the existing server-side research proxy and
retain the existing maintenance and staging write guards.

This slice produces a **proposal**, not a frozen or official monthly vintage.
The operator evidence database is not included in Git and was not available in
this implementation workspace. The synthetic fixture demonstrates the workflow;
it is explicitly labelled and is never offered as the actual operator roster.
Run the read-only actual-roster block below before any future universe freeze.

## Fixed requirements and method

- Reuse main's exact `security_id` matched roster; ticker/name matching is forbidden.
- A company must have one active ordinary USD US listing, a consistent stored CIK,
  current non-conflicting ordinary-company classification with source references,
  and exactly one approved, source-referenced `issuer_mapping_candidates` record
  whose qualified symbol and CIK agree. Its declared effective interval must cover
  the entire price window and cutoff, `conflict_state=none`, and
  `ticker_reuse_protected=true`. No mapping is created or approved here. Main's
  `first_seen_at`, `last_seen_at`, `mapped_at` and a durable-ID match do not prove
  an effective interval. Missing/ambiguous approved evidence blocks eligibility.
  These are checks of stored evidence, not a claim of original-document verification.
- Require 127 unique, exact, visible US session prices (126 intervals), valid finite
  positive OHLC/adjusted close, consistent OHLC ranges, nonnegative volume,
  USD/US source identity and retrieval time. No filling missing sessions. Reuse
  existing `ObservationPolicy` freshness (seven days), momentum mathematics and
  price-segmentation safeguards. The single-US-region adapter does not fabricate
  four other regions or FX to satisfy the five-region observation builder.
- Only sessions completed by the cutoff are considered. In the absence of stored
  close timestamps, same-day sessions are conservatively not used until 22:00 UTC;
  earlier/half-day closes are not inferred. Stored sessions are not reconstructed
  from weekdays. Synthetic fixtures use invented session calendars solely for tests.
- Require explicit visible corporate-action coverage over the entire price window.
  `verified_no_action` and `action_present` must agree with stored events; unknown,
  unresolved, conflicting or incomplete coverage blocks eligibility. A detected
  unresolved price discontinuity, delisting or unsupported action also blocks.
- Require at least one usable direct SEC fact among cash, assets, equity, revenue,
  net income and operating income. These are bounded direct observations only:
  exact supported concepts, USD units, finite values, reported instant/duration
  shape, permitted forms, original fact key/accession, exact stored Company Facts
  source endpoint, consistent identity, public/retrieval timestamps and a maximum
  reported-period age of 550 days. Cash/assets cannot be negative; signed negative
  income/equity remain observations. Latest visible revisions must have an
  unambiguous exact interval/concept and value. No context or accounting-note
  completeness is asserted; no quarterly/YTD conversion or TTM/FCF/debt/EV is built.
- After evidence eligibility, order durable IDs by
  `sha256(version + ':' + security_id)`, then ID. Propose the first 15; ten is the
  unchanged minimum. `--target-members` can configure 10–20 without changing
  calculation code; the effective configuration is hashed. The complete eligible
  roster and all withholdings remain visible. Returns do not choose membership.
- Within the proposed members, require positive
  `adjusted_close[end] / adjusted_close[start] - 1`. Order return descending, then
  durable ID ascending; show at most three. No qualifying company means zero
  results. Fewer than ten eligible companies blocks **all** results.
- Direct facts never change ordering. Missing, stale, incompatible, post-cutoff,
  ambiguous and conflicting evidence remains unknown, never zero or neutral.
  Both endpoint prices/dates, 126 intervals, price sources and latest retrieval
  are shown; direct-fact citations retain known-at dates, period and accession.

## Isolation and bounds

`app.prototype` owns a GET-only router and read-only CLI. It creates no database,
schema, vintage, Track B output or validation observation. Both existing database
paths must be distinct regular non-aliased/non-hard-linked files. Both are
fingerprinted before and after, including failed reads; production is never
opened as DuckDB. Research uses `read_only=True` with external access disabled,
one SQL thread and a 256 MB SQL memory limit. Fixed projections omit payloads and
capabilities. Limits: 1 GB per database file, 500,000 projected rows total, 256
roster identities, 1,024 characters per projected cell, 2 MB JSON output. Market
reads cover at most 450 calendar days before the cutoff; facts remain direct
reported evidence. Limits refuse rather than truncate membership/evidence.

Track A's frozen mathematics, registration and vintage/ledger paths are untouched.
Track B's no-model/no-candidate/no-vintage contracts are untouched. Existing
reusable readers are called; no guard, alias or accounting contract is weakened.
PR #96's branch and completed work are preserved; this branch neither expands it
nor requires it. No financial provider requests or operator writes are performed.

## PowerShell 5.1 verification and synthetic startup

Prerequisites: Python 3.12+, Node 24, npm 11, installed `.venv` and frontend
`node_modules`. If dependencies are absent, install them with these commands
(package downloads only; no financial provider requests):

```powershell
$ErrorActionPreference = "Stop"
$Project = (Get-Location).Path # run from the repository root
if (-not (Test-Path -LiteralPath ".venv\Scripts\python.exe")) {
    & py -3.12 -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw "venv creation failed" }
}
& .\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
if ($LASTEXITCODE -ne 0) { throw "backend dependency installation failed" }
Push-Location frontend
try {
    & npm.cmd ci --no-audit --no-fund
    if ($LASTEXITCODE -ne 0) { throw "frontend dependency installation failed" }
} finally { Pop-Location }
.\scripts\verify-prototype.ps1
```

For an already installed repository, `.\scripts\verify-prototype.ps1` is the
complete verification command. It runs the new offline fixture/CLI/API tests,
reused Track A/B regression tests, real backend-to-rendered-UI workflow tests,
frontend lint and production build. Tests make new system-temporary files only.
This does not claim a full browser automation pass or a Windows pass unless the
Windows verifier actually ran; the PR records the environments verified.

Start two local services against **new synthetic temporary databases**. Run in a
fresh PowerShell session; this session sets only synthetic development settings.
Ports 8015 and 3015 must be free. No real credentials are needed. Existing `.env`
files are not edited. The parent directories/logs are temporary; nothing opens an
operator database:

```powershell
$ErrorActionPreference = "Stop"
$Project = (Get-Location).Path # repository root
$Python = Join-Path $Project ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $Python)) { throw "Install dependencies first" }
foreach ($Port in @(8015,3015)) {
    if (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) {
        throw "Port $Port is already in use; do not stop another service"
    }
}
$Demo = Join-Path ([System.IO.Path]::GetTempPath()) ("SignalLens Prototype " + [guid]::NewGuid().ToString("N"))
Push-Location (Join-Path $Project "backend")
try {
    & $Python -m app.prototype.fixture --new-temporary-directory $Demo
    if ($LASTEXITCODE -ne 0) { throw "Synthetic fixture creation failed" }
} finally { Pop-Location }
$Research = Join-Path $Demo "synthetic-research.duckdb"
$Production = Join-Path $Demo "synthetic-production.duckdb"
$ResearchBefore = (Get-FileHash -Algorithm SHA256 -LiteralPath $Research).Hash
$ProductionBefore = (Get-FileHash -Algorithm SHA256 -LiteralPath $Production).Hash
$env:SIGNALLENS_ENVIRONMENT = "development"
$env:SIGNALLENS_STAGING_MODE = "true" # existing guard prohibits every write
$env:SIGNALLENS_SCHEDULER_ENABLED = "false"
$env:SIGNALLENS_DATABASE_PATH = $Production
$env:SIGNALLENS_RESEARCH_DATABASE_PATH = $Research
$env:SIGNALLENS_PERSISTENT_VOLUME_PATH = $Demo
$env:SIGNALLENS_API_TOKEN = "synthetic-prototype-local-token-0001"
$env:SIGNALLENS_API_URL = "http://127.0.0.1:8015"
$env:NEXT_PUBLIC_API_URL = "http://127.0.0.1:8015"
$env:SIGNALLENS_ALLOWED_ORIGINS = "http://localhost:3015,http://127.0.0.1:3015"
$env:SIGNALLENS_DASHBOARD_USERNAME = "prototype"
$env:SIGNALLENS_DASHBOARD_PASSWORD = "synthetic-local-only"
$WebHeaders = @{ Authorization = "Basic " + [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes("prototype:synthetic-local-only")) }
$Api = $null
$Web = $null
try {
    $Api = Start-Process -FilePath $Python -ArgumentList "-m uvicorn app.main:app --host 127.0.0.1 --port 8015" -WorkingDirectory (Join-Path $Project "backend") -RedirectStandardOutput (Join-Path $Demo "api.log") -RedirectStandardError (Join-Path $Demo "api-error.log") -PassThru
    $Web = Start-Process -FilePath $env:ComSpec -ArgumentList '/d /s /c "npm.cmd run dev -- --hostname 127.0.0.1 --port 3015"' -WorkingDirectory (Join-Path $Project "frontend") -RedirectStandardOutput (Join-Path $Demo "web.log") -RedirectStandardError (Join-Path $Demo "web-error.log") -PassThru
    $Ready = $false
    for ($Attempt = 0; $Attempt -lt 30; $Attempt++) {
        if ($Api.HasExited -or $Web.HasExited) { throw "A service exited; inspect logs in $Demo" }
        try {
            $Health = Invoke-RestMethod -Uri "http://127.0.0.1:8015/api/v1/health" -TimeoutSec 2
            $Page = Invoke-WebRequest -UseBasicParsing -Headers $WebHeaders -Uri "http://127.0.0.1:3015/prototype" -TimeoutSec 5
            if ($Health.status -eq "ok" -and $Page.StatusCode -eq 200) { $Ready = $true; break }
        } catch { Start-Sleep -Seconds 1 }
    }
    if (-not $Ready) { throw "Startup timed out; inspect logs in $Demo" }
    $Headers = @{ Authorization = "Bearer $env:SIGNALLENS_API_TOKEN" }
    $Roster = Invoke-RestMethod -Headers $Headers -Uri "http://127.0.0.1:8015/api/v1/research/prototype/roster?decision_at=2026-10-01T00%3A00%3A00Z" -TimeoutSec 30
    if ($Roster.eligible_count -ne 18 -or $Roster.proposed_membership.Count -ne 15 -or $Roster.results.Count -ne 3 -or $Roster.validation_credit -ne 0 -or -not $Roster.synthetic_fixture) { throw "Synthetic roster verification failed" }
    foreach ($Id in $Roster.results) {
        $Detail = Invoke-RestMethod -Headers $Headers -Uri ("http://127.0.0.1:8015/api/v1/research/prototype/companies/" + [uri]::EscapeDataString($Id) + "?decision_at=2026-10-01T00%3A00%3A00Z") -TimeoutSec 30
        if (-not $Detail.qualifying_result -or $Detail.company.calculation.session_intervals -ne 126) { throw "Company detail verification failed" }
    }
    Write-Host "Open http://127.0.0.1:3015/prototype; login prototype / synthetic-local-only; enter 2026-10-01T00:00:00Z."
    Write-Host "SYNTHETIC ONLY. Confirm the roster, open a result, inspect calculation/citations/missing fields."
    [void](Read-Host "Press Enter after reviewing to stop only these two service trees")
} finally {
    foreach ($Process in @($Web,$Api)) {
        if ($null -ne $Process -and -not $Process.HasExited) { & taskkill.exe /PID $Process.Id /T /F | Out-Null }
    }
    if ((Get-FileHash -Algorithm SHA256 -LiteralPath $Research).Hash -ne $ResearchBefore -or (Get-FileHash -Algorithm SHA256 -LiteralPath $Production).Hash -ne $ProductionBefore) { throw "A synthetic database fingerprint changed" }
    Write-Host "Temporary fixture/logs preserved at $Demo; both database hashes unchanged."
}
```

## Actual eligible roster: read-only operator review, no freeze

Run separately against explicit existing operator files, preferably with the
application stopped to avoid concurrent writer changes. No provider, ingestion,
materialization, mapping approval or database initialization command is invoked.
The full report names actual eligible companies, their evidence and blockers;
`eligible_roster` gives their hash order and `proposed_membership` gives the first
15. Missing identity intervals or corporate-action coverage remain blockers.
Exit 0 means the proposal has at least ten eligible companies, **not** approval;
exit 2 means a valid report with blocked results; exit 1 means a safe read refusal.

```powershell
$ErrorActionPreference = "Stop"
$Project = (Get-Location).Path
$Python = Join-Path $Project ".venv\Scripts\python.exe"
$Research = "C:\SignalLens\Research Data\research.duckdb" # replace with existing file
$Production = "C:\SignalLens\Production Data\production.duckdb" # replace with existing file
$Decision = "2026-10-01T00:00:00Z" # choose a real non-future evidence cutoff
$Report = Join-Path ([System.IO.Path]::GetTempPath()) ("signallens-prototype-roster-" + [guid]::NewGuid().ToString("N") + ".json")
$ResearchBefore = (Get-FileHash -Algorithm SHA256 -LiteralPath $Research).Hash
$ProductionBefore = (Get-FileHash -Algorithm SHA256 -LiteralPath $Production).Hash
$ExitCode = 1
Push-Location (Join-Path $Project "backend")
try {
    $Output = & $Python -X utf8 -m app.prototype.cli --research-db $Research --production-db $Production --decision-at $Decision
    $ExitCode = $LASTEXITCODE
    $Utf8 = New-Object System.Text.UTF8Encoding -ArgumentList $false
    [System.IO.File]::WriteAllText($Report, ($Output -join [Environment]::NewLine), $Utf8)
} finally {
    Pop-Location
    if ((Get-FileHash -Algorithm SHA256 -LiteralPath $Research).Hash -ne $ResearchBefore -or (Get-FileHash -Algorithm SHA256 -LiteralPath $Production).Hash -ne $ProductionBefore) { throw "Operator database fingerprint changed" }
}
if ($ExitCode -eq 1) { throw "Read refused; inspect the fixed error code in $Report" }
if ($ExitCode -notin @(0,2)) { throw "Unexpected CLI exit code $ExitCode" }
$Roster = Get-Content -Raw -LiteralPath $Report | ConvertFrom-Json
$Roster.companies | Select-Object security_id,qualified_symbol,company_name,eligible,reasons | Format-Table -Wrap
$Roster.withholding_counts | Format-List
Write-Host "Eligible: $($Roster.eligible_count); proposed: $($Roster.proposed_membership.Count); blockers: $($Roster.blockers -join ', ')"
Write-Host "Review complete evidence at $Report. Universe remains unfrozen; no validation credit."
```

## Remaining work

1. Obtain and review the actual read-only roster report; resolve blockers only
   through separately reviewed evidence work, never by weakening this screen.
2. Review exact durable IDs and explicitly freeze membership in a later slice.
3. Add a separate prototype database, with isolated migrations and deliberate
   write controls; no prototype table belongs in existing operator databases.
4. Add durable-ID watchlist notes and immutable monthly result snapshots, including
   configuration, all eligibility decisions and input/evidence references.
5. Add prospective exact-session subsequent-performance tracking, missing/delisting/
   action states and descriptive comparisons; validation credit stays zero.
6. Verify Windows PowerShell 5.1 startup on the operator machine and complete browser
   interaction/deployment verification. Deployment and scheduling are separate work.

Historical backfills, complex accounting constructions, provider acquisition,
PR #96 expansion, scoring-weight tuning and further general audit tooling remain
paused/deferred. Completed Track A/B and PR #96 work is preserved.
