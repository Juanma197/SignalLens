# SignalLens Status

Last updated: 2026-09-20

## Current milestone

Milestone 3 — honest research baseline: complete and ready to merge.

## Delivered

### Data foundation

- A fixed 30-stock research universe with sector metadata.
- Daily OHLCV history from 2015-01-02 through 2026-09-18.
- DuckDB persistence, ingestion-run metadata, validation, idempotent upserts, and a data-status endpoint.
- Automatic database-directory creation and reliable connection cleanup.

### Point-in-time research dataset

- Monthly observations generated only from information available at each prediction timestamp.
- Momentum features over 5, 21, 63, and 126 trading days.
- 21-day volatility, 50-day and 200-day moving-average ratios, and 20-day relative volume.
- Signals formed after the as-of close, entry at the next trading close, and a 21-trading-day forward holding period.
- Cross-sectional outperformance labels with explicit exit dates.
- Leakage-safety and feature-timing tests.

The current local market-data snapshot produces:

- 3,578 labeled rows across 30 tickers.
- 130 prediction months from 2015-10-30 through 2026-07-31.
- No missing feature values.
- A 49.33% positive-label rate.

### Honest walk-forward evaluation

- Expanding-window logistic-regression baseline.
- A minimum 36-month training window.
- Exit-date embargo: a row is trainable only after its forward-return window has completed.
- Top-3 monthly portfolio evaluation with 10 basis points of transaction cost per side.
- Formal deterministic momentum benchmarks evaluated over the same dates.

Logistic baseline over 94 out-of-sample months:

- Brier score: 0.253054 versus 0.249945 for the constant-rate baseline.
- ROC AUC: 0.487626.
- Top-3 mean net monthly return: 2.937%.
- Universe mean monthly return: 2.475%.
- Mean excess return: 0.462%.
- Monthly excess-return win rate: 48.94%.

The logistic model does not demonstrate useful predictive classification skill and should not be presented as validated alpha.

Best simple benchmark, 126-day momentum over the same 94 months:

- Top-3 mean net monthly return: 4.213%.
- Median net monthly return: 3.398%.
- Mean excess return: 1.738%.
- Median excess return: 0.528%.
- Monthly excess-return win rate: 56.38%.
- Positive-return rate: 59.57%.
- Maximum drawdown: -24.70%.
- Worst month: -17.48%; best month: 29.88%.
- Bootstrap 95% interval for mean monthly excess return: 0.24% to 3.22%.
- NVDA was the most selected ticker, representing 10.99% of selections.

This is encouraging historical evidence for a benchmark, not proof of a deployable strategy.

### Immutable prediction storage

- Append-only prediction-run and prediction-row tables in DuckDB.
- Stored model/strategy identity, parameters, training cutoff, creation time, ranks, scores, and feature snapshots.
- Retrieval and counting helpers.
- Tests proving an existing prediction vintage cannot be overwritten.

## Verification

- 19 backend tests pass on Windows with Python 3.13.
- Frontend ESLint passes.
- The optimized Next.js production build completes successfully.
- The only backend warning is a third-party Starlette/AnyIO deprecation warning.
- The working tree is clean after restoring the generated `frontend/next-env.d.ts` change.

## Known limitations

- The universe is fixed today, so historical results are exposed to survivorship and selection bias.
- Yahoo Finance data is suitable for research prototyping, not production-grade market-data guarantees.
- The momentum variants were compared on the same sample; the reported bootstrap interval does not correct for multiple testing or serial dependence.
- Transaction costs are simplified and exclude spread variation, slippage, liquidity limits, taxes, and market impact.
- No untouched holdout period or live forward test has yet confirmed the 126-day momentum result.
- Immutable storage exists, but a production publishing workflow and distinction between historical backtests and live prediction vintages are not yet exposed through the API.
- The dashboard still contains demonstration ranking content.

## Next action

1. Merge `feature/milestone-3-research-baseline` into `main`.
2. Start Milestone 4 on a new branch.
3. Keep research evidence, stored prediction vintages, and user-facing rankings clearly separated.
