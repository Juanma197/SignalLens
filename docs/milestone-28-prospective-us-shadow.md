# Milestone 28 — prospective US dilution-overlay shadow

## Frozen pre-registration

The canonical machine-readable specification is
`backend/app/prospective_us_shadow_v1.json`. Its version is
`prospective-us-dilution-1.0.0`; its SHA-256 configuration hash is emitted by
every plan and status command. Registration is **2026-10-01 00:00:00 UTC**.
The first legally permissible vintage is the October 2026 month-end *after*
registration, and only after the exact US month-end session and required FX are
complete. September 2026 and every earlier month are permanently refused.

The score is fixed at **90% existing price-only percentile + 10% point-in-time
dilution percentile**. Lower year-over-year diluted-share growth is better, so
buybacks may score favourably. Missing, stale, nonfinite, incompatible,
unreliable, late-filed, or late-retrieved evidence is unavailable—not zero,
neutral, or poor. Leverage, profitability, cash flow, growth, and valuation are
coverage/safety diagnostics only. Stable ordering is score descending then
qualified symbol ascending, with zero to three paper selections.

## Why this is prospective

The frozen 40% fundamentals composite failed: incremental excess was -4.16
percentage points at 126 sessions and -5.23 points at 252 sessions, with both
adjusted p-values equal to 1.0. Milestone 27 found no calculation or data defect.
Dilution was the only consistently helpful exploratory family, but reusing that
same history to confirm a revised rule would be selection bias. The small 10%
overlay is therefore an economic pre-registration, not a historically optimized
weight, and only future evidence can assess it.

## Monthly operator workflow

All commands require explicit, distinct research and production database paths.
Planning, status, and evaluation fingerprint both files and are strictly
read-only. The fixture is a decision-time, offline input bundle; commands never
contact a provider.

```powershell
$ResearchDb = "C:\SignalLens\data\research.duckdb"
$ProductionDb = "C:\SignalLens\data\production.duckdb"
$Fixture = "C:\SignalLens\inputs\prospective-us-2026-10.json"
$DecisionAt = "2026-10-30T22:00:00+00:00"
python -m app.prospective_us_shadow_cli plan-prospective-us-shadow --research-db $ResearchDb --production-db $ProductionDb --fixture $Fixture --decision-at $DecisionAt
python -m app.prospective_us_shadow_cli prospective-us-shadow-status --research-db $ResearchDb --production-db $ProductionDb
python -m app.prospective_us_shadow_cli evaluate-prospective-us-shadows --research-db $ResearchDb --production-db $ProductionDb --as-of $DecisionAt
```

Creation is a separate transactional research-only command. It requires a
matching plan generated within ten minutes and the exact phrase
`I AUTHORIZE RESEARCH-ONLY PROSPECTIVE SHADOW CREATION`. A plan is not reusable;
the strategy/month is idempotent; two horizon cohorts are inserted together;
production is fingerprinted and unchanged. Do not put creation into a scheduler.

## Outcomes and promotion

The 126- and 252-trading-session cohorts mature only on the exact subsequent
session. Each compares the overlay with (1) the price-only top three drawn from
the identical eligible cohort and decision date and (2) that cohort's
equal-weight return. Missing outcomes make the result incomplete. Until maturity,
the dashboard must say evidence is immature; no early success claim is allowed.

Promotion discussion requires at least 12 completed monthly vintages, at least
500 security predictions where applicable, complete outcomes, positive
incremental performance versus price-only, positive excess versus equal weight,
a dependence-aware interval excluding zero, at least 50% positive periods,
acceptable concentration, no integrity failures, no unresolved material
missingness bias, and multiplicity correction over both locked horizons.

These are **PAPER RESEARCH SELECTIONS — NOT INVESTMENT ADVICE**, never validated
candidates, Top 3 recommendations, or highest-conviction investments. They never
enter production recommendation tables and cannot place broker orders.

## Limitations and next work

The experiment is US-only and uses current catalogue membership, which is not
survivorship-free. Permanently unmapped SEC securities remain separately
reported. A configuration change requires a new semantic version and separate
evidence series. Next work is point-in-time news/event capability and non-US
fundamentals.
