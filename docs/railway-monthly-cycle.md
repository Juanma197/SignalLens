# Railway monthly-cycle production runbook

## Verified repository architecture and safety decision

The backend is the long-running Railway web service. Its container starts Uvicorn
from `backend/Dockerfile`, and `backend/railway.toml` does not define a cron job.
Production must mount its existing Railway volume at `/data` and set
`SIGNALLENS_DATABASE_PATH=/data/signallens.duckdb`. DuckDB is a single local file;
a volume belongs to the service it is attached to and must not be treated as a
shared network filesystem.

**Do not create a second database writer or attach a new empty volume.** The safe
architecture is a small, stateless Railway cron service that makes an authenticated
request over Railway private networking to the existing backend. The cron service
never opens DuckDB. The endpoint executes inside the existing backend process, so
all writes use the backend's already-mounted production volume. Keep the backend at
one replica because concurrent DuckDB writers are not supported by this deployment.

Production uses this architecture: the API remains the sole database writer, with
one replica and its persistent volume mounted at `/data`. A stateless Railway cron
service calls the authenticated endpoint over Railway private networking. The
scheduler has no volume, no DuckDB access, and exits after the request.

## Required backend variables

Set these only on the backend service (values shown are shapes, not secrets):

```text
SIGNALLENS_ENVIRONMENT=production
SIGNALLENS_DATABASE_PATH=/data/signallens.duckdb
SIGNALLENS_PERSISTENT_VOLUME_PATH=/data
SIGNALLENS_BACKUP_PATH=/data/backups
SIGNALLENS_BACKUP_RETENTION_COUNT=3
SIGNALLENS_BACKUP_MAX_AGE_HOURS=48
SIGNALLENS_SEC_USER_AGENT=SignalLens ops monitored-address@example.com
SIGNALLENS_FRED_API_KEY=<secret>
SIGNALLENS_API_TOKEN=<at-least-32-character-secret>
```

The application creates transactionally consistent backups with DuckDB's supported
`EXPORT DATABASE` and `IMPORT DATABASE` operations. It writes a temporary database
under `/data/backups`, opens that database read-only to check the required tables,
and only then atomically renames it into the rolling set. The default retention is
three validated backups.
For an unfinished month, the authenticated endpoint first runs every non-backup
preflight check, then creates and validates a fresh backup, and only then evaluates
backup freshness and starts the first cycle-ledger, ingestion, or publication write.
Any backup failure therefore aborts the run before **every** monthly-cycle database
mutation. The pre-existing backup may be older than 48 hours: it cannot block the
endpoint from making the fresh backup required for this run. The freshly created
backup must satisfy `SIGNALLENS_BACKUP_MAX_AGE_HOURS=48` before writes begin.

Before that sequence, the endpoint checks the current UTC month's cycle ledger
read-only. If the month is already completed, it returns `already_completed`
without creating another backup, attempt-ledger row, or vintage. This avoids an
unnecessary backup while preserving the completed month's append-only publication.

**Backups on `/data/backups` protect against database corruption and operator error,
but they do not protect against total loss of the Railway volume.** Copy validated
backups off-platform when that protection is required.

For initial setup, or to take a supervised manual backup, run:

```bash
cd /app
python -m app.database_backup create
python -m app.database_backup verify /data/backups/signallens-backup-<timestamp>.duckdb
```

The commands return non-zero on failure. Never use `cp` on the open live DuckDB
file. Preflight ignores incomplete, corrupt, incorrectly named, and structurally
invalid files; only a recent backup that passes the same read-only validation meets
the backup check.

## Mandatory preflight and rehearsal

Run these using a Railway shell in the **existing backend service**, not locally and
not in a new service:

```bash
cd /app
python -m app.monthly_cycle --preflight
python -m app.monthly_cycle --dry-run
```

Both commands are read-only with respect to DuckDB. Preflight uses a temporary file
on the mounted volume to prove write access, checks the existing database read-only,
checks required variables and upstream provider configuration, and verifies backup
freshness. Dry-run reports the reserved UTC-month vintage and intended stages. A
non-zero exit means scheduling is prohibited. If the current UTC month is already
complete, dry-run must report `no_op`; do not publish another September 2026 vintage.

