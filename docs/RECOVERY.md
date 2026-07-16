# Backup, restore and disaster-recovery runbook

Document owner: Traffic Radius platform operations  
Last reviewed: 2026-07-15

## Recovery guarantees and limits

PostgreSQL is the canonical source of projects, evidence lineage, workflow, approvals, artifact metadata, hashes and audit events. Private object storage holds uploaded evidence and rendered artifacts. Redis contains queues, cache and transient coordination and is not an authoritative backup source.

Recovery is complete only when database records, private objects, encryption keys, deployed code/templates and provider configuration reconcile. Restoring a database without matching objects may recover metadata but not deliverables. Restoring objects without the database does not recover authorization or provenance.

Recovery point objective (RPO) and recovery time objective (RTO) must be set in the production service policy and proved by exercises. This repository does not claim numeric objectives that have not been measured.

## Recovery assets

| Asset | Required protection | Restore role |
|---|---|---|
| Managed PostgreSQL | Provider automated backups and PITR plus verified logical dumps | Canonical application state |
| Logical dump | Encrypted custom-format `pg_dump` plus SHA-256 | Portable validation and isolated restore tests |
| Private object storage | Versioning/inventory, encryption, lifecycle and provider recovery | Evidence, uploads, previews and exports |
| Django secret | Secret manager with controlled recovery access | Sessions/signing; rotation may invalidate sessions |
| Credential encryption key ring | Separately protected secret manager | Decrypts stored provider credentials |
| Provider credentials | Provider/secret manager recovery | Reconnects external sources; data may remain unavailable until restored |
| Container image and source revision | Immutable registry and source control | Recreates compatible runtime and render templates |
| Migration history | Source revision and database table | Establishes schema compatibility |
| Package manifests/checksums | Database and package copies | Verifies recovered artifact integrity |
| Operational evidence | Change, backup and restore-test records | Proves what was restored and by whom |

Keep database backup data and the credential-decryption keys in separate security domains. Access to both is highly privileged.

## Backup policy

### PostgreSQL provider backups

Enable managed PostgreSQL automated backups and point-in-time recovery in staging and production. Verify, rather than assume:

- the actual retention window;
- the earliest and latest restorable timestamps;
- backup-region and encryption settings;
- permissions for initiating and observing a restore;
- alerting when the backup chain is degraded.

Provider backup configuration belongs in the environment change record. A screenshot alone is insufficient; periodic restore evidence is required.

### Logical dumps

`deployment/backup.sh` requires `DATABASE_URL`, writes a custom-format dump, and creates an adjacent checksum. It defaults to `/tmp/backups`, which is ephemeral; production scheduling must upload both files to approved encrypted backup storage before the job exits.

Example inside the deployed image or an equivalent administrative job:

```sh
BACKUP_DIRECTORY=/secure-staging-area /app/deployment/backup.sh
```

The script outputs the dump path. The operator or scheduled job then:

1. verifies the checksum locally;
2. copies the dump and `.sha256` to encrypted backup storage;
3. records database/environment, timestamp, size, SHA-256, object version and job ID;
4. confirms the uploaded objects are private;
5. removes the ephemeral local copy under the approved retention policy.

Do not place a database password in command output. `DATABASE_URL` is supplied through the process environment/secret manager and excluded from logs.

### Object storage

Database dumps do not include private binary objects. Production object-storage policy must provide:

- server-side encryption and TLS;
- versioning or an equivalent accidental-deletion recovery mechanism;
- regular inventory containing key, version, size, timestamp and checksum/ETag semantics;
- lifecycle rules that align with each client's retention period and legal holds;
- protection against cross-environment replication mistakes;
- tested restore of a deleted or previous-version object.

Application SHA-256 values, not multipart ETags, are the artifact integrity authority.

### Schedule and retention

The production owner records exact schedules and retention in the environment runbook. At minimum, there must be:

- automated provider backups/PITR;
- recurring logical dumps with failure alerts;
- an off-service or independently protected copy;
- object inventory/version protection;
- routine isolated restore tests;
- a retention policy that covers operational needs without retaining client data indefinitely.

The existence of `backup.sh` does not prove a schedule. A missed backup or failed checksum is an alert and a release blocker until recovery assurance is restored.

