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
- eligible companies, with close, market cap and automatic thesis signs;
- every value-ranking assessment (status, upside, conviction, risk, score);
- the picks;
- reasons companies were withheld.

Phase 2 measures returns from this. Nothing here is a result yet.

## Phase 2: measuring a run

```powershell
# Index funds, including VT (global stocks, standing in for VALL): one EODHD request each.
& $Py -m app.prototype.benchmarks --prototype-db data\prototype\signallens-prototype.duckdb

# Measure the latest completed run (or --run-id ID); the result is also stored in replay_reports.
& $Py -m app.prototype.measure --replay-db data\research\backtest\replay.duckdb
```

All returns are in pounds and include dividends: adjusted closes converted at
each session's stored GBP/USD rate, entering and leaving at the first session
after each month's cutoff. The report has:

- **picks**, for 1, 3, 6 and 12 months:
  - the Top 3's average return against the eligible average, which is what
    random picks earn on average;
  - the skill interval, from a block bootstrap over months;
  - where the picks fall among 1,000 random-pick strategies;
  - the hit rate, and the share of months beating VT and SPY;
- **ranking buckets**: excess return by ranking status (candidate, watch, not
  undervalued, value trap), which shows whether the value-trap filter avoided
  losers;
- **policy**: the live rules run on simulated money:
  - £200 a month into the cash pool, at most 10 holdings, the 25% limit and
    fractional shares;
  - costs of 0.15% currency conversion plus a 0.1% spread on every trade,
    executed at the next session;
  - results reported as money-weighted and time-weighted returns, drawdown,
    trades, costs and the average cash share;
- **global_index / sp500**: the same deposits into VT (bought in pounds, no
  conversion fee) and SPY;
- **criteria**: the pre-registered verdict. It is never decisive before the
  holdout *and* phase 3.

Simplification: positions are held in adjusted-close units, so dividends are
reinvested in the same company rather than paid into the cash pool.
