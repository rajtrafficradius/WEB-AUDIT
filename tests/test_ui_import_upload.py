from __future__ import annotations

from io import BytesIO

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from app.domain.constants import AvailabilityStatus, UserRole
from app.domain.models import Client, Membership, Project, SourceImport, User
from integrations.import_service import persist_validated_import


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
            raise ValueError("Test storage is immutable")
        return BytesIO(self.objects[name])


@pytest.fixture
def upload_project(db) -> tuple[User, Project]:
    user = User.objects.create_user(
        username="upload-admin",
        password="Upload-test-password-6632!",  # noqa: S106 - test credential
        role=UserRole.AGENCY_ADMIN,
        must_change_password=False,
    )
    client = Client.objects.create(name="Upload Client", slug="upload-client")
    project = Project.objects.create(
        client=client,
        name="Upload Project",
        slug="upload-project",
        primary_domain="example.org",
        approved_domains=["example.org"],
        business_type=Project.BusinessType.SERVICE,
    )
    return user, project


@pytest.mark.django_db
def test_browser_upload_quarantines_validates_and_deduplicates_csv(
    client, upload_project, monkeypatch, settings, tmp_path
) -> None:
    user, project = upload_project
    storage = MemoryStorage()
    monkeypatch.setattr("integrations.import_service.default_storage", storage)
    settings.MEDIA_ROOT = tmp_path
    client.force_login(user)
    url = reverse("source-upload", args=(project.pk,))

    page = client.get(url, secure=True)
    assert page.status_code == 200
    assert b'multipart/form-data' in page.content
    assert b'name="evidence_file"' in page.content
    assert b"Upload CDX / CDD / XML file here" in page.content
    assert b'value="crawl_data_file"' in page.content
    assert b".cdx,.cdd,.xml" in page.content

    def upload():
        return SimpleUploadedFile(
            "../../safe-evidence.csv",
            b"url,clicks\nhttps://example.org/,10\n",
            content_type="text/csv",
        )

    first = client.post(
        url,
        {"source_type": "ahrefs", "as_of_date": "2026-07-15", "evidence_file": upload()},
        secure=True,
    )
    item = SourceImport.objects.get(project=project)
    assert first.status_code == 302
    assert first.url == reverse("source-import-detail", args=(project.pk, item.pk))
    assert item.status == SourceImport.Status.ACCEPTED
    assert item.availability == AvailabilityStatus.AVAILABLE
    assert item.column_mapping["row_count"] == 1
    assert item.column_mapping["as_of_date"] == "2026-07-15"
    assert item.storage_key.startswith(f"clients/{project.client_id}/projects/{project.pk}/")
    assert ".." not in item.storage_key
    assert storage.objects[item.storage_key].startswith(b"url,clicks")

    second = client.post(
        url,
        {"source_type": "ahrefs", "as_of_date": "2026-07-15", "evidence_file": upload()},
        secure=True,
    )
    assert second.status_code == 302
    assert SourceImport.objects.filter(project=project).count() == 1
    assert len(storage.objects) == 1


@pytest.mark.django_db
def test_browser_upload_accepts_and_persists_xml_crawl_data(
    client, upload_project, monkeypatch, settings, tmp_path
) -> None:
    user, project = upload_project
    storage = MemoryStorage()
    monkeypatch.setattr("integrations.import_service.default_storage", storage)
    settings.MEDIA_ROOT = tmp_path
    client.force_login(user)

    response = client.post(
        reverse("source-upload", args=(project.pk,)),
        {
            "source_type": "crawl_data_file",
            "as_of_date": "2026-07-16",
            "evidence_file": SimpleUploadedFile(
                "crawl.xml",
                (
                    b"<?xml version='1.0' encoding='UTF-8'?>"
                    b"<urlset><url><loc>https://example.org/</loc><status>200</status></url>"
                    b"<url><loc>https://example.org/about/</loc><status>200</status></url></urlset>"
                ),
                content_type="application/xml",
            ),
        },
        secure=True,
    )

    item = SourceImport.objects.get(project=project)
    assert response.status_code == 302
    assert item.source_type == "crawl_data_file"
    assert item.schema_version == "imports-1.1"
    assert item.media_type == "application/xml"
    assert item.storage_key.endswith(".xml")
    assert item.column_mapping["row_count"] == 2
    assert item.column_mapping["media_type"] == "application/xml"
    assert storage.objects[item.storage_key].startswith(b"<?xml")


