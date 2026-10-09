# SignalLens

## Unvalidated 15-company prototype — first slice

The `/prototype` page opens with **Top 3 undervalued candidates**: every eligible
company is ranked by middle-scenario upside x conviction x risk after value-trap
exclusions (losses, negative free cash flow, materially falling revenue, negative
equity). At most three picks, never forced; every reason is shown. Rules live in
`backend/app/prototype/ranking.py`. Unvalidated arithmetic, not advice.

`/prototype/checks` re-checks every holding and watched company against your own
thesis conditions and automatic warning signs, so a broken thesis shows up even
when the price has not fallen. `/prototype/monthly` brings it together: the Top 3
plus BUY MORE / HOLD / REDUCE / SELL / REVIEW for every holding, with reasons, and a
suggested allocation of your cash pool within a 25% position limit. Recording a month
freezes its calls; `/prototype/scorecard` later shows whether each was right.

To widen the US catalogue from a 100-company sample to every listing near the size
band (about 2,000), follow [the widening runbook](docs/us-catalogue-widening.md).

The separate `/prototype` view proposes a deterministic, unfrozen membership and
zero-to-three positive 126-session momentum results from stored evidence only.
Company details show calculations, citations, risks and exact missing-data states.
Run it locally with `.\scripts\start-prototype.ps1 -Operator`; the [monthly routine](docs/prototype-monthly-routine.md) covers refresh, review and snapshots. Membership
review is required before any freeze. Track A/B are unchanged and validation credit is zero. See [the PowerShell verification/startup and roster-review
runbook](docs/prototype-first-slice.md). Watchlist/snapshot persistence and subsequent
performance tracking are deferred to a separate prototype database.

`/prototype/portfolio` records your own trades (append-only, voidable) and shows
holdings at average cost, valued at the latest stored close. It also keeps the
**cash pool**: uninvested pounds that carry over each month, built only from
deposits you confirm and the pound total your broker reports for each trade (sale
proceeds return to the pool). Your plan, meaning the monthly contribution (£200 to
start) and the maximum number of holdings (10), can be changed at any time. The
monthly allocation spends the cash pool, optionally plus this month's planned
contribution, and opens new names only while holdings stay within the maximum.

## Milestone 36: investment-grade research foundation

SignalLens now provides strictly read-only investment coverage, repair-planning,
execution-cost capability, and future undervalued-quality readiness reports. The
existing 90/10 prospective hypothesis remains frozen as Track A. Track B is
explicitly **not a model** and produces zero recommendations, candidates, rankings,
paper selections, prospective vintages, or validation credit. See
[`docs/milestone-36-investment-grade-research.md`](docs/milestone-36-investment-grade-research.md).

## Prospective paper-portfolio cycle (Milestone 35)

SignalLens now exposes an operator-controlled, read-only month-end plan, immutable
official paper vintages, exact-session mark-to-market reports, and an aggregate
prospective validation ledger at `/validation`. The registered model remains
`prospective-us-dilution-1.0.0` (90% price / 10% dilution). Interim 21/63-session
returns receive zero validation credit; only complete exact 126/252-session
outcomes can enter the ledger. Creation still requires the immediately preceding,
database-bound expiring plan and the exact separate authorization phrase. See the
[Milestone 35 runbook](docs/milestone-35-prospective-validation.md).

**PAPER RESEARCH ONLY · NO BROKER ACTIVITY · INTERIM RETURNS ARE NOT VALIDATION ·
INSUFFICIENT PROSPECTIVE EVIDENCE.**

## Read-only Model Laboratory (Milestone 34)

The authenticated `/model` laboratory exposes the exact frozen 90/10 mathematics and a bounded, non-persistent indicative Top-3 preview. It also fails closed when September 2026 cannot be reconstructed from strictly point-in-time evidence. This is **NOT VALIDATION**, **NOT A PAPER SELECTION**, and **NOT INVESTMENT ADVICE**. See the [Milestone 34 runbook](docs/milestone-34-model-laboratory.md).

## Safe Railway research synchronization (Milestone 33)

An operator-controlled staging workflow now validates an uploaded research
database, creates a verified rollback artifact, isolates readers, and atomically
publishes or restores it while fingerprinting the untouched production database.
Scheduling and production publication remain unavailable. Follow the
[Milestone 33 Railway runbook](docs/milestone-33-railway-research-sync.md); never
overwrite `/data/signallens.duckdb`.

## Explainable company research (Milestone 32)

SignalLens now provides bounded, point-in-time company briefs through the read-only CLI, authenticated research API, and `/research` dashboard. The brief separates the frozen 90% price / 10% dilution score from financial and official-SEC-event context, and never produces an investment recommendation. See the [Milestone 32 runbook](docs/milestone-32-company-research-briefs.md) for its structure, safety rules, citations, pre-vintage behavior, and exact PowerShell commands.

## Milestone 31: research-only SEC material events

Official SEC submissions metadata for 8-K, 8-K/A, 6-K, and defensible 6-K/A
records can now be ingested transactionally using the existing stored CIK map.
Acceptance time is the sole public-availability boundary; filing bodies and
exhibits are never requested or stored. The dashboard labels this evidence
**OFFICIAL FILING CONTEXT — NOT USED IN SCORE**. See the
[Milestone 31 runbook](docs/milestone-31-sec-events.md).

## Milestone 30: point-in-time news and company events

SignalLens now has provider-neutral, research-only event records, deterministic
issuer matching/deduplication, bounded SEC 8-K readiness, and a source capability
matrix. News remains **CONTEXT ONLY — NOT USED IN SCORE** and produces no news
recommendation. The prospective model remains frozen at 90% price and 10%
dilution. See the [Milestone 30 runbook](docs/milestone-30-news-events.md).

```bash
cd backend
python -m app.news_events_cli news-events-capability --research-db /absolute/path/research.duckdb --production-db /absolute/path/production.duckdb
```

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

## Milestone 37: comparable-universe evidence repair

Track B now separates ordinary US operating companies from special security/economic types and exposes read-only, point-in-time raw value and financial-strength capabilities. It remains **TRACK B RESEARCH FOUNDATION — NOT A MODEL**, with no candidates, rankings, recommendations, selections, vintages, or validation credit. See [the Milestone 37 runbook](docs/milestone-37-comparable-universe-repair.md).
# Milestone 38

Authorized research-only classification and canonical factor-evidence materialization is documented in [the Milestone 38 runbook](docs/milestone-38-investment-evidence-materialization.md). It preserves original point-in-time availability, production isolation, and the strict Track B no-model/no-recommendation boundary.
