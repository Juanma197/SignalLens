# SignalLens Status

Last updated: 2026-09-23

## Current milestone

Milestone 6 — private deployment: complete. The API, persistent database,
application-managed backups, authenticated monthly endpoint, and stateless monthly
scheduler are deployed and verified.

## Delivered

### Existing research foundation

- Fixed 30-stock universe with reproducible adjusted daily price history in DuckDB.
- Leakage-safe point-in-time features, 21-trading-day outcomes, and walk-forward evaluation.
- Immutable prediction vintages, latest ranking API, outcome tracking, archive, and personal watchlist.
- Live ranking remains the deterministic 126-trading-day momentum benchmark.
- The benchmark is historically encouraging research evidence, not validated alpha or investment advice.

### SEC filings and fundamentals

- SEC EDGAR filing ingestion with descriptive user-agent support, provenance, timestamps, deduplication, and per-ticker failure records.
- SEC Company Facts ingestion for revenue, net income, diluted EPS, assets, liabilities, and cash.
- Point-in-time fundamental queries enforce both availability and retrieval boundaries.
- Complete, partial, and missing coverage states are exposed through the API and dashboard.
- Full-universe local ingestion completed without failed tickers.
- Repeated ingestion is idempotent.

### Macro context

- FRED ingestion for the federal funds rate, CPI, unemployment, and 10-year Treasury yield.
- Retry and timeout handling for intermittent public-source failures.
- Point-in-time macro API with observation dates, retrieval dates, units, freshness, and missing/stale states.
- The current local snapshot contains all four expected series.

### Public news evidence

- Google News RSS ingestion supplies lawful public headline metadata and publisher attribution.
- Optional GDELT support remains available, with rate-limit failures recorded rather than hidden.
- Full-universe Google News ingestion completed with no failed tickers.
- News storage is content-addressed and idempotent.

### Immutable research snapshots

Every newly published ranking vintage freezes:

- The price-based ranking and strategy provenance.
- Macro observations available at publication.
- Latest SEC fundamental facts available at publication.
- Up to three SEC filings and three news items per selected ticker.
- Source URLs, publication dates, retrieval dates, freshness states, missing coverage, and provider errors.

Older vintages are never rewritten. New evidence cannot silently alter the historical explanation shown for an earlier ranking.

### Dashboard

- Current macro environment panel with source links and freshness.
- Top-three momentum cards with frozen macro, fundamentals, filings, and news.
- Explicit labels distinguish frozen context from the price signal.
- Predicted-versus-actual tracking, watchlist notes, and immutable vintage history remain available.
- Honest unavailable, partial, missing, stale, failed, and pending states are displayed.

### Monthly automation

- One command refreshes price data and all research context before publication.
- A durable UTC-month ledger makes completed runs idempotent and failed runs retryable.
- Each UTC month has a deterministic vintage ID; retries reuse that ID and the
  append-only store rejects collisions instead of overwriting a vintage.
- The runner refuses a missing database rather than silently replacing production data.
- Price refreshes begin after the latest stored observation.
- Publication is explicitly restricted to the live `momentum_126d` strategy; the
  multifactor model remains research-only.
- Windows Task Scheduler and Linux scheduler commands are documented.
- Production runs one API replica with `/data` mounted. The API is the sole DuckDB
  writer; the scheduler has neither a volume nor database access.
- The live Railway cron calls the authenticated API over private networking at
  `0 2 3 * *` UTC and exits after the request.
- Backups are validated under `/data/backups`, retain the three newest valid copies,
  and must be no more than 48 hours old before monthly writes begin.
- The manual scheduler rehearsal reached the API successfully and correctly no-op'd
  because September 2026 was already complete.

## Verification

- All 115 backend tests and Python compilation pass in the release verification environment.
- GitHub Actions passes, including a clean frontend `npm ci`, ESLint, and the
  optimized production build under Node 24 and npm 11.
- The only backend warning is a third-party Starlette/AnyIO deprecation warning.
- The immutable September 2026 production vintage is
  `5dcce39d-f98a-55b1-9010-279501142186` and uses `momentum_126d`.

## Known limitations

- The universe is fixed, so historical results remain exposed to survivorship and selection bias.
- Yahoo Finance and public RSS feeds are research-grade sources, not production market-data guarantees.
- The reported momentum result has no untouched holdout or completed live forward window.
- Fundamentals, filings, news, and macro data are research context only; they do not yet alter the ranking score.
- Fundamental comparison across sectors requires normalization before it can be used responsibly in a model.
- News metadata is not a validated sentiment signal.
- Same-volume backups do not protect against total Railway volume loss; automated
  off-platform backup replication remains outstanding.
- Automated failure notifications are not configured by this repository. Operators
  must check each cron deployment and escalate failures until alerting is added.
- Watchlist mutations are intended only for local/private use.

## Next action

1. After each monthly invocation, verify the cron exit, API result, completed ledger
   entry, single deterministic vintage, dashboard evidence, and fresh valid backup.
2. Add automated off-platform backup replication and alerting for failed cron
   deployments or cycle-ledger entries.
