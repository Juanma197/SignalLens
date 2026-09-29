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
the byte count and digest are rechecked after the atomic rename, and retention
defaults to three. Production and research use explicit, non-interchangeable
profiles. The production profile requires `price_bars`, `prediction_vintages`, and
`prediction_records`. The research profile instead requires the initialized EODHD
catalogue/listing, global price, FX, corporate-action, ingestion run/failure,
checkpoint/catalogue-validation, and sanitized operation-journal tables. Shadow
tables remain prospective and are validated as ordinary DuckDB contents when they
exist; their absence before the first separately authorized shadow creation is not
an error. The source is opened read-only and copied by DuckDB `EXPORT DATABASE` /
`IMPORT DATABASE`, so the output file need not be byte-identical to the source.
Integrity means that the imported database passes its named schema profile and the
temporary and atomically published files have identical SHA-256 and byte count.

The status endpoint applies the research profile, is read-only, and never reveals a
path. Use `python -m app.database_backup verify BACKUP --profile research` for a
research backup (or `--profile production` for an application backup) and a restore
plan first. Never infer a profile from whichever tables happen to be present.
Restore always targets an absent path and is never automatic; an operator must stop
writers, verify the hash/tables, select a new destination, and deliberately switch
configuration after independent review.

### Operator-observed initial-backup failure (2026-09-29)

The authorized `create-initial` attempt correctly left the managed-backup directory
empty, but rejected the valid isolated research database because the shared verifier
incorrectly demanded the three production-only tables. The repair gives
`create-initial` and `/operations/backups/status` the explicit research profile;
validation failures still remove export/temporary artifacts and publish nothing.
Manual files are neither adopted nor pruned, and a second managed initial backup is
still refused.

The repaired research-profile backup subsequently reached atomic publication on
Windows, then failed with `PermissionError: [Errno 13] Permission denied` when the
POSIX durability path attempted `os.open` on
`C:\Users\Juan Estrada\Projects\SignalLens\backend\data\research\managed-backups`.
This was an unsupported parent-directory-open operation, not evidence that the
operator needed administrator rights or weaker directory permissions. The backup
path now flushes and fsyncs the temporary file, atomically replaces the destination,
then reopens and fsyncs the published file before its SHA-256, byte-count, and
research-profile validation. POSIX additionally fsyncs the parent directory.
Standard Python provides no corresponding POSIX-style parent-directory fsync on
Windows, so only that directory operation is explicitly omitted there; all ordinary
file, publication, hashing, and validation errors remain fatal and remove the newly
published managed artifact. Operator-owned manual backups remain outside cleanup.

A second Windows durability defect was then reproduced: Windows `_commit()` rejects
the read-only descriptor that POSIX accepts for file `fsync`, producing
`OSError: [Errno 9] Bad file descriptor`. Backup artifacts are now reopened with a
binary, read/write descriptor on Windows only; the research and production sources
remain strictly read-only. The focused backup suite must pass on GitHub Actions'
actual `windows-latest` runner, including its path-with-spaces end-to-end case,
before the operator is asked to retry.

The operator recorded that both source databases remained byte-for-byte unchanged.
The previously published orphan was independently verified as a valid research
backup with SHA-256
`9C11CBC4C2B0D71B0266EC7351BE808B00907944C9B38E2607F9501029EFE20B` and was
preserved in quarantine rather than adopted, overwritten, or removed.

The operator's before/after evidence for this failed publication reported both the
isolated research database and the production database as byte-for-byte unchanged.
The failure occurred in backup-destination durability handling after the read-only
source workflow; it did not authorize or perform a provider request, ingestion,
scheduler enablement, deployment, production publication, or other production
operation.

The operator reported current SHA-256
`1E8A44E2F07207863151D53259F62D93F12C2282CD516EB6568A80CA33860244`, versus the
earlier read-only assessment's
`9811DF2FFAF758C19618329780EA86C2D79F90026236923A18A9CA540E611DB4`. The operator
database is not present in this repository/environment, so those digests alone
cannot prove a page-level or row-level delta and no corruption conclusion is
warranted. The exact *known structural* delta in the intervening application path
is initialization of `research_operations`: eleven columns (`operation_id`,
`operation_type`, `state`, `created_at`, `started_at`, `finished_at`, `progress`,
`summary`, `strategy_version`, `configuration_version`, and `failure_class`) and no
operation row merely from table creation. Current summary/status reads do not create
that schema. No other legitimate operator operation is evidenced by the supplied
hashes; confirming that the journal is the sole physical change would require a
read-only table/schema comparison of the two operator-held versions, which was not
available and must not be represented as completed.

## Notifications

Local and tests use `OfflineNotificationSink`. A future email/webhook adapter need
only implement `send(kind, message)`. Pass every message through `bounded_message`,
allow only documented event kinds, keep delivery timeouts/retries outside the
research transaction, and never include credentials, raw payloads, database paths,
exceptions, or unbounded security lists.
