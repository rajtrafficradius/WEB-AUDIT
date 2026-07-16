# Operations and release runbook

Document owner: Traffic Radius platform operations  
Audience: operators, agency administrators and on-call engineers  
Last reviewed: 2026-07-15

## Operating principles

- PostgreSQL canonical records are authoritative; Redis, rendered previews and worker memory are replaceable.
- Staging always proves a release before production promotion.
- A missing integration is an explicit availability state, not an emergency reason to invent or manually pad data.
- Long-running work is checkpointed and idempotent; operators resume or retry a stage instead of editing output files by hand.
- Approval and QA gates remain active during incidents and urgent releases.
- Every command is run against an explicitly identified environment and recorded in the change log.

## Environment matrix

| Property | Local development | Staging | Production |
|---|---|---|---|
| Database | SQLite fallback or local PostgreSQL | Separate managed PostgreSQL | Separate managed PostgreSQL with PITR |
| Cache/broker | Local-memory fallback or local Redis | Separate managed Redis | Separate managed Redis |
| Object storage | `.local-media` or local private volume | Private staging bucket | Private production bucket |
| HTTPS | Optional on loopback | Required | Required |
| Credentials | Test/replay or developer-scoped | Staging-scoped | Production-scoped, least privilege |
| Email/public signup | Not part of product | Disabled | Disabled |
| External mutation | Disabled | Disabled | Disabled |
| Data | Synthetic/replay | Approved staging data | Authorized client data |

Never point local or staging workers at a production queue, database, Redis namespace or bucket.

## Process inventory

| Process | Start mode | Expected evidence of health |
|---|---|---|
| Web | `/app/deployment/entrypoint.sh web` | `/healthz/` returns 200 and `/readyz/` returns 200 |
| Analysis worker | `/app/deployment/entrypoint.sh analysis-worker` | Worker is registered on `analysis`; smoke task succeeds; active stage heartbeat is current |
| Render worker | `/app/deployment/entrypoint.sh render-worker` | Worker is registered on `render`; small render smoke succeeds |
| Scheduler | `/app/deployment/entrypoint.sh scheduler` | One scheduler only; scheduled-job timestamp advances |

The image build collects static assets. Railway's web manifest runs
`/app/deployment/entrypoint.sh release` as its one pre-deploy migration phase; web replicas only
start Gunicorn. A migration failure blocks the deployment before any new replica serves traffic.

## Configuration

Start from `.env.example`; never commit `.env`. Environment variables belong in Railway environment settings or an approved secret manager.

### Required production configuration

| Group | Variables or service | Readiness expectation |
|---|---|---|
| Django | `DJANGO_ENV`, `DJANGO_SECRET_KEY`, `DJANGO_ALLOWED_HOSTS`, `DJANGO_CSRF_TRUSTED_ORIGINS`, `DJANGO_TIME_ZONE` | Explicit environment, strong unique secret, exact hosts/origins, `DEBUG=false` |
| Database | `DATABASE_URL`, `DB_CONN_MAX_AGE` | TLS as required by provider; migration and query check pass |
| Redis/Celery | `REDIS_URL`, optional broker/result overrides | Web and all worker processes use the same intended environment |
| Cookies/TLS | `SECURE_SSL_REDIRECT`, HSTS settings | Secure cookies and redirect verified through external HTTPS endpoint |
| Credential encryption | `CREDENTIAL_ENCRYPTION_KEYS`, `CREDENTIAL_ENCRYPTION_ACTIVE_KEY` | Active key exists; round-trip and old-key read test pass |
| Object storage | bucket, endpoint, region and storage credentials | Private backend configured; upload/download/hash test passes |
| Providers | Google OAuth, PageSpeed, SEMrush, OpenAI variables | Each source reports available or a truthful unavailable reason |
| Observability | log level, tracing and error-reporting endpoints | Test event carries request and run correlation IDs |

