# SignalLens Status

Last updated: 2026-09-28

## Current milestone

## Milestone 17 — Point-in-time fundamentals capability (implementation complete)

- [x] Added a no-network fixture assessment and an explicitly authorized live
  probe capped at five representative securities and five total requests.
- [x] Added public-availability boundaries, revision-aware as-of selection,
  annual/quarterly separation, explicit currency/unit evidence, and current-
  summary leakage prevention.
- [x] Reports only aggregate field/date/currency/period coverage and feature
  classifications; raw payloads, company records, URLs, tokens, provider errors,
  and sensitive values cannot enter output.
- [x] Fingerprints research and production databases without opening either as a
  database, and verifies unchanged files after assessment.
- [ ] Live provider capability is not claimed until the operator runs the bounded
  command after merge. No bulk ingestion, scoring, gate, publisher, production,
  Railway or deployment change is included.
- [ ] Current-catalogue evaluation remains non-survivorship-free.

## Milestone 14 — Trustworthy model-ready observations

- [x] Added a pure research transformation that accepts caller-provided current
  catalogue, adjusted OHLCV, corporate-action, point-in-time FX and structured
  failure frames. It does not open DuckDB or make network requests.
- [x] Enforced exact five-region coverage and venue currencies for US, LSE, TO,
  XETRA and PA, unique identities and natural keys, timezone-aware retrieval
  boundaries and explicit current-catalogue provenance.
- [x] Invalid prices/actions, missing or stale FX, stale prices, fewer than 127
  sessions and permanent provider failures are withheld with explicit reasons.
  Dataset-wide duplicate/identity defects reject the build entirely.
- [x] Reused the existing global research momentum feature implementation,
  provider-adjusted close semantics and GBP/GBX conversion rules instead of
  introducing a parallel model or publisher.
- [x] The readiness report is labelled `RESEARCH ONLY — NOT INVESTMENT ADVICE`,
  publishes no ranking, and leaves highest conviction unavailable. A future Top
  3 remains conditional on sufficient eligible evidence and evaluation gates.
- [x] Sanitized deterministic fixtures cover every pilot region and exercise
  valid data plus missing FX, insufficient history, invalid/stale prices, stale
  FX, duplicate observations, invalid actions and permanent provider failures.
- [ ] The operator-held 499-security DuckDB and its backup were not accessed,
  inspected, modified or required. No claim is made about its actual model-ready
  row count.
- [ ] Current catalogues are not survivorship-free. Historical membership,
  delistings, point-in-time fundamentals and representative walk-forward evidence
  remain prerequisites for any ranked output or promotion.

## Milestone 13 — Pilot audit and safe incremental refresh

- [x] Read-only aggregate quality audit with bounded sanitized affected-symbol output.
- [x] Seven-day-overlap incremental price/dividend/FX planning and deterministic,
  revision-counting execution with resumable safety stops.
- [x] Routine refresh excludes permanent and unknown/nonretryable failures; only
  explicitly retryable failures are automatic. Permanent retries require both
  the dedicated `retry-failures` operation and a separate authorization flag.
- [x] Refresh plans report eligible/skipped/pending aggregates and default to a
  ten-record sanitized request sample. Offline regression coverage confirms the
  reported 500-security state estimates 1,001 requests after skipping its one
  permanent failure; the local database itself was not inspected.
- [x] Separately authorized full reconciliation remains available.
- [x] PowerShell operator runbook and explicit production-path, token, payload,
  request, response, pacing, timeout and runtime controls.
- [x] Operator-reported local state: 500 selected, 499 price histories completed,
  zero pending, complete three-pair FX, and `AIIA-U.US` retained as
  `invalid_provider_payload`; approximately 78 MB with a SHA-256-verified backup.
- [ ] No live audit or incremental refresh was performed in the cloud environment;
  its ignored local database was not available, inspected, uploaded, or fabricated.
- [ ] Catalogue membership remains current-only, not survivorship-free, and is
  unsuitable for production promotion or historical-membership claims.

## Milestone 12 — Bounded global market-data ingestion (live venue aliases corrected)

- [x] Resumable EODHD pipeline for US, LSE, TO, XETRA and PA, limited to 100
  securities per region and 500 total.
- [x] Current catalogue metadata is conservatively classified; ETFs, funds,
  indices, preferreds, warrants, depositary receipts and ambiguous types retain
  explicit exclusion reasons. Existing canonical company logic removes secondary
  listings.
- [x] Existing security-master, global price, corporate-action and FX schemas are
  reused. Prices retain adjusted and unadjusted OHLCV, provider, retrieval time,
  currency and exchange-qualified symbol; dividends are imported and splits are
  explicitly `provider_unsupported`.
