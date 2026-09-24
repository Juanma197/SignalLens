# SignalLens Status

Last updated: 2026-09-24

## Current milestone

## Milestone 7 — Global investable-universe discovery (complete)

- [x] Provider-independent point-in-time security master
- [x] Lawful reference-file discovery adapters and Nasdaq Trader parser
- [x] Deterministic company/listing deduplication and canonical selection
- [x] Configurable eligibility with explicit exclusion reasons
- [x] Immutable monthly snapshots and point-in-time FX contract
- [x] JSON operations, authenticated coverage API, and shadow dashboard
- [x] Complete separation from the live 30-stock production ranking path

### Post-release hardening (non-blocking)

- [ ] Replicate validated backups outside the Railway volume
- [ ] Add automated monthly-cycle failure notifications

## Delivered

### Global universe shadow infrastructure

- Immutable, content-addressed listing retrievals preserve source metadata, identifiers,
  local currency, activity, primary/secondary status, and first/last-seen timestamps.
- Ticker collisions are exchange-qualified; LEI, CIK, ISIN evidence, then normalized
  issuer identity drive deterministic company grouping.
- Raw ADRs, secondary listings, alternate share classes, inactive listings, and
  excluded instruments remain stored while one canonical listing is selected.
- Monthly snapshots store the exact retrieval, source timestamp, configuration,
  metrics, decision, and every exclusion reason. Existing months cannot be rewritten.
- GBP is the default reporting currency. Historical conversion remains unavailable
  unless a point-in-time FX observation was actually retrieved by that date.
- Preview is mutation-free; refresh, snapshot, and status are separate explicit JSON
  commands. The private API and dashboard expose shadow coverage and missing/stale data.
- The live 30-stock universe, September 2026 vintage, monthly scheduler, Railway
  configuration, and `momentum_126d` publication logic are unchanged.

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

- All 130 backend tests and Python compilation pass in the release verification environment.
- GitHub Actions passes, including a clean frontend `npm ci`, ESLint, and the optimized production build under Node 24 and npm 11.
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
- Only the public Nasdaq Trader Symbol Directory has a built-in network provider.
  Approved UK, Canadian, and developed-European exchange reference files must be
  licensed/configured by an operator; unavailable providers are not approximated.
- Global price and reliable point-in-time FX ingestion are not part of Milestone 7.
  Global snapshots therefore record missing price/FX coverage and cannot replace
  the production universe or support a new live ranking yet.

## Next action

1. After each monthly invocation, verify the cron exit, API result, completed ledger
   entry, single deterministic vintage, dashboard evidence, and fresh valid backup.
2. Add automated off-platform backup replication and alerting for failed cron
   deployments or cycle-ledger entries.
3. Configure licensed regional reference feeds, then build global price/FX coverage
   and walk-forward validation before considering any production-universe change.
