# Railway service layout

Create four services per environment from the same image and assign each service the matching
configuration file below. The service's Railway **config file path** must point at that file; do
not copy start commands into an untracked dashboard field.

| Environment | Web | Analysis | Render | Scheduler |
|---|---|---|---|---|
| Staging | `railway.staging.json` | `railway.staging.analysis.json` | `railway.staging.render.json` | `railway.staging.scheduler.json` |
| Production | `railway.production.json` | `railway.production.analysis.json` | `railway.production.render.json` | `railway.production.scheduler.json` |

Only the web service has a pre-deploy command. It runs `entrypoint.sh release` once before the web
deployment and blocks that deployment if migrations fail. Web replicas never run migrations at
startup. Deploy the web service first, verify `/readyz/`, then deploy workers and the singleton
scheduler against the migrated schema.

Every start command sets `DJANGO_ENV` explicitly. The image itself defaults to `production`, so a
missing service override fails closed instead of silently enabling development behavior. The
scheduler manifests always specify one replica; operating more than one beat scheduler is a
release blocker.

Secrets and provider URLs remain Railway variables or approved secret-manager references. At a
minimum, configure the values documented in `.env.example`, use different PostgreSQL, Redis,
object-storage, credential-encryption, and cookie domains for staging and production, and verify
the resolved deployment configuration before promotion.