## Restore-test procedure

`deployment/restore_test.sh` verifies a dump checksum, restores it into a disposable database, checks for unapplied migrations and runs Django deployment checks.

### Safety prerequisites

- Use a new, isolated restore-test PostgreSQL database with no production application traffic.
- Verify the resolved target name and environment before issuing any clean/restore command.
- Use a dedicated restore credential with no authority over production.
- Use the application image/source revision compatible with the backup timestamp.
- Do not direct workers, scheduler or web replicas at the test database until intentionally running smoke tests.
- Never use a production database URL as `RESTORE_TEST_DATABASE_URL`.

### Restore

1. Retrieve the dump and its exact adjacent `.sha256` file.
2. Verify download metadata and private access controls.
3. Start a disposable database and capture its identifier.
4. Run:

```sh
RESTORE_TEST_DATABASE_URL='postgresql://.../seo_studio_restore_test' \
  /app/deployment/restore_test.sh /secure-staging-area/seo-studio-YYYYMMDDTHHMMSSZ.dump
```

The script uses `pg_restore --clean --if-exists --no-owner --no-acl`, so the target must be disposable.

### Application reconciliation

The script's exit code is necessary but not sufficient. Against the restored database:

1. Confirm migration history and expected application version.
2. Count clients, projects, runs, source snapshots, evidence, artifacts, approvals, QA results, manifests and audit events; compare to backup-time inventory when available.
3. Verify foreign-key and model constraints.
4. Select representative approved and unavailable evidence records; confirm source metadata and reasons.
5. Select representative artifacts; retrieve the matching object versions and verify application SHA-256 and size.
6. Verify package manifest hashes and ZIP checksum for at least one approved package.
7. Sign in with a recovery-only test account or cloned non-production credentials; test project isolation and client-reviewer download filtering.
8. Run `/healthz/` and `/readyz/` with the restored database and an isolated cache.
9. Run a no-network fixture replay through a checkpoint and QA read path.
10. Destroy the test web/worker processes, database and restored object copies under the test retention policy.

Do not send provider requests or client notifications from a restore test. Provider connections remain disabled unless an exercise specifically authorizes isolated test credentials.

### Restore-test record

Record:

| Field | Required value |
|---|---|
| Exercise ID | Unique identifier |
| Operator and reviewer | Named accountable people |
| Environment | Staging or isolated recovery environment |
| Backup timestamp and source | Provider/PITR/logical dump |
| Dump SHA-256 and byte size | Verified values |
| Image digest/source revision | Exact compatible release |
| Start/end timestamps | Measured duration |
| Database checks | Pass/fail with counts |
| Object checks | Keys/versions and SHA-256 results |
| Authorization smoke | Pass/fail, including negative tenant test |
| Manifest/package check | Pass/fail |
| Exceptions | Every deviation or missing asset |
| Final result | Pass, fail, or partial |
| Follow-up owner/date | Required for every exception |

A partial restore is not a successful recovery test. It may still provide useful evidence, but production promotion remains blocked until required gaps are closed.

## Point-in-time recovery procedure

Use PITR for database corruption, destructive migration or unauthorized modification when the desired recovery time is inside the provider window.

1. Declare an incident and stop writes: disable new run starts, drain/stop workers and scheduler, and put web in maintenance/read-only mode where available.
2. Record the suspected bad-change interval using audit events, deployment logs, database logs and request/run IDs.
3. Preserve a current snapshot for forensics before recovery.
4. Choose a target time immediately before the first verified bad write, accounting for timezone and transaction boundaries.
5. Restore to a new database instance. Do not overwrite the original in place.
6. Attach an isolated copy of the compatible application and run the full reconciliation above.
7. Compare changes after the restore point. Decide, with business/security owners, whether and how to reapply valid later work.
8. Switch staging or production connection only after approval and a rollback plan.
9. Rotate database credentials and invalidate old connection pools.
10. Re-enable web writes, then workers, then scheduler while observing health and queue behavior.
11. Record data loss interval, recovered records, intentionally omitted writes and client impact.

PITR does not restore object versions. Reconcile object storage separately, especially artifacts created or deleted around the incident window.

