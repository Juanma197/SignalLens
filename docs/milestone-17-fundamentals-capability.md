# Milestone 17 point-in-time fundamentals capability runbook

## Why this assessment exists

The corrected price-only evaluation retained 41,271 predictions across 113
monthly vintages. Mean excess return was -0.00462999, median was -0.00139906,
the 95% interval was [-0.0154861, 0.0059936], positive-period rate was 0.477876,
and rank correlation was 0.0428387. Integrity, label integrity, concentration,
sample size, coverage, discrimination and walk-forward gates passed; baseline
performance, temporal stability and regional stability failed. Ranking therefore
remains correctly withheld. Thresholds are unchanged and this milestone does not
force candidates.

## Scope and point-in-time contract

`fundamentals-capability` examines EODHD's per-symbol fundamentals endpoint for
at most five representative securities: AAPL.US, AZN.LSE, RY.TO, SAP.XETRA and
OR.PA. Output never includes those symbols, company records, raw payloads, field
values, request URLs, provider error text or the token. It includes only region-
level counts, allow-listed field names, date-field names, currencies, earliest/
latest fiscal periods, and a conservative reconstruction flag.

A record is visible only on or after a valid `filing_date`, `filingDate`,
`accepted_date`, `acceptedDate`, `reporting_date`, or `reportedDate`. Fiscal-period
end is descriptive and is never substituted. Missing availability dates remain
unusable. Later restatements replace earlier versions only for decisions on or
after the later public date. Annual and quarterly records remain distinct.
Current `Highlights`/summary ratios are ignored for historical vintages.

Both database paths are byte-counted and SHA-256 hashed through ordinary read-only
file handles before and after; neither is opened by DuckDB. Missing paths are not
created. No table, ingestion, score, evidence threshold, rank or publisher is
changed.

## Feature assessment

The provider statement fields can make revenue/earnings growth, operating/net
margins, ROE, ROA, free cash flow, leverage, interest coverage and share dilution
`derivable_with_constraints`. ROIC additionally needs a documented invested-
capital convention. Earnings yield, FCF yield and book-to-market require a
decision-date market price/capitalization joined in the same currency and unit.
Enterprise-value measures are `ambiguous_requires_validation` until cash, debt,
minority/preferred interests, share count, units and price timing are verified.
Provider current-summary ratios are `latest_only_not_backtestable`. A live probe
may downgrade any derivable item to `ambiguous_requires_validation` or
`unavailable` when date or field coverage is inadequate; the command makes no
claim that a formula is ready for model use.

Important limitations remain: statement taxonomy and signs can vary by issuer,
venue and accounting regime; GBX versus GBP and reported currencies require
explicit conversion; quarterly flows may be discrete or year-to-date; revisions
can be incomplete; filing dates require validation against primary filings; and
market value must be reconstructed with split-consistent point-in-time prices and
shares. The five-security sample establishes capability only, not universe
coverage. The current catalogue is still non-survivorship-free and all existing
evaluation must be described accordingly.

## Offline command (safe default)

```powershell
Push-Location backend
& ..\.venv\Scripts\python.exe -m app.fundamentals_capability_cli fundamentals-capability `
  --fixture `
  --research-db "C:\SignalLensData\global-research.duckdb" `
  --production-db "C:\SignalLensData\signallens.duckdb"
Pop-Location
```

Confirm `mode` is `fixture`, `request_count` is zero, and
`database_immutability.verified` is true.

## Explicitly authorized live command after merge

Set the credential without printing it, then run exactly this bounded probe:

```powershell
$env:SIGNALLENS_EODHD_API_TOKEN = Read-Host "EODHD API token" -MaskInput
Push-Location backend
& ..\.venv\Scripts\python.exe -m app.fundamentals_capability_cli fundamentals-capability `
  --authorize-live --max-requests 5 --timeout-seconds 10 --pacing-seconds 1 `
  --max-response-bytes 5000000 `
  --research-db "C:\SignalLensData\global-research.duckdb" `
  --production-db "C:\SignalLensData\signallens.duckdb"
Pop-Location
Remove-Item Env:SIGNALLENS_EODHD_API_TOKEN
```

Do not redirect HTTP diagnostics or capture raw traffic. Retain only the sanitized
JSON report. Stop if either fingerprint changes. This authorization covers five
requests only; it does not authorize bulk ingestion, Railway access or deployment.
