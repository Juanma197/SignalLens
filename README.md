# SignalLens

## Milestone 29: database-backed prospective readiness

The prospective US shadow now plans directly from the authoritative research
database. Operational planning no longer accepts a hand-authored fixture; the
fixture command is explicitly offline/test-only. Creation still requires a fresh
database-bound plan identifier and deliberate operator authorization, which the
scheduler cannot supply. See the [Milestone 29 operator runbook](docs/milestone-29-database-shadow-readiness.md).

**Do not run the October command until the complete October 2026 month-end US
session, price, applicable FX, and SEC refresh data are available.**

## Milestone 28: prospective US paper research

SignalLens now contains an immutable, prospective-only US hypothesis: 90% of
the existing price percentile plus a 10% point-in-time dilution percentile.
It is **not validated and not investment advice**. The frozen specification,
strict month-end boundary, read-only planning/status workflow, exact-session
maturity rules, and promotion gates are documented in
[`docs/milestone-28-prospective-us-shadow.md`](docs/milestone-28-prospective-us-shadow.md).
No historical vintage is reconstructed and no production recommendation is
created.

## Milestone 27 fundamentals failure diagnosis

SignalLens now includes a bounded, strictly read-only attribution command for
the failed frozen US fundamentals experiment. It reports factor/family,
missingness, accounting and concentration diagnostics and leave-one-family-out
counterfactuals without changing a factor or weight. Everything is labelled
**EXPLORATORY DIAGNOSTICS — NOT A NEW MODEL** and produces no ranking or
candidate. See the [Milestone 27 runbook](docs/milestone-27-us-fundamentals-diagnostics.md).

```powershell
Push-Location backend
& ..\.venv\Scripts\python.exe -m app.eodhd_ingestion_cli research-us-fundamentals-diagnostics --research-db "data\research\signallens-research.duckdb" --production-db "data\signallens.duckdb" --decision-at "2026-10-01T00:00:00+00:00"
Pop-Location
```

The Milestone 26 frozen blend failed at both 126 sessions (101 vintages, 4,670
matched predictions, -0.0416297 incremental excess) and 252 sessions (97
vintages, 4,265 predictions, -0.0522613). Current membership is not
survivorship-free, scope is US-only, and international fundamentals are absent.

## Milestone 25 SEC research ingestion

SignalLens now has a resumable, research-only point-in-time SEC fundamentals
store for the 100-security US catalogue, plus a storage-free documentary source
assessment for LSE, TO, XETRA and PA. It remains isolated from scoring and the
production publisher; no ranking or candidate can be produced. Planning and
status are read-only, while ingestion requires an exact authorization phrase and
contact-bearing SEC User-Agent. See the [Milestone 25 runbook](docs/milestone-25-sec-ingestion.md).

## Milestone 21 autonomous research operations

SignalLens now includes persistent sanitized operation history, deterministic
fail-closed research orchestration, explicit UTC schedules, verified research
database backups, safe notification adapters, and an operator-first summary.
Scheduling is disabled by default and staging is entirely read-only. Railway uses
separate backend and frontend services and a backend volume mounted at `/data`.
See the [Milestone 21 runbook](docs/milestone-21-operations-deployment.md).

No workflow can publish production rankings, contact a broker, or create a shadow
vintage without separate human authorization.

## Milestone 20 research operations

The Next.js application now provides a mobile-friendly `/operations` dashboard for
read-only status and deliberately authorized research workflows. Shadow plan responses
are bounded by default, month-end creation requires explicit regional session dates and
FX readiness, and production publishing remains unavailable. See the
[operator/deployment-readiness runbook](docs/milestone-20-operations-runbook.md).

**RESEARCH ONLY — NOT INVESTMENT ADVICE.** Current catalogue membership is not
survivorship-free and fundamentals remain unavailable under the current EODHD entitlement.

SignalLens is a research dashboard for periodically ranking public companies and tracking each ranking against what happened afterward. It is decision support, not financial advice.

## Current milestone