@pytest.mark.django_db
def test_browser_upload_rejects_mismatched_crawl_source_extension(
    client, upload_project, monkeypatch, settings, tmp_path
) -> None:
    user, project = upload_project
    storage = MemoryStorage()
    monkeypatch.setattr("integrations.import_service.default_storage", storage)
    settings.MEDIA_ROOT = tmp_path
    client.force_login(user)

    response = client.post(
        reverse("source-upload", args=(project.pk,)),
        {
            "source_type": "crawl_data_file",
            "evidence_file": SimpleUploadedFile(
                "crawl.csv", b"url,status\nhttps://example.org/,200\n", content_type="text/csv"
            ),
        },
        secure=True,
    )

    item = SourceImport.objects.get(project=project)
    assert response.status_code == 400
    assert item.validation_issues[0]["code"] == "source_file_type"
    assert storage.objects == {}

@pytest.mark.django_db
def test_browser_upload_rejects_formula_without_storing_payload(
    client, upload_project, monkeypatch, settings, tmp_path
) -> None:
    user, project = upload_project
    storage = MemoryStorage()
    monkeypatch.setattr("integrations.import_service.default_storage", storage)
    settings.MEDIA_ROOT = tmp_path
    client.force_login(user)

    response = client.post(
        reverse("source-upload", args=(project.pk,)),
        {
            "source_type": "mapped_csv_xlsx",
            "evidence_file": SimpleUploadedFile(
                "unsafe.csv", b"name,value\nsafe,=2+2\n", content_type="text/csv"
            ),
        },
        secure=True,
    )

    item = SourceImport.objects.get(project=project)
    assert response.status_code == 400
    assert b"Spreadsheet formulas" in response.content
    assert b'aria-invalid="true"' in response.content
    assert b'id="id_evidence_file_error"' in response.content
    assert item.status == SourceImport.Status.REJECTED
    assert item.availability == AvailabilityStatus.ERROR
    assert item.storage_key == ""
    assert item.validation_issues[0]["code"] == "formula"
    assert storage.objects == {}


@pytest.mark.django_db
def test_browser_upload_rejects_wrong_source_schema_without_storage(
    client, upload_project, monkeypatch, settings, tmp_path
) -> None:
    user, project = upload_project
    storage = MemoryStorage()
    monkeypatch.setattr("integrations.import_service.default_storage", storage)
    settings.MEDIA_ROOT = tmp_path
    client.force_login(user)

    response = client.post(
        reverse("source-upload", args=(project.pk,)),
        {
            "source_type": "ahrefs",
            "evidence_file": SimpleUploadedFile(
                "not-ahrefs.csv", b"name,value\nsafe,10\n", content_type="text/csv"
            ),
        },
        secure=True,
    )

    item = SourceImport.objects.get(project=project)
    assert response.status_code == 400
    assert item.status == SourceImport.Status.REJECTED
    assert item.validation_issues[0]["code"] == "source_schema"
    assert storage.objects == {}


@pytest.mark.django_db
def test_client_reviewer_cannot_bypass_upload_authorization(upload_project) -> None:
    _, project = upload_project
    reviewer = User.objects.create_user(
        username="upload-reviewer",
        password="Upload-reviewer-password-8841!",  # noqa: S106 - test credential
        role=UserRole.CLIENT_REVIEWER,
        must_change_password=False,
    )
    Membership.objects.create(
        user=reviewer,
        client=project.client,
        project=project,
        access_role=UserRole.CLIENT_REVIEWER,
    )

    with pytest.raises(PermissionError, match="Project management permission"):
        persist_validated_import(
            project=project,
            actor=reviewer,
            source_type="ahrefs",
            uploaded=SimpleUploadedFile("evidence.csv", b"url\nhttps://example.org/\n"),
        )

    assert SourceImport.objects.filter(project=project).count() == 0


@pytest.mark.django_db
def test_reviewer_scoped_analyst_cannot_manage_imports(upload_project) -> None:
    _, project = upload_project
    analyst = User.objects.create_user(
        username="scoped-analyst",
        password="Scoped-analyst-password-1844!",  # noqa: S106 - test credential
        role=UserRole.ANALYST,
        must_change_password=False,
    )
    Membership.objects.create(
        user=analyst,
        client=project.client,
        project=project,
        access_role=UserRole.CLIENT_REVIEWER,
    )

    with pytest.raises(PermissionError, match="Project management permission"):
        persist_validated_import(
            project=project,
            actor=analyst,
            source_type="ahrefs",
            uploaded=SimpleUploadedFile("evidence.csv", b"url\nhttps://example.org/\n"),
        )

    assert SourceImport.objects.filter(project=project).count() == 0
