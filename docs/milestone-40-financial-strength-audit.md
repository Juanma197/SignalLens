# Milestone 40: financial-strength evidence audit

## Purpose and boundary

This is a **TRACK B RESEARCH FOUNDATION — NOT A MODEL**. It produces no score,
rank, candidate, Top 3, purchase recommendation, selection, vintage, or validation
credit. It does not change Track B weights. Both databases are opened read-only,
fingerprinted before and after, and all evidence must be visible at the supplied
timezone-aware decision boundary. No provider is contacted.

The observed operator snapshot at `2026-10-02T18:15:00+00:00` had 71 comparable
US operating companies, 71 with any input, 69 minimum-calculable, and zero fully
ready. This follows mechanically from the old all-required definition: all 71
lacked `interest_expense`, 34 lacked `current_debt`, 33 lacked
`non_current_debt`, and two lacked `shareholders_equity`. These are operator
counts, not fixture-derived results and are not embedded as audit output.

Milestone 40 replaces that diagnosis with component readiness. Interest expense
is mandatory only for a coverage component, not leverage or liquidity. Absence
does not mean zero. Coverage may be explicitly not applicable only after positive,
contracted evidence establishes zero debt; missing debt remains undetermined.

## Evidence and alias semantics

`financial-strength-alias-contract-1.0.0` maps exact, case-sensitive XBRL concepts.
There is no fuzzy label matching or plural normalization. Every entry records its
canonical field, expected USD monetary unit and currency, instant/duration nature,
sign, accounting meaning, allowed transformation, excluded meanings, and
confidence. An observation is usable only when its public, retrieval, and
canonical availability timestamps exist, availability equals the later source
timestamp, and all three are at or before the decision boundary. Filing/accession,
period, source concept, unit, currency, scale, and withholding reason remain in
the company audit.

Restricted cash is separate from unrestricted cash. Gross interest expense,
non-operating interest expense, interest-and-debt expense, net interest, and
interest income remain distinct. A net-interest fact is never silently used as
gross expense. Explicit zero is evidence; absence is not. Finance-lease and
operating-lease liabilities remain separate.

## Debt construction and unresolved judgments

The preview reports alternatives without picking whichever maximizes coverage:

* A — explicitly reported total debt;
* B — current debt plus non-current debt;
* C — short-term borrowings plus current long-term debt plus non-current long-term debt;
* D — a future, explicitly documented compatible composition (none is silently invented).

Alternatives are never added together. A component is used once within a
construction, and leases are not silently folded into funded debt. Remaining
accounting judgments include whether a particular issuer's reported aggregate
includes finance leases, treatment of commercial paper and convertible debt,
whether an EBIT bridge is defensible, bank/insurer capital structure, and whether
NCI-inclusive equity is comparable. These require preregistered decisions, not a
coverage-maximizing choice.

## Metric feasibility rules

The contract assessment reports numerator, denominator, inputs, denominator
rules, sector limits, history, point-in-time rules, usable count, and exact
withholding counts for debt/assets, debt/equity, net debt/assets, net debt/EBIT,
interest coverage, operating cash flow/debt, current ratio, equity ratio, change
in leverage, and distress flags. EBITDA is never generated from an ambiguous
shortcut. Negative equity is a distress flag rather than an ordinary ratio;
non-positive EBIT, assets, debt, interest, or current liabilities are handled as
documented invalid-denominator or not-applicable states.

Banks, insurers, and financial-sector accounting require a separate contract and
are not forced into an operating-company ratio. Change in leverage requires two
comparable point-in-time periods; other metrics require at least one.

## Readiness and counterfactuals

* `any_input_available`: one usable contracted field exists.
* `minimum_calculable`: at least one component is ready.
* Component states: `ready`, explicit `not_applicable`, or `unavailable`.
* `full_family_ready`: leverage, liquidity, coverage, and cash-generation/debt-service
  are ready or explicitly not applicable.

The report compares, without selecting, Contract A (all components), Contract B
(leverage and liquidity mandatory; coverage optional), Contract C (two independent
components), and Contract D (explicit debt-free and leveraged branches). These
are research-contract counterfactuals, not model variants. No contract was selected
using future returns, apparent performance, or sample size.

## Operator procedure (Windows PowerShell, after merge)

Run from a clean checkout. Substitute local paths; paths containing spaces are safe.

```powershell
git switch main
git pull --ff-only
$Decision = "2026-10-02T18:15:00+00:00"
$Research = "C:\SignalLens Data\research.duckdb"
$Production = "C:\SignalLens Data\production.duckdb"

Push-Location backend
python -m app.investment_research_cli financial-strength-evidence-audit `
  --research-db $Research --production-db $Production --decision-at $Decision |
  Tee-Object -FilePath "..\financial-strength-evidence-audit.json"
python -m app.investment_research_cli financial-strength-contract-assessment `
  --research-db $Research --production-db $Production --decision-at $Decision |
  Tee-Object -FilePath "..\financial-strength-contract-assessment.json"
python -m app.investment_research_cli financial-strength-company-preview `
  --research-db $Research --production-db $Production --decision-at $Decision `
  --qualified-symbol "EXAMPLE.US" |
  Tee-Object -FilePath "..\financial-strength-company-preview.json"
Pop-Location
```

Verify both JSON reports say `read_only: true`, both fingerprint checks say
unchanged, all zero-output arrays are empty, validation credit is zero, and review
the bounded concept samples and withholding counts. Do not run an ingestion or
repair command as part of this procedure.

## Path forward

The operator audit supplies evidence coverage and unresolved judgments to a future
Track B research panel. That panel must settle definitions, sector scope, lease
treatment, denominator policy, and component/readiness contract before constructing
a historical panel. Only then may a separate preregistration freeze factor
definitions and an evaluation plan. This milestone neither authorizes repair nor
provides validation evidence for that future work. Track A
`prospective-us-dilution-1.0.0` and its registered configuration remain untouched.
