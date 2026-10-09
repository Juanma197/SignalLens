# Backtest phase 1: replay runbook

Implements phase 1 of [the pre-registration](backtest-preregistration.md):
the prototype's monthly assessment replayed at past dates, without changing
the live rules.

## How the replay sees the past

The live prototype uses only data SignalLens had **retrieved** by the cutoff.
The replay adds a knowledge horizon (the time of the run): a row counts once it
was **public by the cutoff** and retrieved by the run.

| Data | Public from |
|---|---|
| SEC financial facts (as originally filed) | the filing's `public_at` |
| Cover-page share counts | the day after the filing date |
| Prices and sessions | the session's close (22:00 UTC) |
| Dividends | the ex-date |
| SEC filing events (risk flags) | `public_at` |

Taken as known today (documented look-ahead, small): the security
classification, the CIK mapping and the SIC industry code. The catalogue is
today's listings, chosen by today's size (**survivorship; phase 3 adds delisted
companies**). Each month is assessed at 23:59 UTC on its first weekday.

With the horizon equal to the cutoff (live use) every check is unchanged; the
existing prototype tests cover this, and `test_prototype_replay.py` checks the
replay for leakage.

## Commands (PowerShell, from `backend`)

```powershell
$Py = "..\.venv\Scripts\python.exe"

# 1. Are stored closes adjusted for later splits? (read-only)
& $Py -m app.prototype.replay split-check

# 2. Tuning window, 2019-01 to 2022-12 (48 months)
& $Py -m app.prototype.replay run --replay-db data\research\backtest\replay.duckdb --note "first look, survivors only"

# 3. Stored runs
& $Py -m app.prototype.replay list --replay-db data\research\backtest\replay.duckdb
```

The holdout (2023-01 onwards) needs `--holdout --start 2023-01 --end 2026-09`
and runs only once per replay database. Do not run it until the rules are final.

Both databases are opened read-only and must be byte-identical after the run
(SHA-256), so do not run the replay while an ingestion or the daily alert
update is writing. Results go only to the replay database.

## What is stored

`replay_runs`: one row per run (knowledge horizon, months, holdout flag, rules
version, configuration hash, research database SHA-256, status).
`replay_months`: one row per month with the SHA-256 of the full report and a
compact JSON:
- eligible companies, with close, market cap, automatic thesis signs and warnings;
- every value-ranking assessment (status, upside, conviction, risk, score);
- the picks;
- reasons companies were withheld.

Phase 2 measures returns from this. Nothing here is a result yet.
