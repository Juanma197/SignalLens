# Milestone 25 — point-in-time SEC ingestion foundation

This milestone collects research evidence only. It cannot score a security,
generate a candidate or Top 3, publish a ranking, create a shadow vintage, or
write the production database. The frozen 21/63/126/252-session price-only
results remain failed baselines.

## Storage and point-in-time contract

All state is held in the separate research DuckDB:

| Table | Purpose | Natural key |
|---|---|---|
| `sec_issuers` | security/ticker/CIK mapping | `(security_id, cik)` |
| `sec_filings` | filing and acceptance provenance | `(cik, accession_number)` |
| `sec_facts` | normalized historical fact versions | SHA-256 of security, taxonomy, concept, unit, start/end, accession and value |
| `sec_ingestion_runs` | bounded execution audit | `run_id` |
| `sec_checkpoints` | resumable per-security state | `security_id` |
| `sec_failures` | sanitized retryable/permanent failures | `failure_id` |

Facts retain the qualified symbol, ticker, zero-padded CIK, taxonomy/concept,
value, unit/currency, start/end, FY/FP/frame, form/accession, filed/public time,
amendment and revision flags, official endpoint and retrieval time. Public time
comes from SEC acceptance/filing provenance. **A fiscal period end is never an
availability timestamp.** New accession/value versions are inserted rather
than rewriting what was historically known; exact natural-key reruns are no-ops.
Provider payloads, response bodies, credentials and secrets are not stored.

## Safety and fair access

Back up both databases before the first run and verify that the backup opens.
Every command requires existing, distinct regular files and rejects identical,
symlinked and hard-linked paths. Plan/status open both files read-only and hash
both before and after. Write commands open production read-only and prove its
SHA-256 fingerprint unchanged. Each security is a transaction and budget stops
leave a checkpoint. Output contains bounded symbol samples and sanitized codes.

Live execution is impossible without both the exact authorization phrase and a
contact-bearing `SIGNALLENS_SEC_USER_AGENT`. Only official `sec.gov` ticker,
submissions and Company Facts endpoints are used. Defaults allow 205 requests
(one mapping plus two per 100 issuers), two attempts, exponential retry pacing,
a 20-second timeout, 5 MB responses, and 900 seconds. SEC fair-access guidance
still applies; operators should keep the default pacing or make it slower.

## Post-merge PowerShell workflow

```powershell
Push-Location backend
$ResearchDb = "C:\SignalLens\data\research.duckdb"
$ProductionDb = "C:\SignalLens\data\production.duckdb"
$env:SIGNALLENS_SEC_USER_AGENT = "SignalLens Research your-name@example.com"

# Read-only plan.
& ..\.venv\Scripts\python.exe -m app.sec_ingestion_cli plan-sec-ingestion --research-db $ResearchDb --production-db $ProductionDb

# Deliberately small first live batch: at most one mapping plus two issuer calls.
& ..\.venv\Scripts\python.exe -m app.sec_ingestion_cli ingest-sec-fundamentals --research-db $ResearchDb --production-db $ProductionDb --authorization "I AUTHORIZE RESEARCH-ONLY SEC INGESTION" --max-requests 3 --runtime-seconds 120

# Read-only readiness/status report.
& ..\.venv\Scripts\python.exe -m app.sec_ingestion_cli sec-ingestion-status --research-db $ResearchDb --production-db $ProductionDb

# Resume incomplete securities with the standard 100-security ceiling.
& ..\.venv\Scripts\python.exe -m app.sec_ingestion_cli ingest-sec-fundamentals --research-db $ResearchDb --production-db $ProductionDb --authorization "I AUTHORIZE RESEARCH-ONLY SEC INGESTION" --max-requests 205 --runtime-seconds 900

# Retry only checkpoints classified retryable.
& ..\.venv\Scripts\python.exe -m app.sec_ingestion_cli retry-sec-failures --research-db $ResearchDb --production-db $ProductionDb --authorization "I AUTHORIZE RESEARCH-ONLY SEC INGESTION" --max-requests 205 --runtime-seconds 900
Pop-Location
```

Add `--dry-run` to an execution command to exercise selection and provider
normalization without fact/checkpoint writes (the run audit is retained).

## Readiness interpretation and model isolation

Status reports selected/mapped/completed/pending/retryable/permanent counts,
fact and availability ranges, concept/family coverage, forms, units, currencies,
amendments/revisions, missing availability, conflicting units and at most ten
affected symbols. Families are `available`, `partially_available`, or
`unavailable`; debt/interest coverage stays unavailable unless defensible
`InterestExpenseNonOperating` evidence exists. Missing facts are unknown—not
zero and not evidence of poor fundamentals.

SEC coverage must never penalize non-US securities. A later, separately reviewed
evaluation must compare (a) US price-only with US price-plus-fundamentals, (b)
the unchanged global price-only baseline, then (c) international fundamentals
only after comparable point-in-time evidence exists.

## Non-US documentary assessment (no live confirmation)

The storage-free offline model assesses one catalogue representative each for
LSE (Companies House), TO (SEDAR+), XETRA (Unternehmensregister), and PA (AMF
regulated information). It records access, history, timestamp/version support,
units, automation, limits, licensing and backtest suitability. These are
candidates, not live-confirmed capabilities. Portal terms, timestamp semantics,
structured coverage and licensing require operator/legal validation. Yahoo
Finance is explicitly latest-only/unsuitable—not an authoritative historical
filing source for point-in-time backtesting.
