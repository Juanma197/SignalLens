# EODHD configured-account capability probe

Milestone 11 adds a provider-independent capability report for the configured EODHD
account. The September 26 follow-up established that the configured paid entitlement
is valid; this probe does not infer capabilities for other subscription levels.
It is discovery only: it does not ingest data, write DuckDB, alter the production
30-stock universe or `momentum_126d`, publish a ranking/vintage, or run on Railway.
The result proves the configured production and research database files have the
same existence, byte count, and SHA-256 digest before and after the run.

## Safety and scope

- The live probe uses only `AAPL.US` and the `US` exchange symbol catalogue.
  Results do not imply that any untested symbol or exchange is accessible.
- A default run makes three endpoint calls. `--include-actions` adds splits and
  dividends, subject to a hard total request budget (default six, maximum eight).
- Requests have a ten-second timeout, one-second pacing, at most two attempts, and
  a 5,000,000-byte response ceiling. Exchange-list and exchange-symbol-list calls
  alone use a separately configurable ceiling that defaults to, and may never exceed,
  16 MiB. Retry attempts count against the total request budget.
- Endpoint results are one of `available`, `restricted`, `unauthorized`,
  `rate_limited`, `unsupported`, or `response_too_large`. The last classification is
  a local safety rejection, not a claim that the provider endpoint is unsupported.
- Responses are normalized to sanitized field names, validation/record counts,
  dates, exchanges, and limitations. The catalogue and individual company records,
  provider payloads, and exception text are not emitted.
- The token is read only from the process environment and is never placed in a
  fixture, database, report, documentation command, exception, or committed file.

The exchange-list check parses every record and requires code, name, country and
currency. The symbol metadata check requires code, name, country, exchange, currency
and instrument type for the test symbol. End-of-day rows require an ISO date, positive OHLC,
nonnegative volume, and internally consistent high/low bounds. Adjusted close is
reported only when present and positive. Historical depth is the earliest/latest
valid date and calendar-day span actually returned; it is not described as complete.

## Linux runbook

Set the secret without putting it on the command line, then run from `backend`:

```bash
export SIGNALLENS_EODHD_API_TOKEN="<set in your secret manager>"
cd backend
python -m app.eodhd_probe_cli probe
python -m app.eodhd_probe_cli probe --include-actions
```

To verify specific database locations, use `--production-db` and `--research-db`.
The files may be absent; the probe confirms that absence remains unchanged.

## PowerShell runbook

```powershell
$env:SIGNALLENS_EODHD_API_TOKEN = '<set in your secret manager>'
Set-Location backend
python -m app.eodhd_probe_cli probe
python -m app.eodhd_probe_cli probe --include-actions
```

Remove the process-scoped credential when finished:

```powershell
Remove-Item Env:SIGNALLENS_EODHD_API_TOKEN
```

Never paste JSON containing a credential into an issue. The command's JSON itself
is designed not to contain the credential; automated tests use sanitized recorded
fixtures and make no live calls.

## Bounded live follow-up result (2026-09-26)

One `--include-actions` run made five provider requests, within the six-request
budget. `AAPL.US` EOD was `available`: all 11,539 rows were valid, spanning
1980-12-12 through 2026-09-25 (16,723 calendar days), and all rows had a valid
adjusted close. The exchange list was `available` with 70/70 valid records and the
US symbol list was `available` with 51,071 records. Only field names and counts were
reported; no catalogue entries were emitted. Splits were classified `unsupported`
from the returned payload, while dividends were `available` with 57 valid rows.

The local production and research paths were both absent before and after the run,
so their SHA-256 values remained `null`, byte counts remained zero, and immutability
was verified. No database was created. This run did not ingest, persist, rank,
publish, deploy, access Railway, or alter `momentum_126d`.

## Initial bounded live result (2026-09-24)

The prerequisite live check made exactly one request to the end-of-day endpoint for
`AAPL.US`. The credential existed (23 characters; value not displayed), HTTP status
was 200, and the endpoint was classified `available`. It returned 16 valid-dated
rows for 2026-09-01 through 2026-09-23 with open, high, low, close, adjusted close,
and volume fields. Metadata, splits, dividends, other symbols, other exchanges and
full-history completeness were not claimed by that prerequisite check.
