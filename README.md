# Traffic Radius Enterprise SEO Studio

An evidence-led, approval-gated production system for enterprise SEO audits, strategy, action planning, content review, and professional client packages.

The repository is a clean-room implementation. The Kakawa v18 package and the initiation material are regression references only; this application neither imports nor depends on either project at runtime.

## What is production-grade here

- Django 5.2 application with server-enforced project isolation and role-based permissions.
- Resumable, checkpointed audit workflow from evidence collection through approval.
- Deterministic URL normalization, evidence lineage, coverage-aware scoring, graph validation, and QA gates.
- Defensive crawler and import pipeline with SSRF, redirect-boundary, formula, archive, and file-size controls.
- Explicit integration availability; missing credentials never become invented evidence.
- OpenAI boundary with strict schemas, immutable prompt/generation ledgers, claim validation, and human approval.
- Private, content-addressed artifacts with audited downloads and manifest/checksum reconciliation.
- A protected one-click audit-results download that prefers the verified package and safely falls back to an HTML run summary for authorised agency users.
- Professional XLSX, DOCX, PDF, PPTX, HTML, JSON, CSV, and ZIP outputs.
- Structured logging, correlation IDs, worker heartbeats, health checks, deployment and recovery runbooks.

## Local start

Prerequisites: Python 3.12, PostgreSQL 16, Redis 7, and Node 20 for artifact rendering.

```powershell
Copy-Item .env.example .env
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
python manage.py migrate
python manage.py bootstrap_demo --admin-id agency.admin
python manage.py runserver
```

For a complete local stack:

```powershell
docker compose up --build
```

Open `http://localhost:8000/healthz/` for the liveness check and `http://localhost:8000/readyz/` for dependency readiness.

## Process topology

| Process | Command | Responsibility |
|---|---|---|
| web | `deployment/entrypoint.sh web` | UI, API, authentication, authorization, downloads |
| analysis worker | `deployment/entrypoint.sh analysis-worker` | crawl, imports, evidence, audit and scoring |
| render worker | `deployment/entrypoint.sh render-worker` | immutable client artifact rendering |
| scheduler | `deployment/entrypoint.sh scheduler` | stale-run heartbeat detection and scheduled operations |

## Security defaults

Public registration is absent. Passwords use Argon2id, sessions are server-side and expire on inactivity, CSRF protection is enabled, and password changes revoke existing sessions. Agency administrators create accounts and issue expiring one-time recovery passwords. Every object query is scoped to the authenticated user's agency, client, and project memberships; every artifact download is re-authorized.

Never put production credentials in this repository. Copy `.env.example`, use Railway environment variables or a managed secret store, rotate any secret exposed to logs, and keep object storage private.

## Verification

```powershell
python -m pytest
python manage.py check --deploy
python -m scripts.build_kakawa_package --phase prepare
python -m scripts.build_kakawa_package --phase finalize
python -m scripts.verify_package exports/Kakawa_Chocolates_Enterprise_SEO_Package_v19 `
  --zip exports/Kakawa_Chocolates_Enterprise_SEO_Package_v19.zip `
  --report exports/v19-verification-result.json
```

The `finalize` phase requires the workbook, deck, and render-verification outputs produced by the
approved artifact rendering runtime. It fails closed when any required renderer output is absent.
Use module invocation (`python -m scripts...`) so repository packages resolve consistently.

The package verifier fails on unresolved Critical/High QA items, unsupported claims, wrong-domain URLs, reconciliation drift, unsafe deployment assets, placeholders, broken links, or checksum mismatches.

## Documentation

- [Architecture](docs/ARCHITECTURE.md)
- [Security model](docs/SECURITY.md)
- [Operations](docs/OPERATIONS.md)
- [Backup and recovery](docs/RECOVERY.md)
- [API contract](docs/API.md)
- [Kakawa acceptance record](docs/KAKAWA_ACCEPTANCE.md)
- [Railway service layout](deployment/README.md)

## Fixed product boundaries

The studio is English-first and locale-aware. It supports one Traffic Radius agency workspace with multiple client organizations. It does not provide public signup, billing, live CMS publishing, outreach sending, or automatic disavow submission. All externally consequential outputs are proposals requiring approval.

