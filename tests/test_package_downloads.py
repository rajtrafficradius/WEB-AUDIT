"""Downloadable history of past run packages."""

from __future__ import annotations

import hashlib

import pytest
from django.core.files.base import ContentFile
from django.urls import reverse

from app.domain import storage as domain_storage
from app.domain.constants import RunState, UserRole
from app.domain.models import Artifact, AuditRun, Client, Project, User

PASSWORD = "Download-test-password-1!"  # noqa: S105 - test credential


@pytest.fixture
def seeded(db):
    user = User.objects.create_user(
        username="dl-admin", password=PASSWORD, role=UserRole.AGENCY_ADMIN,
        must_change_password=False,
    )
    client = Client.objects.create(name="Harbour Lane Ceramics", slug="harbour-lane")
    project = Project.objects.create(
        client=client, name="Harbour Lane SEO", slug="harbour-lane",
        primary_domain="harbourlane.com.au", approved_domains=["harbourlane.com.au"],
        business_type=Project.BusinessType.ECOMMERCE,
    )
    return user, project


def _package_artifact(project, *, version, payload):
    run = AuditRun.objects.create(
        project=project, profile="quick", idempotency_key=f"run-{version}",
        rule_version="1.1.0", state=RunState.GATE_1_REVIEW,
    )
    key = domain_storage.default_storage.save(
        f"clients/{project.client_id}/pkg-{version}.zip", ContentFile(payload)
    )
    return Artifact.objects.create(
        run=run, artifact_type="package", title=f"Package v{version}",
        format="zip", storage_key=key, sha256=hashlib.sha256(payload).hexdigest(),
        size_bytes=len(payload),
        media_type="application/zip",
        metadata={"run_version": version, "package_name": f"Harbour_Lane_v{version}",
                  "file_count": 60},
    )


@pytest.mark.django_db
def test_every_past_package_is_listed_and_downloadable(client, seeded):
    user, project = seeded
    a1 = _package_artifact(project, version=1, payload=b"first-package-bytes")
    a2 = _package_artifact(project, version=2, payload=b"second-package-bytes")

    assert client.login(username="dl-admin", password=PASSWORD)
    detail = client.get(reverse("project-detail", args=(project.pk,)), secure=True)
    body = detail.content.decode("utf-8")
    assert "Report downloads" in body
    assert "Harbour_Lane_v1" in body
    assert "Harbour_Lane_v2" in body

    for artifact, expected in ((a1, b"first-package-bytes"), (a2, b"second-package-bytes")):
        response = client.get(
            reverse("package-download", args=(project.pk, artifact.pk)), secure=True
        )
        assert response.status_code == 200
        assert response["Content-Disposition"].startswith("attachment;")
        assert b"".join(response.streaming_content) == expected


@pytest.mark.django_db
def test_download_is_scoped_to_the_owning_project(client, seeded):
    user, project = seeded
    artifact = _package_artifact(project, version=1, payload=b"scoped")
    other_client = Client.objects.create(name="Other Co", slug="other")
    other = Project.objects.create(
        client=other_client, name="Other SEO", slug="other",
        primary_domain="other.com.au", approved_domains=["other.com.au"],
        business_type=Project.BusinessType.ECOMMERCE,
    )
    assert client.login(username="dl-admin", password=PASSWORD)
    # The artifact belongs to `project`, not `other` — the route must 404.
    response = client.get(
        reverse("package-download", args=(other.pk, artifact.pk)), secure=True
    )
    assert response.status_code == 404


@pytest.mark.django_db
def test_missing_bytes_are_not_offered(client, seeded, monkeypatch):
    user, project = seeded
    _package_artifact(project, version=1, payload=b"present")
    # Simulate an ephemeral-storage wipe: the row exists but the object does not.
    monkeypatch.setattr(
        "app.views.artifact_bytes_available", lambda artifact: False
    )
    assert client.login(username="dl-admin", password=PASSWORD)
    detail = client.get(reverse("project-detail", args=(project.pk,)), secure=True)
    body = detail.content.decode("utf-8")
    assert "No report packages yet" in body