Milestone 19 adds prospective, monthly, research-only shadow portfolios for the
locked 126- and 252-session exploratory hypotheses. Each vintage freezes its
manifest, complete eligible score universe, zero-to-three deterministic
selections, entry inputs, and equal-weight baseline. These records are isolated
from production and are explicitly **not investment advice**. No historical
vintages may be backfilled. See
[`docs/milestone-19-shadow-portfolios.md`](docs/milestone-19-shadow-portfolios.md).

```powershell
Push-Location backend
& ..\.venv\Scripts\python.exe -m app.eodhd_ingestion_cli plan-shadow-vintage --research-db "C:\data\research.duckdb" --production-db "C:\data\production.duckdb" --decision-at "2026-10-30T21:00:00+00:00"
& ..\.venv\Scripts\python.exe -m app.eodhd_ingestion_cli create-shadow-vintage --research-db "C:\data\research.duckdb" --production-db "C:\data\production.duckdb" --decision-at "2026-10-30T21:00:00+00:00" --authorize-research-shadow
Pop-Location
```

### Prior milestone

Milestone 18 adds a strictly read-only, pre-registered multi-horizon evaluation
over 21, 63, 126 and 252 trading sessions. It reuses the validated price, FX,
segmentation and model-ready paths, applies a horizon-aware moving-block
bootstrap and Holm-Bonferroni family correction, and always emits zero
candidates. The failed 21-session result remains frozen rather than replaced.
See [`docs/milestone-18-horizon-evaluation.md`](docs/milestone-18-horizon-evaluation.md).

```bash
cd backend
PYTHONPATH=. python -m app.eodhd_ingestion_cli research-horizon-evaluation \
  --research-db /path/to/research.duckdb \
  --production-db /path/to/production.duckdb \
  --decision-at 2026-09-28T20:00:00+00:00
```

The command opens the research database read-only, never contacts a provider,
and fingerprints both database paths before and after the run.

### Earlier milestone

Milestone 17 adds a bounded `fundamentals-capability` assessment for historical
EODHD statements. It probes at most one representative security in each of US,
LSE, TO, XETRA and PA, emits aggregates only, and requires a filing/accepted/
reporting date before a record can enter a historical vintage. It does not ingest,
score, rank, publish or write a database. The default documented workflow is the
no-network fixture mode; see
[`docs/milestone-17-fundamentals-capability.md`](docs/milestone-17-fundamentals-capability.md).

Milestone 24 adds a storage-free, bounded SEC EDGAR fundamentals pilot. It is
offline by default, selects at most three eligible US securities from the active
research catalogue, and proves both databases unchanged. See the dedicated
[`SEC operator runbook`](docs/milestone-24-sec-fundamentals-pilot.md).

```bash
cd backend
python -m app.sec_capability_cli sec-fundamentals-offline --research-db /absolute/path/research.duckdb --production-db /absolute/path/production.duckdb
SIGNALLENS_SEC_USER_AGENT='SignalLens Research ops@example.com' python -m app.sec_capability_cli sec-fundamentals-live --authorize-live-sec --research-db /absolute/path/research.duckdb --production-db /absolute/path/production.duckdb
```

After merge, run the deterministic offline assessment from PowerShell:

```powershell
Push-Location backend
& ..\.venv\Scripts\python.exe -m app.fundamentals_capability_cli fundamentals-capability --fixture --research-db "C:\path\to\research.duckdb" --production-db "C:\path\to\production.duckdb"
Pop-Location
```

## Requirements

- Windows PowerShell 5.1+
- Python 3.12+
- Node.js 20+
- Git

## Start the application

Open PowerShell in the project folder and run:

```powershell
.\start.ps1
```

- Dashboard: http://localhost:3000
- Backend health: http://127.0.0.1:8000/api/v1/health
- Data status: http://127.0.0.1:8000/api/v1/data/status
- API documentation: http://127.0.0.1:8000/docs

## Download reproducible price history

From the project root, after running the launcher once:

```powershell
Push-Location backend
& ..\.venv\Scripts\python.exe -m app.ingest --start 2015-01-01
Pop-Location
```

The command stores daily unadjusted OHLC, adjusted close, volume, source, ingestion time, and ingestion-run provenance in `backend/data/signallens.duckdb`. The database is local research data and is excluded from Git.

## Refresh research evidence

Set a descriptive SEC user agent before using SEC endpoints:

