# Milestone 20 — research operations runbook

> **RESEARCH ONLY — NOT INVESTMENT ADVICE.** Proposed and shadow securities are
> research observations, not recommendations. Production publishing is unavailable.

## Start and inspect

Run the API and Next.js application using the existing local startup procedure, then
open `/operations`. The dashboard composes fingerprint-protected, read-only coverage,
model-readiness, scoring and shadow reports. Public results never include credentials,
database paths, provider payloads or arbitrary exception text.

The default shadow plan returns total score count, zero-to-three proposed selections,
25-row affected/withheld samples, and score/region aggregates. It does **not** return
the eligible-universe score array. CLI diagnostics require `--verbose-scores` and are
hard-capped at 100 rows; authorized creation still persists all scores transactionally.

## Authorization and confirmations

Set distinct secrets outside source control:

* `SIGNALLENS_API_TOKEN` authenticates the private API in production.
* `SIGNALLENS_REFRESH_AUTHORIZATION_TOKEN` authorizes refresh/evaluation writes.
* `SIGNALLENS_SHADOW_AUTHORIZATION_TOKEN` separately authorizes shadow creation.
* `SIGNALLENS_EODHD_API_TOKEN` is required only for a deliberately confirmed refresh.

The operator must review operation type, expected request count, research database
target, strategy version, and write status. Refresh requires `REFRESH RESEARCH DATA`;
shadow creation requires `CREATE RESEARCH SHADOW`. Never put a secret in a browser URL,
log, result, or `NEXT_PUBLIC_*` variable. The backend refuses identical/aliased research
and production databases, and monthly uniqueness prevents duplicate vintages.

## Explicit month-end readiness

Supply the expected last session for **every** configured region as explicit
`REGION=YYYY-MM-DD` values and supply `--latest-required-fx-date YYYY-MM-DD`. Do not
infer sessions from weekdays: the operator-approved exchange calendar is an input.
Creation fails closed unless observed effective price dates exactly match those inputs
and available FX reaches the required date. Planning remains offline and read-only.

## Scheduling design (not deployed)

`SchedulerService` exposes routine incremental refresh, month-end shadow planning, and
matured-shadow evaluation jobs. It is disabled by default; an embedding service must
explicitly read `SIGNALLENS_SCHEDULER_ENABLED=true`. Each scheduled timestamp is an
idempotency key and each job type has a non-blocking concurrency lock. Retry only a
failed invocation with the same key, using bounded exponential backoff; completed keys
must not retry. On restart, recover missed runs oldest-first after re-running read-only
plans and readiness gates. Durable production scheduling should replace the included
process-local state before deployment. No external scheduler or Railway cron is created.

## Current limitations and declaration

The model remains research-only. Current catalogue membership is not survivorship-free.
Fundamentals remain unavailable under the current EODHD entitlement. September 2026's
vintage has not been created. Milestone 20 performed no live provider request, Railway
access, production publishing, deployment, or external scheduler configuration.
