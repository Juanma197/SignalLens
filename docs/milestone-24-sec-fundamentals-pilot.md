# Milestone 24: bounded SEC fundamentals pilot

This assessment is discovery only. It does **not** store observations, alter a
database, retrain or tune a model, change evidence thresholds, generate a
candidate or Top 3, schedule work, create a shadow vintage, or publish a ranking.
It never calls EODHD, Yahoo Finance, Railway, or SEC HTML pages.

## Preconditions

Both database arguments are mandatory, distinct, existing DuckDB files. The
research database must contain an active universe snapshot with eligible,
canonical US securities. The command opens both files read-only and reports the
before/after byte counts and SHA-256 digests. Live mode additionally requires the
literal authorization flag and a non-placeholder contact-bearing user agent.

## Offline fixture assessment (default and recommended first)

From the repository root:

```bash
cd backend
python -m app.sec_capability_cli sec-fundamentals-offline --research-db /absolute/path/research.duckdb --production-db /absolute/path/production.duckdb
```

To use another sanitized local fixture, append `--fixture /absolute/path/fixture.json`.
Offline mode constructs no HTTP client and makes zero network requests.

## Explicitly authorized live assessment

```bash
cd backend
SIGNALLENS_SEC_USER_AGENT='SignalLens Research ops@example.com' python -m app.sec_capability_cli sec-fundamentals-live --authorize-live-sec --research-db /absolute/path/research.duckdb --production-db /absolute/path/production.duckdb
```

The default total budget is seven HTTP requests: one official ticker/CIK mapping
plus submissions and Company Facts for each of at most three securities. Defaults
are two attempts, 120 ms pacing, a 10-second timeout, and a 5,000,000-byte response
limit. Bounds may only be tightened within the CLI's enforced ceilings.

The JSON output is intentionally aggregate-only: attempted count, requests,
forms/date range, concepts found/missing, usable point-in-time count, units and
currencies, amendments/revisions, family classifications, bounded reason codes,
and database immutability. Errors contain a bounded code and fixed redacted text;
payloads, issuer descriptions, credentials, provider messages, and parameterized
URLs are never printed.

## Interpretation

`public_at` comes from the SEC submissions filing/acceptance fields. A fiscal
period end is never substituted for public availability. Amendments remain
separate accession-bearing records; exact duplicates are deterministically
collapsed without overwriting historical versions. Successful capability does
not authorize storage, feature integration, selection, or publication.
