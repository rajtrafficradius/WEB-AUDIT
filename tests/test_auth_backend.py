# ruff: noqa: S106
import json
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.hashers import identify_hasher
from django.test import Client as WebClient
from django.test import TestCase
from django.utils import timezone

from app.domain.constants import UserRole
from app.domain.models import AuditEvent, User
from app.domain.services import issue_temporary_password


class AuthenticationTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username="agency-admin",
            password="A-secure-initial-password-2026!",
            role=UserRole.AGENCY_ADMIN,
            must_change_password=False,
        )
        self.user = User.objects.create_user(
            username="analyst-one",
            password="A-secure-analyst-password-2026!",
            role=UserRole.ANALYST,
            must_change_password=False,
        )

    def test_argon2_is_primary_password_hasher(self):
        self.assertEqual(
            settings.PASSWORD_HASHERS[0], "django.contrib.auth.hashers.Argon2PasswordHasher"
        )
        self.assertEqual(identify_hasher(self.user.password).algorithm, "argon2")

    def test_admin_reset_issues_expiring_temporary_password_and_audits(self):
        value = issue_temporary_password(target=self.user, issued_by=self.admin, valid_minutes=30)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(value))
        self.assertTrue(self.user.must_change_password)
        self.assertGreater(self.user.temporary_password_expires_at, timezone.now())
        self.assertTrue(
            AuditEvent.objects.filter(
                event_type="auth.temporary_password_issued", actor=self.admin
            ).exists()
        )

    def test_non_admin_cannot_reset_password(self):
        with self.assertRaises(PermissionError):
            issue_temporary_password(target=self.admin, issued_by=self.user)

    def test_expired_temporary_password_is_rejected_without_account_disclosure(self):
        self.user.temporary_password_expires_at = timezone.now() - timedelta(minutes=1)
        self.user.save(update_fields=["temporary_password_expires_at"])
        response = self.client.post(
            "/auth/login/",
            {"username": self.user.username, "password": "A-secure-analyst-password-2026!"},
            secure=True,
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"]["code"], "invalid_credentials")

    def test_csrf_is_required_for_session_login(self):
        client = WebClient(enforce_csrf_checks=True)
        response = client.post(
            "/auth/login/",
            {"username": self.user.username, "password": "A-secure-analyst-password-2026!"},
            secure=True,
        )
        self.assertEqual(response.status_code, 403)

    def test_admin_can_issue_temporary_password_through_api(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            f"/api/v1/users/{self.user.pk}/temporary-password/",
            data=json.dumps({"valid_minutes": 20}),
            content_type="application/json",
            secure=True,
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(payload["temporary_password"]))
        self.assertTrue(self.user.must_change_password)

    def test_non_admin_cannot_issue_temporary_password_through_api(self):
        self.client.force_login(self.user)
        response = self.client.post(
            f"/api/v1/users/{self.admin.pk}/temporary-password/",
            data=json.dumps({"valid_minutes": 20}),
            content_type="application/json",
            secure=True,
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"]["code"], "forbidden")

    def test_forced_password_change_blocks_data_routes(self):
        self.user.must_change_password = True
        self.user.save(update_fields=["must_change_password"])
        self.client.force_login(self.user)
        response = self.client.get("/api/v1/projects/", secure=True)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"]["code"], "password_change_required")

    def test_forced_password_change_redirects_browser_to_form(self):
        self.user.must_change_password = True
        self.user.save(update_fields=["must_change_password"])
        self.client.force_login(self.user)

        response = self.client.get("/", HTTP_ACCEPT="text/html", secure=True)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "/auth/change-password/?next=%2F")

    def test_browser_can_open_and_complete_password_change(self):
        self.user.must_change_password = True
        self.user.temporary_password_expires_at = timezone.now() + timedelta(minutes=30)
        self.user.save(update_fields=["must_change_password", "temporary_password_expires_at"])
        self.client.force_login(self.user)

        page = self.client.get(
            "/auth/change-password/?next=%2F", HTTP_ACCEPT="text/html", secure=True
        )
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "Set your permanent password")
        self.assertContains(page, 'name="current_password"')
        self.assertContains(page, 'name="confirm_password"')

        response = self.client.post(
            "/auth/change-password/?next=%2F",
            {
                "current_password": "A-secure-analyst-password-2026!",
                "new_password": "A-different-permanent-password-2026!",
                "confirm_password": "A-different-permanent-password-2026!",
                "next": "/",
            },
            HTTP_ACCEPT="text/html",
            secure=True,
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "/")
        self.user.refresh_from_db()
        self.assertFalse(self.user.must_change_password)
        self.assertTrue(self.user.check_password("A-different-permanent-password-2026!"))
