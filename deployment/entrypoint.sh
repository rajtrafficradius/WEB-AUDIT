#!/bin/sh
set -eu

case "${1:-web}" in
  release)
    python manage.py migrate --noinput
    python manage.py migrate --check
    ;;
  web)
    # Keep a single-service Railway deployment usable even when the platform's
    # optional pre-deploy command has not been configured in the dashboard.
    python manage.py migrate --noinput
    python manage.py migrate --check
    if [ -n "${SEO_STUDIO_BOOTSTRAP_ADMIN_ID:-}" ] && [ -n "${SEO_STUDIO_BOOTSTRAP_PASSWORD:-}" ]; then
      python manage.py bootstrap_admin_from_env
    fi
    if [ "${EMBED_ANALYSIS_WORKER:-1}" = "1" ]; then
      celery -A app worker --loglevel=${LOG_LEVEL:-INFO} --queues=analysis --concurrency=1 --max-tasks-per-child=20 &
    fi
    exec gunicorn app.wsgi:application --bind 0.0.0.0:${PORT:-8000} --workers ${WEB_CONCURRENCY:-3} --threads 2 --timeout 120 --access-logfile - --error-logfile -
    ;;
  analysis-worker)
    exec celery -A app worker --loglevel=${LOG_LEVEL:-INFO} --queues=analysis --concurrency=${ANALYSIS_CONCURRENCY:-2} --max-tasks-per-child=50
    ;;
  render-worker)
    exec celery -A app worker --loglevel=${LOG_LEVEL:-INFO} --queues=render --concurrency=${RENDER_CONCURRENCY:-1} --max-tasks-per-child=10
    ;;
  scheduler)
    exec celery -A app beat --loglevel=${LOG_LEVEL:-INFO} --schedule=/tmp/celerybeat-schedule
    ;;
  *)
    exec "$@"
    ;;
esac

