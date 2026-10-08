# First slice: unfrozen 15-company research prototype

The `/prototype` shortlist and `/prototype/company/<durable-id>` detail pages
use a separate `research-prototype` namespace, version `momentum-prototype-1.1.0`.
Every result is **UNVALIDATED RESEARCH PROTOTYPE — ZERO VALIDATION CREDIT**.
This is a 126-session price-behaviour baseline with no scoring weights. It does
not infer undervaluation, quality, predicted growth or investment conviction.

## One workflow

Enter an ISO evidence cutoff with a timezone. Review the eligible roster, the
deterministic membership proposal and every withheld identity with its exact
blockers. Inspect zero to three qualifying results, then open a result to see the
calculation, source citations, identity/action evidence, risks and missing
financial fields. There is no approval, freeze, write or provider button. Reads go
through the existing authenticated server-side research proxy and staging guard.

## Operator result (read-only, 2026-10-08, cutoff 2026-10-02T12:00:00Z)

Run against `backend\data\research\signallens-research.duckdb` and
`backend\data\signallens.duckdb`; both SHA-256 hashes were unchanged afterwards.

- 71 visible ordinary-company identities; **32 eligible**; 15 proposed; 3 results:
  NEU.US (+43.75%), EPR.US (+18.34%), WMG.US (+13.51%).
- Withheld: 39 `corporate_action_coverage_missing_or_incomplete`, 3
  `unresolved_price_discontinuity`, 1 `missing_exact_session_prices`.
- Derived calendar: 307 sessions (2025-07-09 to 2026-09-25) from 317 priced dates;
  the 11 dates excluded are US market holidays with one stray symbol each.
- Direct facts across the 32: assets 32, revenue 29, net income 29, equity 28,
  operating income 28. Cash is `evidence_after_cutoff` for all 32 at this cutoff
  (cash facts were retrieved on 2026-10-04).
- Stored prices end on 2026-09-25, so with the 7-day freshness rule the latest
  usable cutoff is about 2026-10-02. A current shortlist needs new prices, which
  this slice does not acquire.

Membership is still a **proposal**. Freezing it is a later, explicit step.

## Requirements and method

- **Identity at the cutoff** (`identity_rule`): an active US listing in the
  completed catalogue visible at the cutoff, an exact durable-ID match through
  main's `_reconcile`, a unique qualified symbol, and exactly one CIK agreed by
  `sec_issuers` and the classification evidence. The operator catalogue stores CIK
  there, not in `security_listings` (NULL for all rows); a listing CIK, when present,
  must agree. No full-window listing interval is proven: the operator database has
  no `issuer_mapping_candidates` rows. Each such company carries the explicit risk
  "ticker reuse earlier in the price window is not excluded"; the price-discontinuity
  check still applies. A visible mapping conflict blocks; an approved full-window
  mapping is cited when it exists. Ticker/name matching is never used.
- **Classification**: current, non-conflicting `us_operating_company` evidence
  with a source record identifier and no review flag.
- **Session calendar** (`session_calendar`): the operator database has no
  `global_exchange_sessions` rows, so sessions are derived from stored prices. A
  date counts when at least 80% of US symbols priced in the read window have a
  visible price on it, and only after 22:00 UTC on that date. Weekdays are never
  assumed. Each company must have a price on every one of the last 127 sessions;
  nothing is filled.
- **Prices**: 127 exact visible session prices (126 intervals), finite positive
  OHLC/adjusted close, consistent OHLC ranges, nonnegative volume, USD/US source and
  retrieval time not before the trading date. Last session within 7 days of the
  cutoff. Existing momentum mathematics and price-segment safeguards are reused.
- **Corporate actions**: visible coverage over the whole price window is required.
  `coverage_missing`, unknown or conflicting states block. `verified_no_action`
  conflicts with any stored event in the window; `action_present` describes its
  whole assessed interval and conflicts only if that interval has no stored event.
  Accepted stored types: `cash_distribution`, `dividend`, `split`.
- **Direct financial facts**: at least one usable direct SEC fact among cash, assets,
  equity, revenue, net income and operating income: exact concept, USD, finite,
  correct instant/duration shape, 10-K/10-Q forms, accession, Company Facts endpoint,
  consistent CIK, visible by the cutoff, period end within 550 days. At the latest
  period end, each exact reported interval (for example a quarter and a year-to-date)
  is shown separately with its length in days; intervals are never combined,
  converted to quarters/TTM or chosen between. Conflicting values for one interval
  are withheld. Facts never affect ordering.
