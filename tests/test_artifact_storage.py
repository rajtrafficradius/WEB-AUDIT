from __future__ import annotations

from io import BytesIO

import pytest

from app.domain.constants import RunProfile, UserRole
from app.domain.models import AuditRun, Client, Project, User
from app.domain.storage import (
    ArtifactIntegrityError,
    open_verified_artifact,
    save_artifact_bytes,
)


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
        if name not in self.objects:
            raise FileNotFoundError(name)
        return BytesIO(self.objects[name])


@pytest.fixture
def run(db) -> AuditRun:
    user = User.objects.create_user(
        username="analyst",
        password="A-secure-test-password-448!",  # noqa: S106 - test credential
        role=UserRole.ANALYST,
        must_change_password=False,
    )
    client = Client.objects.create(name="Kakawa", slug="kakawa")
    project = Project.objects.create(
        client=client,
        name="Enterprise SEO",
        slug="enterprise-seo",
        primary_domain="kakawachocolates.com.au",
        approved_domains=["kakawachocolates.com.au"],
        business_type=Project.BusinessType.ECOMMERCE,
    )
    return AuditRun.objects.create(
        project=project,
        profile=RunProfile.ENTERPRISE,
        idempotency_key="storage-test",
        rule_version="2026.07.1",
        created_by=user,
    )


@pytest.mark.django_db
def test_artifacts_use_tenant_scoped_content_addressed_keys(run: AuditRun, monkeypatch) -> None:
    storage = MemoryStorage()
    monkeypatch.setattr("app.domain.storage.default_storage", storage)

    artifact, created = save_artifact_bytes(
        run=run,
        payload=b"verified package bytes",
        filename="../../client-report.pdf",
        title="Client report",
        artifact_type="executive_report",
        media_type="application/pdf",
        created_by=run.created_by,
    )

    assert created is True
    assert artifact.storage_key.startswith(f"clients/{run.project.client_id}/projects/")
    assert ".." not in artifact.storage_key
    assert artifact.storage_key.endswith(f"{artifact.sha256}.pdf")
    with open_verified_artifact(artifact) as stream:
        assert stream.read() == b"verified package bytes"


@pytest.mark.django_db
def test_same_bytes_are_idempotent(run: AuditRun, monkeypatch) -> None:
    storage = MemoryStorage()
    monkeypatch.setattr("app.domain.storage.default_storage", storage)
    kwargs = {
        "run": run,
        "payload": b"same immutable bytes",
        "filename": "report.pdf",
        "title": "Report",
        "artifact_type": "report",
        "media_type": "application/pdf",
        "created_by": run.created_by,
    }

    first, first_created = save_artifact_bytes(**kwargs)
    second, second_created = save_artifact_bytes(**kwargs)

    assert first_created is True
    assert second_created is False
    assert second.pk == first.pk
    assert len(storage.objects) == 1


@pytest.mark.django_db
def test_corrupted_content_addressed_object_fails_closed(run: AuditRun, monkeypatch) -> None:
    storage = MemoryStorage()
    monkeypatch.setattr("app.domain.storage.default_storage", storage)
    artifact, _ = save_artifact_bytes(
        run=run,
        payload=b"original bytes",
        filename="report.pdf",
        title="Report",
        artifact_type="report",
        media_type="application/pdf",
        created_by=run.created_by,
    )
    storage.objects[artifact.storage_key] = b"tampered bytes"

    with pytest.raises(ArtifactIntegrityError, match="SHA-256"):
        open_verified_artifact(artifact)
