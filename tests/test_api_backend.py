# ruff: noqa: S106
import json

from django.test import TestCase

from app.domain.constants import UserRole
from app.domain.models import Artifact, AuditRun, Client, Membership, Project, User


class APISecurityTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username="api-admin",
            password="A-secure-api-admin-password-2026!",
            role=UserRole.AGENCY_ADMIN,
            must_change_password=False,
        )
        self.reviewer = User.objects.create_user(
            username="api-reviewer",
            password="A-secure-api-reviewer-password-2026!",
            role=UserRole.CLIENT_REVIEWER,
            must_change_password=False,
        )
        self.other = User.objects.create_user(
            username="api-outsider",
            password="A-secure-api-outsider-password-2026!",
            role=UserRole.CLIENT_REVIEWER,
            must_change_password=False,
        )
        client = Client.objects.create(name="API Client", slug="api-client")
        self.project = Project.objects.create(
            client=client,
            name="API Project",
            slug="api-project",
            primary_domain="example.com",
            approved_domains=["example.com"],
            business_type=Project.BusinessType.SERVICE,
        )
        Membership.objects.create(
            user=self.reviewer,
            client=client,
            project=self.project,
            access_role=UserRole.CLIENT_REVIEWER,
        )

    def test_project_list_is_scoped(self):
        self.client.force_login(self.reviewer)
        response = self.client.get("/api/v1/projects/", secure=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()), 1)
        self.client.force_login(self.other)
        response = self.client.get("/api/v1/projects/", secure=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [])

    def test_cross_client_detail_is_hidden_as_not_found(self):
        self.client.force_login(self.other)
        response = self.client.get(f"/api/v1/projects/{self.project.pk}/", secure=True)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["error"]["code"], "not_found")

    def test_run_creation_requires_idempotency_key(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            f"/api/v1/projects/{self.project.pk}/runs/",
            data=json.dumps({"profile": "enterprise", "rule_version": "2026.07.1"}),
            content_type="application/json",
            secure=True,
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "validation_error")

    def test_run_creation_replays_idempotently(self):
        self.client.force_login(self.admin)
        headers = {"HTTP_IDEMPOTENCY_KEY": "api-request-1"}
        url = f"/api/v1/projects/{self.project.pk}/runs/"
        body = json.dumps({"profile": "enterprise", "rule_version": "2026.07.1"})
        first = self.client.post(
            url, data=body, content_type="application/json", secure=True, **headers
        )
        second = self.client.post(
            url, data=body, content_type="application/json", secure=True, **headers
        )
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.json()["id"], second.json()["id"])

    def test_client_cannot_download_unapproved_artifact(self):
        run = AuditRun.objects.create(
            project=self.project,
            profile="enterprise",
            idempotency_key="artifact-run",
            rule_version="2026.07.1",
            created_by=self.admin,
        )
        artifact = Artifact.objects.create(
            run=run,
            created_by=self.admin,
            artifact_type="executive_report",
            title="Draft report",
            format="pdf",
            storage_key="private/draft.pdf",
            sha256="a" * 64,
            media_type="application/pdf",
            approval_required=False,
            review_status="draft",
        )
        self.client.force_login(self.reviewer)
        response = self.client.get(f"/api/v1/artifacts/{artifact.pk}/download/", secure=True)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["error"]["code"], "not_found")

    def test_health_and_readiness_are_machine_readable(self):
        self.assertEqual(self.client.get("/healthz/", secure=True).json()["status"], "ok")
        self.assertIn(
            self.client.get("/readyz/", secure=True).json()["status"], {"ready", "not_ready"}
        )
