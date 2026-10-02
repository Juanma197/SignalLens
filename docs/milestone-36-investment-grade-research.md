# Milestone 36 — investment-grade research runbook

## Boundary: Track A versus Track B

Track A remains the frozen `prospective-us-dilution-1.0.0` hypothesis: registered
2026-10-01, hash `7b11264778fd120c03c820275d9c048d002bdb8564510fcf989cb590ce1b7ebd`,
90% price percentile plus 10% dilution percentile, no more than three selections,
and exact 126/252-session horizons. Nothing in Milestone 36 changes its inputs,
comparators, gates, vintages, or results.

Track B is **a research foundation, not a model**. It returns no rankings,
candidates, selections, vintages, validation observations, recommendations, fair
values, or purchase eligibility. Its six intended families are value, business
quality, financial strength, growth, shareholder treatment, and price/risk. No
weights have been selected or optimized. The existing indicative Top 3 explains
frozen Track A mathematics before a vintage; it is neither a buy list nor a claim
of suitability, validation, or expected accuracy.

## Coverage and point-in-time rules

Coverage means that a catalogue member has finite, compatible evidence for the
specific field—not merely a row in a database. Every SEC fact requires a public
availability timestamp and retrieval timestamp at or before the timezone-aware
decision instant. Later-retrieved facts, missing timestamps, stale evidence,
ambiguous mappings, incompatible units/currencies, unsupported taxonomies,
unreliable denominators, and ingestion failures are separate stable reason codes.
No missing value becomes zero, neutral, average, or synthetic. Samples are capped
at ten symbols. Both DuckDB files are opened read-only and SHA-256 fingerprints
must match before and after.

The catalogue is current-membership based, so historical reports have a
survivorship limitation. Coverage is US-only. International taxonomies,
currencies, filing regimes, calendars, and issuer identities have not been made
comparable. Financial-sector accounting and negative or near-zero denominators
require separate review. Official event metadata can disclose filing context, but
does not justify inferring going concern where metadata cannot represent it.

## Total return, costs, and gates

Gross total return is `(split-adjusted ending price + cash distributions) /
split-adjusted starting price - 1`, subject to resolved actions and exact completed
sessions. Net return subtracts explicit round-trip spread, commission, FX, and
slippage assumptions multiplied by turnover. Default research assumptions are
25, 5, 10, and 10 basis points respectively; the spread is labelled a conservative
estimate unless observed. Assumptions are bounded and included in a deterministic
configuration hash. Average daily traded value and position value / traded value
support liquidity review. Missing, delisted, unresolved-action, or incomplete
horizons are withheld.

Research gates cover history, freshness, liquidity, estimated cost, identity,
finite compatible accounting, denominators, filing staleness, defensible official
going-concern context, extreme dilution, unresolved actions, valuation inputs,
financial-sector comparability, and current-membership survivorship. A failure
withholds the affected calculation; it never assigns a bad score and is not a
recommendation gate.

## Safe repair workflow

Run the audit, then the plan. The plan only separates automatic retrieval work,
human review, and unavailable cases and estimates requests; it performs no
ingestion and changes no mapping. A concept alias is usable only after an audited
mapping proves concept, accounting meaning, unit, duration, and sign compatibility.
An alternate fact never marks the original item repaired. Operators must use the
existing separately authorised ingestion workflows later, preserve point-in-time
timestamps, re-run tests, and re-audit.

## Exact post-merge PowerShell commands

```powershell
$DecisionAt = "2026-10-31T23:59:59+00:00"
$ResearchDb = "C:\SignalLens\data\research\signallens-research.duckdb"
$ProductionDb = "C:\SignalLens\data\signallens.duckdb"
python -m app.investment_research_cli investment-grade-coverage-audit --research-db $ResearchDb --production-db $ProductionDb --decision-at $DecisionAt
python -m app.investment_research_cli plan-investment-data-repair --research-db $ResearchDb --production-db $ProductionDb --decision-at $DecisionAt
python -m app.investment_research_cli execution-cost-capability --research-db $ResearchDb --production-db $ProductionDb --decision-at $DecisionAt
python -m app.investment_research_cli undervalued-quality-research-readiness --research-db $ResearchDb --production-db $ProductionDb --decision-at $DecisionAt
```

Run from `backend`; redirect JSON if an audit artifact is needed. These commands
must point at distinct, regular, non-symlink/non-hard-linked databases.

## Evidence required before “purchase-eligible” can be considered

A future model needs materially complete, unbiased point-in-time inputs; resolved
identity, actions, delistings and denominators; adequate liquidity; realistic
locked costs; a distinct semantic version and hash; a pre-registration timestamp;
locked horizons and comparators; documented weights justified before outcomes;
development and untouched holdout periods; multiple-testing controls; and complete
prospective evidence collected only after registration. Suitability, material
risks, accounting comparability, execution capacity, and refusal rules must also
be reviewed. Milestone 36 supplies none of those conclusions.
