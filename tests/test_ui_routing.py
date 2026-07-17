# ruff: noqa: S106
from decimal import Decimal
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from app.domain.constants import (
    ApprovalDecision,
    ApprovalGate,
    AvailabilityStatus,
    ReviewStatus,
    RunState,
    Severity,
    UserRole,
)
from app.domain.models import (
    ActionItem,
    Approval,
    AuditEvent,
    AuditRun,
    ClaimLedger,
    Client,
    Connection,
    ContentBrief,
    ContentDraft,
    Evidence,
    Finding,
    Membership,
    PackageManifest,
    Project,
    QAResult,
    SourceImport,
    User,
)
from generation.openai_boundary import GenerationPurpose, GenerationStatus

TEST_STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}


@override_settings(STORAGES=TEST_STORAGES)
class UIRoutingTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username="ui-admin",
            password="A-secure-ui-admin-password-2026!",
            role=UserRole.AGENCY_ADMIN,
            must_change_password=False,
        )
        self.reviewer = User.objects.create_user(
            username="ui-reviewer",
            password="A-secure-ui-reviewer-password-2026!",
            role=UserRole.CLIENT_REVIEWER,
            must_change_password=False,
        )
        self.outsider = User.objects.create_user(
            username="ui-outsider",
            password="A-secure-ui-outsider-password-2026!",
            role=UserRole.CLIENT_REVIEWER,
            must_change_password=False,
        )
        self.client_org = Client.objects.create(name="UI Client", slug="ui-client")
        self.project = Project.objects.create(
            client=self.client_org,
            name="UI Project",
            slug="ui-project",
            primary_domain="example.com",
            approved_domains=["example.com"],
            business_type=Project.BusinessType.SERVICE,
        )
        Membership.objects.create(
            user=self.reviewer,
            client=self.client_org,
            project=self.project,
            access_role=UserRole.CLIENT_REVIEWER,
        )
        self.run = AuditRun.objects.create(
            project=self.project,
            profile="enterprise",
            state=RunState.DRAFT,
            idempotency_key="ui-run",
            rule_version="2026.07.1",
            created_by=self.admin,
            evidence_coverage=Decimal("72.00"),
        )
        self.connection = Connection.objects.create(
            project=self.project,
            provider=Connection.Provider.GSC,
            label="Primary",
            availability=AvailabilityStatus.UNAVAILABLE,
            unavailable_reason="Credentials are not connected.",
        )
        self.source_import = SourceImport.objects.create(
            project=self.project,
            created_by=self.admin,
            source_type="ahrefs",
            original_filename="links.csv",
            media_type="text/csv",
            size_bytes=12,
            sha256="a" * 64,
            storage_key="private/imports/a.csv",
            status=SourceImport.Status.ACCEPTED,
            availability=AvailabilityStatus.AVAILABLE,
        )
        self.evidence = Evidence.objects.create(
            run=self.run,
            evidence_type="crawl",
            title="Crawl evidence",
            excerpt="Observed source response.",
            availability=AvailabilityStatus.AVAILABLE,
            confidence=Decimal("0.90"),
        )
        self.finding = Finding.objects.create(
            run=self.run,
            category="technical",
            code="TECH-001",
            title="Example finding",
            description="A supported technical finding.",
            severity=Severity.HIGH,
            affected_count=1,
            affected_share=Decimal("0.25"),
            score_penalty=Decimal("5"),
            confidence=Decimal("0.90"),
            rule_version="2026.07.1",
        )
        self.finding.evidence.add(self.evidence)
        self.action = ActionItem.objects.create(
            run=self.run,
            title="Fix the issue",
            description="Implement the evidence-backed correction.",
            week=1,
            owner_label="SEO lead",
            impact=Decimal("80"),
            evidence_confidence=Decimal("90"),
            reach=Decimal("50"),
            business_criticality=Decimal("70"),
            dependency_urgency=Decimal("60"),
            effort=Decimal("40"),
            priority_score=Decimal("72"),
            priority_tier="P1",
        )
        self.approval = Approval.objects.create(
            run=self.run,
            gate=ApprovalGate.GATE_1,
            decision=ApprovalDecision.PENDING,
            requested_by=self.admin,
        )
        self.brief = ContentBrief.objects.create(
            run=self.run,
            title="Evidence-backed guide",
            slug="evidence-backed-guide",
            target_url="https://example.com/guide",
            primary_keyword="example guide",
            search_intent="informational",
        )
        self.draft = ContentDraft.objects.create(
            brief=self.brief,
            version=1,
            body="A concise, evidence-backed draft.",
            review_status=ReviewStatus.IN_REVIEW,
        )
        self.claim = ClaimLedger.objects.create(
            draft=self.draft,
            claim_text="The crawl observed the page.",
            status=ClaimLedger.ClaimStatus.SUPPORTED,
        )
        self.claim.evidence.add(self.evidence)
        QAResult.objects.create(
            run=self.run,
            check_code="domain-safety",
            check_version="1",
            severity=Severity.HIGH,
            status=QAResult.Status.PASS,
            message="No wrong-domain URLs.",
        )
        self.manifest = PackageManifest.objects.create(
            run=self.run,
            version=1,
            manifest={},
            manifest_sha256="b" * 64,
            generated_by=self.admin,
        )

    def test_login_page_and_auth_redirect_are_reachable(self):
        response = self.client.get(reverse("login"), secure=True)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "registration/login.html")
        response = self.client.get(reverse("dashboard"), follow=True, secure=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.redirect_chain[0][0], "/auth/login/?next=/")

    def test_browser_login_rejects_external_next_and_preserves_json_contract(self):
        response = self.client.post(
            reverse("login"),
            {
                "username": self.admin.username,
                "password": "A-secure-ui-admin-password-2026!",
                "next": "https://attacker.example/steal",
            },
            HTTP_ACCEPT="text/html",
            secure=True,
        )
        self.assertRedirects(response, reverse("dashboard"), fetch_redirect_response=False)
        self.client.logout()
        response = self.client.post(
            reverse("login"),
            {
                "username": self.admin.username,
                "password": "A-secure-ui-admin-password-2026!",
            },
            secure=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["user"]["role"], UserRole.AGENCY_ADMIN)

    def test_all_template_route_names_reverse(self):
        one_arg = {
            "project-detail",
            "project-intake",
            "project-sources",
            "source-connect",
            "source-upload",
            "project-findings",
            "action-plan",
            "action-create",
            "project-approvals",
            "content-list",
            "export-qa",
            "export-build",
            "audit-results-download",
        }
        two_args = {
            "source-detail",
            "source-refresh",
            "source-import-detail",
            "finding-detail",
            "action-detail",
            "approval-decide",
            "content-approve",
            "content-revision",
            "export-download",
        }
        no_args = {"dashboard", "project-list", "project-create", "login", "logout"}
        run_names = {"run-detail", "run-cancel", "run-resume"}
        for name in no_args:
            self.assertTrue(reverse(name).startswith("/"))
        for name in one_arg:
            self.assertTrue(reverse(name, args=(uuid4(),)).startswith("/"))
        for name in two_args:
            self.assertTrue(reverse(name, args=(uuid4(), uuid4())).startswith("/"))
        for name in run_names:
            self.assertTrue(reverse(name, args=(uuid4(),)).startswith("/"))

    def test_authorized_get_routes_render_without_placeholder_route_fallbacks(self):
        self.client.force_login(self.admin)
        urls = (
            reverse("dashboard"),
            reverse("project-list"),
            reverse("project-create"),
            reverse("project-detail", args=(self.project.pk,)),
            reverse("project-intake", args=(self.project.pk,)),
            reverse("project-sources", args=(self.project.pk,)),
            reverse("source-connect", args=(self.project.pk,)),
            reverse("source-upload", args=(self.project.pk,)),
            reverse("source-detail", args=(self.project.pk, self.connection.pk)),
            reverse("source-import-detail", args=(self.project.pk, self.source_import.pk)),
            reverse("project-findings", args=(self.project.pk,)),
            reverse("finding-detail", args=(self.project.pk, self.finding.pk)),
            reverse("action-plan", args=(self.project.pk,)),
            reverse("action-create", args=(self.project.pk,)),
            reverse("action-detail", args=(self.project.pk, self.action.pk)),
            reverse("project-approvals", args=(self.project.pk,)),
            reverse("content-detail", args=(self.project.pk, self.draft.pk)),
            reverse("run-detail", args=(self.run.pk,)),
            reverse("export-qa", args=(self.project.pk,)),
        )
        for url in urls:
            with self.subTest(url=url):
                response = self.client.get(url, secure=True)
                self.assertEqual(response.status_code, 200)
                self.assertNotContains(response, 'href="#"')
                self.assertNotContains(response, 'action="#"')

    def test_cross_client_project_and_child_routes_are_hidden(self):
        self.client.force_login(self.outsider)
        urls = (
            reverse("project-detail", args=(self.project.pk,)),
            reverse("source-detail", args=(self.project.pk, self.connection.pk)),
            reverse("finding-detail", args=(self.project.pk, self.finding.pk)),
            reverse("action-detail", args=(self.project.pk, self.action.pk)),
            reverse("content-detail", args=(self.project.pk, self.draft.pk)),
            reverse("run-detail", args=(self.run.pk,)),
            reverse("audit-results-download", args=(self.project.pk,)),
        )
        for url in urls:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url, secure=True).status_code, 404)

    def _seed_package_artifact(self):
        from django.core.files.base import ContentFile

        from app.domain import storage as domain_storage
        from app.domain.models import Artifact

        payload = b"zip-bytes"
        key = domain_storage.default_storage.save(
            f"clients/{self.project.client_id}/package.zip", ContentFile(payload)
        )
        self.addCleanup(domain_storage.default_storage.delete, key)
        return Artifact.objects.create(
            run=self.run,
            artifact_type="package",
            title=f"{self.project.client.name} SEO audit package",
            format="zip",
            storage_key=key,
            sha256="a" * 64,
            size_bytes=len(payload),
            media_type="application/zip",
            metadata={"run_version": self.run.version},
        )

    def test_project_detail_hides_download_until_the_package_exists(self):
        self.client.force_login(self.admin)
        url = reverse("project-detail", args=(self.project.pk,))

        response = self.client.get(url, secure=True)
        self.assertContains(response, "Short summary below.")
        self.assertNotContains(response, "Download audit results")

        self._seed_package_artifact()
        response = self.client.get(url, secure=True)
        self.assertContains(response, "Download audit results")

    def test_download_without_package_redirects_instead_of_serving_html(self):
        self.client.force_login(self.admin)
        response = self.client.get(
            reverse("audit-results-download", args=(self.project.pk,)), secure=True
        )
        self.assertEqual(response.status_code, 302)
        self.assertIn(
            reverse("project-detail", args=(self.project.pk,)), response["Location"]
        )

    def test_client_reviewer_without_approved_package_is_redirected(self):
        self.client.force_login(self.reviewer)
        self._seed_package_artifact()  # review_status stays draft
        response = self.client.get(
            reverse("audit-results-download", args=(self.project.pk,)), secure=True
        )
        self.assertEqual(response.status_code, 302)

    def test_mutation_routes_reject_get(self):
        self.client.force_login(self.admin)
        urls = (
            reverse("source-refresh", args=(self.project.pk, self.connection.pk)),
            reverse("approval-decide", args=(self.project.pk, self.approval.pk)),
            reverse("content-approve", args=(self.project.pk, self.draft.pk)),
            reverse("content-revision", args=(self.project.pk, self.draft.pk)),
            reverse("run-cancel", args=(self.run.pk,)),
            reverse("run-resume", args=(self.run.pk,)),
            reverse("export-build", args=(self.project.pk,)),
        )
        for url in urls:
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url, secure=True).status_code, 405)

    def test_approval_requires_explicit_no_publish_acknowledgement(self):
        self.client.force_login(self.admin)
        url = reverse("approval-decide", args=(self.project.pk, self.approval.pk))
        self.client.post(
            url,
            {"decision": "approved", "note": "Reviewed against the evidence."},
            secure=True,
        )
        self.approval.refresh_from_db()
        self.assertEqual(self.approval.decision, ApprovalDecision.PENDING)
        self.client.post(
            url,
            {
                "decision": "approved",
                "note": "Reviewed against the evidence.",
                "acknowledge_no_publish": "yes",
            },
            secure=True,
        )
        self.approval.refresh_from_db()
        self.assertEqual(self.approval.decision, ApprovalDecision.APPROVED)

    def test_content_decision_and_run_cancel_use_posted_scoped_mutations(self):
        self.client.force_login(self.reviewer)
        self.client.post(
            reverse("content-approve", args=(self.project.pk, self.draft.pk)), secure=True
        )
        self.draft.refresh_from_db()
        self.assertEqual(self.draft.review_status, ReviewStatus.APPROVED)
        self.client.force_login(self.admin)
        self.client.post(reverse("run-cancel", args=(self.run.pk,)), secure=True)
        self.run.refresh_from_db()
        self.assertEqual(self.run.state, RunState.CANCELLED)


