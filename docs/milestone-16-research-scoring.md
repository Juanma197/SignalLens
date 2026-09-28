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

Every historical vintage rebuilds features from prices whose trading and
retrieval timestamps are visible at that vintage. Labels begin after the
vintage and are never used for normalization. Ranking requires all of:

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
