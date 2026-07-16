from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from app.domain.constants import RunProfile, RunState, StageStatus, UserRole
from app.domain.models import AuditRun, Client, Project, RunStage, User
from app.domain.tasks import (
    checkpoint_run_stage,
    complete_run_stage,
    mark_stale_run_stages,
)


@pytest.fixture
def run(db) -> AuditRun:
    user = User.objects.create_user(
        username="worker.analyst",
        password="Worker-test-password-7429!",  # noqa: S106 - test credential
        role=UserRole.ANALYST,
        must_change_password=False,
    )
    client = Client.objects.create(name="Worker Client", slug="worker-client")
    project = Project.objects.create(
        client=client,
        name="Worker Project",
        slug="worker-project",
        primary_domain="example.org",
        approved_domains=["example.org"],
        business_type=Project.BusinessType.SERVICE,
    )
    return AuditRun.objects.create(
        project=project,
        profile=RunProfile.STANDARD,
        state=RunState.COLLECTING,
        idempotency_key="worker-run",
        rule_version="2026.07.1",
        created_by=user,
    )


@pytest.mark.django_db
def test_stage_checkpoint_and_completion_are_idempotent(run: AuditRun) -> None:
    first = checkpoint_run_stage.run(str(run.pk), "collecting", {"cursor": 8}, "attempt-1")
    second = checkpoint_run_stage.run(str(run.pk), "collecting", {"cursor": 8}, "attempt-1")

    stage = RunStage.objects.get(run=run, name="collecting")
    assert first["attempts"] == 1
    assert second["attempts"] == 1
    assert second["idempotent"] is True
    assert stage.checkpoint["data"] == {"cursor": 8}

    completed = complete_run_stage.run(str(run.pk), "collecting", "attempt-1", {"cursor": 9})
    repeated = complete_run_stage.run(str(run.pk), "collecting", "attempt-1", {"cursor": 9})
    stage.refresh_from_db()
    assert completed["idempotent"] is False
    assert repeated["idempotent"] is True
    assert stage.status == StageStatus.SUCCEEDED
    assert stage.checkpoint["data"] == {"cursor": 9}


@pytest.mark.django_db
def test_different_token_cannot_replace_completed_stage(run: AuditRun) -> None:
    checkpoint_run_stage.run(str(run.pk), "collecting", {}, "attempt-1")
    complete_run_stage.run(str(run.pk), "collecting", "attempt-1")

    with pytest.raises(ValueError, match="completed stage"):
        checkpoint_run_stage.run(str(run.pk), "collecting", {}, "attempt-2")


@pytest.mark.django_db
def test_stale_worker_fails_run_with_resumable_checkpoint(run: AuditRun, settings) -> None:
    checkpoint_run_stage.run(str(run.pk), "collecting", {"cursor": 42}, "attempt-1")
    stale_at = timezone.now() - timedelta(minutes=10)
    RunStage.objects.filter(run=run, name="collecting").update(heartbeat_at=stale_at)
    settings.STAGE_HEARTBEAT_TIMEOUT_SECONDS = 60

    result = mark_stale_run_stages.run()

    run.refresh_from_db()
    stage = RunStage.objects.get(run=run, name="collecting")
    assert result == {"checked": 1, "failed": 1}
    assert stage.status == StageStatus.FAILED
    assert stage.checkpoint["data"] == {"cursor": 42}
    assert run.state == RunState.FAILED
    assert run.error_code == "worker_heartbeat_expired"
