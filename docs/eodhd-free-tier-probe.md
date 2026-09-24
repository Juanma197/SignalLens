# EODHD free-tier capability probe

Milestone 11 adds a provider-independent capability report for EODHD's free tier.
It is discovery only: it does not ingest data, write DuckDB, alter the production
30-stock universe or `momentum_126d`, publish a ranking/vintage, or run on Railway.
The result proves the configured production and research database files have the
same existence, byte count, and SHA-256 digest before and after the run.

## Safety and scope

- The live probe uses only `AAPL.US`, which was confirmed available to this free
  account. Results do not imply that any other symbol or exchange is accessible.
- A default run makes two endpoint calls. `--include-actions` adds splits and
  dividends, subject to a hard total request budget (default six, maximum eight).
- Requests have a ten-second timeout, one-second pacing, at most two attempts, and
  a 5 MB response ceiling. Retry attempts count against the total request budget.
- Endpoint results are one of `available`, `restricted`, `unauthorized`,
  `rate_limited`, or `unsupported`. Optional action access is never assumed.
- Responses are normalized to field names, validation counts, dates, exchanges,
  and limitations. Provider payloads and exception text are not emitted.
- The token is read only from the process environment and is never placed in a
  fixture, database, report, documentation command, exception, or committed file.

The metadata check requires code, name, country, exchange, currency and instrument
type for the test symbol. End-of-day rows require an ISO date, positive OHLC,
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

## Initial bounded live result (2026-09-24)

The prerequisite live check made exactly one request to the end-of-day endpoint for
`AAPL.US`. The credential existed (23 characters; value not displayed), HTTP status
was 200, and the endpoint was classified `available`. It returned 16 valid-dated
rows for 2026-09-01 through 2026-09-23 with open, high, low, close, adjusted close,
and volume fields. Metadata, splits, dividends, other symbols, other exchanges and
full-history completeness were not claimed by that prerequisite check.
