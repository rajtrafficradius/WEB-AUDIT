# Security and privacy standard

Document owner: Traffic Radius security and engineering  
Applies to: local development, staging, production, workers, exports and recovery media  
Last reviewed: 2026-07-15

## Security objectives

The Studio processes client site evidence, analytics data, credentials, draft strategy, implementation proposals and export packages. Its security design prioritizes:

- strict client and project isolation;
- confidentiality of credentials, private evidence and unapproved work;
- integrity and traceability of measured claims and exports;
- predictable behavior under hostile URLs, files, HTML, formulas and prompt text;
- explicit failure and unavailable states rather than insecure fallback;
- human control over high-risk SEO assets;
- recoverability without weakening the above controls.

The detailed attack analysis is in [THREAT_MODEL.md](THREAT_MODEL.md). This document is the operational control standard.

## Identity and account lifecycle

### Account creation

There is no public registration route. Agency administrators create user accounts and assign a global role plus client- or project-scoped memberships. Production administration must use an authenticated administrator account; direct database account creation is reserved for controlled recovery and must be recorded as an incident or change.

Usernames are login IDs. Passwords are never stored or logged in plaintext. Django's first configured hasher is Argon2; PBKDF2 remains only as a compatible verification/migration fallback. Password validation requires at least 12 characters and rejects common, numeric-only and attribute-similar passwords.

### Sign-in and session controls

| Control | Implemented behavior | Production requirement |
|---|---|---|
| Authentication | Django server-side session authentication | HTTPS only |
| Login throttling | Eight failed attempts per `(REMOTE_ADDR, normalized username)` block the key for 15 minutes | Shared Redis cache and trusted-proxy address normalization |
| Session fixation | Session key is cycled at login and password change | Verify under the deployed proxy |
| Inactivity expiry | 30-minute default session age with save-on-every-request | Adjust only through risk review |
| Cookie confidentiality | `HttpOnly`, `SameSite=Lax`; `Secure` outside debug | Production must run with `DEBUG=false` and HTTPS |
| CSRF | CSRF middleware, session-backed token, explicit protection on unsafe auth endpoints | Non-browser clients must fetch `/auth/csrf/` and send the token |
| Clickjacking | `X-Frame-Options: DENY` | No framing exceptions without security review |
| Password change | Validates current password and new policy, cycles session, changes auth hash | Other sessions become invalid when their stored auth hash no longer matches |
| Forced change | Temporary-password users are blocked from application/API paths until password replacement | Health, static, logout and change-password paths are the only exceptions |

The login message does not distinguish unknown, disabled, expired-temporary-password or wrong-password accounts. Provider and internal exception text must not be reflected to the user.

### Administrator recovery

Only an agency administrator or superuser may issue a temporary password. The password:

- is generated with a cryptographically secure generator;
- is at least 16 characters and contains mixed character classes;
- is returned once to the administrator and never persisted in plaintext;
- expires after a bounded interval, 30 minutes by default;
- forces replacement before data access;
- is recorded as an audit event without recording the password.

The administrator communicates the password through an approved secure channel. It must not be sent in an ordinary project comment, export, support ticket, or log. If the one-time value may have been exposed, issue a new one and invalidate the old value immediately.

### Offboarding and emergency lockout

1. Set the user inactive and deactivate their memberships.
2. Change or invalidate their password/session auth hash.
3. Rotate provider credentials the user could independently access.
4. Review recent `AuditEvent` entries and artifact downloads.
5. Preserve the audit trail according to retention and legal requirements.

Emergency account lockout must not delete the user because historical audit-event attribution is required.

## Roles and permissions

Global roles do not replace membership checks. Except for an agency administrator, every project access requires an active client-wide or project-specific membership. The API returns a not-found response for out-of-scope objects where practical, reducing tenant enumeration.

| Capability | `agency_admin` | `analyst` | `client_reviewer` |
|---|:---:|:---:|:---:|
| Create users and issue temporary passwords | Yes | No | No |
| Create client/project configuration | Yes | No | No |
| View assigned project | All agency projects | Assigned only | Assigned only |
| Edit assigned project | Yes | Yes | No |
| Create and operate runs | Yes | Yes | No |
| Review evidence, findings and proposals | Yes | Yes | Yes, assigned only |
| Request revision | Yes | Yes | Yes, assigned only |
| Approve Gate 1 or Gate 2 | Yes | No | Yes, assigned only |
| Approve high-risk asset | Yes | No | No |
| Approve final package | Yes | No | Yes, assigned only |
| Download unapproved artifact | Yes | Yes, assigned only | No |
| Download approved artifact | Yes | Yes, assigned only | Yes, assigned only |

