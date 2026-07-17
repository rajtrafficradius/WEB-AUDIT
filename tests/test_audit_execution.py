from app.domain.constants import RunState, StageStatus, UserRole
from app.domain.models import AuditRun, Client, Project, User
from audit_engine.crawler import CrawledPage, CrawlResult
from audit_engine.tasks import run_website_audit


def test_automatic_audit_persists_pages_findings_and_actions(db, monkeypatch):
    user = User.objects.create_user(
        username="auto-audit-admin", password="A-secure-test-password-2026!",  # noqa: S106 - test credential
        role=UserRole.AGENCY_ADMIN, must_change_password=False,
    )
    client = Client.objects.create(name="Audit Test", slug="audit-test")
    project = Project.objects.create(
        client=client, name="Audit Test", slug="audit-test",
        primary_domain="example.com.au", approved_domains=["example.com.au"],
        business_type=Project.BusinessType.ECOMMERCE,
    )
    run = AuditRun.objects.create(
        project=project, profile="quick", idempotency_key="automatic-test",
        rule_version="1.0.0", created_by=user,
    )
    result = CrawlResult(
        pages=(CrawledPage(
            requested_url="https://example.com.au/", final_url="https://example.com.au/",
            status_code=200, content_type="text/html", body_sha256="a" * 64,
            title=None, meta_description=None, h1=(), canonical_url=None,
            robots_directives=(), links=(), redirect_chain=("https://example.com.au/",),
        ),),
        failures=(), discovered_count=1, stopped_reason="queue_exhausted",
    )

    class FakeCrawler:
        def __init__(self, config):
            self.config = config
        def crawl(self, seeds):
            return result

    monkeypatch.setattr("audit_engine.tasks.BoundedCrawler", FakeCrawler)
    output = run_website_audit.run(str(run.pk))
    run.refresh_from_db()

    assert output["state"] == RunState.GATE_1_REVIEW
    assert run.pages.count() == 1
    assert run.findings.count() >= 3
    assert run.actions.count() == run.findings.count()
    assert set(run.stages.values_list("status", flat=True)) == {StageStatus.SUCCEEDED}

    # Findings are grouped: exactly one persisted row per rule code.
    codes = list(run.findings.values_list("code", flat=True))
    assert len(codes) == len(set(codes))

    # New crawl facts are persisted per the shared PageSnapshot.facts contract.
    facts = run.pages.get().facts
    for key in (
        "h1_values", "robots_directives", "links", "external_links", "word_count",
        "body_bytes", "response_ms", "images_total", "images_missing_alt", "schema_types",
        "h2_values", "og_title", "og_description", "lang", "viewport", "hreflang_count",
        "analytics_tags", "url_depth",
    ):
        assert key in facts

    # Crawl-only coverage for the ecommerce profile is 63% weighted, so the overall
    # health score is withheld (never coalesced to 0) per the DB constraint.
    assert float(run.evidence_coverage) == 63.0
    assert run.health_score is None

    # The per-category scorecard is checkpointed on the auditing stage.
    checkpoint = run.stages.get(name="auditing").checkpoint
    assert {entry["category"] for entry in checkpoint["scorecard"]} == {
        "technical", "on_page", "performance", "analytics", "keyword_architecture",
        "authority", "cro", "ecommerce", "geo_aeo",
    }
    assert checkpoint["stopped_reason"] == "queue_exhausted"