## Full environment recovery

For region, provider or environment loss:

1. Open the incident and establish a recovery lead, communications lead and security reviewer.
2. Select a clean region/provider project that meets data-residency and contractual requirements.
3. Deploy the last known-good immutable image by digest.
4. Provision isolated PostgreSQL, Redis and private object storage.
5. Restore PostgreSQL to the selected point and reconcile it before traffic.
6. Restore or reconnect exact object versions; verify hashes against canonical records.
7. Load application and credential-encryption secrets through the secret manager. Rotate secrets if exposure is possible.
8. Reconfigure provider OAuth redirect URIs and service allowlists where necessary; keep connections unavailable until verified.
9. Apply exact hosts, origins, HTTPS and proxy settings.
10. Run deployment checks, readiness, role isolation, forced-password, queue, render, manifest and download smoke tests.
11. Update DNS/traffic gradually and monitor.
12. Re-enable provider collection, analysis workers, render workers and finally the scheduler.

Redis queues are not blindly restored. Reconstruct runnable work from PostgreSQL `AuditRun`/`RunStage` state and checkpoints. Before requeueing, prove that no task is still active in the old environment and that the operation is idempotent.

## Failure-specific playbooks

### Database unavailable but intact

- Keep the application unready; do not redirect writes to SQLite.
- Stop workers from repeatedly retrying database writes.
- Check provider status, connection limits, TLS and credentials.
- Restore connectivity; run readiness and transaction smoke tests.
- Resume workers from canonical checkpoints.

### Redis unavailable or lost

- Web readiness will fail when production cache is unavailable.
- Stop queue submission until Redis is healthy.
- Provision/restore Redis according to provider policy, but treat its contents as non-authoritative.
- Reconcile running stages and task ownership from PostgreSQL.
- Requeue only checkpoint-safe operations; watch for duplicates.
- Confirm shared login throttling and session behavior before full traffic.

### Object missing or corrupt

- Block download/packaging of the affected artifact.
- Compare canonical size/SHA-256 with object versions and inventory.
- Restore the exact version from object history when available.
- If derived, rerender from the same canonical inputs and template version; create a new artifact hash/version and repeat QA/approval rather than impersonating the lost bytes.
- If source evidence cannot be recovered, mark dependent claims unavailable and invalidate packages that relied on it.

### Credential-encryption key unavailable

- Do not replace encrypted credentials with plaintext or bypass decryption.
- Keep connections unavailable and preserve ciphertext/key IDs.
- Recover the exact key from the separate secret backup under dual control.
- If unrecoverable, re-authorize each provider connection and write new encrypted credentials; document permanently unreadable historical connection secrets.

### Django secret exposed or lost

- Generate a new secret in the secret manager and deploy it.
- Expect all existing sessions and signed values to become invalid; communicate forced reauthentication.
- Investigate exposure and rotate related secrets if stored together.
- The Django secret does not decrypt provider credentials; do not confuse it with the credential key ring.

### Bad deployment or migration

- Stop harmful writes and preserve current state.
- Determine whether previous code is schema-compatible.
- Prefer a forward corrective deploy when new-schema writes exist.
- Use PITR only when canonical integrity is lost and after assessing later valid writes.
- Re-run full staging gates before returning to production.

### Compromised account or tenant isolation incident

- Disable the account and affected sessions/connections.
- Preserve logs, audit events and object access records.
- Scope every client/project/artifact accessed.
- Rotate exposed credentials and signed URL mechanisms.
- Restore data only if integrity changed; do not erase forensic evidence.
- Revalidate authorization with cross-client negative tests before reopening.

## Recovery acceptance gate

Recovery is accepted only when:

- the selected restore point and data-loss interval are documented;
- database checks and canonical counts reconcile or exceptions are approved;
- representative private objects match canonical size and SHA-256;
- manifests and package checksums validate;
- authentication, forced password change and client isolation pass;
- `/readyz/`, analysis queue and render queue smoke tests pass;
- no provider or OpenAI source is reported available without a successful scoped check;
- unresolved exceptions have owners, impact and due dates;
- incident and change records identify operator and approver.

Never declare recovery complete solely because the home page loads.
