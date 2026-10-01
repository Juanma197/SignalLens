# Milestone 27 — US fundamentals attribution and failure diagnosis

## Boundary and frozen evidence

This is a **strictly read-only, US-only exploratory diagnosis**, not model
selection. It reuses the Milestone 26 point-in-time SEC layer, eligible current
membership, segmentation, decision vintages, matched observations, and 126/252
labels. Current membership is not survivorship-free. International fundamentals
remain unavailable. The frozen 60% price / 40% fundamental blend, family weights,
and configuration hash
`3441bfb9e8055846bd2223673d3a76ce7c16b892463ce4b4e25a6281161d9a36`
are unchanged.

The observed 126-session result was 101 vintages and 4,670 predictions: price
excess +0.0159358, enhanced excess -0.0256939, incremental -0.0416297, CI
[-0.1209685, +0.0224712], raw p 0.8653, adjusted p 1.0, and concentration
0.1056 versus 0.0495. The observed 252-session result was 97 vintages and 4,265
predictions: price excess -0.0292988, enhanced excess -0.0815601, incremental
-0.0522613, CI [-0.1400109, +0.0202068], raw p 0.8920, adjusted p 1.0, and
concentration 0.1168 versus 0.0515. Both horizons failed; no ranking or candidate
was generated.

## What the diagnosis means

Factor and family tables report coverage, withholding, rank correlation,
quantile spread and monotonicity, positive-vintage rate, temporal halves,
bounded best/worst vintages, contributions, concentration, price correlation,
and top-group overlap. Leave-one-family-out rows hold all other frozen rules
fixed, disclose their comparison count and Holm adjustment, and **cannot be used
as confirmation**. They must never be described as a replacement strategy.

The accounting audit separately counts losses, negative equity/FCF, denominator
hazards, extreme growth, debt-free observations, share discontinuities,
alternative taxonomy concepts, duration composition, staleness, visible
revisions, historical-price use, incompatible units/currencies and overlapping
periods. Evidence is retained: robust summaries do not silently delete or
winsorize observations. Missing data never receives zero, neutral or weak score.
Point-in-time sector/industry is unavailable and must not be inferred.

The plain-finance reading must answer which families helped or hurt, whether the
loss was broad or concentrated, whether coverage shifted through time, and
whether a calculation defect exists. Synthetic fixtures can establish exact
mechanical causes (for example reversed direction, unsafe denominator,
missing-value scoring, sparse early coverage or a concentrated extreme), but
cannot establish the operator-data root cause. Unless a defect is demonstrated,
the honest live conclusion remains an inconclusive or failed frozen hypothesis,
not a reason to tune it.

No weight or factor changes in this milestone. Before another model is
registered, a distinct economic hypothesis, directions, transformations,
coverage rules, horizons, dependence controls, multiplicity family and pass
criteria must be pre-registered, then evaluated once on independent evidence.

## Exact post-merge PowerShell command

Use the backend paths configured for the operator checkout:

```powershell
Push-Location backend
& ..\.venv\Scripts\python.exe -m app.eodhd_ingestion_cli research-us-fundamentals-diagnostics --research-db "data\research\signallens-research.duckdb" --production-db "data\signallens.duckdb" --decision-at "2026-10-01T00:00:00+00:00"
Pop-Location
```

Both explicit paths must exist and be distinct regular, non-symlinked,
non-hard-linked files. They are opened read-only and fingerprinted before and
after. The command makes no provider/network request, displays bounded aggregate
output, raises on mutation, and always emits zero rankings and candidates. Do
not run it against operator data as part of development, do not access Railway,
and do not deploy or merge from this runbook.