## Live scheduler

The stateless Railway cron service has only the API token and backend private
hostname required for this request (an image containing `curl` is sufficient):

```bash
curl --fail-with-body --silent --show-error --max-time 1800 \
  -X POST \
  -H "Authorization: Bearer ${SIGNALLENS_API_TOKEN}" \
  "http://${SIGNALLENS_BACKEND_PRIVATE_HOST}:8000/api/v1/admin/monthly-cycle"
```

Variables for that stateless caller:

```text
SIGNALLENS_API_TOKEN=<same backend token>
SIGNALLENS_BACKEND_PRIVATE_HOST=<backend Railway private DNS hostname>
```

The live UTC cron expression is **`0 2 3 * *`** (02:00 UTC on the third calendar
day of each month). This avoids month-boundary timing and normally allows prior-month
US market data to settle. Railway cron schedules are UTC. A manual scheduler
rehearsal successfully reached the endpoint and returned the expected no-op for the
already completed September 2026 cycle. Its immutable production vintage ID is
`5dcce39d-f98a-55b1-9010-279501142186`.

## Deployment result and monthly operator checks

Railway creates a deployment for each cron invocation. A successful invocation is
the deployment whose command exits with status 0 after an HTTP 2xx response. A
failed invocation is shown as a failed/crashed deployment with a non-zero command
exit; inspect its sanitized logs and the API cycle ledger to identify the failed
stage. An HTTP 401/403 indicates caller authentication configuration, while an HTTP
5xx or timeout requires API and cycle-ledger investigation. Do not print or copy
the token while troubleshooting. Railway does not provide email notification by
this repository's configuration, so reviewing failed cron deployments (or adding
an external alert) remains an operator requirement.

After every scheduled monthly run, the operator must:

1. Confirm the cron deployment exited successfully and the API response is either
   `completed` or the expected idempotent `already_completed` result.
2. Confirm exactly one completed UTC-month ledger entry and the deterministic
   vintage ID; never delete or replace an immutable prediction vintage.
3. Confirm the dashboard/API exposes that vintage using `momentum_126d`, the only
   production publication strategy, and review its evidence-coverage summary.
4. Confirm a validated backup exists under `/data/backups`, no more than 48 hours
   old, and that retention has kept the configured three newest validated backups.
5. Review the API and cron logs for provider failures, timeouts, or a failed stage.
   Arrange notification/escalation manually until automated alerts are configured.

Backups in `/data/backups` share the API's Railway volume. They protect against
database corruption and operator mistakes, **not total Railway volume loss**;
off-platform replication remains a non-blocking production-hardening task.

## Failure handling and safe retry

The cycle writes append-only attempt records, records the failed stage and sanitized
error, and reserves a deterministic vintage ID for each UTC month. A retry refreshes
stages and either publishes the one live `momentum_126d` vintage or reuses the exact
reserved vintage after a post-publish crash. It never publishes the multifactor
research model. Never delete or edit a prediction vintage to force a retry.

## Rollback / disable procedure

1. Disable the stateless cron service schedule; do not delete the backend volume.
2. Leave the backend web service running and inspect `monthly_cycle_runs` and
   `monthly_research_cycles` in a read-only DuckDB session.
3. If the run failed before publication, correct the provider/configuration problem,
   take a fresh backup, repeat preflight and dry-run, then invoke the endpoint once.
4. If the reserved vintage exists, do not restore over it, delete it, or republish;
   retrying will reuse it and repair only the cycle ledger.
5. Restore a backup only for verified database corruption. Stop/scale down the API
   so no DuckDB connection is open. Move (do not overwrite) the damaged database to
   a separate diagnostic name, leaving `/data/signallens.duckdb` absent. Verify and
   restore with:

   ```bash
   python -m app.database_backup verify /data/backups/signallens-backup-<timestamp>.duckdb
   python -m app.database_backup restore /data/backups/signallens-backup-<timestamp>.duckdb /data/signallens.duckdb
   ```

   The restore command refuses an existing destination, copies through DuckDB into
   a temporary database, validates it, and atomically installs it. Run preflight
   read-only, then start exactly one API replica. Confirm immutable vintage counts
   before re-enabling any future schedule.
