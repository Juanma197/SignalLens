# Liquidity preview response-size failure after controlled ingestion

A `liquidity-company-preview` can resolve one eligible company from the complete
population, construct valid observations, and still return
`INVESTMENT_RESEARCH_NOT_READY`. The CLI redacts the internal
`liquidity response exceeds size contract` exception raised by `_finish` when
compact UTF-8 JSON exceeds 262,144 bytes. Company and observation sample limits
bound item counts, but previously did not bound the repeated provenance payload.

`sec_liquidity_plan._encode_identifier` encodes the full issuer cohort and plan
identity into a capability token. Controlled ingestion stores that full token as
`sec_facts.ingestion_plan_id`. Measurement validation retains it in
`validation.lossless_normalization_provenance.controlled_ingestion.plan_id`.
Repeating it in selected observations and three samples per field can exceed the
preview size contract with normal history and issuer cohorts. The SEC fact's
accounting eligibility and canonical revision visibility are independent of
this serialization failure.

Company preview now projects each non-null string plan identifier to
`controlled_ingestion.plan_id_reference`, containing
`representation: "sha256_utf8"`, the exact UTF-8 `sha256`, and `utf8_bytes`.
The full identifier remains in persisted evidence and unprojected validation
results. This is a report representation change: preview consumers that used the
full `plan_id` string must use the reference instead. Run ID, contract identity,
evidence identity, selected measurements, diagnoses, construction semantics,
observation/citation bounds, and the existing response byte limit are preserved.

The offline regression uses 69 companies with NEU sorted last, seventeen years
of quarterly observations, a real-format 10,803-byte ingestion plan token, and
187 directly seeded canonical revisions. It calls no ingestion or materialization
producer. Current-main code fails at both requested boundaries with compact
responses of 626,235 and 626,255 bytes. The projected responses are 124,975 and
124,995 bytes; both public CLI invocations succeed. Coverage reconciles at 61
assets, 61 liabilities and 65 cash companies, resolution transitions from zero
to 187 visible revisions, and both synthetic database hashes remain unchanged.
These byte measurements belong to the synthetic fixture, not operator databases.

For a focused Windows PowerShell 5.1 retry after this fix is merged, run from the
backend directory. These two commands only read the existing evidence; they do
not repeat ingestion or materialization:

```powershell
$Python = '..\.venv\Scripts\python.exe'
$Research = 'data\research\signallens-research.duckdb'
$Production = 'data\signallens.duckdb'
$Before = @((Get-FileHash -Algorithm SHA256 -LiteralPath $Research).Hash,
            (Get-FileHash -Algorithm SHA256 -LiteralPath $Production).Hash)
try {
  foreach ($Decision in @('2026-10-04T21:30:00+00:00','2026-10-05T00:30:00+00:00')) {
    $Output = & $Python -m app.investment_research_cli liquidity-company-preview `
      --research-db $Research --production-db $Production `
      --decision-at $Decision --qualified-symbol 'NEU.US'
    $Code = $LASTEXITCODE
    if ($Code -ne 0) { throw "Liquidity preview failed at $Decision (exit $Code)" }
    $Report = ($Output -join [Environment]::NewLine) | ConvertFrom-Json -ErrorAction Stop
    [pscustomobject]@{decision_at=$Decision; symbol=$Report.company.qualified_symbol;
      compact_utf8_bytes=$Report.compact_utf8_bytes;
      maximum_compact_utf8_bytes=$Report.bounds.maximum_compact_utf8_bytes;
      observations=$Report.company.observation_population.total_count;
      returned=$Report.company.observation_population.returned_count} | Format-List
  }
} finally {
  $After = @((Get-FileHash -Algorithm SHA256 -LiteralPath $Research).Hash,
             (Get-FileHash -Algorithm SHA256 -LiteralPath $Production).Hash)
  $Unchanged = ($Before[0] -eq $After[0] -and $Before[1] -eq $After[1])
  [pscustomobject]@{database_hashes_unchanged=$Unchanged} | Format-List
  if (-not $Unchanged) { throw 'Database hash changed during read-only preview retry' }
}
```
