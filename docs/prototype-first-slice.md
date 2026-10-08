# First slice: unfrozen 15-company research prototype

The `/prototype` shortlist and `/prototype/company/<durable-id>` detail pages
use a separate `research-prototype` namespace, version `momentum-prototype-1.2.0`.
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
and `SIGNALLENS_DASHBOARD_PASSWORD` are set (as Windows user environment variables, or both in
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

## Slice 2: watchlist, notes, monthly snapshots and follow-up

Pages: `/prototype/watchlist`, `/prototype/snapshots`, `/prototype/snapshots/<id>`,
and a "Watchlist and notes" panel on every company page.

- **Separate store**: `backend\data\prototype\signallens-prototype.duckdb`
  (`SIGNALLENS_PROTOTYPE_DATABASE_PATH`; Git-ignored). It must differ from the
  research and production paths; those two are never opened for writing.
- **Writes are opt-in**: `SIGNALLENS_PROTOTYPE_WRITES_ENABLED=true` allows POST to
  `/api/v1/research/prototype/store/*` only, even in staging mode; every other write
  is still refused. The frontend forwards POST only for those paths. The start
  script enables it; in synthetic mode the store is a temporary file.
- **Append-only**: watchlist add/remove are events; notes cannot be edited (a
  correction is a new note). There is no update or delete code.
- **Snapshots**: one per calendar month of the cutoff, frozen as the full assessed
  report (membership, results, eligibility decisions, evidence references and
  configuration hash) with a SHA-256 checked on every read. The cutoff must be in
  the past and no more than 14 days before recording; missed months are never
  backfilled. The 15 members are frozen with the results in one record.
- **Follow-up**: return from the snapshot's decision session to exactly the 21st,
  63rd, 126th and 252nd derived US session after it, using stored adjusted closes
  (both endpoints from the current series, since later retrievals can re-adjust
  history). Unreached checkpoints are `pending`; a missing price is `missing_price`
  (possibly a halt or delisting), never filled. The averages of the results and of
  all members are shown for comparison. Description only; zero validation credit.

Verified on 2026-10-08 against the operator databases with a temporary store:
snapshot 2026-10 recorded (15 members, 3 results, integrity verified); a second
October snapshot and a 37-day-old cutoff were refused; watchlist and notes round-
tripped through the web proxy; tracking is `pending` because stored prices end on
the decision session (2026-09-25). Protected database hashes unchanged.

## Slice 3: structured research thesis

Each company page has a "Your research thesis" panel below the stored facts:
business, financial health, why it might be cheap, potential catalysts, downside
case, what would invalidate the thesis, and assumptions, plus a status
(researching, active or rejected). The operator writes every section; SignalLens
does not generate, score or check them, and they never affect eligibility or the
shortlist. Sections are labelled as interpretation, or as assumption, separately
from the stored facts. Each save is a new immutable version in `research_theses`
(prototype store); earlier versions stay visible, so the thesis held at any
snapshot can be reviewed later. The watchlist shows each company's current
thesis status. A store created before this slice gains the table on its next write.

## Universe corrections (version momentum-prototype-1.2.0)

Three more eligibility gates, applied before the hash order (never using returns):

- **Size**: market cap must be $300M to $10B. Market cap = latest visible
  `dei:EntityCommonStockSharesOutstanding` from the SEC companyfacts documents
  retained by the liquidity run (10-K/10-Q cover pages; share counts from one filing
  are summed and flagged, because class detail is not stored) x unadjusted close on
  the decision session. `sec_facts` holds no share counts. Each document is parsed
  one at a time in Python (several MB each; parsing them in SQL exceeded the 256 MB
  limit) and used only if its bytes match the stored SHA-256 and byte count. An
  entry is known from the later of its filing day (end of day UTC) and retrieval. Share counts older than 550 days, ambiguous across filings or
  missing give `market_cap_unavailable`. Shown with its inputs as a calculation,
  not a quoted market value.
- **Industry**: SIC code from the SEC submissions documents already retained by
  the controlled liquidity run (`sec_liquidity_raw_provenance`, read in SQL so the
  payload is never projected), visible from its retrieval time and matched on
  CIK. SIC 6000-6799 (banks, credit, brokers, insurance, real estate, REITs,
  holding and investment offices) gives `specialist_sector_excluded`. Missing,
  not-yet-visible or mismatched records give `industry_classification_unavailable`.
  No new SEC requests are made.
- **Partnerships**: a listing or SEC name ending in "LP"/"L.P." or containing
  "Partners" gives `partnership_units_excluded`.

- **Coverage extension**: a stored corporate-action coverage record that ends
  inside the price window (the operator records end on 2026-10-02) is extended to
  the window end only by a completed EODHD `refresh` checkpoint for the symbol,
  recorded after 22:00 UTC on the last window session and by the cutoff. That
  refresh fetched prices and dividends through its run date, the same provider
  evidence the stored record was built from; splits remain provider-unsupported in
  both, and the price-discontinuity check still applies. `verified_no_action` is a
  claim only up to the stored record's end. Companies whose record is
  `coverage_missing` are not extended. Re-materializing coverage was rejected
  because that operation also writes classification and canonical factor evidence.

Operator result (read-only, cutoff 2026-10-08T16:54Z, after a partial refresh
with 17 US securities still pending): 12 eligible, 12 proposed, 3 results: NSP.US
(+90.7%), NEU.US (+41.3%), OPLN.US (+16.2%). Withheld: 45 coverage missing or
incomplete, 26 market cap unavailable, 18 outside the band, 16 specialist sector,
13 missing session prices, 3 price discontinuity, 1 partnership, 1 industry
unavailable. Both protected database hashes unchanged.

The version change also changes the membership hash seed, so the proposed 15
differ from version 1.1.0; nothing had been frozen under the earlier version.

## Stage 2: financial-health brief (annual SEC figures)

Each company page has a "Financial health (annual 10-K figures)" section, and
result cards show counts of strengths, weaknesses and gaps. Context only: it never
affects eligibility, membership or ordering.

- **Data**: stored `sec_facts` from 10-K/10-K/A only. Full fiscal years are
  durations of 350-380 days; balance-sheet items are instants at those year ends.
  Up to 5 latest fiscal years. For each concept and exact period the latest visible
  revision is used (known at the later of public and retrieval time); a period
  with conflicting values in that revision is withheld. Revenue uses
  `RevenueFromContractWithCustomerExcludingAssessedTax`, then `Revenues`, then
  `SalesRevenueNet`; the concept is kept per year and growth is not compared
  across a concept change.
- **Calculations** (marked `*`): operating and net margin, revenue growth, free
  cash flow = operating cash flow - capital expenditure (both full-year 10-K
  figures), free-cash-flow margin, cash conversion, current ratio,
  liabilities/assets, diluted share change. Multi-year rates use only the latest
  run of consecutive fiscal years, so a missing year (often a merger or change of
  reporting entity) ends the comparison.
- **Observations**: fixed rules in `financials.FINANCIAL_RULES` (for example 10%
  revenue growth a year, 10% operating margin, current ratio 1.5/1.0, liabilities
  70% of assets, 3% annual dilution). Each names its evidence years. They are
  interpretation, not a rating; there is no overall score.
- **Not available**: interest coverage (no interest-expense facts stored), total
  debt (borrowing components are not combined), quarterly or TTM figures.
- Yearly tables are included for eligible companies; others carry observations
  only, keeping the report bounded (about 0.76 MB on operator data).

Operator check (read-only, 2026-10-08): all 12 eligible companies have at least
3 full years. NSP.US (top momentum result) shows a latest operating margin of
-0.1% and free cash flow of -309M USD; NEU.US shows positive margins and free
cash flow in all 5 years and a falling share count. Hashes unchanged.

## Stage 3 (first step): current valuation multiples

Each company page has a "Valuation snapshot": price/earnings, price/sales,
price/free cash flow and price/book, plus earnings and free-cash-flow yields,
from the calculated market cap against the last full fiscal year (the two dates
differ, and the page says so). Zero or negative denominators are listed as not
meaningful instead of producing negative multiples. Result cards show P/E and FCF
yield. There is deliberately no cheap/expensive verdict yet: that needs sector
context and comparison with the company's own history (next step), and
business-model caveats (for example lease-to-own companies whose purchases run
through operating cash flow).

## Stage 3 (continued): valuation against the company's own history

For eligible companies the valuation section adds "Against its own history":

- **Basis**: at each fiscal year end, the unadjusted close on the last stored US
  session on or before that date (within 7 days, visible by the cutoff) x that
  year's weighted diluted shares, with every figure **as first reported** in that
  year's 10-K. Later filings restate history (for example share counts after a
  split); pairing a restated figure with an old unadjusted price would be wrong.
  The current row uses the same basis with today's close and the latest year's
  shares, so it is comparable with the history but differs slightly from the
  cover-page snapshot above. Approximate, and labelled so.
