# Milestone 42 financial-strength consumer investigation

The all-71 missing current-assets/current-liabilities result is an integration defect, not an accounting-contract exclusion. Both AssetsCurrent and LiabilitiesCurrent are explicit financial-strength aliases. Operator-reported 64 passing tests, successful assessment commands, and unchanged hashes established execution and immutability, but did not assert post-materialization financial-strength component coverage. No operator database or provider was accessed during this investigation.

## Trace and repair

Both financial-strength-evidence-audit and financial-strength-contract-assessment (and company preview) use financial_strength._report. It reads canonical evidence and source facts read-only and calls resolve_canonical_liquidity. The resolver validates controlled lineage, revision/run identity, accounting meaning, source matching, values, units, and timestamps; it selects only revisions whose canonical availability is no later than the decision. Canonical availability is max(public_at, retrieved_at, materialized_at).

The resolver returns source-shaped effective rows for liquidity measurement, carrying _canonical_available_at and _canonical_row. Previously _report removed controlled canonical rows and appended those effective source-shaped rows. The financial-strength _visible validator expected available_at, which source SEC rows lack, and therefore withheld the otherwise valid observations as missing_canonical_availability_timestamp. Simply adding available_at would still fail its legacy max(public_at,retrieved_at) rule after materialization.

The shared consumer now appends the resolver-validated canonical envelope with a private validation marker. Only marked rows use max(public_at,retrieved_at,materialized_at); legacy canonical evidence and classifications keep their existing timestamp rules. Raw SEC facts are not promoted into financial-strength observations. Resolver validation and fail-closed corruption behavior are preserved.

## Cash meanings and unchanged contracts

Liquidity's unrestricted_cash is its own canonical field. The controlled direct alias CashAndCashEquivalentsAtCarryingValue maps to it. It excludes restricted cash; broader liquidity discovery may discuss separately validated constructions from cash-plus-restricted-cash less restricted cash. Such a construction is not automatically the same evidence as a directly reported cash-and-cash-equivalents balance.

Financial strength's cash_and_cash_equivalents is an exact alias for the directly reported CashAndCashEquivalentsAtCarryingValue concept, with restricted cash excluded. Its net-debt contracts require that field. Although these two contracts can reference the same original concept, their canonical identities, permitted constructions, and acceptance paths are distinct. A controlled unrestricted_cash row is intentionally withheld there as canonical_concept_mismatch; this repair does not rename it or substitute it for cash_and_cash_equivalents. The 65 canonical unrestricted-cash observations therefore do not promise 65 financial-strength cash observations. Previously accepted legacy cash_and_cash_equivalents evidence remains accepted.

Neither financial_strength's all-components-ready-or-explicitly-not-applicable definition nor investment_research.family_readiness's existing required fields changed. Liquidity coverage does not establish full-family readiness, replace missing interest, or select Contracts A–D. Track A and all model-output prohibitions remain unchanged.

## Offline verification

The 187-revision lifecycle fixture now includes 71 classified companies and asserts 61 current-assets, 61 current-liabilities, and 65 unrestricted-cash materialization observations. After synthetic materialization, historical financial-strength coverage remains zero; later both assessment paths see 61/61 fields and 60 ready liquidity components/current ratios (one fixture company explicitly has zero liabilities). Company preview verifies the canonical timestamp, historical absence, and unchanged full-family unavailability. Database bytes are unchanged across reads; contracts remain unselected and model-output collections empty. All lifecycle writes are confined to temporary test databases, never operator data.

Commands run from backend:

```bash
../.venv/bin/python -m pytest tests/test_financial_strength.py tests/test_liquidity_materialization.py tests/test_liquidity_evidence.py tests/test_liquidity_preview_history.py -q
../.venv/bin/python -m pytest tests/test_investment_research.py -q
```

## Exact read-only operator verification commands (documented, not executed)

Run in the existing operator checkout after installing this repair. These commands use the previously documented operator paths and boundaries. They do not ingest, materialize, call providers, or produce model outputs. JSON reports are evidence/contract diagnostics only. Stop other database writers first so hash comparisons are meaningful.

```powershell
$ErrorActionPreference = 'Stop'
Set-Location 'C:\Users\Juan Estrada\Projects\SignalLens\backend'
$Python = '..\.venv\Scripts\python.exe'
$Research = 'data\research\signallens-research.duckdb'
$Production = 'data\signallens.duckdb'
$Historical = '2026-10-04T21:30:00+00:00'
$After = '2026-10-05T00:30:00+00:00'
$ResearchBefore = (Get-FileHash -Algorithm SHA256 -LiteralPath $Research).Hash
$ProductionBefore = (Get-FileHash -Algorithm SHA256 -LiteralPath $Production).Hash
foreach ($Decision in @($Historical, $After)) {
  & $Python -m app.investment_research_cli liquidity-measurement-compatibility-audit --research-db $Research --production-db $Production --decision-at $Decision
  if ($LASTEXITCODE -ne 0) { throw 'Compatibility audit failed' }
  & $Python -m app.investment_research_cli financial-strength-evidence-audit --research-db $Research --production-db $Production --decision-at $Decision
  if ($LASTEXITCODE -ne 0) { throw 'Financial-strength evidence audit failed' }
  & $Python -m app.investment_research_cli financial-strength-contract-assessment --research-db $Research --production-db $Production --decision-at $Decision
  if ($LASTEXITCODE -ne 0) { throw 'Financial-strength contract assessment failed' }
}
$ResearchAfter = (Get-FileHash -Algorithm SHA256 -LiteralPath $Research).Hash
$ProductionAfter = (Get-FileHash -Algorithm SHA256 -LiteralPath $Production).Hash
if ($ResearchBefore -ne $ResearchAfter -or $ProductionBefore -ne $ProductionAfter) { throw 'Database SHA-256 changed' }
[pscustomobject]@{research_sha256_before=$ResearchBefore; research_sha256_after=$ResearchAfter; production_sha256_before=$ProductionBefore; production_sha256_after=$ProductionAfter}
```

Expected controlled resolver counts: historical visible=0/future=187; later visible=187/future=0/deduplicated=187. Later financial-strength current-assets/current-liabilities coverage should reflect 61/61 usable classified companies if the reported dataset is unchanged. Ready liquidity/current-ratio counts additionally require positive liabilities, so must not be asserted as 61 merely from field coverage. Historical legacy evidence, if any, remains governed by its own contract; no new revision may leak backwards. Cash coverage must be assessed under its separate financial-strength field, not copied from 65 unrestricted_cash rows. No claim is made here about newly verified live counts.
