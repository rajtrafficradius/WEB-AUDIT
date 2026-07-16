"""Idempotent Celery tasks for resumable worker stages and stale-worker detection."""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

from celery import shared_task
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .audit import record_event
from .constants import RunState, StageStatus
from .models import AuditRun, RunStage

STAGE_SEQUENCE = {
    "collecting": 10,
    "auditing": 20,
    "planning": 30,
    "generating": 40,
    "final_qa": 50,
    "packaging": 60,
}
TERMINAL_RUN_STATES = {RunState.APPROVED, RunState.CANCELLED}


def _validated_checkpoint(value: dict[str, Any] | None) -> dict[str, Any]:
    checkpoint = value or {}
    if not isinstance(checkpoint, dict):
        raise ValueError("checkpoint must be an object")
    encoded = json.dumps(checkpoint, allow_nan=False, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > 64 * 1024:
        raise ValueError("checkpoint exceeds 64 KiB")
    return checkpoint


def _stage_name(value: str) -> str:
    cleaned = value.strip().casefold()
    if cleaned not in STAGE_SEQUENCE:
        raise ValueError("Unknown worker stage")
    return cleaned


@shared_task(
    bind=True,
    name="studio.analysis.checkpoint_stage",
    acks_late=True,
    reject_on_worker_lost=True,
)
def checkpoint_run_stage(
    self,
    run_id: str,
    stage_name: str,
    checkpoint: dict[str, Any] | None,
    idempotency_token: str,
) -> dict[str, Any]:
    """Start or heartbeat a stage while persisting an atomic resume checkpoint."""
    name = _stage_name(stage_name)
    token = idempotency_token.strip()
    if not token or len(token) > 128:
        raise ValueError("A bounded idempotency token is required")
    data = _validated_checkpoint(checkpoint)
    now = timezone.now()
    with transaction.atomic():
        run = AuditRun.objects.select_for_update().select_related("project").get(pk=run_id)
        stage, _ = RunStage.objects.select_for_update().get_or_create(
            run=run,
            name=name,
            defaults={"sequence": STAGE_SEQUENCE[name]},
        )
        previous_token = stage.checkpoint.get("idempotency_token")
        if stage.status == StageStatus.SUCCEEDED:
            if previous_token != token:
                raise ValueError("A completed stage cannot be replaced by a different task")
            return {
                "run_id": str(run.pk),
                "stage": name,
                "status": stage.status,
                "attempts": stage.attempts,
                "idempotent": True,
            }
        if run.state in TERMINAL_RUN_STATES or run.cancelled_at:
            stage.status = StageStatus.CANCELLED
            stage.finished_at = now
            stage.heartbeat_at = now
            stage.save(update_fields=["status", "finished_at", "heartbeat_at", "updated_at"])
            return {
                "run_id": str(run.pk),
                "stage": name,
                "status": stage.status,
                "attempts": stage.attempts,
                "idempotent": True,
            }
        new_attempt = stage.status != StageStatus.RUNNING or previous_token != token
        if new_attempt:
            stage.attempts += 1
            stage.started_at = now
        stage.status = StageStatus.RUNNING
        stage.heartbeat_at = now
        stage.finished_at = None
        stage.error_code = ""
        stage.error_summary = ""
        stage.checkpoint = {
            "idempotency_token": token,
            "task_id": str(getattr(self.request, "id", "") or ""),
            "saved_at": now.isoformat(),
            "data": data,
        }
        stage.save()
        record_event(
            event_type="worker.stage_checkpointed",
            run=run,
            object_instance=run,
            payload={"stage": name, "attempts": stage.attempts},
        )
    return {
        "run_id": str(run.pk),
        "stage": name,
        "status": stage.status,
        "attempts": stage.attempts,
        "idempotent": not new_attempt,
    }


@shared_task(
    name="studio.analysis.complete_stage",
    acks_late=True,
    reject_on_worker_lost=True,
)
def complete_run_stage(
    run_id: str,
    stage_name: str,
    idempotency_token: str,
    checkpoint: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Mark exactly the matching active attempt complete; repeated calls are harmless."""
    name = _stage_name(stage_name)
    data = _validated_checkpoint(checkpoint)
    now = timezone.now()
    with transaction.atomic():
        run = AuditRun.objects.select_for_update().get(pk=run_id)
        stage = RunStage.objects.select_for_update().get(run=run, name=name)
        if stage.checkpoint.get("idempotency_token") != idempotency_token:
            raise ValueError("Stage completion token does not match the active attempt")
        if stage.status == StageStatus.SUCCEEDED:
            return {"run_id": str(run.pk), "stage": name, "status": stage.status, "idempotent": True}
        if stage.status != StageStatus.RUNNING:
            raise ValueError("Only a running stage can be completed")
        stage.status = StageStatus.SUCCEEDED
        stage.heartbeat_at = now
        stage.finished_at = now
        stage.checkpoint = {
            **stage.checkpoint,
            "saved_at": now.isoformat(),
            "data": data or stage.checkpoint.get("data", {}),
        }
        stage.save()
        record_event(
            event_type="worker.stage_completed",
            run=run,
            object_instance=run,
            payload={"stage": name, "attempts": stage.attempts},
        )
    return {"run_id": str(run.pk), "stage": name, "status": stage.status, "idempotent": False}


@shared_task(name="studio.scheduler.mark_stale_stages")
def mark_stale_run_stages() -> dict[str, int]:
    """Fail stages whose worker heartbeat expired so operators can resume explicitly."""
    timeout_seconds = int(getattr(settings, "STAGE_HEARTBEAT_TIMEOUT_SECONDS", 300))
    cutoff = timezone.now() - timedelta(seconds=max(60, timeout_seconds))
    candidate_ids = list(
        RunStage.objects.filter(status=StageStatus.RUNNING, heartbeat_at__lt=cutoff).values_list(
            "pk", flat=True
        )
    )
    failed = 0
    for stage_id in candidate_ids:
        with transaction.atomic():
            stage = (
                RunStage.objects.select_for_update()
                .select_related("run__project")
                .get(pk=stage_id)
            )
            if stage.status != StageStatus.RUNNING or not stage.heartbeat_at:
                continue
            if stage.heartbeat_at >= cutoff:
                continue
            now = timezone.now()
            stage.status = StageStatus.FAILED
            stage.finished_at = now
            stage.error_code = "worker_heartbeat_expired"
            stage.error_summary = "The worker stopped heartbeating; resume from the stored checkpoint."
            stage.save()
            run = AuditRun.objects.select_for_update().get(pk=stage.run_id)
            if run.state not in TERMINAL_RUN_STATES:
                run.state = RunState.FAILED
                run.error_code = stage.error_code
                run.error_summary = stage.error_summary
                run.version += 1
                run.save(
                    update_fields=[
                        "state", "error_code", "error_summary", "version", "updated_at"
                    ]
                )
            record_event(
                event_type="worker.stage_stale",
                run=run,
                object_instance=run,
                payload={"stage": stage.name, "heartbeat_at": stage.heartbeat_at.isoformat()},
            )
            failed += 1
    return {"checked": len(candidate_ids), "failed": failed}
