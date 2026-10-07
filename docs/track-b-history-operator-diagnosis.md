# Confirmed operator failure and bounded market repair (PR #94)

Operator diagnostics at both established boundaries identified the same failure:
`research.global_price_observations`, `TABLE_ROW_WORK_LIMIT`, 1,035,884 rows
against the existing 500,000-row collection cap. Internal and external hashes
remained unchanged. This is confirmed operator evidence, not an inference from
passing tests or a schema warning. No operator database/provider was accessed
by the implementation agent. Successful repaired operator verification remains
pending operator execution of the supplied script.

## Confirmed cause and repair

The old adapter counted the full table and then rejected it before projecting
metadata because it would collect all records into a Python list. PR #93 only
changed test fixtures; it did not change this runtime collection path.

Version `track-b-history-inventory-1.1.0` keeps exact SQL row counts and complete
metadata date-range aggregates, including invalid/missing date counts. For only
`global_price_observations`, the adapter replaces list collection with a complete
ordered metadata stream. It projects only qualified_symbol, trading_date, status
and retrieved_at when present. Prices, volumes, adjusted prices and realised
outcomes are never selected. Other supported adapters retain the unchanged
500,000-row collection cap; chain work remains 50,000 per call. The bounds report
explicitly names the market streaming exemption, rather than implying that its
population is below the collection cap.

Inventory SQL connections are read-only, configured at creation with a 128 MB
engine memory budget, one thread, an empty temporary directory and zero permitted
temporary disk space. There is no sort spill or operator database write. Each
fetch returns at most 2,048 rows. Projected metadata cells are checked against
1,024 characters before decoding (at most 4,096 UTF-8 bytes per string). The
Python stream retains at most 50,000 distinct visible dates for one symbol at a
time. The SQL ordering uses binary encoded symbols, so a persisted case-insensitive
collation cannot interleave different symbol identities. Within-symbol row order
has no effect on counts. Engine/cell/date-state exhaustion fails closed using
redacted domain errors, never partial coverage or sampled population counts.
These are storage/resource budgets, not approved market/accounting/sample minima.
Full-table scans and file hashing remain proportional to persisted input size;
there is no claimed fixed wall-clock runtime on arbitrary databases.

The stream reconciles every decoded row to the exact SQL population count.
Duplicates increment observation counts but contribute once to a symbol's visible
date set. The report preserves the exact number of symbols with at least 253
visible dates and adds an exact `boundary_visible_distinct_symbol_date_count`.
The existing global-market UTC-naive TIMESTAMP convention, aware timestamp
normalization, boundary equality and exclusion of future retrievals/trading dates
are unchanged. Naive timestamp strings do not gain the TIMESTAMP producer
convention. Canonical/raw layers, deterministic samples, public errors, byte cap,
point-in-time rules and research/production separation are unchanged.

## The independent one-column canonical warning

The established legacy `canonical_factor_evidence` DDL is in
`backend/app/investment_evidence.py`, `SCHEMA`. Compared with the 18-column fixed
inventory adapter, it lacks only `materialization_run_id`. It includes
`materialized_at`, `instant_date` and `source_fact_key`. Thus that established
schema reproduces `SCHEMA_PARTIAL` with exactly one missing adapter column.
This comparison explains the legacy warning; it does not imply an operator
migration is needed or that a warning identifies the cause of a work-limit error.

`row.get('materialization_run_id')` is absent for legacy rows. The existing legacy
availability rule remains `max(public_at, retrieved_at)` with aware timestamps.
Only rows declaring a materialization run require
`max(public_at, retrieved_at, materialized_at)`. No run declaration, provenance,
source identity or canonical certification is invented. The optional missing
column does not make the canonical identity/field adapter unsupported and does
not raise `TABLE_ROW_WORK_LIMIT`. No schema migration or materialization is
performed by inventory, diagnostics, or verification.

## Failure-code paths

At PR #93 there were five InventoryError raise sites: `_read` row cap and count
mismatch; `_chains` initial-state and cumulative-expansion cap; `inventory` final
fingerprint mismatch. The confirmed operator failure was the first site.
The repair retains those conditions for collected adapters and adds bounded
market cell/date-state/engine errors, plus in-stream/final count reconciliation.
The outer inventory also redacts SQL engine memory exhaustion. All these domain
errors retain `TRACK_B_HISTORY_INVENTORY_FAILED`. Unhandled metadata query/parsing,
specification and fingerprint I/O exceptions retain the generic public internal
error code. Invalid input/report byte bounds retain their existing research codes.

The separate opt-in diagnostic module discards the inventory report and emits at
most 52 events containing only fixed stage identifiers, integer counts and stable
reason codes. It distinguishes TABLE_ROW_WORK_LIMIT, METADATA_COUNT_MISMATCH,
PERIOD_CHAIN_WORK_LIMIT, MARKET_METADATA_CELL_LIMIT, MARKET_DATE_STATE_LIMIT,
MARKET_SQL_RESOURCE_LIMIT and INVENTORY_SQL_RESOURCE_LIMIT from parsing/query and
fingerprint failures. Raw exception contents, paths, records, payloads, hashes
and outcome values are never printed. It uses temporary instrumentation and is
intended for a dedicated process. Its outer finally independently attempts both
fresh after hashes; the nested inventory's fingerprint calls use cached baselines.
Always interpret completion together with the after-hash events.

## Offline coverage and operator verification

The new population fixture contains 1,046,294 synthetic market records, exceeding
1,035,884. It checks both established boundaries, exact observation-state totals,
full date ranges and missing/invalid counts, 10,000 duplicate dates, 2,048 retrievals
one microsecond after the historical boundary, a future trading date, deterministic
repeat output, bounded samples/UTF-8 output, explicit metadata-only queries,
bounded fetches, production separation and before/after file hashes. Separate
fixtures check the actual legacy DDL, binary collation ordering, naive text versus
aware source timestamps, absent-date schemas, and engine/cell/date-state failures.
No lifecycle operation occurs outside synthetic fixture creation.

Use `scripts/track-b-history-verify.ps1` as the one complete Windows PowerShell
5.1 verification script after checking out the updated PR #94 branch. Stop
concurrent writers first. It runs relevant offline regressions, verifies the public
inventory independently at both boundaries, checks deterministic repeats and the
report's count/bounds/zero-model contracts, and attempts both external after hashes
in finally. It prints only safe stage/count/reason JSON, suppressing subprocess
stderr and report contents. Live market counts are reconciled rather than
hardcoding the previously observed population. It does not fetch providers,
ingest, materialize, change Track A, select contracts or execute models.

The Python portion can be tested against offline fixtures; that is not Windows
PowerShell 5.1 execution and is not operator verification.

Validation: all 84 relevant regressions passed with `TZ=Europe/London`. The
verification script's extracted Python portion also completed both boundary
checks, deterministic repeats and independent external hash cleanup against
synthetic database copies. The focused inventory/diagnostic/stream selection
passed 29 tests. `git diff --check` passed. Windows execution is not claimed.