`SESSION_COOKIE_SECURE`, `CSRF_COOKIE_SECURE`, and `SECURE_SSL_REDIRECT` are explicit runtime
settings. Staging and production validation rejects false values, non-HTTPS trusted origins, weak
HSTS, local hosts, non-PostgreSQL databases, missing Redis, or non-private object storage.

### Secret-key readiness

When no secret key is configured outside debug, an explicit test process, or the opt-in insecure-development mode, Django refuses to start. Debug/test tooling creates a process-local random key. `/readyz/` retains a sentinel-key defense-in-depth check. Production must provide a stable environment-specific secret.

## Local start

From the repository root:

```powershell
Copy-Item .env.example .env
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
python manage.py migrate
python manage.py runserver
```

For the container topology:

```powershell
docker compose up --build
```

Local probes:

```powershell
Invoke-WebRequest http://localhost:8000/healthz/
Invoke-WebRequest http://localhost:8000/readyz/
```

Do not treat a local SQLite or local-memory-cache pass as proof of PostgreSQL locking, shared throttling or Celery behavior.

## Staging deployment

### Pre-deploy

1. Identify the commit/image digest and change owner.
2. Review schema changes for backward and forward compatibility.
3. Run the full test suite, security/static checks and dependency audit.
4. Build the image once; record its digest. Staging and production must use the same digest.
5. Confirm staging variables, provider scopes, bucket and database are isolated.
6. Create a current database backup before a destructive or non-backward-compatible migration.

Suggested checks:

```powershell
python -m pytest
python manage.py check
python manage.py check --deploy
python -m ruff check .
python -m bandit -r app audit_engine integrations generation exporters
python -m pip_audit
```

`check --deploy` must run with staging-like security variables. If run under local debug/fallback settings, record it as a local diagnostic only.

### Deploy and verify

1. Deploy the recorded image to staging.
2. Run migrations through one release process.
3. Verify `/healthz/` and `/readyz/` from outside the deployment network.
4. Verify web, analysis worker, render worker and exactly one scheduler.
5. Sign in with each role using staging accounts; test forced password change.
6. Execute positive and cross-client-negative API and artifact-access tests.
7. Run a recorded-fixture collection through `COLLECTING`, `AUDITING` and Gate 1.
8. Approve Gate 1 with an authorized reviewer; prove an analyst cannot approve it.
9. Complete generation using replay or an approved staging provider account. Exercise the no-key/unavailable path too.
10. Approve Gate 2; prove high-risk approval is administrator-only.
11. Render the full package, run manifest/checksum/reconciliation QA, and approve the package.
12. Create and restore a backup in an isolated restore-test database.
13. Record results, timestamps, image digest, migration version and approvers.

Any Critical/High failure, health-route mismatch, missing private storage, cross-client access, checksum drift or untested restore blocks promotion.

## Production promotion

Production promotion is an explicit change event, not an automatic side effect of a staging deploy.

1. Confirm all staging evidence is current for the exact image digest.
2. Confirm the Kakawa acceptance contract in [KAKAWA_ACCEPTANCE.md](KAKAWA_ACCEPTANCE.md) is satisfied by machine-readable QA evidence, not prose alone.
3. Confirm both approval gates and package approval were tested.
4. Confirm a recent backup and isolated restore test.
5. Confirm on-call ownership and rollback criteria.
6. Put nonessential schedule jobs on hold if the migration requires it; do not interrupt an active package without a checkpoint.
7. Deploy the same image digest.
8. Run the migration through one release process.
9. Verify readiness, sessions, queues, storage and an authorized download.
10. Resume schedules and observe error rate, queue depth, worker heartbeats and database health through the defined watch window.

Do not copy staging data or credentials into production during promotion.

## Migrations

### Safe migration pattern

Prefer expand-and-contract changes:

