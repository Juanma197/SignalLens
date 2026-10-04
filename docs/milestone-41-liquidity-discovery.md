# Milestone 41: liquidity evidence discovery

## Boundary and observed gap

This is **TRACK B RESEARCH FOUNDATION — NOT A MODEL**. It generates no purchase
recommendation, candidate, ranking, selection, vintage, or validation credit. At
the `2026-10-02T18:15:00+00:00` operator boundary, the comparable population was
71 US operating companies and existing liquidity readiness was 0/71. Milestone
41 explains that gap without ingesting, repairing, normalizing, or mutating data.
It does not activate an alias or select a Track B contract.

The audit reads both databases in DuckDB read-only mode and fingerprints each
before and after. It never contacts SEC, a provider, Railway, or another service.
The raw stored SEC facts are the discovery source; canonical evidence is a
supplement when the same raw observation is absent. Exact aggregate counts use
the entire comparable population, while returned detail is bounded.

## Exact discovery and accounting rules

The versioned discovery rule uses a case-sensitive allow-list for the core
concepts and reports other exact names only when they contain one of the published
liquidity tokens. A token or similar label is not an alias. Each occurrence keeps
taxonomy/version when stored, balance type (or `unknown_not_stored`), instant or
duration nature, unit, currency, scale, sign, period end, form, accession, public,
retrieval, and canonical-availability timestamps. Reports give observation and
company coverage, latest visible periods, relationship (direct, broader,
narrower, or incompatible), confidence, and the exact acceptance/withholding
reason.

Accepted balance-sheet facts must be instant, finite and nonnegative; use USD or
monetary units with USD currency; and have a finite positive scale. Public and
retrieval timestamps must exist and be no later than the decision. When canonical
availability is stored, it must equal their maximum. Facts older than 550 days
are stale. Company, date, currency, scale, and accounting scope must match for a
construction. Conflicting values for the same semantic period are disclosed and
withheld from a clean diagnosis.

The high-confidence direct proposal covers `AssetsCurrent`,
`LiabilitiesCurrent`, `CashAndCashEquivalentsAtCarryingValue`, and `Assets`.
`CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents` is a broader cash
aggregate, while `RestrictedCashAndCashEquivalentsCurrent`, `ShortTermInvestments`,
`MarketableSecuritiesCurrent`, `InventoryNet`, `AccountsReceivableNetCurrent`, and
`AccountsPayableCurrent` are separately assessed components. Total liabilities
are not current liabilities. Every output proposal is versioned
`liquidity-alias-proposal-1.0.0` and has status `proposed_not_authorized`.

## Cash separation and construction risks

Unrestricted cash, restricted cash, combined cash plus restricted cash,
short-term investments, and marketable securities are never silently equated.
Unrestricted cash is preferred directly. Combined cash may be decomposed only
when separately reported restricted cash matches company, period, USD currency,
scale, and decision boundary; a negative result is refused. Investments and
marketable securities are not silently added to cash.

Working capital is current assets minus current liabilities. It is valid when
both instant observations match even if the result is negative; zero current
liabilities does not prevent reporting working capital. Ratios require a strictly
positive denominator. Current ratio uses direct current assets and liabilities.
Quick assets may be assessed as current assets minus inventory only with fully
compatible facts—never merely because both labels exist. Cash ratio uses direct
unrestricted cash or a disclosed compatible decomposition. Working-capital-to-
assets additionally requires compatible, positive total assets. Net-debt-to-assets
requires an independently defensible debt construction and unrestricted cash;
this milestone does not relax Milestone 40's debt rules. Financial companies need
a separate accounting contract.

Counterfactuals show existing readiness, high-confidence direct-alias readiness,
compatible-construction readiness, direct cash coverage, direct-or-constructed
cash coverage, and effects on Contracts A–D. They keep `contract_selected: null`.
Coverage does not authorize whichever choice produces the largest sample.

## Unresolved judgments

Review remains necessary for issuer extensions, balance attributes not retained
in storage, current/non-current classification differences, cash legally or
contractually unavailable despite an unrestricted-looking tag, the scope of
marketable securities, receivable and inventory definitions, consolidated versus
parent scope, fiscal-calendar staleness, finance-sector presentation, debt/lease
scope, and whether a broader aggregate can ever be decomposed safely. No judgment
is resolved from a similar label or from coverage alone.

