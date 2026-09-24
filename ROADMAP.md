# SignalLens Roadmap

Each milestone must end with passing tests, a production frontend build, an updated `STATUS.md`, and a Git checkpoint.

## Milestone 1 — Application foundation (complete)

- Monorepo structure
- FastAPI backend and Next.js frontend
- Health check and demo ranking contract
- Windows startup and test scripts
- Documentation and Git baseline

## Milestone 2 — Research data foundation (complete)

- Define investable universe and ranking horizon
- Store reproducible market data in DuckDB
- Adjust prices for splits and distributions
- Validate gaps, duplicates, coverage, and freshness
- Expose data status in the dashboard

## Milestone 3 — Honest baseline model (complete)

- Point-in-time features with no look-ahead leakage
- Walk-forward evaluation
- Baseline comparison and transaction-cost assumptions
- Prediction vintages stored immutably

## Milestone 4 — Ranking experience (complete)

- Monthly top-one/top-three view
- Evidence, risks, uncertainty, and model explanation
- Predicted-versus-actual tracking
- Watchlist and research notes

## Milestone 5 — Broader evidence (complete)

- Fundamentals, filings, news, macro data, and lawful public disclosures
- Source timestamps and provenance
- Missing-data and stale-data handling

## Milestone 6 — Private deployment (complete)

- [x] Authentication and secrets management
- [x] Continuous integration
- [x] Private GitHub repository
- [x] Managed frontend, API, and database deployment
- [x] Application-managed backups with freshness and retention gates
- [x] Stateless scheduled monthly ranking job through the authenticated API

### Post-release hardening (non-blocking)

- [ ] Replicate validated backups outside the Railway volume
- [ ] Add automated monthly-cycle failure notifications

## Milestone 7 — Global investable-universe discovery (complete)

- [x] Provider-independent, point-in-time security-master schema and parsers
- [x] Stable listing/company identity and deterministic canonical selection
- [x] Explicit configurable eligibility decisions and exclusion reasons
- [x] Immutable monthly shadow-universe snapshots
- [x] Point-in-time FX interface/schema without fabricated conversion rates
- [x] JSON preview, refresh, snapshot, and coverage commands
- [x] Authenticated API and dashboard shadow-coverage reporting
- [x] Production ranking, scheduler, Railway, and live 30-stock universe isolation

### Required follow-on validation

- [ ] License and configure approved UK, Canadian, and developed-Europe reference feeds
- [ ] Ingest global point-in-time prices and FX observations
- [ ] Complete global walk-forward validation before proposing a live-universe change

## Guardrails

- Never describe a rank as guaranteed growth.
- Never train on information unavailable at prediction time.
- Keep research results separate from real-money execution.
- Preserve every published prediction so performance cannot be rewritten later.

## Milestone 8 — Global market data infrastructure (complete)

- [x] Validated operator-file adapters for listings, prices, actions, and FX
- [x] Idempotent bounded ingestion with batching, retries, checkpoints, and failures
- [x] Historical GBP conversion, including GBX and bounded missing/stale FX handling
- [x] Venue-specific exchange calendars and authenticated shadow coverage
- [x] Research-only eligibility and representative cross-region fixtures
- [x] Production universe, publisher, vintages, scheduler, and Railway isolation
- [ ] License/configure complete regional operator feeds and ingest at scale
- [ ] Add global fundamentals and multifactor scoring
- [ ] Complete global walk-forward validation
- [ ] Review and explicitly approve any production promotion
