# Milestone 20 — research operations runbook

> **RESEARCH ONLY — NOT INVESTMENT ADVICE.** Proposed and shadow securities are
> research observations, not recommendations. Production publishing is unavailable.

## Start and inspect

Run `./start.ps1`, then open `http://127.0.0.1:3000/operations`. The launcher refuses
unrelated listeners on ports 8000/3000, recognizes current-project listeners, starts
detached service windows, and performs bounded API and dashboard checks. It never kills
an occupied-port owner.

The merged Milestone 20 health route hung because it synchronously composed model
readiness, research scoring and shadow status (including database assessment and file
fingerprinting) before returning. The browser consequently left all status values
unconfirmed. The original launcher also started the backend before detecting conflicts,
did not check frontend readiness, and tied useful lifecycle feedback to a launcher that
could fail between starts. The repaired health route only reports process/configuration,
safe path separation, scheduler state and database-file existence; it neither opens nor
hashes a database. Coverage, shadow status, readiness and scoring load independently.
Readiness/scoring run as bounded, cancellable, single-flight in-process jobs.

Chrome interpreted generic session and password field names as login fields and inserted
saved credentials. Operational controls now use purpose-specific names, `autocomplete`
hints and password-manager ignore hints, validate the complete five-region mapping on
both sides, and clear sensitive rendered values after each operation and on unmount.

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
and available FX reaches the required date. It additionally requires a short-lived,
single-use identifier from the immediately preceding successful plan. Planning remains
offline and read-only.

## Safe post-merge local verification (still pending)

1. Ensure ports 8000 and 3000 are free (or owned by this checkout); run `./start.ps1`.
2. Confirm the launcher prints `http://127.0.0.1:3000/operations` and the API docs URL.
3. Run `Invoke-RestMethod http://127.0.0.1:8000/api/v1/operations/health -TimeoutSec 3`;
   verify `status=available`, path isolation, scheduler disabled and file-existence flags.
4. Open `/operations`; confirm health, coverage and shadow settle independently. Start
   each assessment and confirm its own loading/progress/result (or sanitized error).
5. Confirm saved email/password values do not populate operational fields. Paste malformed
   text and verify Plan stays disabled; then type all five documented region/date entries.
6. Review the read-only plan only. Do **not** authorize creation, provider requests,
   September vintage creation, deployment, Railway access or ranking publication.

These steps have not yet been performed against the operator's live local environment;
the post-merge operator retry remains pending.

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