```powershell
$env:SIGNALLENS_SEC_USER_AGENT = "SignalLens research your-email@example.com"
```

Then ingest the current universe:

```powershell
Push-Location backend
& ..\.venv\Scripts\python.exe -m app.ingest_evidence --limit 3
& ..\.venv\Scripts\python.exe -m app.ingest_fundamentals --periods 8
& ..\.venv\Scripts\python.exe -m app.ingest_macro
& ..\.venv\Scripts\python.exe -m app.ingest_news --limit 3 --lookback-days 30
Pop-Location
```

Publishing a ranking is an explicit action. It freezes the price ranking and all available context:

```powershell
Push-Location backend
& ..\.venv\Scripts\python.exe -c "from app.config import get_settings; from app.market_data import MarketDataRepository; from app.rankings import publish_latest_momentum_ranking; repository=MarketDataRepository(get_settings().database_path); print(publish_latest_momentum_ranking(repository))"
Pop-Location
```

## Automated monthly research cycle

The monthly runner performs the same workflow in a fixed order: append new price
history, refresh SEC filings, refresh SEC fundamentals, refresh FRED macro data,
refresh public news metadata, and finally publish the **existing 126-day momentum**
ranking. The multifactor model remains research-only and is not used by this job.

Run it manually from Windows Task Scheduler or schedule this command once per
month (after a US market close):

```powershell
.\scripts\monthly-research-cycle.ps1
```

For a Linux scheduler whose working directory is `backend`, use:

```bash
python -m app.monthly_cycle
```

Production readiness and a mutation-free rehearsal are available with
`python -m app.monthly_cycle --preflight` and `python -m app.monthly_cycle --dry-run`.
Create the first application-managed **research** backup only with the deliberate
authorization command
`python -m app.database_backup create-initial --authorize "CREATE INITIAL RESEARCH BACKUP"`.
It opens the research database read-only, rejects the production database, refuses
to run when a managed backup already exists, and leaves unrelated manual backups
untouched. Verify a managed backup with
`python -m app.database_backup verify /data/backups/<backup>.duckdb`. Production
monthly runs then create a validated backup before any cycle mutation and retain
three by default (`SIGNALLENS_BACKUP_RETENTION_COUNT`). Same-volume backups cover
corruption and operator mistakes, not loss of the entire Railway volume; see the
runbook for safe restore and off-platform guidance.
The exact, intentionally-not-enabled Railway architecture, UTC schedule, variables,
and rollback procedure are in [`docs/railway-monthly-cycle.md`](docs/railway-monthly-cycle.md).

The scheduled process must use the same persistent database path and secrets as
the API (`SIGNALLENS_DATABASE_PATH`, `SIGNALLENS_SEC_USER_AGENT`, and
`SIGNALLENS_FRED_API_KEY`). Do not schedule a separate service with an ephemeral
or independent volume. The runner refuses to create a missing database, appends
only dates newer than the latest stored price, records failures for safe retry,
and returns the already-published vintage when the same UTC month is invoked
again. Each month also has a deterministic reserved vintage ID, so a retry after
publication can only reuse the existing immutable row; a colliding insert fails
rather than updating it. Back up the persistent database before enabling the
schedule.

## Verify the project

```powershell
.\scripts\test.ps1
```

## Project layout

```text
SignalLens/
├── backend/app/         API, universe, ingestion and DuckDB repository
├── backend/tests/       API and data-quality tests
├── backend/data/        Local database (ignored by Git)
├── frontend/            Next.js dashboard
├── scripts/             Windows startup and verification
├── ROADMAP.md           Planned milestones
└── STATUS.md            Current verified state
```

## Important

The live ranking is the historically encouraging 126-trading-day momentum benchmark. Fundamentals, filings, news, and macro data currently provide frozen research context; they do not yet alter the score. SignalLens is decision support, not investment advice.

## Milestone 26 US point-in-time fundamentals

A deterministic, read-only, **US-only** comparison now tests the unchanged price model against a pre-registered fundamental enhancement. See the [Milestone 26 runbook](docs/milestone-26-us-fundamentals.md) for frozen formulas, weights, safeguards and the operator command. International markets remain on the price-only baseline; no candidates or recommendations are generated.
