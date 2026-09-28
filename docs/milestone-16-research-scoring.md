# Milestone 16 research scoring and evidence gates

## Contract

`research-scoring` is a read-only planning/reporting command. It opens only the
research DuckDB in DuckDB read-only mode; the explicit production path is used
for alias protection and before/after SHA-256 fingerprints. It creates no table,
WAL, checkpoint, frontend output, official ranking, or provider request.

The decision timestamp is explicit (or captured once at command start). Only
catalogue and price/FX evidence available by that timestamp enters the existing
Milestone 14 validator. Only rows whose resulting `eligible` flag is true enter
feature preparation. Withheld rows cannot enter scores, ranks, evaluation rows,
or eligible-universe denominators.

## Evidence currently available

The database supports provider adjusted-close total-return inputs, 126-session
momentum, 21-session trend, 63-session annualized volatility and 126-session
drawdown. GBP is identity, GBX uses `/100`, and CAD/EUR/USD retain historical
as-of GBP FX validation. Return ratios are currency invariant. Cross-sectional
percentiles are calculated separately inside US, LSE, TO, XETRA and PA, then
combined with a small global tie-break so one market or currency cannot dominate.

The market-data schema does **not** establish point-in-time fundamentals,
valuation, quality, catalysts or sentiment. Existing multifactor weights and
neutral missing-factor convention are reused; the report identifies these
inputs as unavailable. Raw momentum alone is not represented as sufficient.

## Leakage controls and gates

Every historical vintage rebuilds features from prices retrieved by the final
evaluation cutoff and effectively dated by that vintage. The effective
trading-date boundary—not the database ingestion audit timestamp—determines
what enters each vintage. This distinction matters because the research adapter
loads historical provider rows in a recent batch: requiring that batch timestamp
to precede a years-old vintage produced no rows. Historical as-of FX validation
still resolves the latest observation on or before each price date. Labels begin
after the vintage and are never used for normalization. Ranking requires all of:

1. eligible coverage and count;
2. historical vintages and prediction count;
3. valid forward label boundaries;
4. positive excess performance over the equal-weight eligible universe;
5. rank discrimination plus score-quintile calibration reporting;
6. temporal and all-five-region stability; and
7. unchanged database fingerprints/no integrity failure.

Any failure returns `ranking_withheld`, bounded reason codes and an empty
candidate list. Passing permits zero to three candidates above the score floor,
with a highest-conviction field only when a candidate exists. Synthetic fixture
passes are labelled synthetic/research-only and say nothing about live data.

## Zero-vintage incident and repair

The first operator Milestone 16 run selected 500 catalogue securities and found
492 model-ready (98.4% coverage), but returned zero predictions, zero vintages,
and an invalid evaluation. Integrity and coverage correctly passed and ranking
was correctly withheld; both database fingerprints remained unchanged.

The adapter had passed the one latest model-ready cross-section to the evaluator.
For each old decision date the evaluator changed only `decision_at`, then asked
feature preparation to require `retrieved_at <= old decision_at`. Since the ten
years of historical prices were batch-ingested recently, every historical row
was removed despite its old effective `trading_date`. The repair builds a distinct
calendar-month-end historical panel, bounds ingestion at the final evaluation
cutoff, bounds features at each effective decision date, and requires a complete
21-session forward label. It reports selected/loaded/model-ready counts, price
range, possible/generated vintages, feature/label rows, removals, feature/label
date bounds, and bounded explicit zero-vintage reason codes.

The current scoring cross-section remains separate from the historical evidence
panel. Both use the **current catalogue**, so this pilot is not survivorship-free;
historical output must not be described as a survivorship-free backtest or as a
live strategy pass.

## Post-merge PowerShell command

Run from the repository root with distinct real paths:

```powershell
Push-Location backend
& ..\.venv\Scripts\python.exe -m app.eodhd_ingestion_cli research-scoring `
  --research-db "C:\SignalLensData\global-research.duckdb" `
  --production-db "C:\SignalLensData\signallens.duckdb"
Pop-Location
```

Optionally add `--decision-at "2026-09-28T20:00:00+00:00"`. Confirm every
fingerprint is unchanged and treat all output as research only, not investment
advice. The reported live Milestone 15 result (492 eligible, eight withheld) did
not itself pass these new gates; this command must assess them after merge.
