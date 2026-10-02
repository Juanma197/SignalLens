# Milestone 37 — comparable-universe evidence repair runbook

## Boundary

An active, US-labelled catalogue is a current-membership inventory, not automatically a comparable investment universe. It retains survivorship bias and can mix operating companies with banks, insurers, REITs, BDCs, funds, SPACs, units, foreign issuers/ADRs, development-stage businesses, and other special structures. Durable point-in-time evidence (including its source, public time, retrieval time, and identifier) classifies each security; conflicting, future, name-only, or ticker-only evidence is refused. Exclusions and bounded symbol samples remain visible.

Track B initially includes only ordinary US operating companies. Every other type is excluded until a sector-specific method is separately pre-registered. International accounting, currency, withholding-tax, market-structure, and ADR limitations are not assessed here.

## Mapping and taxonomy

CIK plus effective-dated listing identity controls mapping. The read-only plan distinguishes mapped, genuinely unmapped, stale ticker mapping, ticker reuse/ambiguity, unavailable evidence, provider/ingestion failure, and evidence available but not persisted. Stored evidence can resolve readiness with zero requests. It never writes a mapping; a later write needs explicit authorization and a transaction.

Alias contracts specify economic meaning, exact SEC concept, monetary units, instant/duration nature, sign, point-in-time/TTM construction, currency, aggregation permission, double-count group, ordinary-company limitation, and failure modes. Similar spelling is never enough. Cash/restricted cash, separately identified short-term investments, current and non-current borrowing, commercial paper, and interest expense are accepted only under those contracts.

## Mathematics and missing data

Earnings yield, FCF yield, and positive reliable book-to-market use market capitalization independently. Capex supports positive-outflow and negative-cash-flow conventions without reversing the subtraction. Negative earnings/FCF remain explicit valid raw observations; invalid, nonfinite, currency-incompatible, overlapping, and near-zero-denominator inputs are withheld. Book-to-market is restricted to the comparable class.

EV is optional: market cap + non-duplicated current debt + non-current debt − eligible cash. Missing debt is unknown, never zero, unless a filing positively confirms debt-free status. Debt/assets, debt/equity (positive meaningful equity only), net debt, and interest coverage are independent; zero/absent/inapplicable interest expense withholds coverage. Comparable history is required before describing balance-sheet deterioration.

Share evidence distinguishes diluted growth, basic/diluted consistency, issuance, defensible repurchases/SBC, official SEC capital-raise metadata, and reverse splits. Absence is not friendliness. Corporate-action state is explicitly one of verified coverage/no action, missing coverage, stored action, or unresolved action. A valid assessment with no action passes without fabricating an event.

No weights, score, percentile, candidate, rank, Top 3, recommendation, fair value, vintage, outcome, or validation credit is produced. Before Track B can be frozen, coverage targets must be met without relaxing safeguards; definitions, universe, factors, costs, comparators, horizons, and tie-breaks must be pre-registered; development and untouched holdout periods must be separated; multiple testing controlled; and prospective evidence collected after registration.

## Post-merge PowerShell (read only)

```powershell
$ResearchDb = "C:\\SignalLens\\data\\research.duckdb"
$ProductionDb = "C:\\SignalLens\\data\\production.duckdb"
$DecisionAt = "2026-10-31T23:59:59+00:00"
python -m app.investment_research_cli comparable-universe-research-readiness --research-db $ResearchDb --production-db $ProductionDb --decision-at $DecisionAt
python -m app.investment_research_cli plan-investment-data-repair --research-db $ResearchDb --production-db $ProductionDb --decision-at $DecisionAt
python -m app.investment_research_cli undervalued-quality-research-readiness --research-db $ResearchDb --production-db $ProductionDb --decision-at $DecisionAt
python -m app.investment_research_cli company-investment-factor-preview --research-db $ResearchDb --production-db $ProductionDb --decision-at $DecisionAt --qualified-symbol "AAPL.US"
```

Run from `backend`. These commands require distinct regular database files; alias, symlink, and hard-link protections apply. They fingerprint both databases before and after and perform no provider request or ingestion.