An eventual materialization requires a separate authorization: approve a new
alias-contract version, pin exact concepts and accounting exclusions, add an
idempotent plan/apply workflow with database backup and immutable audit journal,
rerun point-in-time regression tests, and only then update consumers. This
milestone intentionally provides no apply command and leaves financial-strength
readiness unchanged.

## Output guarantees

Aggregate discovery is at most 512 KiB compact UTF-8. Contract assessment and one
company preview are each at most 256 KiB. Samples contain at most 10 companies,
symbols, concepts, and citations; preview alternatives contain at most three
observations per canonical field. Collections publish total/returned counts and
truncation. Errors are stable and redact paths, SQL, and payloads. Aggregate APIs
never return unrestricted observations, filing bodies, provider payloads,
filesystem paths, SQL, or an unbounded company list.

## Exact post-merge PowerShell procedure

```powershell
git switch main
git pull --ff-only
$Decision = "2026-10-02T18:15:00+00:00"
$Research = "C:\SignalLens Data\research.duckdb"
$Production = "C:\SignalLens Data\production.duckdb"
$BeforeResearch = (Get-FileHash -Algorithm SHA256 -LiteralPath $Research).Hash
$BeforeProduction = (Get-FileHash -Algorithm SHA256 -LiteralPath $Production).Hash

Push-Location backend
python -m app.investment_research_cli liquidity-evidence-discovery `
  --research-db $Research --production-db $Production --decision-at $Decision |
  Set-Content -Encoding utf8 "..\liquidity-evidence-discovery.json"
python -m app.investment_research_cli liquidity-contract-assessment `
  --research-db $Research --production-db $Production --decision-at $Decision |
  Set-Content -Encoding utf8 "..\liquidity-contract-assessment.json"
python -m app.investment_research_cli liquidity-company-preview `
  --research-db $Research --production-db $Production --decision-at $Decision `
  --qualified-symbol "EXAMPLE.US" |
  Set-Content -Encoding utf8 "..\liquidity-company-preview.json"
Pop-Location

$AfterResearch = (Get-FileHash -Algorithm SHA256 -LiteralPath $Research).Hash
$AfterProduction = (Get-FileHash -Algorithm SHA256 -LiteralPath $Production).Hash
if ($BeforeResearch -ne $AfterResearch -or $BeforeProduction -ne $AfterProduction) {
  throw "Read-only invariant failed"
}
python -c "import json,pathlib; fs=['liquidity-evidence-discovery.json','liquidity-contract-assessment.json','liquidity-company-preview.json']; [print(f,len(json.dumps(json.loads(pathlib.Path(f).read_text(encoding='utf-8-sig')),sort_keys=True,separators=(',',':')).encode('utf-8'))) for f in fs]"
```

Verify `read_only`, both immutability flags, proposal status, null contract
selection, all empty zero-output arrays, and zero validation credit. Do not run an
ingestion, repair, enrichment, or materialization command.

Track A remains `prospective-us-dilution-1.0.0`, configuration hash
`7b11264778fd120c03c820275d9c048d002bdb8564510fcf989cb590ce1b7ebd`, registered
`2026-10-01T00:00:00Z`, with its 90% price / 10% dilution percentiles, maximum
three selections, exact 126/252-session outcomes, comparators, and tie-breaking
unchanged.

## Post-Milestone-41 raw-versus-canonical reconciliation

The operator run at `2026-10-02T18:15:00+00:00` found 71 comparable companies,
0/71 ready for each required field, 71/71 `only_broader_aggregate_exists` for
current assets (the stored concept was `Assets`), and 71/71
`no_relevant_fact_stored` for current liabilities and unrestricted cash. All
direct and constructed metrics were consequently 0/71. Proposals remained
unapproved and both fingerprints were unchanged.

Code review established that Milestone 41 did read `sec_facts` from **both** the
research and production databases, then supplemented those raw observations with
canonical rows only when their stable observation identity was not already in
the raw set. It therefore did not query canonical materialization alone. Its
diagnosis, however, merged raw and canonical evidence and could not prove which
layer caused an absence. The two new commands preserve the point-in-time reader
boundary while reporting that distinction explicitly.

### Persisted SEC/XBRL evidence path

