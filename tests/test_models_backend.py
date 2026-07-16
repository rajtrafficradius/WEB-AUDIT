# ruff: noqa: S106
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from app.domain.constants import AvailabilityStatus, RunProfile, UserRole
from app.domain.models import AuditEvent, AuditRun, Client, MetricObservation, Project, User


class CanonicalModelTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="model-admin",
            password="A-secure-model-password-2026!",
            role=UserRole.AGENCY_ADMIN,
            must_change_password=False,
        )
        self.client_org = Client.objects.create(name="Kakawa Chocolates", slug="kakawa")
        self.project = Project.objects.create(
            client=self.client_org,
            name="Enterprise SEO",
            slug="enterprise-seo",
            primary_domain="kakawachocolates.com.au",
            approved_domains=["kakawachocolates.com.au"],
            business_type=Project.BusinessType.ECOMMERCE,
        )

    def test_primary_domain_must_be_approved(self):
        project = Project(
            client=self.client_org,
            name="Invalid",
            slug="invalid",
            primary_domain="example.com",
            approved_domains=["example.org"],
            business_type=Project.BusinessType.SERVICE,
        )
        with self.assertRaises(ValidationError):
            project.full_clean()

    def test_health_score_requires_seventy_percent_coverage(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            AuditRun.objects.create(
                project=self.project,
                profile=RunProfile.ENTERPRISE,
                idempotency_key="invalid-score",
                rule_version="2026.07.1",
                created_by=self.user,
                evidence_coverage=Decimal("69.99"),
                health_score=Decimal("80"),
            )

    def test_metric_requires_value_or_explicit_unavailable_state(self):
        run = AuditRun.objects.create(
            project=self.project,
            profile=RunProfile.ENTERPRISE,
            idempotency_key="metric-run",
            rule_version="2026.07.1",
            created_by=self.user,
        )
        with self.assertRaises(IntegrityError), transaction.atomic():
            MetricObservation.objects.create(
                run=run, metric_key="organic_clicks", availability=AvailabilityStatus.AVAILABLE
            )
        with self.assertRaises(IntegrityError), transaction.atomic():
            MetricObservation.objects.create(
                run=run,
                metric_key="organic_clicks",
                availability=AvailabilityStatus.UNAVAILABLE,
            )
        metric = MetricObservation.objects.create(
            run=run,
            metric_key="organic_clicks",
            availability=AvailabilityStatus.UNAVAILABLE,
            unavailable_reason="Google Search Console is not connected.",
        )
        self.assertEqual(metric.availability, AvailabilityStatus.UNAVAILABLE)

    def test_audit_event_is_immutable(self):
        event = AuditEvent.objects.create(
            actor=self.user, project=self.project, event_type="test.created"
        )
        event.event_type = "test.changed"
        with self.assertRaises(ValidationError):
            event.save()
        with self.assertRaises(ValidationError):
            event.delete()