`is_superuser` is an emergency/operator override and must be tightly limited. It is treated as agency-administrator authority in permission code and audit review.

### Authorization invariants

- Every record lookup starts from an authenticated user and an accessible project scope.
- A UUID in a URL or request body does not grant access.
- Artifact listing filters client reviewers to `review_status=approved`.
- An artifact download must call `can_download_artifact` at request time, not rely on an earlier list response or a guessable storage key.
- Membership client and project must reconcile; database and model validation prevent a membership from attaching a project to another client.
- Workers re-authorize canonical scope through trusted job metadata and do not accept a caller-supplied client ID as authority.
- Approval decisions are version checked and row locked to prevent stale or duplicated decisions.

Any new endpoint must add a positive in-scope test and negative cross-client tests for read, write and download behavior.

## Credential security

### Application secrets

Secrets are provided through environment variables or a managed secret store. `.env` is local-only and excluded from source control. The example file contains names and non-secret defaults only.

Required production secrets include:

- `DJANGO_SECRET_KEY` with at least 50 random characters;
- database and Redis connection URLs;
- one active and any still-needed historical credential-encryption keys;
- provider access tokens, OAuth client secrets and API keys;
- private object-storage credentials;
- observability credentials.

Never include secrets in a task payload, audit payload, artifact metadata, exception message, screenshot, test fixture or package manifest.

### Integration credential envelope

Provider credentials stored in canonical records use authenticated encryption through Fernet. `CREDENTIAL_ENCRYPTION_KEYS` is a comma-separated key ring of `key_id:base64url-encoded-32-byte-key`; `CREDENTIAL_ENCRYPTION_ACTIVE_KEY` identifies the write key. Encryption refuses to operate without a valid active key. Decryption requires the recorded key ID and fails closed if the key is missing or the ciphertext cannot be authenticated.

Rotation procedure:

1. Generate a new random 32-byte key and assign a new immutable key ID.
2. Add it to the key ring while retaining keys referenced by existing records.
3. Set it as active; new writes now use it.
4. Re-encrypt existing credentials in a checkpointed, audited job.
5. Prove no records reference the old key ID.
6. Remove the old key from the environment and secret manager.

Key material must not be backed up in the same security domain as encrypted database data. Recovery access to both is privileged and audited.

### Provider requests

Provider adapters restrict destinations to explicit official hosts, do not follow redirects, bound payloads, and translate failures to safe error classes. Tokens are placed only in the provider-required header or query field. Query-bearing URLs must not be logged. Request headers and provider bodies are excluded from audit payloads.

## Network and crawler security

The crawler is a deliberate SSRF trust boundary. A project's `approved_domains` is the only client-site allowlist; user-provided URLs do not broaden it.

### URL validation

Before DNS or socket access, the URL layer:

- permits only HTTP and HTTPS;
- rejects embedded usernames/passwords, malformed hosts, invalid ports, control characters and backslashes;
- canonicalizes international hosts through IDNA;
- percent-decodes before removing dot-segments;
- enforces the exact approved domain or a real subdomain boundary;
- strips fragments and known tracking parameters for canonical identity.

### DNS and socket validation

`SSRFGuard` resolves the approved hostname, examines a bounded number of answers and rejects every non-global address class, including loopback, private, link-local, multicast, reserved, unspecified, documentation and carrier-grade NAT ranges. If any returned address is unsafe, the target is rejected. The transport then connects directly to one of the approved IPs while retaining the hostname for TLS SNI and the HTTP `Host` header. This closes the ordinary validate-then-resolve DNS rebinding gap.

Every redirect is resolved relative to the previous URL and independently revalidated. The crawler permits a bounded redirect count and never follows a redirect outside approved domains. Provider adapters do not follow redirects at all, preventing credential forwarding.

### Crawl resource controls

