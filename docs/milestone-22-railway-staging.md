# Milestone 22: Railway staging runbook

This procedure creates two Railway services but **does not deploy from this
repository automatically**. Staging is continuously hosted, read-only, and is
not an investment model validation, production publishing system, scheduler,
broker, or trading system. Never upload the local live DuckDB file. Use only an
application-managed, validated **research-profile** backup made while the
backup workflow held a consistent read-only snapshot.

## 1. Project, services, and volume

1. In Railway create a project named `signallens-staging` from the merged Git
   repository. Create `signallens-backend` with root directory `/backend` and
   `signallens-frontend` with root directory `/frontend`. Both use their
   checked-in Dockerfiles and `railway.toml` files. Do not add a worker.
2. Add one persistent volume to the backend service and mount it at exactly
   `/data`. Do not attach it to the frontend. Create no database plugin.
3. Generate public domains for both services. The frontend calls the backend
   server-to-server; browsers call only the authenticated frontend proxy.
4. Configure the backend liveness path `/api/v1/health`. `/api/v1/ready` is the
   separate readiness probe and deliberately returns 503 until bootstrap.

### Backend variables

Set all of the following on `signallens-backend` (examples are placeholders):

```text
SIGNALLENS_ENVIRONMENT=staging
SIGNALLENS_STAGING_MODE=true
SIGNALLENS_ALLOWED_ORIGINS=https://<frontend-domain>
SIGNALLENS_API_TOKEN=<random-secret-at-least-32-characters>
SIGNALLENS_REFRESH_AUTHORIZATION_TOKEN=<different-random-secret>
SIGNALLENS_SHADOW_AUTHORIZATION_TOKEN=<different-random-secret>
SIGNALLENS_EODHD_API_TOKEN=
SIGNALLENS_DATABASE_PATH=/data/production/signallens-production.duckdb
SIGNALLENS_RESEARCH_DATABASE_PATH=/data/research/signallens-research.duckdb
SIGNALLENS_PERSISTENT_VOLUME_PATH=/data
SIGNALLENS_BACKUP_PATH=/data/backups
SIGNALLENS_BACKUP_RETENTION_COUNT=3
SIGNALLENS_BACKUP_MAX_AGE_HOURS=48
SIGNALLENS_SCHEDULER_ENABLED=false
SIGNALLENS_SCHEDULER_INSTANCE_ID=
SIGNALLENS_SEC_USER_AGENT=SignalLens research <monitored-email>
SIGNALLENS_FRED_API_KEY=
TZ=UTC
```

Railway supplies `PORT`; do not set it. An empty provider token prevents EODHD
use. Staging rejects every non-GET API method before endpoint code runs except
the two exact read-only assessment start paths. Those assessments keep job state
in process memory and fingerprint both databases before and after; they do not
write the operations journal. Refresh, shadow planning/creation/evaluation,
watchlist writes, and monthly publication remain unavailable. Keep the production
path absent and distinct; startup configuration fails closed if paths alias or
leave `/data`. Secrets and absolute paths are never returned by health output.

### Frontend variables

Set these only on `signallens-frontend`:

```text
SIGNALLENS_API_URL=https://<backend-domain>
SIGNALLENS_API_TOKEN=<same-backend-api-token>
SIGNALLENS_DASHBOARD_USERNAME=<operator-name>
SIGNALLENS_DASHBOARD_PASSWORD=<independent-long-password>
NEXT_PUBLIC_API_URL=
```

Do not use `NEXT_PUBLIC_` for any secret. `SIGNALLENS_API_URL` makes the Next.js
server-side proxy use Railway's backend rather than a browser/local URL.

## 2. First deployment (uninitialized and safe)

1. Leave scheduler and provider credentials disabled, attach the volume, set
   variables, then deploy the backend. Its production command is Uvicorn bound
   to `0.0.0.0:$PORT`; it neither creates nor opens a database at startup.
2. Check `curl -fsS https://<backend-domain>/api/v1/health`. It must say
   `staging`. Check `curl -sS -o /tmp/ready.json -w '%{http_code}\n'
   https://<backend-domain>/api/v1/ready`; expect `503` and `not_initialized`.
3. Deploy the frontend (the standalone Next production server binds Railway's
   `PORT`). Sign in and confirm `/operations` says **STAGING / RESEARCH ONLY**,
   `not_initialized`, scheduler disabled, and publication disabled. Do not
   interpret missing data as zero.

No health check performs provider I/O or opens/hashes DuckDB. The Railway
liveness check intentionally remains healthy before data bootstrap so an
operator can enter the service shell.

## 3. One-time, authenticated bootstrap