1. Add nullable/new columns or tables and deploy compatible code.
2. Backfill in bounded, checkpointed batches.
3. Switch reads/writes after reconciliation.
4. Add constraints or remove old columns in a later release.

Before migration:

- inspect the generated SQL;
- estimate lock duration and table size;
- identify rollback behavior;
- create a backup for changes that cannot be reversed safely;
- confirm workers running older code cannot corrupt the new schema.

After migration:

```powershell
python manage.py migrate --check
python manage.py check --deploy
```

Schema rollback is never performed blindly. If reversing would discard valid writes, restore service with compatible forward code and plan a corrective migration.

## Run operations

### Starting a run

- Confirm the project primary domain is included in approved domains.
- Confirm run profile and budgets.
- Capture the source cutoff/as-of time.
- Review connection availability and import validation.
- Use a unique, stable `Idempotency-Key` for the create request.
- Record rule version and expected source scope.

Repeated create requests with the same project and idempotency key return the existing run. A different request must use a different key.

### Monitoring a run

Monitor:

- `AuditRun.state`, `version`, evidence coverage and error code;
- each `RunStage.status`, attempts, `heartbeat_at` and checkpoint;
- analysis/render queue depth and oldest queued age;
- integration status, attempts and safe error categories;
- crawler discovered, completed, failed and stop-reason counts;
- object writes and integrity hashes;
- unresolved Critical/High QA results.

An active stage without a heartbeat beyond the stage-specific threshold is stale. Verify the worker and broker before marking failed. Never fabricate a successful checkpoint to advance the workflow.

### Retry and resume

1. Identify the failed stage and last durable checkpoint.
2. Confirm the failure is retryable. Authentication, validation and missing-configuration errors require correction, not repeated retries.
3. Ensure no healthy worker is still executing the same stage.
4. Resume from the checkpoint with the same run and stage identity.
5. Increment attempts, restore heartbeat and record an audit event.
6. Reconcile counts/hashes from previous partial work before accepting new output.

Provider retries are bounded and circuit-protected. When a circuit is open, let the reset window expire or correct the upstream condition; do not bypass it by creating unbounded parallel tasks.

### Cancellation

Cancellation is terminal in the workflow. A worker must check cancellation at safe boundaries, stop scheduling dependent work, finish or abandon the current atomic operation, and preserve completed evidence. A new run is required to restart intentionally.

### Revision

A revision request includes a nonempty reason and returns the run to an allowed planning, generation or QA state. New artifacts receive new hashes and review states. Prior approvals do not transfer to changed high-risk artifacts or the changed package.

## Integration operations

### Availability handling

| Condition | Operator action | Run behavior |
|---|---|---|
| Credential absent | Confirm whether source is expected; do not add fake data | Source is `unavailable` with reason; coverage reduces |
| Credential rejected | Rotate/re-authorize; review scopes | Non-retryable authentication failure |
| Rate limited | Respect retry window; reduce concurrency or sample | Bounded retry, then unavailable/partial |
| Timeout/upstream error | Check provider and network; allow bounded retry | Retryable until policy exhausted |
| Invalid request/schema | Correct configuration or mapping | Non-retryable validation failure |
| Malformed response | Quarantine response metadata; update adapter/fixture | Fail closed; do not parse heuristically |
| Circuit open | Resolve repeated cause or wait for half-open probe | Source paused; run may continue with lower coverage |

Never paste a provider response containing credentials into a ticket. Use safe adapter error codes, request IDs, timestamps and provider-side correlation identifiers where available.

### Credential rotation

Rotate in staging first, verify the adapter, then rotate production. When rotating OAuth tokens, verify scopes and target account/property. When rotating the envelope key, follow the re-encryption procedure in [SECURITY.md](SECURITY.md).

## Package and artifact operations

An operator does not edit generated client files directly. Correct canonical data or templates and rerender.

Package readiness requires:

