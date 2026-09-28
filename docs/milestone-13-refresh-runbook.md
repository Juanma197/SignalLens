# Milestone 13 research audit and incremental refresh runbook (PowerShell)

Milestone 13 audits and refreshes only the isolated EODHD research database. It
does not access Railway, publish rankings, change the production universe, or
alter the official `momentum_126d` path. The current catalogue is **not
survivorship-free** and cannot support historical-membership claims or production
promotion.

## Observed pilot state

The operator reports that the completed local pilot selected 500 securities across
US, LSE, TO, XETRA and PA; 499 price histories completed and `AIIA-U.US` remained
a structured `invalid_provider_payload` failure; no securities were pending; and
historical USD/GBP, CAD/GBP and EUR/GBP were complete. The ignored local DuckDB was
approximately 78 MB and had a byte-identical, SHA-256-verified backup. These facts
were **not re-observed in the cloud checkout**: the database is not committed,
uploaded, inspected, or reconstructed here.

## Safe PowerShell workflow

Run from `backend`. Set explicit, different absolute paths. Every command refuses
identical research and production paths; errors and persisted failures contain
bounded codes rather than provider bodies or the EODHD token.

```powershell
$Research = 'C:\SignalLensData\global-research.duckdb'
$Production = 'C:\SignalLensData\signallens.duckdb'
$Backup = "$Research.pre-m13.bak"

Get-FileHash $Research -Algorithm SHA256
Copy-Item $Research $Backup
Get-FileHash $Backup -Algorithm SHA256

python -m app.eodhd_ingestion_cli audit --research-db $Research --production-db $Production
python -m app.eodhd_ingestion_cli plan-refresh --research-db $Research --production-db $Production
python -m app.eodhd_ingestion_cli refresh --dry-run --research-db $Research --production-db $Production

$env:SIGNALLENS_EODHD_API_TOKEN = '<token supplied outside logs>'
python -m app.eodhd_ingestion_cli refresh --research-db $Research --production-db $Production `
  --daily-request-budget 700 --requests-per-minute 20 --maximum-runtime-seconds 1800
python -m app.eodhd_ingestion_cli retry-failures --research-db $Research --production-db $Production
python -m app.eodhd_ingestion_cli status --research-db $Research --production-db $Production
python -m app.eodhd_ingestion_cli coverage --research-db $Research --production-db $Production
Remove-Item Env:SIGNALLENS_EODHD_API_TOKEN
```

`audit`, `plan-refresh`, and all `--dry-run` paths are byte-for-byte mutation-free.
The audit emits aggregates by region/currency and at most 25 sanitized affected
symbols by default (`--affected-limit`, maximum 100). It checks selection, depth,
freshness, adjusted close, OHLCV validity, currency, FX, corporate actions,
natural-key duplicates, and structured provider failures.

`plan-refresh` reports aggregate counts for eligible, pending,
skipped-permanent, and skipped-nonretryable securities. By default it includes
only a sanitized sample of at most ten request records; add
`--verbose-planned-requests` only when the complete local plan is genuinely
needed. For the operator-reported state above, the routine plan is expected to
contain 499 eligible securities, one skipped permanent failure, zero pending
securities, and 1,001 provider requests (499 price/dividend pairs plus three FX
requests). This expected result has offline regression coverage, but was not
verified against the operator's local database here.

## Routine refresh versus reconciliation

Normal daily or weekly `refresh` starts six days before the latest stored boundary,
giving a documented seven-calendar-day inclusive overlap. It deterministically
upserts natural keys, reports materially changed overlapping rows, never deletes
valid history, preserves retrieval/FX availability timestamps, and checkpoints a
budget/runtime stop for resumption. Incremental ranges reduce response volume and
processing time, but EODHD may still require approximately one request for each
endpoint/security. Routine planning and refresh skip permanent failures such as
`invalid_provider_payload`, while transient `provider_http_error` and
`provider_request_failed` failures are explicitly classified as retryable.
Unknown/nonretryable classifications are also skipped rather than guessed.
`retry-failures` considers pending and retryable failures by default.

A permanent failure must first be investigated. If the operator deliberately
decides it is safe to retry, both the explicit operation and the separate
authorization flag are required:

```powershell
python -m app.eodhd_ingestion_cli retry-failures --authorize-permanent-failures `
  --research-db $Research --production-db $Production
```

Never add this authorization flag to the routine refresh command or scheduler.

Adjusted history can change after splits, dividends, or provider corrections, so
periodic deeper reconciliation is separate and never automatic. Review its plan
and backup first, then deliberately authorize it:

```powershell
python -m app.eodhd_ingestion_cli reconcile --dry-run --authorize-full-reconciliation `
  --research-db $Research --production-db $Production
python -m app.eodhd_ingestion_cli reconcile --authorize-full-reconciliation `
  --research-db $Research --production-db $Production
```

The reconciliation command requests the bounded ten-year history. It still uses
transactional idempotent upserts and does not delete valid observations.
