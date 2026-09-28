# Milestone 19: prospective locked shadow portfolios

The 126- and 252-session observations are hypotheses, not validation. Every
output is labelled **RESEARCH SHADOW PORTFOLIO — NOT INVESTMENT ADVICE**. The
workflow cannot place orders, contact a broker, or publish a production ranking.

## Frozen contract

The manifest canonically hashes the existing feature definitions and weights,
model-ready eligibility, adjusted-price segmentation, point-in-time FX rules,
score threshold, maximum three selections, symbol tie-break, equal-weight
eligible-universe baseline, and both horizons. It also stores the Git commit,
decision timestamp and source-database fingerprint. A logic change therefore
gets a new strategy version and starts a separate evidence series.

Promotion remains prohibited until one unchanged version has at least 12 fully
matured prospective vintages for a horizon, positive mean and median excess,
positive-period rate above 0.5, a dependence-aware confidence interval excluding
zero, temporal and regional stability, and passing integrity gates. These gates
are not automatically relaxed. Monthly cohorts overlap, so results are reported
per independent vintage and inference retains the horizon-aware block method.

## Exact monthly PowerShell workflow

Choose a UTC cutoff after the intended market close. Use the same cutoff for the
plan and create commands. Never substitute a historical month.

```powershell
$ResearchDb = "C:\SignalLens\data\research\signallens-research.duckdb"
$ProductionDb = "C:\SignalLens\data\signallens.duckdb"
$Cutoff = "2026-10-30T21:00:00+00:00" # replace with this month's real cutoff

Push-Location backend

# 1. Mutation-free preview; archive and review its JSON output.
& ..\.venv\Scripts\python.exe -m app.eodhd_ingestion_cli plan-shadow-vintage `
  --research-db $ResearchDb --production-db $ProductionDb --decision-at $Cutoff
if ($LASTEXITCODE -ne 0) { throw "Shadow plan failed" }

# 2. Deliberately authorize the research-only transaction.
& ..\.venv\Scripts\python.exe -m app.eodhd_ingestion_cli create-shadow-vintage `
  --research-db $ResearchDb --production-db $ProductionDb --decision-at $Cutoff `
  --authorize-research-shadow
if ($LASTEXITCODE -ne 0) { throw "Shadow creation failed" }

# 3. Confirm immutable vintage/cohort state.
& ..\.venv\Scripts\python.exe -m app.eodhd_ingestion_cli shadow-status `
  --research-db $ResearchDb --production-db $ProductionDb
if ($LASTEXITCODE -ne 0) { throw "Shadow status failed" }

Pop-Location
```

After refreshing validated market data, outcomes may be assessed. Unmatured
cohorts are skipped; incomplete outcomes never contribute to results.

```powershell
$AsOf = "2027-11-01T21:00:00+00:00" # actual evaluation cutoff
Push-Location backend
& ..\.venv\Scripts\python.exe -m app.eodhd_ingestion_cli evaluate-matured-shadows `
  --research-db $ResearchDb --production-db $ProductionDb --decision-at $AsOf
if ($LASTEXITCODE -ne 0) { throw "Shadow evaluation failed" }
Pop-Location
```

Do not backfill, edit stored JSON, combine strategy versions, use incomplete
outcomes, or interpret a shadow selection as a recommendation.
