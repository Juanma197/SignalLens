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
apply. Writes are idempotent through existing natural keys. `resume` processes only pending
price checkpoints; completed and actual provider-failed symbols remain distinct.

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

## Pilot correction: selection, checkpointing, and estimates

The first operator-authorized 500-security attempt demonstrated that catalogue
metadata is not a liquidity signal and that provider wall-clock time dominates the
configured pacing floor. The corrected selector first requires an ordinary/common
primary equity, a domestic issuer/listing, and the venue's domestic quote currency
(`US=USD`, `LSE=GBP/GBX`, `TO=CAD`, `XETRA=EUR`, `PA=EUR`). Missing currency is
rejected rather than inferred as GBP. Names/types and provider primary flags reject
OTC/pink, funds, ETFs, indices, preferreds, warrants, acquisition vehicles,
depositary receipts (including Canadian CDRs), ambiguous instruments, and foreign
or secondary lines. ISIN and normalized issuer evidence prevent a company from
being selected twice across regions.

After those rules, a versioned SHA-256 ordering provides a stable sample. It is
intentionally non-alphabetical and reproducible, but **is not liquidity-ranked**;
metadata cannot support that claim. Catalogue results report selected counts by
region/currency and exclusions by region/currency/reason.

A global runtime or request-budget stop is a control event, not a provider error.
The current and remaining items stay `pending`, the run becomes
`partial_checkpointed`, and `resume` processes only `pending` items. It neither
replays `completed` items nor silently retries `failed` provider items. Actual
failures have bounded classifications and one failure record per attempted symbol;
provider messages, URLs, and tokens are not persisted. `latest_run` includes
attempted, completed, actual-failed, pending, request count, elapsed seconds, and
stop reason. Coverage also reports the catalogue mix, checkpoint progress, missing
FX currencies, and each security's history depth and freshness.

`plan` now reports lower/upper request bounds rather than one optimistic number.
It separates pacing-only, observed-provider (when a prior recorded run exists),
provider-timeout, and configured-maximum durations; a warning is raised when the
maximum is insufficient. When checkpoints exist, estimates use only pending
securities.

## Safe handling of the existing partial research database

No command automatically deletes, rewrites, or migrates the partial database. In
particular, do not point these commands at the production database and do not run a
new bulk ingestion merely to repair accounting.

1. Stop all writers and identify the research path. Record its byte size and
   SHA-256 digest, then make a filesystem copy. Review both before proceeding:

   ```bash
   research=/absolute/path/to/global-research.duckdb
   stat --printf='%n %s bytes\n' "$research"
   sha256sum "$research"
   cp --preserve=all "$research" "$research.pre-m12-fix.archive"
   sha256sum "$research.pre-m12-fix.archive"
   ```

2. Run only read-only inspection against the original and save the JSON outputs:

   ```bash
   python -m app.eodhd_ingestion_cli plan --research-db "$research"
   python -m app.eodhd_ingestion_cli coverage --research-db "$research"
   ```

   Legacy runs did not distinguish the 459 unattempted securities, so they cannot
   be safely reconstructed as provider failures in place. Review the selected
   universe and exclusion report under the corrected policy before choosing.

3. **Explicit operator decision required:** either keep the original read-only for
   audit, or archive it and build a *new path*. Never overwrite it. To rebuild,
   choose a new filename, run `dry-run`, compare counts/reasons with the saved plan,
   and only then separately authorize catalogue/FX/price commands. A rebuild is a
   new research dataset, not a migration and not a production publication.

   ```bash
   rebuilt=/absolute/path/to/global-research-m12-v2.duckdb
   test ! -e "$rebuilt" || { echo 'refusing existing rebuild path' >&2; exit 1; }
   python -m app.eodhd_ingestion_cli dry-run --research-db "$rebuilt"
   # Stop here for human review. Mutation requires a later explicit decision.
   ```

Keep the archive and digest with the run report. Promotion, production publishing,
Railway access, and automatic deletion remain prohibited by this runbook.