| Database/table | Layer | Identity and join | Concepts | Measurement/period | Availability |
|---|---|---|---|---|---|
| research and production `sec_facts` | raw SEC companyfacts observation | `security_id`, `qualified_symbol`, `cik`, `fact_key`; exact `security_id` joins the comparable classification, with CIK checked through `sec_issuers` | `taxonomy`, `concept` | `value`, `unit`, `currency`, `period_start`, `period_end`, fiscal period/frame; source does not retain an explicit scale | `filed_date`, `public_at`, `retrieved_at` |
| research `canonical_factor_evidence` | canonical/materialized | `security_id`, `qualified_symbol`, `evidence_key`, `source_fact_key`; exact `security_id` join | `canonical_field`, `original_concept_or_field`, alias contract | `value`, `unit`, `currency`, start/end/instant and fiscal period; source scale may be in provenance | public, retrieved, canonical available, and materialized timestamps |
| research and production `sec_issuers` | normalized identity bridge | `security_id` and SEC `cik`; no ticker or name inference is permitted | not applicable | not applicable | `mapped_at` |
| research and production `sec_filings` | normalized filing metadata | `cik` and `accession_number`, joined through the issuer bridge | not applicable | form and filed date | `public_at`, `retrieved_at` |

The commands also emit this catalog from the actual schemas, omitting tables
that do not exist. `security_classification_evidence` defines the comparable
universe; it is classification evidence rather than an SEC fact source. No row
is associated by ticker or company name.

### Interpretation of the reconciliation

The inventory assigns exactly one state to every company/required-field pair.
Canonical compatible evidence wins. Compatible raw evidence can establish
feasibility but is reported as `compatible_raw_fact_not_materialized`, never
promoted. Post-decision, stale, duration, and incompatible-unit facts remain
withheld. Extensions are separately inventoried as review-required and never
activated. `Assets` is only a broader aggregate, and combined cash is not
unrestricted cash. Same-period conflicts fail closed.

The operator databases, rather than this repository, contain the final exact
counts. In the reported 0/71 run, Milestone 41's raw concept discovery showing
only `Assets`, `NetCashProvidedByUsedInOperatingActivities`, and
`free_cash_flow` is evidence that its raw reader ran; the new inventory is the
required definitive check for research/production copies, identity conflicts,
post-decision observations, and canonical omissions. Interpret outcomes as:

| Inventory outcome | Exact remaining gap |
|---|---|
| compatible raw, no canonical row | materialization gap: the exact standard raw fact was not represented by usable canonical evidence |
| no relevant raw or canonical fact | ingestion gap: the exact required standard concept is absent from stored SEC facts at the boundary |
| extension or conflicting visible facts | accounting review; do not activate an alias |
| unresolved issuer identity | identity repair using durable `security_id`/CIK evidence, never ticker/name inference |

### Safe post-merge PowerShell verification

```powershell
git switch main
git pull --ff-only
$Decision = "2026-10-02T18:15:00+00:00"
$Research = "C:\SignalLens Data\research.duckdb"
$Production = "C:\SignalLens Data\production.duckdb"
$BeforeResearch = (Get-FileHash -Algorithm SHA256 -LiteralPath $Research).Hash
$BeforeProduction = (Get-FileHash -Algorithm SHA256 -LiteralPath $Production).Hash

Push-Location backend
python -m app.investment_research_cli liquidity-raw-canonical-inventory `
  --research-db $Research --production-db $Production --decision-at $Decision |
  Set-Content -Encoding utf8 "..\liquidity-raw-canonical-inventory.json"
python -m app.investment_research_cli liquidity-evidence-gap-assessment `
  --research-db $Research --production-db $Production --decision-at $Decision |
  Set-Content -Encoding utf8 "..\liquidity-evidence-gap-assessment.json"
Pop-Location

$AfterResearch = (Get-FileHash -Algorithm SHA256 -LiteralPath $Research).Hash
$AfterProduction = (Get-FileHash -Algorithm SHA256 -LiteralPath $Production).Hash
if ($BeforeResearch -ne $AfterResearch -or $BeforeProduction -ne $AfterProduction) {
  throw "Read-only invariant failed"
}
```

Verify all 213 company/field classifications sum exactly, inspect the bounded
samples and before/after fingerprints, and confirm every ranking, candidate,
recommendation, selection, vintage, and validation collection is empty with
zero validation credit. These commands make no network request, perform no
ingestion or materialization, activate no alias, and write no database.
