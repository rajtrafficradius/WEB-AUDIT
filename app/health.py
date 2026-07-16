"""Liveness and readiness endpoints for container orchestration."""

from __future__ import annotations

from django.conf import settings
from django.core.cache import cache
from django.db import connection
from django.http import JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET


@require_GET
@never_cache
def healthz(request):
    return JsonResponse({"status": "ok", "service": "traffic-radius-seo-studio"})


@require_GET
@never_cache
def readyz(request):
    checks: dict[str, str] = {}
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        checks["database"] = "ok"
    except Exception:  # pragma: no cover - backend-specific outage path
        checks["database"] = "unavailable"

    try:
        key = f"readyz:{getattr(request, 'request_id', 'probe')}"
        cache.set(key, "ok", timeout=5)
        checks["cache"] = "ok" if cache.get(key) == "ok" else "unavailable"
    except Exception:  # pragma: no cover
        checks["cache"] = "unavailable"

    secret_ok = settings.DEBUG or not settings.SECRET_KEY.startswith("UNCONFIGURED-")
    checks["secret_key"] = "ok" if secret_ok else "unavailable"
    ready = all(value == "ok" for value in checks.values())
    return JsonResponse(
        {"status": "ready" if ready else "not_ready", "checks": checks},
        status=200 if ready else 503,
    )
