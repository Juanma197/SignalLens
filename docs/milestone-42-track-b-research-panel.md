# Milestone 42 — draft research panel and preregistration blockers

Implementation starts from main `7822272` (PR #89). This is a read-only Track B
research specification and evidence assessment, not a registered model. No
panel persistence, model execution, backtest, return tuning, provider request,
materialization, alias activation, production write, deployment or Track A change
is authorized. No scores, ranks, candidates, recommendations, allocations,
selections, prospective vintages, validation observations or validation credit.

The versioned [draft specification](../backend/app/track_b_panel_draft_v0.1.0.json)
is `track-b-research-panel-0.1.0-draft`. The assessment returns its canonical JSON
SHA-256. The six formula drafts retain the earlier research hypothesis; they do
not freeze weights or constitute approval. Schedule, durable share-class identity,
universe, availability, history, factor inputs/formulas/denominator rules,
missingness, exclusions and provenance are explicit in the specification.

## Supplied operator findings, not checkout evidence

Operator verification reported 24 prior aggregate/consumer checks passing,
PR #89 merged, historical NEU preview 104904 compact UTF-8 bytes and later preview
104906 bytes. Both databases were unchanged. Canonical resolution selected zero
historical / 187 later revisions with zero issues and exact source deduplication;
liquidity observations reconciled to 61 current-assets, 61 current-liabilities,
and 65 unrestricted-cash observations. These observation counts are not company
coverage counts, and are not embedded in CLI results.

Reported comparable universe: 71. Full-family company counts: business quality
63, financial strength 0, growth 61, price/risk 70, shareholder treatment 31,
value 33. Reported exact bins were 3:18, 4:30, 5:14, 6:0. Their sum is 62;
**nine companies therefore have fewer than three full families**. The individual
0/1/2 counts cannot be inferred from those aggregates. At least four is the
separate cumulative count 44, not the exact four-family count 30. The new CLI
computes exact 0–6 bins over the complete stored comparable denominator and
fails closed if they do not sum to that denominator. The existing feasibility
CLI now includes the previously omitted bins and labels their semantics.

## Readiness and accounting review

Existing `family_readiness` semantics remain unchanged: any-input means at least
one required/optional usable input; minimum-calculable uses each family's current
minimum-input rule (for some families this is only any-input); full-family means
all existing required fields are available. None proves historical formula
calculability. In particular, liquidity materialization does not supply the
existing financial-strength requirements for debt, equity and gross interest.
Cash and current ratio can improve independent components without making the
existing financial-strength family fully ready.

The assessment separately reuses Milestone 40 component readiness and Contracts
A–D, plus the canonical liquidity resolver. A requires all components ready;
B requires leverage/liquidity; C requires two independent components; D compares
an explicitly debt-free branch with a leveraged leverage/coverage branch.
The audit full-family readiness permits explicitly evidenced not-applicable
components; A's all-mandatory counterfactual is stricter. No contract is selected.
Higher coverage is not a reason to select B, C or D. Unmatched component evidence
is counted as unavailable, never inferred debt-free. Component identity must
match both catalogue security_id and listing symbol; CIK alone cannot distinguish
share classes.

Review must resolve debt composition/overlap, leases, commercial paper and
convertibles; restricted versus unrestricted cash; gross versus net interest;
defensible EBIT; NCI equity; sector exclusions; explicit zero-debt applicability;
and aligned fiscal periods/currency/duration. Existing audit proxies can expose
pre-tax income as an EBIT-compatible audit field and mix latest field periods;
therefore their counts are evidence counterfactuals, not accounting certification.
No new alias or debt construction is activated here.

## Evaluation draft and blocking requirements

Monthly final US-session observations with a 23:59 UTC decision boundary and
next-session open entry are proposed, subject to calendar review. Draft horizons
are 126/252 completed sessions, with 252 primary and 126 secondary. A fixed broad
US investable total-return benchmark, source/version/currency and dividend
reinvestment convention remain to be approved. Entry/exit, split adjustments,
dividends, mergers, spinoffs and delisting proceeds must be complete. Missing
outcomes cannot silently become price returns or disappear from the sample.

Costs reuse the existing validated `CostAssumptions`: spread 25, commission 5,
FX 10, slippage 10 bps; turnover 1; illustrative position 10000. The combined
50 bps is illustrative. Per-side/round-trip spread and commissions, USD FX,
impact, capacity and participation remain unresolved. Estimated costs are not
observed execution evidence. No outcomes are computed or inspected here.

Chronological development, specification review and untouched holdout splits
require exact dates and minimum samples. Purge overlapping outcome windows and
embargo at least 252 sessions. Control issuer/share-class dependence and
multiplicity across families/horizons. Freeze all choices before outcome access;
log holdout access and register a distinct version/hash/timestamp. No random
split, future filing, retrospective membership or restatement may leak into
features. Historical delisted membership and effective identity continuity must
be established; current catalogue coverage cannot prove survivorship-free history.

Eight requirement groups remain unresolved: accounting contract; historical
universe/identity; sample policy/minima; aligned history/staleness; outcomes and
benchmark; costs; temporal splits/inference; registration/holdout controls.
`preregistration_ready` is always false for this draft. Eight is a count of
unresolved specification groups, not eight missing data rows or affected companies.
Unknown company-level blocker counts are null. Input availability/missing counts,
reason counts, family missing-input counts, component states and financial audit
missing fields are exact over their disclosed stored-evidence denominators.
Financial missing-input counts use the matched component-evidence denominator;
unmatched companies are separately counted as unassessed. Component unavailable
counts include those unassessed companies. Observation withholding counts can
exceed companies and are labelled separately.
Neither a field's presence nor the historical coverage-by-year proxy certifies
aligned TTM history, materiality floors, complete outcomes or benchmark coverage.

## Read-only runbook — Windows PowerShell 5.1

Run after merge, with exclusive access (stop concurrent writers) from Juan's
existing repository. This complete block uses existing paths, checks exit codes,
writes UTF-8 report files, asserts bounded output and prohibited-output emptiness,
and checks both database hashes even on failure. Reports are local files only.
It does not ingest, repair, persist a panel or execute a model.

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
      & $Python -m pytest tests/test_track_b_panel.py tests/test_investment_research.py tests/test_financial_strength.py tests/test_liquidity_evidence.py tests/test_liquidity_materialization.py -q
      if ($LASTEXITCODE -ne 0) { throw "Offline tests failed" }
      $Reports = "data\research\reports"
      New-Item -ItemType Directory -Force -Path $Reports | Out-Null
      $Utf8 = New-Object System.Text.UTF8Encoding($false)
      for ($i = 0; $i -lt $Decisions.Count; $i++) {
        $Decision = $Decisions[$i]
        $Text = & $Python -m app.investment_research_cli track-b-preregistration-assessment --research-db $Research --production-db $Production --decision-at $Decision
        if ($LASTEXITCODE -ne 0) { throw "Assessment failed at $Decision" }
        $File = Join-Path $Reports ("track-b-panel-42-{0}.json" -f $i)
        [System.IO.File]::WriteAllText((Join-Path $Backend $File), ($Text -join "`n"), $Utf8)
        $Check = @'
import json, pathlib, sys
p = pathlib.Path(sys.argv[1])
r = json.loads(p.read_text(encoding='utf-8'))
assert r['read_only'] and not r['preregistration_ready']
assert not r['panel_persisted'] and not r['model_executed']
assert r['validation_credit'] == 0
for key in ('scores','rankings','candidates','recommendations','allocations','selections','paper_selections','vintages','prospective_vintages','validation_observations'):
    assert r[key] == [], key
for db in ('research','production'):
    assert r['database_fingerprints'][db]['unchanged']
d = r['family_distribution']
assert d['reconciled'] and sum(d['exact_bins'].values()) == d['total']
n = len(json.dumps(r,sort_keys=True,separators=(',',':'),default=str).encode('utf-8'))
assert n == r['compact_utf8_bytes'] <= r['bounds']['maximum_compact_utf8_bytes']
print(p, 'compact_utf8_bytes=', n, 'denominator=', d['total'], 'bins=', d['exact_bins'], 'unresolved_groups=', r['unresolved_requirement_count'])
'@
        & $Python -c $Check $File
        if ($LASTEXITCODE -ne 0) { throw "Report verification failed: $File" }
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

Review aggregate missing inputs and blockers at both boundaries. Reconcile any
operator denominator differences before considering historical panel work.
Implementation/offline fixture success supplies no validation credit or model
approval. The next work remains accounting review and evidence planning, followed
by an independently approved preregistration; no panel/model execution is enabled.

## Development verification

Offline regression command (from `backend`):
`../.venv/bin/python -m pytest tests/test_track_b_panel.py tests/test_investment_research.py tests/test_financial_strength.py tests/test_liquidity_evidence.py tests/test_liquidity_materialization.py -q`
passed **64 tests**. The focused panel suite passed **8 tests** after final
missing-input denominator clarification; the draft/size-contract check also passed.
`git diff --check` passed. The Windows PowerShell block is provided for operator
execution and was not run in this Linux development environment. No operator
findings were reproduced or promoted to model/validation evidence here.