- [x] Ten-year ceiling, monotonic/duplicate/date/OHLC/volume validation,
  point-in-time FX availability, idempotent keys and resumable checkpoints.
- [x] Plan and mutation-free dry-run plus catalogue, price, FX, resume, status and
  coverage operations; production database paths are refused.
- [x] Configurable daily/minute request budgets, retries, timeouts, response size,
  maximum runtime and redacted failures.
- [x] Disposable live smoke test stayed within one security per region (five total):
  6,918 validated price rows and 8,042 point-in-time FX rows were written, coverage
  was available for all five regions and all three FX pairs, then the database was
  deleted. The full pilot was not run.
- [ ] The current catalogue is not survivorship-free and is invalid for historical-
  membership backtests, model promotion or production use.
- [x] The first operator 500-security run exposed a 900-second checkpoint/accounting defect: 41 completed securities (55,617 rows, 82 requests) were LSE-only and 459 unattempted securities were mislabeled failures; `latest_run` was null.
- [x] Runtime/request stops now preserve pending checkpoints and return `partial_checkpointed`; resume processes only pending items and sanitized actual failures remain separate.
- [x] Trading-currency/venue primary-equity selection separates issuer domicile from listing venue, normalizes provider aliases, rejects receipts/OTC/clear secondary listings and acquisition vehicles, deduplicates issuers, and uses reproducible non-alphabetical hash sampling without claiming liquidity rank.
- [x] The earlier domicile-regression diagnostic remains documented: five requests
  examined 67,038 records and demonstrated that provider `Country` is issuer
  domicile rather than listing venue. Its repair made no provider requests.
- [x] Aggregate-only local catalogue diagnostics expose normalized country, exchange, currency and type counts without records or credentials.
- [x] Zero-total or zero-region refreshes return `failed_validation`, preserve prior selections, and block price ingestion; valid reports include region/currency counts.
- [x] Live Toronto and Paris provider venue values are `TO` and `PA`; endpoint
  identity is accepted without inventing TSX/NEO/PARIS sub-venues, while raw fields
  retain provenance and TO CDRs remain excluded by receipt and duplicate evidence.
- [x] Catalogue output separates proposed `candidate_accepted` counts from
  `activated_selection_count`; dry-runs and failed validation activate nothing.
- [x] The post-PR-19 live catalogue examined 67,038 records in five requests and
  found 100 candidates each for US/LSE/XETRA but zero for TO/PA. Fail-closed
  validation prevented activation and preserved production; this local repair made
  no further provider request.
- [x] Planning exposes request bounds and separate pacing/observed/timeout estimates; coverage exposes selection mix, progress, FX gaps, history depth/freshness, and complete run accounting.
- [ ] No corrected bulk ingestion, shadow ranking, deployment, Railway access or production change was performed.

## Milestone 11 — EODHD capability probe (paid entitlement validated)

- [x] A one-request prerequisite probe confirmed that the credential exists (23
  characters; value never displayed) and `AAPL.US` EOD returned HTTP 200.
- [x] The prerequisite response contained 16 rows from 2026-09-01 through
  2026-09-23 and OHLC, adjusted close and volume fields.
- [x] Read-only JSON capability command with explicit endpoint classifications,
  validated metadata/OHLCV, historical depth, exchanges and limitations.
- [x] Hard request/response bounds, timeout, pacing and bounded retry/backoff.
- [x] Optional split/dividend checks that do not assume subscription access.
- [x] Token-safe output/errors plus sanitized offline fixtures.
- [x] Before/after byte count and SHA-256 verification for both DuckDB paths.
- [x] Separate metadata-only 16 MiB ceiling corrected the false US catalogue
  rejection without relaxing the conservative 5,000,000-byte default.
- [x] Bounded paid-account follow-up: EOD (11,539 rows, 1980-12-12 through
  2026-09-25), exchange list (70 records), US symbols (51,071), and adjusted close
  available; splits unsupported and dividends available (57 rows), in five requests.
- [x] Local production and research database paths stayed absent and unchanged
  (zero bytes and null SHA-256 before/after); no database was created.
- [x] Linux and PowerShell runbook; no ingestion, ranking, scheduler or deployment.
- [ ] No broad provider suitability conclusion: only the designated symbol and US
  catalogue have been demonstrated, and account capabilities may change upstream.

## Milestone 10 — Controlled global research evaluation (pipeline complete; evaluation unavailable)

- [x] Isolated research database with deterministic, checksum-addressed manifests
- [x] Point-in-time leakage and next-session execution audit contract
- [x] Predetermined coverage, stability, cost, robustness, uncertainty and concentration gates
- [x] Immutable gate-controlled shadow-vintage storage, including zero-candidate behavior
- [x] Authenticated read-only evaluation API and structured dry-run-safe commands
- [x] Source/licensing matrix, methodology, limitations, storage estimate and operator runbook
- [ ] Historical operator/licensed dataset (none is present in the repository)
- [ ] Supportable walk-forward results (no synthetic or fabricated performance)
- [ ] Current shadow candidates (withheld until data, freshness and validation gates pass)
- [ ] Global-model promotion (prohibited)

