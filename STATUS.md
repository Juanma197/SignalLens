# SignalLens Status

Last updated: 2026-09-19

## Current milestone

Milestone 3 — honest research baseline: ready for implementation.

## Milestone 2 completion

- Curated 30-stock universe and 21-trading-day forward horizon
- DuckDB schema for securities, daily adjusted prices, and ingestion provenance
- Live yfinance ingestion covering 2015-01-02 through 2026-09-18
- 81,617 stored price rows across all 30 tickers
- Zero duplicate ticker/date rows
- Automatic database-directory creation
- Explicit DuckDB connection cleanup and bulk price insertion
- Idempotent price upserts and failed-run recording
- Data-status API and dashboard summary
- Backend automated tests: 8 passed on Windows
- Frontend ESLint and production build passed
- Milestone 2 merged into `main` at commit `7f8c5d5`

## Current limitations

- Rankings remain deterministic demonstration data; real prices are not yet used for ranking.
- The curated universe is intentionally small and has survivorship bias; it is suitable for pipeline validation, not historical claims about the whole market.
- yfinance is a convenient research source, not an exchange-grade licensed feed.
- No point-in-time feature table, forward-return labels, walk-forward model, transaction-cost evaluation, or immutable prediction vintages exist yet.

## Milestone 3 objective

Build an honest baseline research pipeline with point-in-time features, leakage-safe 21-trading-day targets, walk-forward evaluation, simple benchmark comparisons, transaction-cost assumptions, and immutable prediction vintages.

## Next action

Specify and implement the Milestone 3 feature and label contracts with automated leakage tests.
