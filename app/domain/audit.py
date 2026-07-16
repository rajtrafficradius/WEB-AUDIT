"""Append-only audit event writer with intentionally narrow safe metadata."""

from __future__ import annotations

import ipaddress

from django.db import transaction

from .models import AuditEvent, AuditRun, Client, Project


def _safe_ip(request):
    if request is None:
        return None
    value = request.META.get("REMOTE_ADDR", "")
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        return None


def record_event(
    *,
    event_type: str,
    actor=None,
    request=None,
    object_instance=None,
    client: Client | None = None,
    project: Project | None = None,
    run: AuditRun | None = None,
    payload: dict | None = None,
) -> AuditEvent:
    if isinstance(object_instance, AuditRun):
        run = run or object_instance
    if run is not None:
        project = project or run.project
    if isinstance(object_instance, Project):
        project = project or object_instance
    if project is not None:
        client = client or project.client
    if isinstance(object_instance, Client):
        client = client or object_instance
    object_type = object_instance._meta.label_lower if object_instance is not None else ""
    object_id = str(getattr(object_instance, "pk", "") or "")
    with transaction.atomic():
        return AuditEvent.objects.create(
            actor=actor if getattr(actor, "is_authenticated", False) else None,
            client=client,
            project=project,
            run=run,
            event_type=event_type[:120],
            object_type=object_type[:120],
            object_id=object_id[:80],
            request_id=str(getattr(request, "request_id", ""))[:100],
            ip_address=_safe_ip(request),
            payload=payload or {},
        )
