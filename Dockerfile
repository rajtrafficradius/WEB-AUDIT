# syntax=docker/dockerfile:1.7
FROM python:3.12-slim-bookworm AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1
WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY app ./app
COPY audit_engine ./audit_engine
COPY integrations ./integrations
COPY generation ./generation
COPY exporters ./exporters
RUN python -m venv /opt/venv && /opt/venv/bin/pip install --upgrade pip wheel \
    && /opt/venv/bin/pip install .

FROM python:3.12-slim-bookworm AS runtime

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DJANGO_SETTINGS_MODULE=app.settings \
    DJANGO_ENV=production
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
      curl libpq5 poppler-utils fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 studio

COPY --from=builder /opt/venv /opt/venv
COPY --chown=studio:studio . .
RUN chmod +x deployment/entrypoint.sh \
    && DJANGO_ENV=development DJANGO_ALLOW_INSECURE_DEV_KEY=1 python manage.py collectstatic --noinput \
    && mkdir -p /app/.local-media/quarantine \
    && chown -R studio:studio /app/.local-media
USER studio

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD curl --fail --silent http://127.0.0.1:8000/healthz/ || exit 1

ENTRYPOINT ["/app/deployment/entrypoint.sh"]
CMD ["web"]

