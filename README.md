# SignalLens

SignalLens is a research dashboard for periodically ranking public companies and tracking each ranking against what happened afterward. It is decision support, not financial advice.

## Current milestone

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

### Prior milestone

Milestone 17 adds a bounded `fundamentals-capability` assessment for historical
EODHD statements. It probes at most one representative security in each of US,
LSE, TO, XETRA and PA, emits aggregates only, and requires a filing/accepted/
reporting date before a record can enter a historical vintage. It does not ingest,
score, rank, publish or write a database. The default documented workflow is the
no-network fixture mode; see
[`docs/milestone-17-fundamentals-capability.md`](docs/milestone-17-fundamentals-capability.md).

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
Create and verify the initial application-managed backup with
`python -m app.database_backup create` and
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
