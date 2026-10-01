# Milestone 29 — database-backed shadow readiness

## Safety boundary

This is prospective **paper research only**, not investment advice. Planning
opens both explicit databases read-only, uses only facts available by the
decision timestamp, fingerprints both files before and after, and performs no
network request. It refuses future decisions, September 2026 or earlier,
non-month-end sessions, stale/incomplete prices, incomplete SEC mapping state,
and unavailable readiness evidence. Missing defensible dilution is bounded and
withheld rather than treated as zero. Output contains at most three paper
selections with separate price and dilution components.

The fixture command is exclusively for deterministic offline tests. It must
never provide operational truth. The disabled-by-default scheduler may refresh
or request a read-only plan only; it cannot possess the creation phrase. Staging
is read-only. Nothing publishes a production recommendation or contacts a broker.

## Month-end operator workflow

1. Wait for the exact final US trading session to close. Refresh prices and any
   genuinely applicable FX through the separately authorized ingestion workflow.
2. Incrementally refresh official SEC filings, mappings, and checkpoints.
3. Validate the current research backup and retain its verified fingerprint.
4. Run readiness and review fingerprints, withholding counts, components, and
   the zero-to-three proposed paper selections.
5. Only for `ready_for_authorized_creation`, copy the `plan_identifier`; within
   ten minutes deliberately run creation with the exact authorization phrase.
6. Run status and verify the 126/252 cohorts and production isolation.

From the repository's `backend` directory, with the actual backend-relative
configured paths:

```powershell
$DecisionAt = "2026-10-30T22:00:00+00:00"
& ..\.venv\Scripts\python.exe -m app.prospective_us_shadow_cli prospective-us-shadow-readiness --research-db "data\research\signallens-research.duckdb" --production-db "data\signallens.duckdb" --decision-at $DecisionAt --us-session-date "2026-10-30"
& ..\.venv\Scripts\python.exe -m app.prospective_us_shadow_cli plan-prospective-us-shadow-from-db --research-db "data\research\signallens-research.duckdb" --production-db "data\signallens.duckdb" --decision-at $DecisionAt --us-session-date "2026-10-30"
```

Do **not** run either October command until the October 2026 month-end session
and all required price, applicable FX, and SEC data are complete.

Creation consumes the exact returned token (placeholder shown here):

```powershell
& ..\.venv\Scripts\python.exe -m app.prospective_us_shadow_cli create-prospective-us-shadow --research-db "data\research\signallens-research.duckdb" --production-db "data\signallens.duckdb" --plan-identifier "<EXACT_PLAN_IDENTIFIER>" --authorization "I AUTHORIZE RESEARCH-ONLY PROSPECTIVE SHADOW CREATION"
```

## Offline rehearsal

Build synthetic research and production DuckDB files without network access and
invoke the same database command. For pure unit fixtures, the separately named
`plan-prospective-us-shadow-offline-fixture` command remains available. Synthetic
output is rehearsal evidence only and cannot establish live readiness.
