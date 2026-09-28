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

## Extreme-return incident and repair

The next read-only operator run produced 41,609 labels over 113 vintages.  Its
58.578998 mean excess return and 90.095465 US regional excess were not credible:
the interval `[-0.013274, 175.754111]`, while very wide, exposed a small number of
enormous adjusted-close ratios rather than stable model evidence. The calculation
had two trust gaps. OHLCV validation checked an entire security at the final
cutoff, but each later-derived entry/exit pair had no denominator, finiteness,
duplicate, discontinuity or concentration validation. In addition, regional
evidence selected approximately three times the average vintage size across the
whole region, rather than selecting three securities inside each vintage. Thus
an unresolved US adjusted-price discontinuity could dominate pooled means. This
is an adjusted-series label-integrity/concentration failure, not percent-versus-
decimal or FX conversion: every return is `exit_adjusted_close /
entry_adjusted_close - 1` in local-price decimal units, so the currency cancels.
The operator database was not inspected during this repair, and the precise live
symbol/corporate-action cause remains for the bounded retry to identify.

Evaluation now validates every label before evidence is calculated. It reports
finite/nonfinite, invalid and near-zero (`<= 0.01` local adjusted-price units)
denominators, duplicate symbol/vintage labels, and unresolved absolute returns
over `10.0` (1,000%). These are conservative investigation thresholds, not
winsorization: observations remain in the diagnostic distribution and bounded
symbol/date/reason sample, while the result fails closed. Corporate actions
within seven calendar days of the label interval are reported as proximity, not
assumed to resolve the discontinuity. The report also includes decimal-return
minima, maxima, medians, 1/5/95/99 percentiles, regional distributions, top
1/5/10 absolute-contribution concentration, and vintage-size distribution.

All aggregate evidence uses one level: top-three and equal-weight baseline means
are first calculated within a vintage, their difference is the vintage excess,
and temporal rate, conventional mean/median, 10% trimmed mean, and deterministic
95% bootstrap interval are calculated across those equally weighted vintages.
Regional top-three excess is likewise formed region-by-vintage before averaging.
Calibration pools score-rank quintiles and reports both mean and median decimal
returns. Rank correlation is calculated only for vintages having at least two
finite, nonconstant observations; other groups report
`insufficient_group_size` without calling a correlation routine. Robust results
are comparative diagnostics only: they cannot override nonfinite, denominator,
extreme-return, duplicate, concentration, or group-size gates.

Deterministic regressions cover a split-like discontinuity, near-zero price,
nonfinite label, single-observation group, dominant US observation,
concentration-created false pass, a legitimate but unresolved high return, and
a warning-free five-region panel. These fixtures demonstrate the repair; they do
not claim that the live model passes.

## Live label-integrity findings and provenance procedure

The completed live retry retained 41,609 labels. Its maximum forward return was
19,999.0 and it found 29 near-zero adjusted-close denominators. The bounded
extreme sample contains ATPC.US (vintage 2023-08-31, label 2023-09-29), SBET.US
(2025-04-30, 2025-05-29), and AUMN.TO (2023-05-31, 2023-06-29), with no recorded
corporate action within seven days. Top-one absolute contribution was 83.708%,
median excess was -0.001703, 10% trimmed-mean excess was -0.003003, and the
positive-period rate was 0.477876. The ranking remained withheld and both
database files were byte-for-byte unchanged. None of these observations has yet
been removed, capped, winsorized, repaired, or assigned a provider-level cause.

`diagnose-extreme-labels` is the next diagnosis step. It calls the same
model-ready builder and `walk_forward_evidence` panel constructor used by
research scoring, then selects the union of labels beyond the configured
absolute-return threshold and labels at or below the configured near-zero
denominator threshold. For at most 25 affected labels it reports sanitized
entry/exit raw and adjusted prices, returns and volumes; label-window adjusted
range; largest adjacent raw/adjusted ratios; discontinuity position; 7/30/90-day
recorded-action counts; retrieval/source provenance; key duplication state; and
at most five sessions around the largest discontinuity. Aggregate classification
and region counts accompany the sample.

Classifications are evidence flags, not provider-cause claims. They include
near-zero amplification, raw split-like and adjusted discontinuities,
raw/adjusted disagreement, possible ticker reuse, possible missing split
adjustment, isolated malformed observation, and insufficient evidence. In
particular, `possible_*` remains explicitly tentative. The command opens only
the research database in DuckDB read-only mode, refuses missing or aliased
paths, hashes both database files before and after, raises on any change, makes
no provider request, and never constructs a ranking.

## Post-merge PowerShell command

Run from the repository root with distinct real paths:

```powershell
Push-Location backend
& ..\.venv\Scripts\python.exe -m app.eodhd_ingestion_cli diagnose-extreme-labels `
  --research-db "C:\SignalLensData\global-research.duckdb" `
  --production-db "C:\SignalLensData\signallens.duckdb"
Pop-Location
```

Optionally add `--decision-at "2026-09-28T20:00:00+00:00"`. Confirm every
fingerprint is unchanged. Retain the JSON for review without sending database
files or provider payloads; do not treat a classification as an established
provider cause and do not alter observations until that review is complete.

## Historical segmentation and label-integrity repair

Milestone 16 now derives deterministic boundaries from material adjacent-session
raw and adjusted price ratios. Boundaries retain source/retrieval provenance and
explicit evidence codes for raw or adjusted discontinuity, raw/adjusted
disagreement, zero-volume discontinuity, and absence of a nearby recorded
action. They are classifications of unresolved evidence, not invented actions,
and no provider observation is edited or deleted.

Validation is window-aware: a 126-session feature window or 21-session label
interval crossing an unresolved boundary is withheld, while a boundary wholly
before both windows has no effect. Nonfinite/nonpositive denominators and
unresolved returns beyond the existing 1,000% threshold are also withheld.
Near-zero price is reported but is not independently disqualifying; continuous
low-price histories remain eligible unless amplification, policy, or other
integrity evidence applies. Evidence metrics are recomputed only from retained
labels without capping, replacement, winsorization, or silent deletion, with
original/retained counts and exclusions reported separately.

`plan-label-repair` is strictly read-only. It reports boundary aggregates by
reason and region, affected feature/label rows, original/retained/withheld label
counts, bounded provenance samples, and before/after database SHA-256 and byte
counts. It proposes classifications only and always confirms that ranking was
withheld and not generated. The weak prior robust live results (trimmed mean
about -0.0030, median about -0.0017, positive-period rate about 0.4779) remain
weak; removing corrupt evidence is not evidence of strategy success.

```powershell
Push-Location backend
& ..\.venv\Scripts\python.exe -m app.eodhd_ingestion_cli plan-label-repair `
  --research-db "C:\SignalLensData\global-research.duckdb" `
  --production-db "C:\SignalLensData\signallens.duckdb"
Pop-Location
```
