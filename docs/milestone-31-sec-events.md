# Milestone 31 — SEC material-event metadata runbook

## Safety boundary

This pipeline stores official public-record **metadata only**. It never requests
or stores complete filing HTML/text, exhibits, arbitrary response payloads,
credentials, sentiment, or invented summaries. It has no scoring import and
returns zero rankings, candidates, and shadow selections. The prospective model
remains frozen at 90% price and 10% dilution.

Both database paths must exist and be distinct regular files; equal, symlinked,
and hard-linked paths are rejected. Planning and status open both databases read
only and compare their SHA-256 fingerprints. Execution opens production read
only, writes research transactions, and verifies production's final fingerprint.
Back up research before execution.

## Schema and natural keys

- `sec_event_ingestion_runs`: budgets, counts, state, stop reason, and historical flag.
- `sec_event_checkpoints`: one durable security/CIK state plus separate recent and
  historical-file progress.
- `sec_event_metadata`: one version per `(cik, accession_number, content_hash)`, with a deterministic
  `sec-event:{cik}:{accession}:{hash-prefix}` ID. Metadata corrections are preserved.
  A later `/A` accession is separate and cannot appear before its own acceptance
  time. The original relationship stays null unless supplied explicitly.
- `sec_event_failures`: bounded reason, retryability, stage, attempt, and time.

Reruns leave identical accessions unchanged. Amendments remain distinct.

## Forms, classification, and point-in-time rules

Only 8-K, 8-K/A, 6-K, and 6-K/A are eligible. 10-K and 10-Q are ignored. Known
SEC items map deterministically to acquisition/disposal, bankruptcy/distress,
material contract, earnings/financial results, management/director change,
capital raise, delisting/compliance, shareholder matters, or other material
event. Missing items become `general_company_news`; unknown items become
`material_event_unclassified`; both have reduced confidence. Names and filenames
are never classification evidence.

Acceptance/publication times must have a timezone and be no later than retrieval.
Missing, naive, invalid, and future values are withheld. Filing/report dates are
never availability substitutes. Matching requires the stored `sec_issuers`
security/CIK relationship, protecting against ticker reuse and ambiguity.
Explanations are bounded and factual. Every dashboard record includes an internal
accession/form/publication citation and makes no price-direction claim.

## Recent and historical request scope

The main `CIK##########.json` response contains recent submissions and references
older auxiliary files. Planning expects one request per pending mapped issuer and
reports that recent count exactly. Known auxiliary-file cost is separate. Default
ingestion parses only the main response. `--include-historical` records explicit
intent but still downloads zero auxiliary files until reviewed pagination is
implemented; the initial pilot must omit it.

## Post-merge PowerShell procedure

Run from the repository root. These paths are backend-relative. Back up research
first and set a contact-bearing SEC User-Agent only in the process environment.

```powershell
Push-Location backend
$Research = "data\research\signallens-research.duckdb"
$Production = "data\signallens.duckdb"
$Backup = "data\research\backups\signallens-research.pre-sec-events.duckdb"
New-Item -ItemType Directory -Force (Split-Path $Backup) | Out-Null
Copy-Item $Research $Backup -Force

& ..\.venv\Scripts\python.exe -m app.sec_events_cli plan-sec-event-ingestion --research-db $Research --production-db $Production

$env:SIGNALLENS_SEC_USER_AGENT = "SignalLens research operator@example.com"
& ..\.venv\Scripts\python.exe -m app.sec_events_cli ingest-sec-events --research-db $Research --production-db $Production --authorization "I AUTHORIZE RESEARCH-ONLY SEC EVENT INGESTION" --max-requests 1 --runtime-seconds 60 --max-attempts 1 --pacing-seconds 0.2 --timeout-seconds 20 --max-response-bytes 5000000

& ..\.venv\Scripts\python.exe -m app.sec_events_cli sec-event-ingestion-status --research-db $Research --production-db $Production
& ..\.venv\Scripts\python.exe -m app.sec_events_cli ingest-sec-events --research-db $Research --production-db $Production --authorization "I AUTHORIZE RESEARCH-ONLY SEC EVENT INGESTION" --max-requests 20 --runtime-seconds 300 --max-attempts 2 --pacing-seconds 0.2 --timeout-seconds 20 --max-response-bytes 5000000
& ..\.venv\Scripts\python.exe -m app.sec_events_cli retry-sec-event-failures --research-db $Research --production-db $Production --authorization "I AUTHORIZE RESEARCH-ONLY SEC EVENT INGESTION" --max-requests 10 --runtime-seconds 180 --max-attempts 2 --pacing-seconds 0.2 --timeout-seconds 20 --max-response-bytes 5000000
Remove-Item Env:SIGNALLENS_SEC_USER_AGENT
Pop-Location
```

Do not add historical expansion to the pilot. Future issuer-release and licensed
news work requires separate timestamp, identity, copyright, entitlement,
retention, and display review and remains context-only if approved.
