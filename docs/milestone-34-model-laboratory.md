# Milestone 34 — read-only Model Laboratory

## Scope and labels

The Model Laboratory exposes the registered model's arithmetic and can calculate
an indicative, non-persistent preview from the two operator-supplied databases.
Every preview is labelled **INDICATIVE PRE-VINTAGE PREVIEW**, **NOT VALIDATION**,
**NOT A PAPER SELECTION**, and **NOT INVESTMENT ADVICE**. It is not a production
ranking, recommendation, or prospective vintage.

The implementation imports the canonical Milestone 28 specification. Version
`prospective-us-dilution-1.0.0`, registration `2026-10-01T00:00:00Z`, and hash
`7b11264778fd120c03c820275d9c048d002bdb8564510fcf989cb590ce1b7ebd` remain
unchanged. The equation is `0.90 × price percentile + 0.10 × dilution percentile`;
lower diluted-share growth ranks higher. Ordering is score descending and then
qualified symbol ascending. At most three rows are displayed.

## Safety contract

Both commands require explicit, existing, distinct regular files. Missing paths,
symlinks, aliases, and hard links are refused by the shared Milestone 28 path
validator. DuckDB opens are read-only. SHA-256/size fingerprints are taken before
and after and any change fails the request. Evidence is restricted to its public
and retrieval timestamps at or before `decision_at`. Missing, late, stale,
incompatible, unreliable, nonfinite, currency, denominator, and model-readiness
failures withhold a security; they are never imputed.

The commands have no write method. They do not create vintages, observations,
recommendations, candidates, or paper selections. Provider access, Railway
access, publication, and scheduling are absent. Output is bounded to three rows,
three official citation objects per row, aggregate reasons, and fixed descriptive
fields; raw responses, filing bodies, unrestricted text, paths, SQL, secrets, and
tracebacks are never returned.

## Operator commands after merge (PowerShell)

Run the October 1 indicative preview only against local operator-controlled files:

```powershell
Push-Location backend
& ..\.venv\Scripts\python.exe -m app.model_laboratory_cli research-us-top3-preview --research-db "C:\path with spaces\research.duckdb" --production-db "C:\path with spaces\production.duckdb" --decision-at "2026-10-01T23:59:59+00:00"
Pop-Location
```

Assess the completed September boundary (never substitute 25 September):

```powershell
Push-Location backend
& ..\.venv\Scripts\python.exe -m app.model_laboratory_cli assess-september-2026-reconstruction --research-db "C:\path with spaces\research.duckdb" --production-db "C:\path with spaces\production.duckdb" --decision-at "2026-09-30T23:59:59+00:00"
Pop-Location
```

The assessment first verifies exact 30 September prices, point-in-time catalogue,
FX, and public/retrieved dilution evidence. Failure returns `unavailable`, an empty
preview, zero validation credit, and bounded codes such as
`incomplete_month_end_prices`, `evidence_retrieved_after_decision`,
`unavailable_point_in_time_catalogue`, `incomplete_fx`, or
`unavailable_dilution_evidence`. Later retrieval never repairs September.

## API and dashboard

Authenticated GET endpoints are:

- `/api/v1/research/model-laboratory/preview?decision_at=...`
- `/api/v1/research/model-laboratory/september-reconstruction?decision_at=...`

Failures expose stable redacted codes. `/model` displays the equation, row-level
components, counts, validation ledger, September feasibility, zero evidence
counters, comparator definitions, and 126/252-session maturity timeline.

## Validation interpretation

The failed retrospective 40% fundamentals experiment, exploratory diagnostics,
the current unvalidated 90/10 hypothesis, official vintages, and matured 126/252
outcomes are separate evidence classes. Official-vintage and matured-outcome
counts remain zero until genuinely prospective records mature. Reconstruction
always earns zero validation credit, even when technically feasible.
