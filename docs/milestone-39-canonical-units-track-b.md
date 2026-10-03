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

## Operator defect and corrected semantics

The first production-authorized research run appended 7,957 correct revisions,
but the planner continued to scan each original withheld row without resolving
its `supersedes_evidence_key` to the appended revision. Consequently plan and
status again reported all 7,957 sources. The preview consumer also chose rows by
`available_at` alone, so equal-timestamp revisions depended on physical row
order. This was a lineage-resolution defect, not a failure of the locked unit
rule or of the append-only writes.

Plan, status, and consumers now share the exact 1.0.0 lineage contract: source
evidence key, revision type, rule identifier/version, exact source and canonical
units, and scale must all match. JSON key ordering is irrelevant. A matching
completed repair is visible at its declared repair decision boundary, including
the already-written 1.0.0 rows whose original provenance predates the explicit
`repair_decision_at` field. Earlier decision boundaries cannot see that repair,
and later or unknown rule versions cannot silently supersede it. Consumers sort
a matching usable canonical revision ahead of its preserved withheld source.

An identical authorized retry finds the completed deterministic run, rolls back
without writing, and reports `inserted: 0`, `unchanged: <planned_count>`, and
`idempotent_retry: true`. Status reports bounded visible repaired counts grouped
by canonical field and rule version.

## Readiness meanings

Every family now reports these separate states from one shared definition:

* `any_input_available`: at least one required or optional input exists;
* `minimum_calculable`: enough inputs exist for at least one defensible partial
  calculation (not permission to score the family);
* `full_family_ready`: every required factor is available;
* `required_factors` and `optional_factors`;
* exact `missing_required_inputs` and `withholding_reasons` diagnostics.

The EPS repair diagnostic separately counts repaired historical observations now
usable, companies whose EPS inputs were already satisfied, companies having no
qualifying EPS source, and companies with EPS withheld for another reason. Thus
an unchanged growth-family count is valid when repaired history does not fill a
currently missing required input; it no longer implies that consumers missed
the repaired revisions.

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

# Exact post-merge read-only verification commands. Expect zero eligible/pending.
python -m app.investment_research_cli plan-canonical-unit-repair --research-db $Research --production-db $Production --decision-at $DecisionAt
python -m app.investment_research_cli canonical-unit-repair-status --research-db $Research --production-db $Production --decision-at $DecisionAt

# Safe idempotent retry. Expect inserted 0, unchanged 7957, idempotent_retry true.
python -m app.investment_research_cli apply-canonical-unit-repair --research-db $Research --production-db $Production --decision-at $DecisionAt --authorization $Authorization
```

Archive all four JSON outputs with the backup fingerprint. A completed apply can
be safely retried with identical arguments; it reports an idempotent completion.
Never substitute the production path for the research path and never perform the
repair against an operator database during development.