## Milestone 9 — Global multifactor shadow research (complete; promotion incomplete)

- [x] Point-in-time raw and normalized fundamentals with revision lineage
- [x] Research lenses, peer fallback hierarchy, robust outlier handling and risk gates
- [x] Fixed baseline plus constrained learned-weight training boundary
- [x] Immutable research-only vintages and authenticated read-only dashboard/API
- [x] Explicit JSON commands and byte-for-byte non-mutating validation
- [x] Production publisher, tables, universe, scheduler and September vintage isolated
- [ ] Licensed full-region coverage and completed representative walk-forward evidence
- [ ] Production promotion (not approved)

## Milestone 8 — Global market data infrastructure (complete)

- [x] Provider-independent point-in-time security master
- [x] Lawful reference-file discovery adapters and Nasdaq Trader parser
- [x] Deterministic company/listing deduplication and canonical selection
- [x] Configurable eligibility with explicit exclusion reasons
- [x] Immutable monthly snapshots and point-in-time FX contract
- [x] JSON operations, authenticated coverage API, and shadow dashboard
- [x] Complete separation from the live 30-stock production ranking path
- [x] Validated adjusted-price, corporate-action, and historical FX CSV ingestion
- [x] Local/GBP returns with GBX handling and bounded point-in-time FX alignment
- [x] Venue calendars, bounded batches/retries, checkpoints, and structured failures
- [x] Research eligibility and authenticated price/FX dashboard coverage

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

- All 216 backend tests and Python compilation pass in the Milestone 15
  verification environment; readiness tests use only synthetic DuckDB fixtures.
- GitHub Actions passes, including a clean frontend `npm ci`, ESLint, and the optimized production build under Node 24 and npm 11.
- The only backend warning is a third-party Starlette/AnyIO deprecation warning.
- The immutable September 2026 production vintage is
  `5dcce39d-f98a-55b1-9010-279501142186` and uses `momentum_126d`.
- Milestone 10 deliberately reports no evaluation return or shadow ranking because no
  licensed historical global dataset is installed. Its isolated pipeline cannot write
  production prediction vintages.

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
- Global price/FX infrastructure is available, but complete licensed regional feeds
  are not configured and no bulk production ingestion occurred. Missing regions stay
  unavailable rather than being approximated.
- Global fundamentals and shadow scoring infrastructure are implemented, but licensed
  regional coverage, representative walk-forward validation, and production promotion
  remain explicitly incomplete.

### Milestone 15 read-only readiness assessment

- The first operator attempt against a 78,131,200-byte research DuckDB was
  safely cancelled with Ctrl+C after approximately 29 minutes (about 1,705 CPU
  seconds and 645 MB working memory). The database size and modification time
  did not change and Git remained clean; this was not a completed live run.
- Diagnosis found a full historical FX-frame filter and sort inside every
  security/price loop. The transformation now normalizes FX fields and applies
  the decision-time filter once, indexes sorted arrays by currency pair, and
  uses vectorized `searchsorted` as-of selection. Prices and actions are likewise filtered,
  sorted, and grouped once rather than fully rescanned per security. These
  indexes are invocation-local and retain historical availability semantics.
- The operator-only `model-readiness` command now opens the existing research
  DuckDB read-only and adapts its active catalogue, prices, historical FX,
  corporate actions, and structured failure state into the Milestone 14 validator.
- It returns aggregate selection/load/readiness, region/currency, history,
  freshness, FX, failure, observation-key, and action diagnostics with at most ten
  sanitized affected symbols.
- Explicit research and production paths are mandatory. Missing, identical,
  symlinked/hard-linked, incomplete, incompatible, or region-incomplete inputs
  fail closed. Before/after size and SHA-256 fingerprints establish immutability.
- Results remain research-only, current-membership based, not survivorship-free,
  and not investment advice. No ranking, Top 3, or conviction candidate is made.
- Verification includes existing equivalence cases and a synthetic 500-security,
  ten-year daily-price/three-pair FX regression that asserts one vectorized FX
  lookup per non-GBP security. No operator
  database, live provider, Railway service, or deployment was accessed.

## Next action

1. Run the aggregate-only readiness assessment locally against the operator-held
   research database and retain its JSON report; do not send or commit the database.
2. Acquire point-in-time fundamentals and survivorship-aware membership/delisting
   evidence before using historical periods in the controlled walk-forward pipeline.