- artifact rows reconcile to file inventory and hashes;
- every measured/derived claim has evidence and as-of context;
- every unavailable source has a reason;
- no unresolved Critical/High QA result;
- no wrong-domain or broken internal URL;
- no unapproved risky asset;
- no unsupported claim, fabricated rating, placeholder or unapproved near-duplicate;
- counts reconcile across UI, reports, workbooks, deck and manifest;
- cross-format derivatives are declared and byte-identical duplicates are explained or removed;
- the self-contained HTML/PPTX/PDF deck contains no machine-specific paths;
- the ZIP has an internal manifest and an adjacent SHA-256 checksum.

The package verifier's machine-readable JSON is the primary acceptance evidence. PDF/XLSX QA reports are human-readable derivatives and must reconcile to it.

## Observability and alerts

### Required structured context

Web logs include timestamp, severity, service, environment, request ID, route, method, status and duration. Worker logs additionally include run ID, stage ID, task ID, attempt and provider where applicable. Sensitive values are redacted per [SECURITY.md](SECURITY.md).

### Minimum alerts

| Signal | Alert condition | First response |
|---|---|---|
| Readiness | `/readyz/` fails on multiple probes | Check database, cache and secret readiness |
| HTTP errors | Sustained 5xx increase | Correlate request IDs and recent deploy |
| Authentication | Unexpected throttle spike or admin login anomaly | Investigate source, account and proxy IP behavior |
| Queue | Oldest job or depth exceeds agreed threshold | Check workers, broker and provider limits |
| Heartbeat | Running stage heartbeat stale | Check worker/task ownership; resume safely |
| QA | New Critical/High failure | Block packaging/promotion and assign owner |
| Storage | Object write/hash/download failure | Stop packaging; verify bucket and integrity |
| Backup | Scheduled backup missing or checksum failure | Create a new backup and investigate schedule |
| Restore | Restore-test failure | Treat recovery assurance as degraded; block release |

Thresholds depend on deployed capacity and client commitments and must be recorded in the environment's monitoring configuration. This document does not claim an unmeasured service-level objective.

## Backup scheduling

`deployment/backup.sh` creates a PostgreSQL custom-format dump and adjacent SHA-256 file. Schedule it with a Railway cron service or equivalent external scheduler; the presence of the script does not schedule it automatically.

Production policy must define:

- dump frequency and retention;
- managed PostgreSQL PITR retention;
- encrypted off-service copy location;
- backup success alerting;
- monthly or release-linked isolated restore tests;
- object-storage inventory/versioning and recovery behavior.

Full procedure: [RECOVERY.md](RECOVERY.md).

## Rollback and roll-forward

Rollback criteria include cross-client access, authentication failure, corrupt writes, widespread 5xx, queue duplication, unusable exports or failed readiness after the agreed window.

Preferred order:

1. Stop or drain affected workers if they can continue harmful writes.
2. Disable new run starts while preserving authenticated read access where safe.
3. Roll forward with a compatible hotfix when data has already been written under the new schema.
4. Roll back the image only when the old code is compatible with the current schema.
5. Restore data only when integrity is lost and after preserving forensic/current copies.

After rollback or roll-forward, rerun readiness, role isolation, queue smoke, artifact hash and workflow-transition checks. Record the incident/change with image and migration versions.

## Routine maintenance

Daily:

- review readiness, worker/queue health, stale stages, failed integrations and backup status;
- triage Critical/High QA or security events.

Weekly:

- review inactive/stale accounts and provider expiry;
- inspect storage growth, retention candidates and repeated circuit openings;
- run a staging replay smoke against current code.

Monthly:

- perform an isolated restore test;
- review administrator/superuser population and membership drift;
- patch base images and dependencies;
- review audit-event completeness and redaction samples;
- verify object lifecycle and backup retention.

Per release:

- execute the full staging promotion gate;
- review documentation and threat model changes;
- archive machine-readable test, QA, migration, backup and approval evidence.