- **Comparison**: for each multiple with at least three comparable years, the
  current value is placed below, within or above the company's own range, with
  the median. The page says a low multiple is a question to research, not a
  conclusion.
- **Sector notes**: fixed SIC-based notes for business models where standard
  ratios mislead (oil and gas, pharma/biotech, auto dealers' floor-plan debt,
  lease-to-own cash flows, software stock compensation, cruise lines, health
  services, hardware). Context only.

Operator check (read-only, 2026-10-08): 11 of 12 eligible companies have a
history (NPK.US has no year with both a year-end price and diluted shares). LKQ.US
and MMS.US sit below their own five-year range on every available multiple;
NEU.US and ALSN.US sit above on P/E and P/S. Hashes unchanged.

## Remaining work

1. Stage 3 remainder: conservative scenario ranges with explicit assumptions.
   Then Stage 4 (filings-based catalysts) and Stage 5 (one-page analyst brief).
2. Fresh prices for a current-month shortlist and for tracking to advance
   (operator-run acquisition with the existing ingestion). Corporate-action
   coverage for the 39 `coverage_missing` companies would widen the eligible pool.
2. Optional later aids for the thesis, each separately reviewed: comparable
   valuation context, and prompts at snapshot checkpoints to revisit the
   invalidation evidence.

Historical backfills, accounting constructions, PR #96 expansion, scoring weights
and further audit tooling remain deferred.