3. Add a gated research-only monthly Top 3 and highest-conviction selection only
   after coverage, freshness, confidence, robustness and no-recommendation rules
   pass. Production promotion remains separate and prohibited.

### Milestone 16 research scoring and evidence gates

**Historical-price segmentation repair implemented (offline; operator rerun pending).**
Deterministic raw/adjusted adjacent-session boundaries now withhold only feature
or label windows that cross unresolved evidence. Clean later segments and
continuous low-price securities remain eligible. The read-only
`plan-label-repair` command reports bounded proposed classifications, retained
versus withheld counts through an enforced raw → feature-validated → retained
ledger, non-additive reason counts, severity-prioritized samples, distinct
affected-symbol aggregates, unchanged fingerprints, and never generates a ranking.
Stored observations are not rewritten or deleted;
no corporate action is inferred. Existing weak robust live metrics remain weak
and no strategy-success claim is made.

- The operator completed Milestone 15 with 492 of 500 securities model-ready;
  eight were withheld. That result establishes input readiness only and is not a
  claim that the live data passes scoring or ranking gates.
- `research-scoring` reuses model-ready eligibility and existing multifactor and
  walk-forward concepts. Withheld rows are removed before feature, evaluation,
  ranking and denominator construction.
- Available evidence is adjusted-close momentum/trend and price risk, preserving
  point-in-time retrieval and historical FX validation. Point-in-time
  fundamentals, valuation, quality, catalyst and sentiment evidence are reported
  unavailable rather than imputed as evidence.
- A Top 3 is never forced: coverage, historical sample, walk-forward validity,
  equal-weight baseline, rank discrimination/calibration, temporal/region
  stability and integrity must all pass. Failure returns bounded reasons and no
  candidates; passing fixtures are explicitly synthetic and research-only.
- Neither database, frontend, Railway, providers, nor the production
  `momentum_126d` publisher is modified. A live assessment has not been run.
- The first operator scoring run selected 500 securities, found 492 model-ready
  (98.4% coverage), passed integrity and eligible-universe coverage, but produced
  zero predictions/vintages and correctly withheld ranking. Both databases were
  byte-for-byte unchanged; this is not a live strategy pass.
- Root cause: historical rows were batch-ingested recently, while the evaluator
  reused the latest cross-section and required each row's ingestion audit time to
  precede a years-old decision date. That removed every historical feature row.
  The repaired evaluator uses a separate bounded month-end panel: records must be
  loaded by the final cutoff, effective price dates must be no later than each
  vintage, and complete labels must be strictly later and within the cutoff.
- Stage diagnostics now expose selection/load/readiness, price range, possible
  and generated vintages, feature/label eligibility and removals, date bounds,
  and bounded zero-vintage reason codes. Current-catalogue membership remains
  prominently not survivorship-free.
- The second read-only run produced 41,609 predictions over 113 vintages but an
  implausible 58.578998 mean excess return (US regional excess 90.095465) and a
  `[-0.013274, 175.754111]` interval. Baseline/discrimination passed only because
  unresolved adjusted-price return extremes dominated the evidence; temporal
  and regional gates withheld ranking and both files remained unchanged.
- Diagnosis found no percent/decimal or FX scaling path: local adjusted-close
  ratios are decimal returns. The missing control was per-label integrity after
  otherwise valid OHLCV histories, compounded by a regional calculation that
  selected a pooled regional slice rather than top three per region/vintage.
- The evaluator now fails closed on nonfinite/duplicate labels, invalid or
  near-zero denominators, unresolved >1,000% absolute returns, excessive
  contribution concentration, and undersized correlation groups. It reports
  bounded distributions/contributors/action proximity and conventional plus
  robust, equal-vintage metrics without trimming the evidence used by gates.
- Offline fixtures reproduce every diagnosed failure mode and a stable five-
  region case without warnings. They do not establish a live pass; the exact
  operator-held extreme symbol and data-history cause require the post-merge
  read-only retry.
- The subsequent live evidence contains 41,609 labels, a 19,999.0 maximum
  forward return, 29 near-zero denominators, and three unresolved extremes:
  ATPC.US (2023-08-31/2023-09-29), SBET.US
  (2025-04-30/2025-05-29), and AUMN.TO
  (2023-05-31/2023-06-29). No recorded action was within seven days.
  Top-one contribution was 83.708%; median/trimmed-mean excess were
  -0.001703/-0.003003 and the positive-period rate was 0.477876. Ranking
  remained withheld and both databases were byte-for-byte unchanged.
- `diagnose-extreme-labels` now reconstructs the identical scoring panel and
  emits only bounded price/action/provenance evidence for policy-selected
  extremes and near-zero denominators. It neither repairs nor reinterprets the
  observations and uses only `possible_*` causal classifications where stored
  evidence cannot establish a cause.
