"""Contract tests for exporters.run_data.compile_run_data (Track B)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

from django.utils import timezone

from app.domain.constants import AvailabilityStatus, RunState
from app.domain.models import (
    ActionItem,
    AuditRun,
    Client,
    Evidence,
    Finding,
    PageSnapshot,
    Project,
    Recommendation,
    SourceSnapshot,
)
from audit_engine.crawler import CrawledPage, CrawlResult
from audit_engine.tasks import run_website_audit
from exporters.run_data import compile_run_data
from generation.openai_boundary import (
    GenerationLedger,
    GenerationPurpose,
    GenerationResult,
    GenerationStatus,
)

TOP_LEVEL_KEYS = {
    "schema_version",
    "client",
    "project",
    "run",
    "executive_summary",
    "sources",
    "evidence",
    "pages",
    "findings",
    "categories",
    "content_assets",
    "opportunities",
    "actions",
    "strategy_sections",
    "measurement_plan",
    "generation_ledger",
    "qa",
    "limitations",
    "deployment",
    "deck",
}
GATE_STATUSES = {"PASS", "FAIL", "UNAVAILABLE", "NOT_RUN"}
DOMAIN = "example.com.au"


def _crawled(
    path: str,
    *,
    status: int = 200,
    title: str | None = None,
    meta: str | None = None,
    h1: tuple[str, ...] = (),
    links: tuple[str, ...] = (),
    sha: str = "a",
) -> CrawledPage:
    url = f"https://{DOMAIN}{path}"
    return CrawledPage(
        requested_url=url,
        final_url=url,
        status_code=status,
        content_type="text/html",
        body_sha256=sha * 64,
        title=title,
        meta_description=meta,
        h1=h1,
        canonical_url=None,
        robots_directives=(),
        links=links,
        redirect_chain=(url,),
    )


def _build_crawled_run(monkeypatch, settings) -> AuditRun:
    settings.AUTO_BUILD_PACKAGE = False
    client = Client.objects.create(name="Example Retail", slug="example-retail")
    project = Project.objects.create(
        client=client,
        name="Example Retail SEO",
        slug="example-retail-seo",
        primary_domain=DOMAIN,
        approved_domains=[DOMAIN],
        business_type=Project.BusinessType.ECOMMERCE,
    )
    run = AuditRun.objects.create(
        project=project,
        profile="quick",
        idempotency_key="run-data-contract",
        rule_version="1.0.0",
    )
    pages = (
        _crawled(
            "/",
            title="Example Retail | Handmade Widgets Australia",
            meta="Shop handmade widgets made in Australia with tracked delivery options.",
            h1=("Example Retail",),
            links=(
                f"https://{DOMAIN}/collections/widgets",
                f"https://{DOMAIN}/blogs/news",
                f"https://{DOMAIN}/pages/about-us",
            ),
            sha="1",
        ),
        _crawled(
            "/collections/widgets",
            title="Widgets Collection | Example Retail",
            meta="Browse the current widgets collection with sizes and finishes explained.",
            h1=("Widgets",),
            links=(f"https://{DOMAIN}/collections/widgets/products/alpha-widget",),
            sha="2",
        ),
        _crawled(
            "/collections/widgets/products/alpha-widget",
            title=None,
            meta=None,
            h1=("Alpha Widget", "Alpha Widget Duplicate"),
            links=(f"https://{DOMAIN}/collections/widgets",),
            sha="3",
        ),
        _crawled(
            "/products/beta-widget",
            title="Beta Widget | Example Retail",
            meta="Short.",
            h1=("Beta Widget",),
            sha="4",
        ),
        _crawled(
            "/blogs/news",
            title="News | Example Retail",
            meta="Updates and care guides from the Example Retail workshop.",
            h1=("News",),
            sha="5",
        ),
        _crawled(
            "/blogs/news/widget-care-guide",
            title="Widget care guide | Example Retail",
            meta="How to look after a handmade widget, based on workshop guidance.",
            h1=("Widget care guide",),
            links=(f"https://{DOMAIN}/blogs/news",),
            sha="6",
        ),
        _crawled(
            "/pages/about-us",
            title="About us | Example Retail",
            meta="Who makes the widgets and where the workshop is located.",
            h1=("About Example Retail",),
            sha="7",
        ),
        _crawled("/missing-page", status=404, sha="8"),
        _crawled(
            "/cart",
            title="Cart | Example Retail",
            meta=None,
            h1=("Cart",),
            sha="9",
        ),
    )
    result = CrawlResult(
        pages=pages, failures=(), discovered_count=12, stopped_reason="queue_exhausted"
    )

    class FakeCrawler:
        def __init__(self, config):
            self.config = config

        def crawl(self, seeds):
            return result

    monkeypatch.setattr("audit_engine.tasks.BoundedCrawler", FakeCrawler)
    output = run_website_audit.run(str(run.pk))
    assert output["state"] == RunState.GATE_1_REVIEW
    run.refresh_from_db()
    return run


def _build_legacy_run() -> AuditRun:
    """A run whose facts JSON has only the legacy keys (no Track-A extensions)."""
    now = timezone.now()
    client = Client.objects.create(name="Legacy Co", slug="legacy-co")
    project = Project.objects.create(
        client=client,
        name="Legacy Co SEO",
        slug="legacy-co-seo",
        primary_domain="legacy.com.au",
        approved_domains=["legacy.com.au"],
        business_type=Project.BusinessType.SERVICE,
    )
    run = AuditRun.objects.create(
        project=project,
        profile="quick",
        idempotency_key="legacy-run",
        rule_version="1.0.0",
        state=RunState.GATE_1_REVIEW,
        evidence_coverage=Decimal("100"),
        confidence=Decimal("1"),
        health_score=Decimal("88.50"),
        source_cutoff_at=now,
    )
    source = SourceSnapshot.objects.create(
        run=run,
        source_type="crawl",
        availability=AvailabilityStatus.AVAILABLE,
        record_count=1,
        scope="1 fetched; 1 discovered; queue_exhausted",
        captured_at=now,
        confidence=Decimal("1"),
    )
    page = PageSnapshot.objects.create(
        run=run,
        source_snapshot=source,
        original_url="https://legacy.com.au/",
        normalized_url="https://legacy.com.au/",
        domain="legacy.com.au",
        approved_domain=True,
        status_code=200,
        content_type="text/html",
        title="Legacy Co | Services",
        meta_description="",
        h1="Legacy Co",
        robots_indexable=True,
        captured_at=now,
        confidence=Decimal("1"),
        facts={
            "h1_values": ["Legacy Co"],
            "robots_directives": [],
            "links": ["https://legacy.com.au/pages/about"],
        },
    )
    evidence = Evidence.objects.create(
        run=run,
        source_snapshot=source,
        page=page,
        evidence_type="website_crawl_page",
        title="Crawl observation: https://legacy.com.au/",
        availability=AvailabilityStatus.AVAILABLE,
        captured_at=now,
        confidence=Decimal("1"),
    )
    finding = Finding.objects.create(
        run=run,
        page=page,
        category="on_page",
        code="on_page.meta_description",
        title="Missing meta description",
        description="The page has no meta description.",
        severity="medium",
        affected_count=1,
        affected_share=Decimal("1"),
        score_penalty=Decimal("5"),
        confidence=Decimal("0.95"),
        rule_version="1.0.0",
    )
    finding.evidence.add(evidence)
    recommendation = Recommendation.objects.create(
        finding=finding,
        title="Add a useful meta description",
        rationale="The page has no meta description.",
        implementation="Draft an accurate page-specific search summary.",
        impact=3,
        effort=2,
        risk_class="low",
    )
    ActionItem.objects.create(
        run=run,
        recommendation=recommendation,
        title="Add a useful meta description",
        description="Draft an accurate page-specific search summary.",
        week=2,
        owner_label="SEO / web team",
        impact=Decimal("60"),
        evidence_confidence=Decimal("95"),
        reach=Decimal("100"),
        business_criticality=Decimal("60"),
        dependency_urgency=Decimal("40"),
        effort=Decimal("20"),
        priority_score=Decimal("60"),
        priority_tier="P2",
        risk_class="low",
    )
    return run


class FakeBoundary:
    """Money-safe stand-in for OpenAIBoundary used by the enrichment test."""

    def generate_structured(
        self, *, task: str, fact_pack: Any, schema_name: str, schema: Any, purpose: Any
    ) -> GenerationResult:
        fact = fact_pack.facts[0]
        now = datetime.now(UTC)
        data = {
            "executive_summary": (
                "Fake enriched executive summary grounded strictly in the approved fact pack."
            ),
            "strategy_synthesis": {
                "title": "Grounded synthesis",
                "paragraphs": ["One grounded paragraph derived from the recorded run posture."],
            },
            "deck_calls_to_action": ["Approve the evidence boundary at Gate 1."],
            "claims": [
                {
                    "text": "The run posture is recorded in the approved fact pack.",
                    "fact_keys": [fact.key],
                    "evidence_ids": list(fact.evidence_ids),
                }
            ],
        }
        ledger = GenerationLedger(
            call_id=str(uuid4()),
            purpose=purpose,
            requested_model="fake-model",
            returned_model="fake-model",
            prompt_version="test-1",
            request_sha256="0" * 64,
            response_sha256="1" * 64,
            input_tokens=10,
            output_tokens=20,
            cost_usd=None,
            started_at=now,
            finished_at=now,
            attempts=1,
        )
        return GenerationResult(GenerationStatus.AVAILABLE, data, ledger)


def test_compile_run_data_produces_full_contract(db, monkeypatch, settings):
    run = _build_crawled_run(monkeypatch, settings)
    data = compile_run_data(run, enrich=False)

    assert TOP_LEVEL_KEYS.issubset(data.keys())
    assert data["schema_version"] == "1.0.0"
    assert data["client"] == {"name": "Example Retail", "domain": DOMAIN, "locale": "en-AU"}
    assert data["run"]["id"].startswith("RUN-") and len(data["run"]["id"]) == 12
    assert data["run"]["configured_page_budget"] == 250
    assert data["run"]["rule_version"] == "1.0.0"
    assert 0 <= data["run"]["evidence_coverage"] <= 1

    # Pages are real, ordered, and each carries a resolvable evidence id.
    assert len(data["pages"]) == 9
    evidence_ids = {row["id"] for row in data["evidence"]}
    for index, page in enumerate(data["pages"], start=1):
        assert page["id"] == f"URL-{index:04d}"
        assert page["evidence_id"] in evidence_ids
        assert page["normalized_url"].startswith(f"https://{DOMAIN}")
        assert "_facts" not in page and "_page_pk" not in page

    # Findings: numeric confidence, valid priority, evidence lineage resolves.
    assert data["findings"]
    for finding in data["findings"]:
        assert isinstance(finding["confidence"], float)
        assert finding["priority"] in {"P1", "P2", "P3", "P4"}
        assert finding["evidence_ids"]
        assert set(finding["evidence_ids"]).issubset(evidence_ids)
        assert finding["severity"] in {"Critical", "High", "Medium", "Low", "Info"}

    # Actions: shaped, phased, evidence-linked.
    assert data["actions"]
    for action in data["actions"]:
        assert action["phase"].startswith("Phase ")
        assert action["effort"] in {"Low", "Medium", "High"}
        assert 0 <= action["confidence"] <= 1
        assert set(action["evidence_ids"]).issubset(evidence_ids)

    # Categories: no scorecard checkpoint exists, so scores are withheld with a reason.
    assert data["categories"]
    for category in data["categories"]:
        if category["score"] is None:
            assert category["unavailable_reason"]

    # Content and opportunities are grounded and withheld.
    assert data["content_assets"]
    for asset in data["content_assets"]:
        assert asset["approval_state"] == "withheld_pending_human_approval"
        assert asset["generation_method"] == "templated_evidence_framework"
        for claim in asset["claims"]:
            assert set(claim["evidence_ids"]).issubset(evidence_ids)
    for opportunity in data["opportunities"]:
        assert opportunity["keyword_volume"] is None
        assert opportunity["ranking"] is None
        assert opportunity["unavailable_reason"]

    # Deployment: schema withheld always present; the 404 page is a redirect candidate.
    deployment = data["deployment"]
    assert deployment["schema"]["withheld"]
    redirect_sources = {row["source_url"] for row in deployment["redirect_candidates"]}
    assert f"https://{DOMAIN}/missing-page" in redirect_sources
    metadata_urls = {row["url"] for row in deployment["metadata_review"]}
    assert f"https://{DOMAIN}/cart" not in metadata_urls
    assert f"https://{DOMAIN}/missing-page" not in metadata_urls

    # Deck contract: exactly nine slides with known kinds.
    assert len(data["deck"]) == 9
    assert {slide["kind"] for slide in data["deck"]} <= {
        "cover",
        "score",
        "generic",
        "timeline",
        "comparison",
    }
    assert data["deck"][0]["kind"] == "cover"

    # QA gates are computed verdicts, not copied strings.
    gates = data["qa"]["gates"]
    assert 6 <= len(gates) <= 8
    for gate in gates:
        assert gate["status"] in GATE_STATUSES
        assert gate["checked_at"]
    assert data["qa"]["wrong_domain_urls"] == 0
    assert data["qa"]["duplicate_normalized_pages"] == 0
    for row in data["qa"]["reconciliation"]:
        assert row["canonical"] == row["package"]

    # Enrichment off: exactly one unavailable ledger row and no fabricated model rows.
    assert len(data["generation_ledger"]) == 1
    assert data["generation_ledger"][0]["status"] == "unavailable"

    # Client-agnostic: no acceptance-fixture branding anywhere in the output.
    serialized = json.dumps(data, ensure_ascii=False, allow_nan=False)
    assert "Kakawa" not in serialized
    assert "kakawachocolates" not in serialized


def test_compile_run_data_handles_legacy_facts_without_new_keys(db):
    run = _build_legacy_run()
    data = compile_run_data(run, enrich=False)

    page = data["pages"][0]
    assert page["word_count"] is None  # legacy facts have no word_count key
    assert page["internal_links"] == 1
    assert page["h1"] == "Legacy Co"
    assert data["findings"] and data["actions"] and data["content_assets"]
    assert data["findings"][0]["evidence_ids"] == [page["evidence_id"]]
    # No 'auditing' stage checkpoint exists, so the category scorecard is withheld.
    assert all(category["score"] is None for category in data["categories"])
    assert all(
        category["unavailable_reason"] == "Scorecard unavailable for this run."
        for category in data["categories"]
    )
    assert len(data["deck"]) == 9
    json.dumps(data, ensure_ascii=False, allow_nan=False)


def test_compile_run_data_applies_fake_boundary_enrichment(db):
    run = _build_legacy_run()
    data = compile_run_data(run, enrich=True, boundary_factory=FakeBoundary)

    assert data["executive_summary"] == (
        "Fake enriched executive summary grounded strictly in the approved fact pack."
    )
    assert data["strategy_sections"][0]["title"] == "Grounded synthesis"
    ledger = data["generation_ledger"]
    assert ledger and ledger[-1]["status"] == "available"
    assert ledger[-1]["returned_model"] == "fake-model"
    assert ledger[-1]["tokens"] == 30
    last_slide_points = data["deck"][-1]["points"]
    assert any(point["text"] == "Approve the evidence boundary at Gate 1." for point in last_slide_points)


def test_compile_run_data_skips_enrichment_when_flag_disabled(db, settings):
    run = _build_legacy_run()
    settings.PACKAGE_AI_ENRICHMENT_ENABLED = False
    data = compile_run_data(run, enrich=None)

    assert len(data["generation_ledger"]) == 1
    row = data["generation_ledger"][0]
    assert row["status"] == "unavailable"
    assert "disabled" in row["unavailable_reason"]
    assert GenerationPurpose.FINAL  # imported contract stays importable without openai
