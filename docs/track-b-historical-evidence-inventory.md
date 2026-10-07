# Track B historical evidence inventory

Implemented from main `90fe75f25e08aac4850ecbd0ad6e0f1012501d12` (PR #91),
confirmed through GitHub branch metadata. The CLI command is
`track-b-historical-evidence-inventory`. This is a strictly read-only evidence
inventory, **TRACK B RESEARCH FOUNDATION — NOT A MODEL**.

The operator-confirmed historical/post-boundary assessments, liquidity readiness
61/71 and unchanged research/production hashes are context only. They are not
embedded as inventory findings and do not establish historical family readiness.
All eight preregistration groups remain unresolved. Contracts A–D remain
unselected, sample minima unset, and Track A unchanged.

## What the report establishes

Research and production inventories are separate, never added together. Each of
22 fixed table adapters reports persisted row counts, present metadata columns,
missing adapter columns and stored date ranges with missing/invalid-date counts.
Ranges cover all persisted metadata, including post-boundary records; they are
not automatically point-in-time usable ranges. Unsupported base tables are
counted through schema metadata but their contents are not read. Views are not
evaluated. No runtime schema initialization or migration occurs.

Legacy `fundamental_facts`, fetch metadata, `security_universe` and `price_bars`
are included in the source inventory. Their ticker-only identity is not silently
joined to security IDs or promoted into canonical/raw SEC period chains.

Membership reporting counts listing identities, completed catalogue retrievals,
snapshot membership, inactive rows and explicitly labelled delisting actions.
Inactive listings, disappearance between snapshots, first/last-seen times and
legacy `sec_issuers.mapped_at` do not prove delisting or effective membership.
Effective mapping candidates are partitioned by missing provenance, conflicting
intervals, boundary applicability and declared approval/ticker-reuse protection.
Even declared approved mappings remain unverified: the inventory does not
certify source lineage or share-class identity from CIK alone.

For each database, the denominator is the union of stored security IDs from
listings, snapshot members, SEC mappings, canonical evidence and SEC facts. It
includes records outside the decision boundary and is **not** an approved
historical universe. Exact field counts distinguish incompatible evidence,
unverified provenance, post-boundary availability and structurally compatible
metadata with unverified lineage/identity/numerical validity. Absence means no
records in a supported adapter, not inferred zero. Unsupported schemas have
unknown absence counts. Unmapped accounting rows are counted separately.

Canonical and raw SEC accounting chains are reported separately. Raw metadata is
never promoted or substituted for canonical evidence. Exact concepts, units,
currency, period shape and aware source timestamps are checked without reading
accounting amounts. Canonical declarations additionally require usable state,
contract/source references and the existing availability rule. Controlled
canonical rows with a materialization run use
`max(public_at,retrieved_at,materialized_at)`; legacy canonical rows retain
`max(public_at,retrieved_at)`. Equality at the boundary is included. Missing or
naive timestamps fail the provenance check. Metadata consistency is not resolver
validation; no value-bearing resolver is invoked.

The global market adapters explicitly interpret their repository `utc_naive()`
storage as UTC for retrieval metadata. This does not establish public/action
availability or source provenance. That convention is never applied to canonical
or raw SEC accounting timestamps.

Draft structural chain diagnostics are:

| Family | Metadata construction examined; never certified |
| --- | --- |
| Value | Four contiguous, identical standalone-quarter OCF/capex periods; market-cap denominator remains unverified. |
| Business quality | Four standalone net-income quarters plus asset instants at the beginning and ending endpoints; the draft five-quarter history requirement is not waived. |
| Financial strength | Four OCF quarters with aligned current-debt, noncurrent-debt and exact cash-and-equivalents instants; this is the draft formula's metadata shape, not selection of a readiness/debt contract. |
| Growth | Eight contiguous standalone revenue quarters, representing two nonoverlapping TTM windows; taxonomy/reorganisation comparability remains unverified. |
| Shareholder treatment | Two contiguous comparable annual diluted-share durations; split/reorganisation comparability remains unverified. |
| Price/risk | Count symbols with at least 253 distinct date-metadata records visible at the boundary; exact exchange-session continuity, identity and total-return construction remain unverified. |

Standalone quarter candidates span 60–120 days and annual candidates 330–400
days. These are versioned **diagnostic adapter rules, not approved accounting
or sample thresholds**. No annual/YTD subtraction is attempted. Exact adjacency
is required; gaps and overlaps do not silently become continuous history. Same
metadata periods are deduplicated for structure only: amounts, competing values,
revision precedence, accounting denominators and materiality are not inspected.
Field summaries include visible period ranges, duration categories and public-age
ranges. No staleness threshold is activated. Primary family gaps use explicit
precedence and reconcile to the full persisted denominator before sampling.

Corporate-action reporting counts metadata for dividends, splits,
mergers/spinoffs, delistings and delisting proceeds separately, retaining an
unknown-action count. Metadata presence is not completeness or proceeds-value
evidence. Coverage declarations are inventoried, not accepted as certification.
No approved benchmark contract/metadata adapter currently exists; benchmark
availability is unverified, rather than inferred from ordinary price rows or
declared absent across unknown tables.

## Decisions versus acquisition

Operator decisions remain accounting definitions/contract, historical scope and
identity, sample minima, period alignment/staleness/materiality, outcome and
benchmark definitions, costs, temporal inference and registration/holdout controls.
The report preserves all eight unresolved blocker codes and
`preregistration_ready=false`; there is no automatic resolution path.

Absent records may require a separately proposed acquisition plan. Incompatible
records first require diagnosis against approved meanings; missing provenance
requires source/identity/availability evidence, not relaxed safeguards. Plans
must identify fields, source/licence, period/security scope, budget, isolation,
lineage, availability constraints and acceptance checks. Historical membership,
delistings, mappings, comparable filing periods, action/proceeds metadata and
benchmark metadata are distinct plan scopes. Neither this command nor its
output authorizes provider access, ingestion, materialization or model execution.

## Bounds, errors and development verification

Only explicit metadata projections are queried. No prices, volumes, action
amounts, benchmark returns, realised outcomes, feature/model collections or raw
provider payloads are selected. Both connections use `read_only=True`; both files
are SHA-256 fingerprinted before and after, including on inventory failure.
No stored path, endpoint, source string or unbounded identifier is returned.
Security samples use SHA-256 identity references, with deterministic ordering.

The compact UTF-8 response limit is 128 KiB; each family returns at most 10
security summaries with exact total/returned/truncated metadata. Each supported
table has a 500,000-row work cap: exceeding it fails closed, never returns a
partial inventory. Unknown schemas cannot acquire dynamic adapters. Period-chain
construction has a 50,000-operation/state work cap per call and fails closed
if exceeded. Errors use the existing stable redacted CLI envelope and nonzero
exit status. A work/hash
failure uses `TRACK_B_HISTORY_INVENTORY_FAILED`; invalid input and byte-limit
failures retain the established investment-research error codes.

Offline tests use synthetic temporary databases only, including poison numerical
columns, explicit query-projection assertions, canonical/raw separation,
boundary equality/future materialization, incompatible metadata, chain gaps,
output/work bounds and pre/post fingerprint checks on failure. No operator
database or provider was accessed during development.

## Windows PowerShell 5.1 verification — complete block

After merge, stop concurrent database writers and run this complete block using
the established operator paths. It saves UTF-8 reports at both established
decision boundaries, verifies bounded deterministic output and zero model
outputs, and checks both database hashes in `finally`, even if tests or report
verification fail. No expected live coverage count is hardcoded.

```powershell
& {
  $ErrorActionPreference = "Stop"
  $Repo = "C:\Users\Juan Estrada\Projects\SignalLens"
  $Backend = Join-Path $Repo "backend"
  $Python = "..\.venv\Scripts\python.exe"
  $Research = "data\research\signallens-research.duckdb"
  $Production = "data\signallens.duckdb"
  $Decisions = @("2026-10-04T21:30:00+00:00", "2026-10-05T00:30:00+00:00")
  Push-Location $Repo
  try {
    git switch main
    if ($LASTEXITCODE -ne 0) { throw "git switch failed" }
    git pull --ff-only
    if ($LASTEXITCODE -ne 0) { throw "git pull failed" }
  } finally { Pop-Location }
  Push-Location $Backend
  try {
    $BeforeResearch = (Get-FileHash -LiteralPath $Research -Algorithm SHA256).Hash
    $BeforeProduction = (Get-FileHash -LiteralPath $Production -Algorithm SHA256).Hash
    try {
      & $Python -m pytest tests/test_track_b_history.py tests/test_track_b_panel.py tests/test_investment_research.py tests/test_financial_strength.py tests/test_liquidity_materialization.py -q
      if ($LASTEXITCODE -ne 0) { throw "Offline regression failed" }
      $Reports = Join-Path $Backend "data\research\reports"
      New-Item -ItemType Directory -Force -Path $Reports | Out-Null
      $Utf8 = New-Object System.Text.UTF8Encoding($false)
      $Check = @'
import json, pathlib, sys
p = pathlib.Path(sys.argv[1])
r = json.loads(p.read_text(encoding='utf-8'))
assert r['command'] == 'track-b-historical-evidence-inventory'
assert r['read_only'] and r['metadata_only']
assert not r['realized_outcome_values_read']
assert not r['preregistration_ready'] and not r['model_executed'] and not r['panel_persisted']
assert not r['acquisition_authorized'] and r['contract_selected'] is None
assert r['sample_thresholds'] is None and r['validation_credit'] == 0
assert r['unresolved_requirement_count'] == len(r['blockers']) == 8
assert all(b['state'] == 'unresolved' for b in r['blockers'])
for k in ('scores','rankings','candidates','recommendations','allocations','selections','paper_selections','vintages','prospective_vintages','validation_observations'):
    assert r[k] == [], k
for db, expected in (('research',sys.argv[2]),('production',sys.argv[3])):
    fp = r['database_fingerprints'][db]
    assert fp['unchanged'] and fp['before'] == fp['after']
    assert fp['before']['sha256'].lower() == expected.lower()
    for layer in ('canonical_history','raw_sec_history'):
        for f in r['databases'][db][layer]['families'].values():
            assert sum(f['primary_gap_counts'].values()) == f['population_denominator']
            assert f['certified_formula_count'] == 0
            s = f['samples']
            assert s['returned_count'] == len(s['items']) <= s['sample_limit'] == 10
            assert s['total_count'] == f['securities_with_structural_chain']
            assert s['truncated'] == (s['total_count'] > s['returned_count'])
n = len(json.dumps(r,sort_keys=True,separators=(',',':')).encode('utf-8'))
assert n == r['compact_utf8_bytes'] <= r['bounds']['maximum_compact_utf8_bytes'] == 131072
print(p.name, 'compact_utf8_bytes=', n, 'unresolved_groups=', r['unresolved_requirement_count'])
'@
      for ($i = 0; $i -lt $Decisions.Count; $i++) {
        $Decision = $Decisions[$i]
        $Text = & $Python -m app.investment_research_cli track-b-historical-evidence-inventory --research-db $Research --production-db $Production --decision-at $Decision
        if ($LASTEXITCODE -ne 0) { throw "Inventory failed at $Decision" }
        $File = Join-Path $Reports ("track-b-history-{0}.json" -f $i)
        [System.IO.File]::WriteAllText($File, ($Text -join "`n"), $Utf8)
        & $Python -c $Check $File $BeforeResearch $BeforeProduction
        if ($LASTEXITCODE -ne 0) { throw "Report verification failed" }
        $Repeat = & $Python -m app.investment_research_cli track-b-historical-evidence-inventory --research-db $Research --production-db $Production --decision-at $Decision
        if ($LASTEXITCODE -ne 0) { throw "Repeat inventory failed" }
        if (($Repeat -join "`n") -cne ($Text -join "`n")) { throw "Output was not deterministic" }
      }
    } finally {
      $AfterResearch = (Get-FileHash -LiteralPath $Research -Algorithm SHA256).Hash
      $AfterProduction = (Get-FileHash -LiteralPath $Production -Algorithm SHA256).Hash
      if ($BeforeResearch -ne $AfterResearch -or $BeforeProduction -ne $AfterProduction) {
        throw "Database hash changed during verification"
      }
      Write-Host "Research and production SHA256 unchanged"
    }
  } finally { Pop-Location }
}
```

Review source ranges, mapping limitations, field state counts and chain gaps at
both boundaries. Reports are inventory evidence, not registrations, samples,
selections, vintages, model validation or acquisition permission. The PowerShell
block is supplied for operator execution and was not executed on operator data.
