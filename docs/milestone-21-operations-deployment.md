# Milestone 21 operations and Railway deployment runbook

**RESEARCH ONLY — NOT INVESTMENT ADVICE.** The model is research-only, current
catalogue membership is not survivorship-free, and fundamentals remain unavailable
under the current EODHD entitlement.

## Safety boundary

This milestone made no live provider request, production publication, real
investment, Railway deployment, or September 2026 shadow-vintage creation. The
autonomous workflow can assess and prepare plans; shadow creation retains its
separate token, exact confirmation phrase, fresh confirmed plan, and readiness
gates. There is no broker or production-ranking publisher in the workflow.

## Railway topology

Create separate services from `backend/Dockerfile` and `frontend/Dockerfile`.
Mount one persistent volume at `/data` on the backend only. Set
`SIGNALLENS_RESEARCH_DATABASE_PATH=/data/research/signallens-research.duckdb` and
`SIGNALLENS_BACKUP_PATH=/data/backups`. Keep `SIGNALLENS_DATABASE_PATH` distinct;
the research backup workflow explicitly rejects that production path. Health checks
use `/api/v1/health` and `/`. Both images use `SIGTERM` for graceful platform stops.

Required production secrets are `SIGNALLENS_API_TOKEN` (32+ characters), matching
server-side `SIGNALLENS_API_URL`, and dashboard username/password. Provider and
write-authorization secrets are optional until a deliberately authorized operation.
Never use `NEXT_PUBLIC_` for a credential.

## Scheduler

Schedules are UTC: freshness 06:15 daily, incremental refresh 06:30 weekdays,
month-end plan checks 18:00 on days 28–31, matured evaluation 19:00 weekdays, and
backup 05:00 daily. Europe/London operators should interpret these as UTC in winter
and one hour later locally during BST. Enable exactly one scheduler service with
both `SIGNALLENS_SCHEDULER_ENABLED=true` and a unique
`SIGNALLENS_SCHEDULER_INSTANCE_ID`. Leave all other instances disabled.

Idempotency keys include job and scheduled UTC instant. Per-job locks, request
budgets, bounded timeouts/retries, and explicit missed-occurrence recovery prevent
overlap. `SIGNALLENS_STAGING_MODE=true` rejects every API write and cannot coexist
with scheduler enablement.

## Backup and restore

Backups are atomically published after DuckDB validation and SHA-256 calculation;
retention defaults to three. The status endpoint is read-only and never reveals a
path. Use `python -m app.database_backup verify BACKUP` and a restore plan first.
Restore always targets an absent path and is never automatic; an operator must stop
writers, verify the hash/tables, select a new destination, and deliberately switch
configuration after independent review.

## Notifications

Local and tests use `OfflineNotificationSink`. A future email/webhook adapter need
only implement `send(kind, message)`. Pass every message through `bounded_message`,
allow only documented event kinds, keep delivery timeouts/retries outside the
research transaction, and never include credentials, raw payloads, database paths,
exceptions, or unbounded security lists.
