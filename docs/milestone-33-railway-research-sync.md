# Milestone 33: Railway research-database synchronization

This staging-only procedure atomically publishes a validated research artifact. It
does not call a provider, enable the scheduler, publish a ranking, or write the
production database. **Never copy, restore, or overwrite
`/data/signallens.duckdb`.** Keep all three paths explicit in every command.

## 1. Create and identify the local artifact

Create an application-managed research backup (not a raw copy of an open DB):

```bash
cd backend
SIGNALLENS_RESEARCH_DATABASE_PATH='data/research/signallens-research.duckdb' \
SIGNALLENS_DATABASE_PATH='data/signallens.duckdb' \
python -m app.database_backup create-initial \
  --destination 'data/research/backups' \
  --authorize 'CREATE INITIAL RESEARCH BACKUP'
sha256sum 'data/research/backups/<managed-backup>.duckdb'
stat --printf='%s\n' 'data/research/backups/<managed-backup>.duckdb'
```

Record the full 64-character digest and decimal byte count. The last observed
authoritative source digest was
`2a5ea0dd29ca8fb1caf2db66796a099f72a19f0414fe9f0b742fce89836d17f1`;
do not assume it still applies—calculate both values for the actual artifact.

## 2. Upload, then make a read-only remote plan

Use Railway CLI's file-transfer facility to upload the single artifact to
`/data/bootstrap/` (consult the installed CLI's `railway --help` for its current
transfer syntax). Do not upload it over either database. Then run in the backend
service shell:

```bash
python -m app.research_sync_cli plan-research-sync \
  --candidate '/data/bootstrap/signallens-research.duckdb' \
  --research '/data/research/signallens-research.duckdb' \
  --production '/data/signallens.duckdb' --bootstrap '/data/bootstrap' \
  --expected-sha256 '<sha256>' --expected-byte-count '<bytes>'
```

Planning is strictly read-only. Review compatibility counts, capacity, production
fingerprint, expiry, and copy the returned `plan_id`. The capacity gate reserves
space for the upload, active DB, rollback artifact, temporary publication, and
safety margin on the 500 MB volume. If it fails, stop; never remove the active
database, production database, sole rollback, quarantine, manual, or historical
backup. Cleanup may remove only a positively identified obsolete bootstrap upload
or redundant operator-verified artifact.

## 3. Apply exactly once

Immediately after planning, while its database-bound ID remains unexpired:

```bash
python -m app.research_sync_cli apply-research-sync \
  --candidate '/data/bootstrap/signallens-research.duckdb' \
  --research '/data/research/signallens-research.duckdb' \
  --production '/data/signallens.duckdb' --bootstrap '/data/bootstrap' \
  --backup-dir '/data/research/backups' \
  --expected-sha256 '<sha256>' --expected-byte-count '<bytes>' \
  --plan-id '<plan_id>' \
  --authorization 'I AUTHORIZE STAGING RESEARCH DATABASE REPLACEMENT'
```

The command exclusively creates `/data/research-sync.lock`, creates the API
maintenance marker, waits a bounded quiescence interval, revalidates the upload,
creates and validates a rollback artifact, validates/fsyncs a temporary file, and
uses atomic `os.replace`. It fsyncs the file and POSIX directory and removes the
maintenance marker in `finally`. A failed post-publication check automatically
attempts an atomic restoration and reports whether that restoration succeeded.

## 4. Verify after synchronization

```bash
python -m app.research_sync_cli research-sync-status \
  --research '/data/research/signallens-research.duckdb' \
  --production '/data/signallens.duckdb' \
  --backup-dir '/data/research/backups'
curl -fsS "$BACKEND_URL/api/v1/operations/health" -H "Authorization: Bearer $TOKEN"
curl -fsS "$BACKEND_URL/api/v1/operations/backups/status" -H "Authorization: Bearer $TOKEN"
curl -fsS "$BACKEND_URL/api/v1/operations/us-fundamentals/evidence" -H "Authorization: Bearer $TOKEN"
curl -fsS "$BACKEND_URL/api/v1/operations/sec-events/status" -H "Authorization: Bearer $TOKEN"
curl -fsS --get "$BACKEND_URL/api/v1/research/company-brief" -H "Authorization: Bearer $TOKEN" --data-urlencode 'qualified_symbol=PGEN.US' --data-urlencode 'decision_at=2026-10-01T21:00:00Z'
curl -fsS --get "$BACKEND_URL/api/v1/research/company-brief" -H "Authorization: Bearer $TOKEN" --data-urlencode 'qualified_symbol=ABTC.US' --data-urlencode 'decision_at=2026-10-01T21:00:00Z'
curl -fsS --get "$BACKEND_URL/api/v1/research/prospective-selection-briefs" -H "Authorization: Bearer $TOKEN" --data-urlencode 'decision_at=2026-10-01T21:00:00Z'
```

Require operations health and backup status to be valid, both SEC statuses to be
readable, PGEN.US to succeed, ABTC.US to return
`COMPANY_BRIEF_EVIDENCE_UNAVAILABLE`, and the prospective response to say no paper
selection exists yet. The sync status must show scheduler disabled, production
publishing unavailable, no marker/lock, a rollback available, and production
unchanged. Only after all checks pass may the bootstrap upload be deleted.

## 5. Explicit rollback

Obtain the rollback artifact's digest and size locally in the service shell, then:

```bash
python -m app.research_sync_cli rollback-research-sync \
  --candidate '/data/research/backups/research-sync-rollback-<timestamp>.duckdb' \
  --research '/data/research/signallens-research.duckdb' \
  --production '/data/signallens.duckdb' --backup-dir '/data/research/backups' \
  --expected-sha256 '<rollback-sha256>' --expected-byte-count '<rollback-bytes>' \
  --authorization 'I AUTHORIZE STAGING RESEARCH DATABASE ROLLBACK'
```

Rollback first preserves the currently active research DB and otherwise uses the
same isolation, durability, validation, and atomic publication controls.

## Stale-lock recovery

Status never ignores a lock. If it reports a lock after an interrupted command:

1. Do not retry and do not remove anything automatically.
2. Confirm no sync/apply/rollback process is active and inspect the bounded JSON
   metadata in `/data/research-sync.lock`.
3. Confirm research and production fingerprints and validate the active DB.
4. Only then explicitly delete `/data/research-sync.lock` and
   `/data/research-maintenance.json`; rerun status and create a new plan.

Liveness remains available during maintenance. Readiness explicitly reports
`research_maintenance`, and research-backed endpoints return bounded 503 responses
without first opening the research database.
