# Bounded EODHD global ingestion operator runbook

Milestone 12 is a **research-only** acquisition pipeline. It cannot publish a
ranking, alter the production 30-stock universe, write an official prediction
vintage, or change `momentum_126d`. The EODHD exchange catalogue describes its
current contents; it is not survivorship-free and must not be treated as valid
historical membership. No global model is approved for production.

## Data and selection contract

The five regions are `US`, `LSE`, `TO`, `XETRA`, and `PA`. Hard validation rejects
configurations above 100 securities per region or 500 overall. Only provider types
that map reliably to common stock or ordinary shares proceed. ETF, fund, index,
preferred, warrant, ADR/GDR, ambiguous, missing-identity, noncanonical and cap
exclusions are reported explicitly. The security master's ISIN/issuer canonical
logic deduplicates secondary listings and depositary receipts.

The initial catalogue shortlist is deterministic. Recent close and volume are
stored so the existing eligibility report can apply deterministic median traded-
value filtering. Newly listed companies remain in the data with
`insufficient_momentum_history`; they are not silently deleted.

Prices are limited to ten years and include local-currency open, high, low, close,
adjusted close and volume. The adapter rejects invalid dates, duplicate or
nonmonotonic dates, nonfinite/negative values and impossible OHLC ranges. EODHD
dividends use the existing `cash_distribution` action type. Splits are never
inferred and each price report says `provider_unsupported`.

USD/GBP, CAD/GBP and EUR/GBP are probed using historical EOD observations. Stored
FX is available at the next UTC day, never retroactively at retrieval time. An
unusable pair remains explicitly `missing_fx`; today's rate is never substituted.

## Commands

Run from `backend/`. The token is read only from
`SIGNALLENS_EODHD_API_TOKEN`; output and persisted rows never contain it.

```bash
python -m app.eodhd_ingestion_cli plan
python -m app.eodhd_ingestion_cli dry-run
python -m app.eodhd_ingestion_cli ingest-catalogue
python -m app.eodhd_ingestion_cli ingest-prices
python -m app.eodhd_ingestion_cli ingest-fx
python -m app.eodhd_ingestion_cli resume
python -m app.eodhd_ingestion_cli status
python -m app.eodhd_ingestion_cli coverage
```

`plan` reports estimated requests, rows, storage, and duration before an operator
authorizes writes. `dry-run` makes bounded catalogue requests and validates the
selection but does not open or create either database; it is byte-for-byte
mutation-free. Use `--research-db` only for a separate research DuckDB. If it
resolves to `--production-db`, the command refuses to run.

Operational limits are configurable with `--daily-request-budget`,
`--requests-per-minute`, `--retries`, `--timeout-seconds`,
`--max-response-bytes`, and `--maximum-runtime-seconds`. Safety ceilings still
apply. Writes are idempotent through existing natural keys. `resume` skips completed
price checkpoints and retries failed or unfinished symbols.

## Bounded smoke test

Use a disposable path and at most five securities per region:

```bash
tmpdir=$(mktemp -d)
python -m app.eodhd_ingestion_cli plan --per-region 5 --total 25 \
  --research-db "$tmpdir/research.duckdb"
python -m app.eodhd_ingestion_cli ingest-catalogue --per-region 5 --total 25 \
  --research-db "$tmpdir/research.duckdb"
python -m app.eodhd_ingestion_cli ingest-prices --per-region 5 --total 25 \
  --research-db "$tmpdir/research.duckdb"
python -m app.eodhd_ingestion_cli ingest-fx --per-region 5 --total 25 \
  --research-db "$tmpdir/research.duckdb"
rm -rf "$tmpdir"
```

Do not run the 500-security job in Codex Cloud. Review the plan, provider licensing,
catalogue exclusions, liquidity/history flags, FX states, request allowance, disk
capacity and runtime before separately authorizing a full operator run.
