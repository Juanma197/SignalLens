# Milestone 35 — prospective paper-portfolio cycle and validation ledger

## Safety boundary

This capability is **PAPER RESEARCH ONLY** and performs **NO BROKER ACTIVITY**.
Interim returns are descriptive and are not validation. The evidence state is
**INSUFFICIENT PROSPECTIVE EVIDENCE** until the pre-registered sample requirements
are actually met. It produces no recommendation, allocation, buy/sell instruction,
future price, fabricated return, or broker action.

The implementation directly reuses frozen model
`prospective-us-dilution-1.0.0`, hash
`7b11264778fd120c03c820275d9c048d002bdb8564510fcf989cb590ce1b7ebd`, registered
`2026-10-01T00:00:00Z`. Its score remains 90% price percentile plus 10% dilution
percentile; at most three securities are ordered by score descending then symbol
ascending. Neither this runbook nor the reporting layer changes eligibility,
timestamps, horizons, gates, or registration.

## Operator cycle

Planning opens both explicit databases read-only, rejects symlinks/aliases/hard
links, verifies identity before and after, and emits all blockers. A successful
plan includes a ten-minute, database-bound integrity token. It writes no table and
creates no vintage. Paths containing spaces are supported when quoted.

Creation is a different command. It requires both the exact token from the
immediately preceding plan and the exact authorization phrase. Inputs are rebuilt,
the token and database identities are rechecked, and the research transaction is
rolled back on failure. A strategy/month retry is idempotent. The production
database is never opened writable. Scheduler code can invoke planning only and
has no creation or authorization capability; scheduling is disabled by default.

The immutable record captures version, decision timestamp/session, configuration
hash, ranked selections, decision prices, both score components, combined score,
price-only Top 3, equal-weight eligible membership, bounded input provenance and
database fingerprints, plus a zero-cash/no-broker/no-trade designation. Evidence
retrieved after the decision cannot enter that vintage.

## Outcomes and ledger

Mark-to-market uses completed US sessions, never calendar-day approximations.
Latest, 21-session, and 63-session views are descriptive and receive zero
validation credit. Confirmatory states are exactly 126 and 252 subsequent completed
sessions. A cohort return is withheld unless every selected, price-only, and
eligible-universe constituent has an exact-session available price; missing,
delisted, and security-action states remain explicit.

For each checkpoint, the report gives all three equal-weight returns, both excess
returns, constituent returns, exact session count, maturity, completeness, and
validation-credit state. The aggregate ledger reports counts, wins/losses/ties,
mean/median excess, positive-period rate, and concentration. Confidence intervals
and gates are withheld until registered sample requirements are met.

Authenticated read-only endpoints are:

- `GET /api/v1/research/validation/readiness`
- `GET /api/v1/research/validation/vintages`
- `GET /api/v1/research/validation/vintages/{vintage_id}`
- `GET /api/v1/research/validation/mark-to-market`
- `GET /api/v1/research/validation/ledger`

## Exact post-merge PowerShell commands

Set paths to the isolated research database and untouched production database.
Do not run creation until the completed October 2026 month-end refresh has been
reviewed. October 30, 2026 is the final scheduled weekday session.

```powershell
Push-Location backend
$ResearchDb = "C:\SignalLens Data\research\signallens-research.duckdb"
$ProductionDb = "C:\SignalLens Data\production\signallens.duckdb"

# 1. Read-only October month-end readiness; copy plan_identifier only if ready.
& ..\.venv\Scripts\python.exe -m app.prospective_us_shadow_cli plan-prospective-monthly-cycle --research-db $ResearchDb --production-db $ProductionDb --decision-at "2026-10-30T21:00:00+00:00" --us-session-date "2026-10-30"

# 2. Deliberately authorized creation using the immediately preceding token.
$PlanIdentifier = "PASTE_PLAN_IDENTIFIER_FROM_THE_IMMEDIATELY_PRECEDING_PLAN"
& ..\.venv\Scripts\python.exe -m app.prospective_us_shadow_cli create-prospective-us-shadow --research-db $ResearchDb --production-db $ProductionDb --plan-identifier $PlanIdentifier --authorization "I AUTHORIZE RESEARCH-ONLY PROSPECTIVE SHADOW CREATION"

# 3. Read-only paper-vintage status.
& ..\.venv\Scripts\python.exe -m app.prospective_us_shadow_cli paper-vintage-status --research-db $ResearchDb --production-db $ProductionDb

# 4. Read-only validation ledger.
& ..\.venv\Scripts\python.exe -m app.prospective_us_shadow_cli prospective-validation-ledger --research-db $ResearchDb --production-db $ProductionDb
Pop-Location
```

The token expires after ten minutes. If it expires or any database identity changes,
run step 1 again and review the new output. Never reuse an old token against a
copied, replaced, aliased, symlinked, or hard-linked database.