@override_settings(STORAGES=TEST_STORAGES, OPENAI_INTAKE_GENERATION_ENABLED=False)
class ProjectCreationRoutingTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username="create-admin",
            password="A-secure-create-admin-password-2026!",
            role=UserRole.AGENCY_ADMIN,
            must_change_password=False,
        )
        self.client.force_login(self.admin)

    def test_project_create_places_crawl_upload_after_required_fields(self):
        response = self.client.get(reverse("project-create"), secure=True)
        self.assertEqual(response.status_code, 200)
        html = response.content.decode("utf-8")
        for field_name in (
            "client_name",
            "primary_domain",
            "business_type",
            "business_summary",
            "crawl_data_file",
        ):
            self.assertIn(f'name="{field_name}"', html)
        for field_name in (
            "name", "conversion_goals", "locale", "approved_domains", "cms_platform",
            "primary_market", "priority_offerings", "competitors", "verified_facts",
            "prohibited_claims", "brand_voice", "review_owner",
        ):
            self.assertNotIn(f'name="{field_name}"', html)
        self.assertNotIn('<details class="advanced-intake"', html)
        self.assertIn('enctype="multipart/form-data"', html)
        self.assertIn("Upload CDX / CDD / XML file here", html)
        self.assertIn("Four details are required; the crawl-data upload is optional.", html)
        self.assertLess(
            html.index('name="business_summary"'),
            html.index('name="crawl_data_file"'),
        )

    def test_project_creation_accepts_only_essential_values_and_locale_default(self):
        response = self.client.post(
            reverse("project-create"),
            {
                "client_name": "Simple Client",
                "primary_domain": "https://simple.example.com.au",
                "business_type": "ecommerce",
                "business_summary": "An Australian online store.",
                "continue": "project",
            },
            secure=True,
        )
        project = Project.objects.get(name="Simple Client SEO Audit")
        self.assertRedirects(response, reverse("project-detail", args=(project.pk,)), fetch_redirect_response=False)
        self.assertEqual(project.approved_domains, ["simple.example.com.au"])
        self.assertEqual(project.locale, "en-AU")
        self.assertEqual(
            project.conversion_goals,
            ["Improve qualified organic visibility and conversions"],
        )

    def test_project_creation_validates_and_links_xml_crawl_data(self):
        with TemporaryDirectory(dir=r"C:\tmp") as media_root, self.settings(
            MEDIA_ROOT=media_root
        ):
            response = self.client.post(
                reverse("project-create"),
                {
                    "client_name": "Crawl Import Client",
                    "primary_domain": "https://crawl-import.example.com.au",
                    "business_type": "ecommerce",
                    "business_summary": "An Australian ecommerce test business.",
                    "crawl_data_file": SimpleUploadedFile(
                        "crawl.xml",
                        (
                            b"<?xml version='1.0' encoding='UTF-8'?>"
                            b"<urlset><url><loc>https://crawl-import.example.com.au/</loc>"
                            b"<status>200</status></url></urlset>"
                        ),
                        content_type="application/xml",
                    ),
                    "continue": "project",
                },
                secure=True,
            )
        project = Project.objects.get(name="Crawl Import Client SEO Audit")
        crawl_import = SourceImport.objects.get(project=project)
        self.assertRedirects(
            response,
            reverse("project-detail", args=(project.pk,)),
            fetch_redirect_response=False,
        )
        self.assertEqual(crawl_import.source_type, "crawl_data_file")
        self.assertEqual(crawl_import.original_filename, "crawl.xml")
        self.assertEqual(crawl_import.status, SourceImport.Status.ACCEPTED)
        self.assertEqual(crawl_import.column_mapping["row_count"], 1)
        event = AuditEvent.objects.get(
            project=project, event_type="source_import.accepted"
        )
        self.assertEqual(event.payload["origin"], "project_setup")

    def test_invalid_setup_crawl_file_does_not_create_project(self):
        with TemporaryDirectory(dir=r"C:\tmp") as media_root, self.settings(
            MEDIA_ROOT=media_root
        ):
            response = self.client.post(
                reverse("project-create"),
                {
                    "client_name": "Rejected Crawl Client",
                    "primary_domain": "https://rejected-crawl.example.com.au",
                    "business_type": "service",
                    "business_summary": "An Australian service test business.",
                    "crawl_data_file": SimpleUploadedFile(
                        "crawl.csv",
                        b"url,status\nhttps://rejected-crawl.example.com.au/,200\n",
                        content_type="text/csv",
                    ),
                    "continue": "project",
                },
                secure=True,
            )
        self.assertEqual(response.status_code, 400)
        self.assertContains(
            response,
            "Choose a CDX, CDD, or XML file for the crawl-data source.",
            status_code=400,
        )
        self.assertFalse(
            Project.objects.filter(name="Rejected Crawl Client SEO Audit").exists()
        )
        self.assertFalse(Client.objects.filter(name="Rejected Crawl Client").exists())
        self.assertFalse(SourceImport.objects.exists())
    def test_project_creation_normalizes_and_persists_domain_allowlist(self):
        response = self.client.post(
            reverse("project-create"),
            {
                "client_name": "New Client",
                "name": "New SEO Project",
                "business_type": "service",
                "locale": "en-AU",
                "business_summary": "A service business.",
                "primary_domain": "https://Example.COM/",
                "approved_domains": "www.example.com\nexample.com",
                "cms_platform": "Django",
                "primary_market": "Australia",
                "conversion_goals": "Qualified enquiry",
                "priority_offerings": "Consulting",
                "competitors": "competitor.example",
                "verified_facts": "Fact with provenance",
                "prohibited_claims": "Unverified superlatives",
                "brand_voice": "Precise",
                "review_owner": "Owner",
                "continue": "project",
            },
            secure=True,
        )
        project = Project.objects.get(name="New SEO Project")
        self.assertRedirects(
            response,
            reverse("project-detail", args=(project.pk,)),
            fetch_redirect_response=False,
        )
        self.assertEqual(project.primary_domain, "example.com")
        self.assertEqual(project.approved_domains, ["example.com", "www.example.com"])
    @override_settings(OPENAI_INTAKE_GENERATION_ENABLED=True)
    @patch("app.views.OpenAIBoundary.generate_structured")
    def test_project_creation_uses_openai_for_evidence_bounded_brief(self, generate):
        now = timezone.now()
        generate.return_value = SimpleNamespace(
            status=GenerationStatus.AVAILABLE,
            data={
                "summary": "A concise evidence-bounded SEO intake summary.",
                "audit_focus": ["Review ecommerce discoverability"],
                "claims": [],
                "unavailable_items": [],
            },
            ledger=SimpleNamespace(
                requested_model="gpt-5.6-luna",
                returned_model="gpt-5.6-luna",
                prompt_version="test-prompt",
                request_sha256="a" * 64,
                response_sha256="b" * 64,
                input_tokens=100,
                output_tokens=50,
                attempts=1,
                finished_at=now,
            ),
            unavailable_reason=None,
        )
        response = self.client.post(
            reverse("project-create"),
            {
                "client_name": "AI Client",
                "primary_domain": "https://ai-client.example.com.au",
                "business_type": "ecommerce",
                "business_summary": "An Australian ecommerce business.",
                "continue": "project",
            },
            secure=True,
        )
        project = Project.objects.get(name="AI Client SEO Audit")
        self.assertEqual(response.status_code, 302)
        self.assertIn("ai_intake_brief", project.brand_facts)
        event = AuditEvent.objects.get(project=project, event_type="generation.intake_brief")
        self.assertEqual(event.payload["status"], "available")
        self.assertEqual(generate.call_args.kwargs["purpose"], GenerationPurpose.EXTRACTION)
