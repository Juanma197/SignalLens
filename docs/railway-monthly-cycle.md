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

This repository deliberately does **not** configure or activate that cron service.
Before doing so, inspect the Railway backend service and confirm that its existing
volume is mounted at `/data`, then run the preflight below in that exact service.

## Required backend variables

Set these only on the backend service (values shown are shapes, not secrets):

```text
SIGNALLENS_ENVIRONMENT=production
SIGNALLENS_DATABASE_PATH=/data/signallens.duckdb
SIGNALLENS_PERSISTENT_VOLUME_PATH=/data
SIGNALLENS_BACKUP_PATH=/data/backups
SIGNALLENS_BACKUP_MAX_AGE_HOURS=48
SIGNALLENS_SEC_USER_AGENT=SignalLens ops monitored-address@example.com
SIGNALLENS_FRED_API_KEY=<secret>
SIGNALLENS_API_TOKEN=<at-least-32-character-secret>
```

The backup location must contain a successfully completed, recent copy made while
DuckDB is quiescent (or by DuckDB's supported export/copy workflow). Prefer an
off-platform backup; if `/data/backups` is used for the freshness gate, separately
replicate it off-platform.

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

## Schedule to create later (do not enable yet)

After the mount and both commands above are confirmed, create a stateless Railway
cron service with no volume. Give it only `SIGNALLENS_API_TOKEN` and the backend
private hostname. Use this exact command (an image containing `curl` is sufficient):

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

Recommended UTC cron expression: **`0 02 3 * *`** (02:00 UTC on the third calendar
day of each month). This avoids month-boundary timing and normally allows prior-month
US market data to settle. Railway cron schedules are UTC. Do not enable it until a
supervised run in a month without an existing vintage succeeds and its run summary,
dashboard vintage, and backup are verified.

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
5. Restore a backup only for verified database corruption: stop/scale down the API,
   preserve the damaged file, restore to the same `/data/signallens.duckdb` path,
   run preflight read-only, and start exactly one API replica. Confirm immutable
   vintage counts before re-enabling any future schedule.