This has no public upload endpoint. Authorization is the operator's access to
Railway CLI/shell plus the exact command phrase. First record the SHA-256 and
byte count of the already validated managed research backup on the operator's
machine. Upload that *backup artifact* (never the live database) via an
authenticated Railway shell/CLI transfer mechanism to the backend volume as
`/data/backups/bootstrap-upload.duckdb`. Railway CLI capabilities change; use
its current authenticated file-transfer/shell facility, and never paste file
contents into logs or variables.

In a backend Railway shell run:

```sh
python -m app.database_backup verify /data/backups/bootstrap-upload.duckdb --profile research
python -m app.database_backup restore-plan /data/backups/bootstrap-upload.duckdb
```

Compare both reported `sha256` and `byte_count` to the separately recorded
local values. Confirm `profile` is `research`, every required table is listed,
`destination_exists` is false, and `automatic_restore` is false. Then—and only
after explicit operator approval—run exactly:

```sh
python -m app.database_backup restore /data/backups/bootstrap-upload.duckdb \
  --authorize 'RESTORE VALIDATED RESEARCH BACKUP' \
  --expected-sha256 '<64-hex-sha256>' --expected-byte-count '<bytes>'
```

The tool refuses a production source/destination, wrong profile, corrupt or
mismatched artifact, absent authorization, and any overwrite. It copies the
static validated backup to a private temporary sibling, revalidates it, flushes
it, and atomically publishes it. The existing `research_operations` table is
therefore the persistent journal in the restored research database.

## 4. Verify, restart, recover, and remove

After bootstrap:

```sh
curl -fsS https://<backend-domain>/api/v1/health
curl -fsS https://<backend-domain>/api/v1/ready
curl -fsS -H 'Authorization: Bearer <api-token>' https://<backend-domain>/api/v1/operations/health
curl -fsS -H 'Authorization: Bearer <api-token>' https://<backend-domain>/api/v1/operations/backups/status
```

Open frontend `/operations`; it must show API available, database present,
backup status, scheduler disabled, and publication disabled. Expensive
assessments remain manual and are permitted through only the exact
model-readiness and research-scoring start paths. Their job state is deliberately
ephemeral in staging. Restart the backend and repeat readiness plus the
operations page checks; the mounted data and journal must remain.

### Observed failure and post-merge correction check

The first Railway verification found healthy staging, confirmed database
isolation, and a present research database and validated backup. However,
clicking **Model readiness** returned `staging_read_only` / “Staging mode
prohibits all writes,” so research scoring was not attempted. The blanket method
guard had classified the POST used to start an otherwise read-only in-process
assessment as a database mutation.

After this correction is merged (do not deploy from an unmerged branch), record
the SHA-256 and byte count of both database paths, then use the authenticated
dashboard to run **Model readiness** followed by **Research scoring**. Confirm
both jobs complete, each response reports unchanged before/after fingerprints,
and independent SHA-256 and byte-count checks of both files still match. Confirm
an incremental-refresh execution, shadow-vintage creation, matured-shadow
evaluation, monthly production cycle, and watchlist mutation each returns
`staging_read_only`; confirm the scheduler remains disabled and no provider or
broker request occurs. A restart may discard assessment job results by design.

For a failed pre-publication restore, remove only the hidden temporary restore
file after checking logs; source and destinations are preserved. If the atomic
publication succeeded but later verification fails, stop the backend, rename
the suspect research database for forensic retention, obtain explicit approval,
and repeat plan/restore from a known-good managed research backup. Never
overwrite, substitute the production database, or copy a live DuckDB file.

Rollback application code by selecting the prior Railway deployment; the
volume is not rolled back. To remove staging, stop both services, take any
separately authorized final managed backup, delete services, and delete the
volume only after the operator confirms retention requirements.

Resource use is expected to be one small always-on Python web service, one
small Next.js service, and persistent storage sized for the database, uploaded
backup, temporary restore copy, and retained backups. CPU/memory rise during
operator-initiated reads. Review Railway's current metered usage and pricing;
this runbook makes no fixed-price claim.

## 5. Later scheduler enablement is a separate milestone

Do not enable the scheduler in this staging deployment: startup rejects that
combination. Provider entitlement, request budgets, a single scheduler
instance, write authorization, backup freshness, and operational approval need
a separate reviewed change. Deployment itself validates neither an investment
strategy nor model evidence and grants no permission to create a shadow
vintage, publish rankings, trade, or connect a broker.

Actions still requiring explicit operator authorization are: creating the
Railway project/services/domains, setting secrets, attaching/deleting a volume,
deploying/restarting/rolling back, transferring the validated backup, approving
the restore phrase after comparing its digest and size, deleting recovery
artifacts, and any later scheduler/provider/write capability.