- configurable page, depth and duration budgets;
- 15-second request timeout and 5 MB body limit by default;
- 512 KB robots limit and conservative robots failure behavior;
- at least 0.5 seconds between requests to the same host by default;
- identity encoding to avoid decompression expansion ambiguity;
- bounded headers and blocked `Host`, `Connection`, `Content-Length` and `Transfer-Encoding` overrides;
- HTML-only analysis for document rules, with content hashes for integrity;
- a descriptive failure record rather than a silent skip.

Robots retrieval failures on authorization, rate-limit, server or network errors fail closed. Disabling robots compliance requires an explicit, documented authorization and is not the production default.

## File and import security

Uploads enter a project-specific quarantine area before canonical acceptance. `validate_import` reads and validates; it never executes a workbook, evaluates a formula, loads external links, or extracts an archive to the filesystem.

### Accepted and rejected types

| Type | Policy |
|---|---|
| UTF-8 CSV | Accepted after header, row, cell, size and formula checks |
| XLSX | Accepted after ZIP-container and worksheet XML checks |
| XLS, XLSB, XLSM | Rejected: legacy, binary or macro-capable |
| Generic ZIP | Rejected |
| Encrypted workbook | Rejected |

### Default hard limits

| Limit | Default |
|---|---:|
| File size | 50,000,000 bytes |
| Archive entries | 5,000 |
| Total uncompressed bytes | 100,000,000 |
| Single archive member | 25,000,000 bytes |
| Compression ratio | 100:1 |
| Rows | 1,000,000 |
| Columns | 500 |
| Cell characters | 32,767 |

The validator also rejects:

- paths outside the allowed staging root, symbolic links and non-regular files;
- archive absolute paths, `..` traversal and archive symlinks;
- encrypted archive members;
- VBA, external links, embedded objects, ActiveX, workbook connections and custom UI;
- XML DTD/entity declarations and malformed XML;
- formula nodes and formula-like CSV/XLSX cells beginning with `=`, `+`, `-` or `@`, except valid negative numeric literals;
- NUL bytes, blank/duplicate/oversized headers, unsafe header text and excessive dimensions.

After validation, map columns to a versioned import schema. Store the original content hash, original filename, media type, byte size, validation issues and quarantine/accepted status. Never use the original filename as an object path.

## Untrusted HTML, text and prompt-injection defense

Crawled pages, uploaded rows, titles, schema text, comments and competitor data are data. They may contain scripts, instructions, tool requests or text designed to override an AI prompt.

- Django templates auto-escape by default; use of `safe`, unescaped HTML or raw insertion requires review.
- Content Security Policy should be enforced by the production proxy or application middleware before promotion; inline scripts should be minimized and nonce/hash controlled.
- Export renderers escape HTML and spreadsheet formula prefixes.
- The OpenAI system instruction identifies delimited source material as untrusted and rejects commands within it.
- The model receives an approved fact pack, not arbitrary database or upload contents.
- Strict JSON Schema and local schema validation reject free-form response drift.
- Deterministic post-generation QA checks claims, evidence IDs, domains, known link status, placeholders, unsupported ratings/reviews and near-duplicates.
- A human approves content and high-risk assets; the model cannot advance workflow state or publish anything.

## Data and object storage

Production evidence, uploads, previews and exports belong in a private S3-compatible bucket. Public ACLs are prohibited. Object keys are generated from tenant-safe prefixes and content hashes; user-controlled filenames are metadata only. Server-side encryption and TLS are mandatory.

An object-storage URL is not an authorization mechanism. Downloads must:

1. authenticate the user;
2. load the artifact and its run/project scope;
3. call the current download permission rule;
4. record an audit event;
5. stream the object or issue a very short-lived, single-object signed URL.

Local `.local-media` and Docker volumes are development facilities. Production promotion is blocked until a private remote backend, bucket policy, encryption, lifecycle, upload integrity check and cross-client negative download test have passed.

Retention is set at the client level. Retention jobs must hold records under legal or incident preservation, delete objects only after canonical references are retired, and retain immutable audit events according to the approved policy. Deletion reports include hashes and object IDs, never object contents.

## Audit logging and observability

`AuditEvent` is append-only through application model methods. It captures authentication actions, workflow changes, approval decisions and other sensitive domain events with actor, client, project, run, object, request ID, normalized remote IP and a deliberately narrow JSON payload.

Application logs are structured JSON and correlated with the validated `X-Request-ID` or a generated UUID. Run and stage identifiers should be added to worker log context. Logs must not contain:

