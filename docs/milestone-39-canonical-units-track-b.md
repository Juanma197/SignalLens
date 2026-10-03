# Milestone 39: canonical units and Track B readiness

## Safety boundary

This capability is a research evidence repair and a read-only panel feasibility
assessment. It does not contact a provider or the SEC, choose weights, rank a
company, produce a Top 3, register a model, create a vintage, or award validation
credit. Track A remains frozen as `prospective-us-dilution-1.0.0`, configuration
hash `7b11264778fd120c03c820275d9c048d002bdb8564510fcf989cb590ce1b7ebd`,
with its 90/10 score and prospective evidence boundaries unchanged.

Every command requires distinct explicit research and production database paths
and a timezone-aware `decision_at`. Existing path, alias, symlink, and hard-link
guards apply. Plan, status, and feasibility open both databases read-only. Apply
writes only the research database, verifies the production fingerprint, and uses
one transaction, so any failure rolls the repair back completely.

## Locked dimensional-unit rule

`USD/shares` is normalized to canonical `USD/share` only for `basic_eps` and
`diluted_eps`, when the exact accounting concept is respectively
`EarningsPerShareBasic` or `EarningsPerShareDiluted`. The numerator must be USD,
the denominator exactly one share, the scale exactly 1, the fact a duration fact,
and no currency conversion may be needed. Existing point-in-time, sign,
provenance, and reliability checks still apply. There is no fuzzy matching,
substring replacement, or generic singular/plural conversion.

The new revision retains the source unit and records the canonical unit, rule ID
`eps-usd-per-share-lossless`, scale, rule version `1.0.0`, and the reason the
conversion is lossless. Repair never updates or deletes the withheld historical
row. It appends a usable revision whose deterministic key binds the source
evidence, rule version, and research database identity. The revision identifies
the evidence it supersedes; retries are idempotent.

## Readiness meanings

Every family now reports these separate states from one shared definition:

* `any_input_available`: at least one required or optional input exists;
* `minimum_calculable`: enough inputs exist for at least one defensible partial
  calculation (not permission to score the family);
* `full_family_ready`: every required factor is available;
* `required_factors` and `optional_factors`;
* exact `missing_required_inputs` and `withholding_reasons` diagnostics.

This explains the operator observations without treating partial evidence as a
ready family. All 71 companies can have a financial-strength input while none
has the full required set of assets, equity, both debt components, and interest
expense. Likewise, companies can have two optional value numerators while only
33 have every required price, market-capitalisation, enterprise-value, and free-
cash-flow input. The diagnostics identify the exact blockers for the other 38
value, all 71 financial-strength, and 40 shareholder-treatment cases; symbol and
pattern examples remain bounded.

## Post-merge PowerShell procedure

Do not run apply until the plan and backup have been reviewed. Substitute the
paths and decision timestamp once, retaining the UTC offset.

```powershell
$Research = 'C:\SignalLens Data\research.duckdb'
$Production = 'C:\SignalLens Data\production.duckdb'
$DecisionAt = '2026-10-03T00:00:00+00:00'
$Backup = "${Research}.pre-canonical-unit-repair.bak"

Copy-Item -LiteralPath $Research -Destination $Backup -ErrorAction Stop
python -m app.investment_research_cli plan-canonical-unit-repair --research-db $Research --production-db $Production --decision-at $DecisionAt

# Exact, case-sensitive authorization phrase:
$Authorization = 'I AUTHORIZE RESEARCH-ONLY CANONICAL EPS UNIT REPAIR 1.0.0'
python -m app.investment_research_cli apply-canonical-unit-repair --research-db $Research --production-db $Production --decision-at $DecisionAt --authorization $Authorization

python -m app.investment_research_cli canonical-unit-repair-status --research-db $Research --production-db $Production --decision-at $DecisionAt
python -m app.investment_research_cli track-b-panel-feasibility --research-db $Research --production-db $Production --decision-at $DecisionAt
```

Archive all four JSON outputs with the backup fingerprint. A completed apply can
be safely retried with identical arguments; it reports an idempotent completion.
Never substitute the production path for the research path and never perform the
repair against an operator database during development.