- **Membership**: after eligibility, order durable IDs by
  `sha256(version + ':' + security_id)`, then ID; propose the first 15 (configurable
  10 to 20). Fewer than ten eligible blocks **all** results. Returns never choose
  membership.
- **Screen**: positive `adjusted_close[end] / adjusted_close[start] - 1`; order by
  return descending, then durable ID; show at most three.

## Isolation and bounds

`app.prototype` owns a GET-only router and a read-only CLI. It creates no database,
schema, vintage, Track B output or validation observation. Both database paths
must be distinct regular files and are SHA-256 fingerprinted before and after each
assessment, including failed reads. Research is opened `read_only=True` with
external access disabled, one thread and a 256 MB memory limit. Limits: 4 GB per
database file, 500,000 projected rows, 256 roster identities, 1,024 characters per
projected cell, 2 MB JSON output. Prices are read for 450 calendar days before the
cutoff; corporate actions are read in full. Limits refuse rather than truncate.

The API keeps the last few reports in memory while both database files keep the
same size and modification time, so opening a company page does not re-read the
database. A new cutoff takes about 10 seconds on the operator database.

Track A and Track B code, contracts and outputs are untouched. PR #96 is neither
expanded nor required. No provider requests or operator writes are performed.

## Windows PowerShell 5.1

Start the UI against the **operator databases** (read-only, staging mode, hashes
checked when you stop it):

```powershell
.\scripts\start-prototype.ps1 -Operator
```

Or against a new synthetic fixture: `.\scripts\start-prototype.ps1`. The script
prints the URL and the cutoff to paste. Login: if `SIGNALLENS_DASHBOARD_USERNAME`
and `SIGNALLENS_DASHBOARD_PASSWORD` are set (as environment variables, or both in
`frontend\.env.local`, which Git ignores) it uses them and never prints the
password; otherwise it prints a one-time login `prototype / <random>`. Press Enter in
that window to stop both services; it then confirms the hashes are unchanged.

Read-only roster report from the CLI (exit 0 = at least ten eligible, 2 = valid
report with blocked results, 1 = safe read refusal):

```powershell
$Project = "C:\Users\Juan Estrada\Projects\SignalLens"
$Report = Join-Path $env:TEMP "signallens-prototype-roster.json"
Push-Location (Join-Path $Project "backend")
try {
    & "$Project\.venv\Scripts\python.exe" -X utf8 -m app.prototype.cli --research-db data\research\signallens-research.duckdb --production-db data\signallens.duckdb --decision-at 2026-10-02T12:00:00+00:00 | Set-Content -Encoding utf8 $Report
    $Exit = $LASTEXITCODE
} finally { Pop-Location }
if ($Exit -notin @(0, 2)) { throw "CLI refused the read (exit $Exit); see $Report" }
$Roster = Get-Content -Raw -LiteralPath $Report | ConvertFrom-Json
Write-Host "Eligible $($Roster.eligible_count); proposed $($Roster.proposed_membership.Count); results $($Roster.results.Count)"
$Roster.companies | Where-Object { $_.eligible } | Select-Object qualified_symbol, company_name | Format-Table -AutoSize
```

Offline verification (fixtures only): `.\scripts\verify-prototype.ps1`.
`tests/test_research_observations.py` imports the POSIX-only `resource` module, so
it runs in Linux CI, not in this Windows verifier.

`next dev` rewrites `frontend/next-env.d.ts` (`.next/dev/types` imports) and creates
`frontend/AGENTS.md`/`CLAUDE.md`; `next build` rewrites `next-env.d.ts` back. These are
generated files, not prototype changes.

## Remaining work

1. Review the 32 eligible companies and 15 proposed members; freeze membership
   explicitly in a later slice.
2. Separate prototype database with isolated migrations: durable-ID watchlist and
   notes, immutable monthly snapshots (configuration, eligibility decisions and
   evidence references).
3. Prospective exact-session tracking at 21/63/126/252 sessions with explicit
   missing/delisting/action states; validation credit stays zero.
4. Fresh prices are needed for a current-month shortlist (separate, approved
   acquisition work). Corporate-action coverage for the 39 `coverage_missing`
   companies would widen the eligible pool.

Historical backfills, accounting constructions, PR #96 expansion, scoring weights
and further audit tooling remain deferred.
