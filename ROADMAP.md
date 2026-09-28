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

## Milestone 9 — Global multifactor shadow research (complete, promotion incomplete)

- [x] Provider-independent raw/report/normalized point-in-time fundamental schemas
- [x] Validated operator-file route for UK, Canada and Europe without prohibited scraping
- [x] Robust peer-relative multifactor features, risk gates and confidence penalties
- [x] Fixed baseline and constrained pre-cutoff learned-weight contract
- [x] Separate immutable shadow vintages, authenticated read API and labelled dashboard
- [x] Structured validation/import/feature/evaluation/vintage/status commands
- [x] Production tables, publisher, scheduler and official cards remain isolated
- [ ] Supply licensed regional data and complete a representative global walk-forward evaluation
- [ ] Approve production promotion (explicitly not approved)

## Milestone 10 — Controlled global research evaluation (pipeline complete; data blocked)

- [x] Separate research database, deterministic manifests, hashes, and import locations
- [x] Leakage audit, next-session boundary, explicit costs, uncertainty, and fixed gates
- [x] Immutable, gate-controlled current shadow storage with zero-candidate support
- [x] Authenticated read-only evaluation status and structured non-mutating commands
- [x] Licensing/coverage register, methodology, operator runbook, and honest evaluation report
- [ ] Acquire licensed historical membership, delistings, prices/actions, FX, and non-US fundamentals
- [ ] Run representative nested walk-forward evaluation (no result fabricated)
- [ ] Create first current shadow ranking (withheld until every gate passes)
- [ ] Production promotion (prohibited by incomplete global data)

## Milestone 11 — EODHD configured-account capability probe (discovery only)

- [x] Confirm the configured credential and EOD endpoint with one bounded request
- [x] Add a mutation-free, provider-independent structured capability report
- [x] Validate free-symbol metadata and EOD OHLCV/adjusted-price fields
- [x] Classify available, restricted, unauthorized, rate-limited and unsupported endpoints
- [x] Make split/dividend checks optional; never assume account-level access
- [x] Enforce timeouts, pacing, bounded retries, response size and total request limits
- [x] Keep the 5,000,000-byte default while applying a hard-capped 16 MiB limit only
  to exchange metadata, with a distinct `response_too_large` classification
- [x] Parse and validate the 70-record exchange list and confirm the 51,071-record US
  symbol list without emitting catalogue or company records
- [x] Redact credentials and test exclusively against sanitized recorded fixtures
- [x] Verify production and research DuckDB files remain byte-for-byte unchanged
- [x] Document Linux and PowerShell operation and honest configured-account limitations
- [ ] Evaluate licensed provider coverage before any ingestion proposal
- [ ] Bulk ingestion, global shadow ranking and production promotion (explicitly out of scope)

## Milestone 12 — Bounded global market-data ingestion (pipeline complete)

- [x] Research-only EODHD catalogue, EOD price, dividend and FX adapters for US,
  LSE, TO, XETRA and PA
- [x] Hard pilot ceilings of 100 securities per region and 500 total
- [x] Conservative ordinary-equity classification, canonical deduplication and
  explicit exclusions
- [x] Ten-year ceiling, adjusted/unadjusted OHLCV validation, point-in-time FX,
  provenance, checkpoints and idempotent writes into the existing schemas
- [x] Plan, dry-run, catalogue, price, FX, resume, status and coverage operations
- [x] Request/runtime/response bounds, production-path refusal and token redaction
- [ ] Licensed historical membership and delisting coverage (current catalogues are
  not survivorship-free)
- [x] Diagnose first 500-security partial run and correct pending checkpoints, run accounting, representative deterministic selection, estimates and coverage
- [ ] Corrected 500-security operator rebuild (requires explicit archive/rebuild review; not run here)
- [ ] Historical-membership backtest or production promotion (prohibited)

## Milestone 13 — Audit and incremental refresh (implementation complete)

- [x] Mutation-free catalogue/price/FX/history/freshness/OHLCV/key/action quality audit
- [x] Bounded-overlap incremental planner for price, dividend and historical FX endpoints
- [x] Idempotent correction upserts, revision counts and resumable budget/runtime stops
- [x] Retryable/pending-only recovery and deliberately authorized periodic reconciliation
- [x] Offline regression coverage and Windows PowerShell operations runbook
- [ ] Run audit and refresh against the operator-held database (not available in cloud)
- [ ] Obtain survivorship-free membership before any historical-membership assertion
- [ ] Production promotion, Railway changes and official `momentum_126d` changes (prohibited)

## Milestone 14 — Trustworthy model-ready observations (implementation complete)

- [x] Pure, offline current-catalogue transformation over existing global market
  data and research feature architecture; no database or provider access
- [x] Five-region identity and trading-currency contract for US, LSE, TO, XETRA
  and PA
- [x] Point-in-time retrieval/FX boundaries, adjusted-close return inputs, GBX
  handling and corporate-action validation
- [x] Fail-closed duplicate-key validation and per-security withholding for
  missing FX, insufficient history, invalid prices/actions, stale evidence and
  permanent provider failures
- [x] Deterministic sanitized five-region fixtures and offline regression tests
- [x] Research-only readiness report that never publishes a rank or forces a
  highest-conviction selection
- [ ] Run the transformation against an operator-approved export or database
  adapter (the operator-held DuckDB was not accessed)
- [ ] Add point-in-time fundamentals and historical membership before controlled
  walk-forward evaluation; current catalogues are not survivorship-free
- [ ] Produce a gated research-only monthly Top 3 and highest-conviction result
  only when evidence thresholds pass
- [ ] Production promotion, Railway changes and official `momentum_126d` changes
  (prohibited)
