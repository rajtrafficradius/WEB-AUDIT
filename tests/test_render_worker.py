from __future__ import annotations

from io import BytesIO

import pytest
from django.conf import settings

from app.domain.constants import RunProfile, UserRole
from app.domain.models import Artifact, AuditRun, Client, Project, User
from exporters.tasks import render_run_summary_html


class MemoryStorage:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def exists(self, name: str) -> bool:
        return name in self.objects

    def save(self, name: str, content) -> str:
        self.objects[name] = content.read()
        return name

    def open(self, name: str, mode: str = "rb") -> BytesIO:
        if mode != "rb":
            raise ValueError("Test storage is read-only after save")
        return BytesIO(self.objects[name])


@pytest.fixture
def run(db) -> AuditRun:
    user = User.objects.create_user(
        username="render.analyst",
        password="Render-test-password-4492!",  # noqa: S106 - test credential
        role=UserRole.ANALYST,
        must_change_password=False,
    )
    client = Client.objects.create(name="Render Client", slug="render-client")
    project = Project.objects.create(
        client=client,
        name="Unsafe <script>alert(1)</script> Project",
        slug="render-project",
        primary_domain="example.org",
        approved_domains=["example.org"],
        business_type=Project.BusinessType.SERVICE,
    )
    return AuditRun.objects.create(
        project=project,
        profile=RunProfile.STANDARD,
        idempotency_key="render-run",
        rule_version="2026.07.1",
        created_by=user,
    )


@pytest.mark.django_db
def test_render_worker_persists_escaped_content_addressed_html(run: AuditRun, monkeypatch) -> None:
    storage = MemoryStorage()
    monkeypatch.setattr("app.domain.storage.default_storage", storage)

    first = render_run_summary_html.run(str(run.pk), "summary-1")
    second = render_run_summary_html.run(str(run.pk), "summary-1")

    artifact = Artifact.objects.get(pk=first["artifact_id"])
    document = storage.objects[artifact.storage_key].decode("utf-8")
    assert first["idempotent"] is False
    assert second["idempotent"] is True
    assert second["artifact_id"] == first["artifact_id"]
    assert artifact.storage_key.startswith(f"clients/{run.project.client_id}/projects/")
    assert "<script>alert(1)</script>" not in document
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in document
    assert '<meta name="robots" content="noindex,nofollow,noarchive">' in document
    assert '<th scope="col">Severity</th>' in document


@pytest.mark.django_db
def test_render_worker_rejects_cross_tenant_requester(run: AuditRun, monkeypatch) -> None:
    monkeypatch.setattr("app.domain.storage.default_storage", MemoryStorage())
    outsider = User.objects.create_user(
        username="render.outsider",
        password="Render-outsider-password-7712!",  # noqa: S106 - test credential
        role=UserRole.CLIENT_REVIEWER,
        must_change_password=False,
    )

    with pytest.raises(PermissionError, match="cannot access"):
        render_run_summary_html.run(str(run.pk), "summary-outsider", str(outsider.pk))


def test_render_and_scheduler_tasks_have_explicit_routes() -> None:
    assert settings.CELERY_TASK_ROUTES["studio.render.*"] == {"queue": "render"}
    assert settings.CELERY_TASK_ROUTES["studio.scheduler.*"] == {"queue": "analysis"}
    schedule = settings.CELERY_BEAT_SCHEDULE["mark-stale-run-stages"]
    assert schedule["task"] == "studio.scheduler.mark_stale_stages"
    assert schedule["options"]["queue"] == "analysis"
