# Operator failure after PR #93: diagnosis pending

Verified operator facts: 68 regression tests passed; inventory at
2026-10-04T21:30:00+00:00 returned TRACK_B_HISTORY_INVENTORY_FAILED; the harness
stopped before the post-boundary inventory; both database hashes were unchanged.
These facts do not identify a root cause. PR #93 changed test fixtures only.

## Exhaustive code paths at PR #93

`InventoryError.reason_code` in `app/track_b_history.py` supplies the code.
`public_error_code()` preserves it; the CLI keeps the redacted public envelope.
There are five raise sites and four conditions:

* `_read`: persisted count > 500,000 (table row-work bound).
* `_read`: projected metadata length differs from the persisted count.
* `_chains`: initial distinct valid period states > 50,000.
* `_chains`: cumulative initial states plus adjacency expansions > 50,000.
* `inventory` finally: before/after database fingerprint dictionaries differ.

Changed fingerprints are inconsistent with the operator's unchanged external
hashes under the stopped-writer assumption, but the supplied facts alone do not
prove another condition. Hash read I/O exceptions are not InventoryError.
Schema/query failures, metadata parsing exceptions, specification loading errors,
and other unhandled exceptions use INVESTMENT_RESEARCH_INTERNAL_ERROR.
Incompatible/partial persisted schemas normally appear as evidence in a completed
report, rather than raising this code. Invalid input and report byte bounds use
their existing input/research codes. There is no general exception-to-history-code
conversion. No limit has been increased.

## Opt-in diagnostic

`python -m app.track_b_history_diagnostic` runs the same inventory adapters in a
dedicated process and discards its returned report. Temporary instrumentation is
restored on exit. It prints at most 52 events containing fixed stage identifiers,
integer counts, and stable reason codes. No exception text, records, payloads,
source strings, identity hashes, paths, date ranges, or outcome values are emitted.
Do not import/run this instrumented diagnostic in a shared multithreaded server.

The original public CLI and its error envelope are unchanged. The diagnostic is
an opt-in separate module. It preserves 500,000 rows/table, 50,000 chain work/call,
metadata-only projections, read-only connections, and all point-in-time rules.
It does not initialize schemas, ingest, materialize, select contracts, change
Track A, or execute models. Unsupported tables and views remain uninspected.
Fresh fingerprint checks belong to the diagnostic's outer finally; the nested
inventory uses cached baseline hashes. Both fresh after hashes are independently
attempted even if either before/after hash read or the inventory fails.
An INVENTORY_COMPLETED event must be interpreted together with the after-hash
events. Failed/changed hashes do not establish immutable successful verification.

The existing row aggregate and full-file fingerprint cost remains: this is
bounded metadata work, not a guaranteed wall-clock deadline on arbitrary files.
Stop concurrent writers before operator execution. The module returns nonzero
for failures, work limits, count mismatches, invalid input or changed hashes.

| Reason | Meaning / next evidence |
| --- | --- |
| SCHEMA_INCOMPATIBLE / SCHEMA_PARTIAL | Unsupported view/no adapter columns, or missing adapter columns. Does not itself prove the reported failure. |
| TABLE_ROW_WORK_LIMIT | Named fixed table count exceeds the unchanged cap. |
| METADATA_COUNT_MISMATCH | Metadata projection length failed count reconciliation. |
| PERIOD_CHAIN_WORK_LIMIT | Initial states or cumulative expansions exceed unchanged cap. |
| METADATA_QUERY_OR_DECODE_FAILED | Fixed table stage identifies schema/count/date/projection query or decode failure. |
| METADATA_PARSING_FAILED | Fixed accounting/identity/market/period-chain processing failed. |
| METADATA_CONNECTION_FAILED | Read-only database connection failed. |
| FINGERPRINT_READ_FAILED / FINGERPRINT_CHANGED | Independent fingerprint read failed or baseline changed. |
| REPORT_CONTRACT_FAILED / INVENTORY_INTERNAL_FAILED | Other research/report failure or unhandled inventory stage; no exception contents disclosed. |

The complete Windows PowerShell 5.1 block is
`scripts/track-b-history-diagnose.ps1`. It diagnoses both boundaries independently
even after a failed inventory. It also independently checks external hashes and
prints only unchanged/changed/unavailable reason codes. Native stderr is discarded.
Only the safe JSON and stage/reason codes should be returned for investigation.

Offline synthetic fixtures reproduce distinct failure categories, including a
real 500,001-row legacy price table and a wrong persisted metadata type. They do
not confirm either category as the operator cause. Repair of the operator cause
is deliberately pending safe diagnostic output; no operator inventory success
is claimed. No operator database/provider was accessed during development.