- passwords or temporary passwords;
- session or CSRF tokens;
- provider credentials or authorization headers;
- credential ciphertext where it is unnecessary;
- raw uploaded rows or model source payloads;
- signed object URLs;
- full provider request URLs containing API keys;
- stack traces in client responses.

Audit rows being application-immutable does not by itself protect a database administrator from mutation. Production requires restricted database roles, database/provider audit logs, backups, and alerting on unexpected audit-table writes or retention changes.

## HTTP and platform hardening

Outside debug mode, settings enable secure session/CSRF cookies, HTTPS redirect, one-year HSTS with subdomains and preload, MIME sniffing protection, proxy SSL header handling and frame denial. Production must additionally verify:

- `DEBUG=false`;
- an environment-specific random secret key;
- exact `ALLOWED_HOSTS` and HTTPS `CSRF_TRUSTED_ORIGINS`;
- the edge proxy overwrites, rather than appends untrusted, forwarding headers;
- the application is reachable only through the trusted proxy;
- a tested Content Security Policy and restrictive `Permissions-Policy`;
- no public media route serves private uploads;
- database, Redis and object storage have no unnecessary public ingress;
- staging and production secrets and buckets are separate.

Run `python manage.py check --deploy` in the actual deployment environment. A source-only check with local fallback settings is not acceptable evidence.

## Dependency and build security

- The runtime image uses a non-root `studio` user.
- The image is built in a separate stage; build tools are absent from runtime.
- Dependency versions are bounded by major/minor ranges in `pyproject.toml`.
- CI must produce a locked dependency snapshot and software bill of materials for a release.
- Run unit/security tests, `pip-audit`, static analysis and container scanning before promotion.
- Base image and system packages must be rebuilt regularly even when application code is unchanged.
- Renderer binaries and fonts are part of the trusted computing base; verify their versions and rendered output.

An advisory is triaged against exploitability and data boundary. Critical exploitable issues block release and trigger emergency patching.

## High-risk SEO controls

Redirects, canonicals, robots rules, structured data, disavow candidates and any asset classified `high` or `dangerous` require agency-administrator approval. The Studio creates recommendations only; it never changes a site or submits an external platform action.

Disavow output is disabled unless all of the following are canonical evidence:

- each candidate backlink and its relevant source/target facts;
- documented removal attempts;
- documented manual-action risk;
- an explicit administrator approval tied to that artifact version.

Changing the artifact invalidates the previous hash and requires a new review. Approval of one artifact does not authorize a different file with the same title.

## Security incident response

1. **Contain:** disable affected accounts, connections, queues or downloads; rotate exposed secrets; preserve evidence.
2. **Scope:** correlate request IDs, audit events, run IDs, storage access, provider logs and deployment changes.
3. **Eradicate:** patch the cause, revoke tokens, rebuild immutable images and remove malicious objects from active paths without destroying forensic copies.
4. **Recover:** follow [RECOVERY.md](RECOVERY.md), validate tenant isolation and artifact hashes, then restore service in stages.
5. **Notify:** follow contractual and legal notification requirements; do not speculate beyond verified evidence.
6. **Learn:** document root cause, affected records, timeline, control changes and regression tests.

Never restore availability by disabling project scoping, CSRF, TLS validation, content hashing or approval gates.

## Release security checklist

- [ ] Staging and production use distinct secrets, databases, Redis instances and buckets.
- [ ] `DEBUG=false`; deployment checks and `/readyz/` pass.
- [ ] Argon2 is available and first in the configured hasher list.
- [ ] Login throttling uses shared cache and correct client IP normalization.
- [ ] Forced password change and recovery expiry are tested.
- [ ] Positive and negative role/membership tests pass for every route and download.
- [ ] Credential encryption round-trip and previous-key decryption pass without logging secrets.
- [ ] SSRF tests cover private IPs, mixed DNS answers, redirects, IDNs and rebinding.
- [ ] Malicious CSV/XLSX regression fixtures are rejected.
- [ ] Provider redirects and oversized/malformed responses fail closed.
- [ ] OpenAI unavailable, refusal and invalid-schema paths are tested.
- [ ] CSP, host, CSRF origin, HSTS and proxy behavior are verified from outside the cluster.
- [ ] Private object policy and short-lived authorized downloads are verified.
- [ ] No unresolved Critical or High security or QA issue remains.
- [ ] A current backup has passed the isolated restore test.
