"""Contract tests for exporters.run_data.compile_run_data (Track B)."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from itertools import combinations
from typing import Any
from uuid import uuid4

from django.utils import timezone

from app.domain.constants import AvailabilityStatus, RunState
from app.domain.models import (
    ActionItem,
    AuditRun,
    Backlink,
    Client,
    Evidence,
    Finding,
    Keyword,
    MetricObservation,
    PageSnapshot,
    Project,
    Recommendation,
    SourceSnapshot,
)
from audit_engine.crawler import CrawledPage, CrawlResult
from audit_engine.tasks import run_website_audit
from exporters.run_data import (
    PROVIDER_MISSING_REASON,
    compile_run_data,
    sentence_overlap,
)
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
    "market",
    "keywords",
    "keyword_clusters",
    "competitors",
    "performance_vs_competitors",
    "backlinks",
    "onpage_proposals",
    "crawl_integrity",
    "methodology",
}
GATE_STATUSES = {"PASS", "FAIL", "UNAVAILABLE", "NOT_RUN"}
DOMAIN = "example.com.au"
BRIEF_MIN_WORDS = 1200
OVERLAP_LIMIT = 0.15


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
        _crawled("/collections/widgets-old", status=404, sha="a"),
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


PROVIDER_KEYWORDS = (
    ("handmade widgets australia", 2400, "7", "1.25"),
    ("widgets collection", 880, "14", "0.90"),
    ("buy alpha widget", 320, "23", "2.10"),
    ("widget care guide", 210, "4", "0.35"),
    ("beta widget price", 140, None, "1.75"),
)
PROVIDER_COMPETITORS = (
    {
        "domain": "competitor-one.com.au",
        "relevance": 0.81,
        "common_keywords": 420,
        "organic_keywords": 3100,
        "organic_traffic": 15200,
        "organic_cost": 24000,
        "adwords_keywords": 90,
        "authority_score": 46,
        "referring_domains": 640,
    },
    {
        "domain": "competitor-two.com.au",
        "relevance": 0.64,
        "common_keywords": 260,
        "organic_keywords": 1100,
        "organic_traffic": 4300,
        "organic_cost": 6100,
        "adwords_keywords": 12,
        "authority_score": 28,
        "referring_domains": 120,
    },
)


def _seed_provider_data(run: AuditRun) -> SourceSnapshot:
    """Persist the market-data rows the provider service writes for a run."""
    now = timezone.now()
    snapshot = SourceSnapshot.objects.create(
        run=run,
        source_type="semrush",
        availability=AvailabilityStatus.AVAILABLE,
        record_count=12,
        scope="domain overview; organic keywords; competitors; backlinks",
        captured_at=now,
        confidence=Decimal("1"),
        metadata={"database": "au", "units_spent": 240},
    )
    overview = (
        ("semrush.domain.organic_keywords", 1450),
        ("semrush.domain.organic_traffic", 8600),
        ("semrush.domain.organic_cost", 12000),
        ("semrush.domain.adwords_keywords", 25),
        ("semrush.domain.rank", 184000),
        ("semrush.domain.authority_score", 34),
        ("semrush.domain.backlinks_total", 5400),
        ("semrush.domain.referring_domains", 210),
    )
    for key, value in overview:
        MetricObservation.objects.create(
            run=run,
            source_snapshot=snapshot,
            metric_key=key,
            numeric_value=Decimal(str(value)),
            availability=AvailabilityStatus.AVAILABLE,
            captured_at=now,
            confidence=Decimal("1"),
        )
    for index, payload in enumerate(PROVIDER_COMPETITORS, start=1):
        MetricObservation.objects.create(
            run=run,
            source_snapshot=snapshot,
            metric_key=f"semrush.competitor.{index:02d}",
            json_value=payload,
            availability=AvailabilityStatus.AVAILABLE,
            captured_at=now,
            confidence=Decimal("1"),
        )
    MetricObservation.objects.create(
        run=run,
        source_snapshot=snapshot,
        metric_key="semrush.keyword.detail.01",
        json_value={
            "phrase": "widgets collection",
            "landing_url": f"https://{DOMAIN}/collections/widgets",
            "previous_position": 19,
            "competition": 0.42,
            "results_count": 1240000,
            "traffic_share": 0.031,
            "trend": "0.8,0.9,1.0",
        },
        availability=AvailabilityStatus.AVAILABLE,
        captured_at=now,
        confidence=Decimal("1"),
    )
    for phrase, volume, position, cpc in PROVIDER_KEYWORDS:
        Keyword.objects.create(
            run=run,
            source_snapshot=snapshot,
            phrase=phrase,
            normalized_phrase=phrase,
            country_code="AU",
            locale="en-AU",
            search_volume=volume,
            cpc=Decimal(cpc),
            position=Decimal(position) if position is not None else None,
            availability=AvailabilityStatus.AVAILABLE,
            captured_at=now,
            confidence=Decimal("1"),
        )
    for index, referrer in enumerate(("blog.example.org", "directory.example.net"), start=1):
        for link in range(1, index + 2):
            Backlink.objects.create(
                run=run,
                source_snapshot=snapshot,
                source_url=f"https://{referrer}/post-{link}",
                target_url=f"https://{DOMAIN}/",
                referring_domain=referrer,
                anchor_text="handmade widgets",
                authority_score=Decimal("45") - index,
                link_type="follow",
                first_seen=date(2025, 1, 10),
                last_seen=date(2026, 6, 30),
                availability=AvailabilityStatus.AVAILABLE,
                captured_at=now,
                confidence=Decimal("1"),
            )
    return snapshot


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


def _ledger(purpose: Any) -> GenerationLedger:
    now = datetime.now(UTC)
    return GenerationLedger(
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


def _claims_from(fact_pack: Any, limit: int = 3) -> list[dict[str, Any]]:
    return [
        {
            "text": f"Fact {fact.key} is recorded in the approved fact pack.",
            "fact_keys": [fact.key],
            "evidence_ids": list(fact.evidence_ids),
        }
        for fact in fact_pack.facts[:limit]
    ]


class FakeBoundary:
    """Money-safe stand-in for OpenAIBoundary used by the enrichment tests."""

    def generate_structured(
        self, *, task: str, fact_pack: Any, schema_name: str, schema: Any, purpose: Any
    ) -> GenerationResult:
        if schema_name == "package_onpage_proposals":
            data: dict[str, Any] = {
                "proposals": [
                    {
                        "page_id": str(fact.value["page_id"]),
                        "proposed_title": f"Model title for {fact.value['page_type']}"[:60],
                        "proposed_meta_description": (
                            "A model-written summary of this page that stays inside the "
                            "approved fact pack and adds no new claims."
                        ),
                        "proposed_h1": f"Model heading {fact.value['page_id']}",
                        "rationale": "Derived from the observed page facts only.",
                    }
                    for fact in fact_pack.facts
                ],
                "claims": _claims_from(fact_pack),
            }
        elif schema_name == "package_content_outlines":
            data = {
                "outlines": [
                    {
                        "asset_id": str(fact.value["asset_id"]),
                        "intent_summary": "Model intent summary bound to the fact pack.",
                        "sections": [
                            {
                                "heading": f"Model section {index} for {fact.value['asset_id']}",
                                "guidance": (
                                    "Write this section only from the observed page facts "
                                    "supplied for the asset."
                                ),
                            }
                            for index in range(1, 4)
                        ],
                    }
                    for fact in fact_pack.facts
                ],
                "claims": _claims_from(fact_pack),
            }
        else:
            fact = fact_pack.facts[0]
            data = {
                "executive_summary": (
                    "Fake enriched executive summary grounded strictly in the approved fact pack."
                ),
                "strategy_synthesis": {
                    "title": "Grounded synthesis",
                    "paragraphs": [
                        "One grounded paragraph derived from the recorded run posture."
                    ],
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
        return GenerationResult(GenerationStatus.AVAILABLE, data, _ledger(purpose))


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
    assert len(data["pages"]) == 10
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
        for claim in asset["claims"]:
            assert set(claim["evidence_ids"]).issubset(evidence_ids)
    for opportunity in data["opportunities"]:
        assert set(opportunity["evidence_ids"]).issubset(evidence_ids)

    # Deployment: schema withheld always present; the 404 page is a redirect candidate.
    deployment = data["deployment"]
    assert deployment["schema"]["withheld"]
    redirect_sources = {row["source_url"] for row in deployment["redirect_candidates"]}
    assert f"https://{DOMAIN}/missing-page" in redirect_sources
    metadata_urls = {row["url"] for row in deployment["metadata_review"]}
    assert f"https://{DOMAIN}/cart" not in metadata_urls
    assert f"https://{DOMAIN}/missing-page" not in metadata_urls

    # Deck contract: nine slides without competitor data, all of known kinds.
    assert len(data["deck"]) == 9
    assert {slide["kind"] for slide in data["deck"]} <= {
        "cover",
        "score",
        "generic",
        "timeline",
        "comparison",
    }
    assert data["deck"][0]["kind"] == "cover"
    for slide in data["deck"]:
        assert slide["title"] and slide["body"]

    # QA gates are computed verdicts, not copied strings.
    gates = data["qa"]["gates"]
    assert 6 <= len(gates) <= 12
    for gate in gates:
        assert gate["status"] in GATE_STATUSES
        assert gate["checked_at"]
    assert data["qa"]["wrong_domain_urls"] == 0
    assert data["qa"]["duplicate_normalized_pages"] == 0
    for row in data["qa"]["reconciliation"]:
        assert row["canonical"] == row["package"]

    # Enrichment off: three unavailable ledger rows (proposals, outlines, enrichment).
    assert len(data["generation_ledger"]) == 3
    assert {row["status"] for row in data["generation_ledger"]} == {"unavailable"}

    # Client-agnostic: no acceptance-fixture branding anywhere in the output.
    serialized = json.dumps(data, ensure_ascii=False, allow_nan=False)
    assert "Kakawa" not in serialized
    assert "kakawachocolates" not in serialized


def test_market_families_are_unavailable_without_provider_rows(db, monkeypatch, settings):
    run = _build_crawled_run(monkeypatch, settings)
    data = compile_run_data(run, enrich=False)

    assert data["keywords"] == []
    assert data["keyword_clusters"] == []
    assert data["competitors"] == []
    assert data["backlinks"]["referring_domains"] == []
    assert data["backlinks"]["status"] == "unavailable"
    assert data["backlinks"]["unavailable_reason"]

    market = data["market"]
    assert market["status"] == "unavailable"
    assert market["unavailable_reason"] == PROVIDER_MISSING_REASON
    assert all(value is None for value in market["domain"].values())

    performance = data["performance_vs_competitors"]
    assert performance["status"] == "unavailable"
    assert performance["metrics"] == []
    assert performance["unavailable_reason"]

    # No synthesised volume leaks into opportunities or metadata rows.
    for opportunity in data["opportunities"]:
        assert opportunity["keyword_volume"] is None
        assert opportunity["unavailable_reason"]
    for row in data["deployment"]["metadata_review"]:
        assert row["target_volume"] is None
        assert row["target_keyword"].startswith("Unavailable")

    statuses = {gate["id"]: gate["status"] for gate in data["qa"]["gates"]}
    assert statuses["QA-09"] == "UNAVAILABLE"
    assert statuses["QA-12"] == "UNAVAILABLE"


def test_provider_rows_populate_market_keyword_and_competitor_contract(
    db, monkeypatch, settings
):
    run = _build_crawled_run(monkeypatch, settings)
    _seed_provider_data(run)
    data = compile_run_data(run, enrich=False)

    market = data["market"]
    assert market["status"] == "available"
    assert market["provider"] == "semrush"
    assert market["database"] == "au"
    assert market["units_spent"] == 240
    assert market["domain"]["organic_keywords"] == 1450
    assert market["domain"]["authority_score"] == 34
    assert market["domain"]["organic_cost"] == 12000.0

    keywords = data["keywords"]
    assert len(keywords) == len(PROVIDER_KEYWORDS)
    assert keywords[0]["id"] == "KW-0001"
    assert keywords[0]["phrase"] == "handmade widgets australia"
    assert keywords[0]["search_volume"] == 2400
    assert keywords[0]["position"] == 7
    assert keywords[0]["source"] == "semrush"
    assert all(row["cluster"] for row in keywords)
    detail = next(row for row in keywords if row["phrase"] == "widgets collection")
    assert detail["previous_position"] == 19
    assert detail["landing_url"] == f"https://{DOMAIN}/collections/widgets"
    assert detail["results_count"] == 1240000
    buy = next(row for row in keywords if row["phrase"] == "buy alpha widget")
    assert buy["funnel_stage"] == "BOFU"
    assert buy["intent"] == "transactional"
    guide = next(row for row in keywords if row["phrase"] == "widget care guide")
    assert guide["funnel_stage"] == "TOFU"

    clusters = data["keyword_clusters"]
    assert clusters
    assert sum(cluster["keyword_count"] for cluster in clusters) == len(keywords)
    mapped = [cluster for cluster in clusters if cluster["primary_url"]]
    assert mapped, "at least one cluster must map to a crawled URL"
    for cluster in mapped:
        assert cluster["primary_url"].startswith(f"https://{DOMAIN}")
        assert cluster["coverage"] in {"covered", "partial"}
    for cluster in clusters:
        if cluster["primary_url"] is None:
            assert cluster["coverage"] == "gap"

    competitors = data["competitors"]
    assert [row["domain"] for row in competitors] == [
        "competitor-one.com.au",
        "competitor-two.com.au",
    ]
    assert competitors[0]["common_keywords"] == 420

    performance = data["performance_vs_competitors"]
    assert performance["status"] == "available"
    organic = next(
        row for row in performance["metrics"] if row["metric"] == "Organic keywords"
    )
    assert organic["client"] == 1450
    assert organic["competitor_median"] == 2100
    assert organic["best_competitor"] == "competitor-one.com.au"
    assert organic["position"] == "behind"
    common = next(
        row
        for row in performance["metrics"]
        if row["metric"] == "Common keywords with the client"
    )
    assert common["position"] == "unknown"
    assert performance["summary"].count(".") >= 2

    backlinks = data["backlinks"]
    assert backlinks["status"] == "available"
    assert [row["domain"] for row in backlinks["referring_domains"]] == [
        "directory.example.net",
        "blog.example.org",
    ]
    assert backlinks["overview"]["referring_domains"] == 210

    # Evidence lineage exists for every provider family.
    evidence_ids = {row["id"] for row in data["evidence"]}
    assert "EV-KW-0001" in evidence_ids
    assert "EV-CMP-1" in evidence_ids
    assert "EV-BL-0001" in evidence_ids
    assert "EV-MARKET-0001" in evidence_ids
    for family in ("keywords", "keyword_clusters", "competitors"):
        for row in data[family]:
            assert row["evidence_ids"]
            assert set(row["evidence_ids"]).issubset(evidence_ids)

    statuses = {gate["id"]: gate["status"] for gate in data["qa"]["gates"]}
    assert statuses["QA-03"] == "PASS"
    assert statuses["QA-09"] == "PASS"
    assert statuses["QA-12"] == "PASS"

    # The competitor comparison slide only appears when the data supports it.
    assert len(data["deck"]) == 10
    comparison = next(slide for slide in data["deck"] if slide["kind"] == "comparison")
    assert comparison["title"] and comparison["body"]


def test_onpage_proposals_never_repeat_the_current_value(db, monkeypatch, settings):
    run = _build_crawled_run(monkeypatch, settings)
    _seed_provider_data(run)
    data = compile_run_data(run, enrich=False)

    proposals = data["onpage_proposals"]
    assert proposals
    evidence_ids = {row["id"] for row in data["evidence"]}
    for row in proposals:
        assert row["source"] == "deterministic"
        assert row["approval_status"] == "withheld_pending_editorial_review"
        assert set(row["evidence_ids"]).issubset(evidence_ids)
        for current_key, proposed_key in (
            ("current_title", "proposed_title"),
            ("current_meta", "proposed_meta"),
            ("current_h1", "proposed_h1"),
        ):
            if row[proposed_key] is not None:
                assert row[proposed_key] != row[current_key]
        if row["proposed_title"]:
            assert len(row["proposed_title"]) <= 60
        if row["proposed_meta"]:
            assert 70 <= len(row["proposed_meta"]) <= 158
        if row["proposed_h1"]:
            assert len(row["proposed_h1"]) <= 70

    keyword_targets = [row["target_keyword"] for row in proposals if row["target_keyword"]]
    assert keyword_targets, "provider keywords should map onto at least one proposal"

    for row in data["deployment"]["metadata_review"]:
        for current_key, proposed_key in (
            ("current_title", "proposed_title"),
            ("current_meta_description", "proposed_meta_description"),
            ("current_h1", "proposed_h1"),
        ):
            if row[proposed_key] is not None:
                assert row[proposed_key] != row[current_key]


def test_content_briefs_are_long_and_mutually_distinct(db, monkeypatch, settings):
    run = _build_crawled_run(monkeypatch, settings)
    _seed_provider_data(run)
    data = compile_run_data(run, enrich=False)

    assets = data["content_assets"]
    assert len(assets) >= 5
    for asset in assets:
        assert asset["word_count"] >= BRIEF_MIN_WORDS
        assert asset["body"]
        assert asset["outline_headings"]
    for left, right in combinations(assets, 2):
        assert sentence_overlap(left, right) < OVERLAP_LIMIT


def test_redirect_proposer_prefers_a_real_match(db, monkeypatch, settings):
    run = _build_crawled_run(monkeypatch, settings)
    data = compile_run_data(run, enrich=False)

    rows = {row["source_url"]: row for row in data["deployment"]["redirect_candidates"]}
    matched = rows[f"https://{DOMAIN}/collections/widgets-old"]
    assert matched["target_url"] == f"https://{DOMAIN}/collections/widgets"
    assert matched["confidence"] >= 0.35
    assert "overlap" in matched["matched_on"] or "similarity" in matched["matched_on"]
    assert matched["included_in_deployment"] is False

    unmatched = rows[f"https://{DOMAIN}/missing-page"]
    assert unmatched["target_url"] is None
    assert unmatched["matched_on"] == "no confident match"

    # No destination is a lazy catch-all repeated across every failing URL.
    targets = [row["target_url"] for row in rows.values() if row["target_url"]]
    assert len(set(targets)) == len(targets)


def test_internal_link_recommender_emits_rows_for_low_inbound_money_pages(
    db, monkeypatch, settings
):
    run = _build_crawled_run(monkeypatch, settings)
    data = compile_run_data(run, enrich=False)

    rows = data["deployment"]["internal_link_candidates"]
    assert rows
    beta = [
        row for row in rows if row["target_url"] == f"https://{DOMAIN}/products/beta-widget"
    ]
    assert beta, "an orphan product page must receive link recommendations"
    for row in beta:
        assert row["anchor"]
        assert row["source_url"] != row["target_url"]
        assert row["approval_status"].startswith("withheld")


def test_crawl_integrity_defaults_to_clean_for_an_unchallenged_crawl(
    db, monkeypatch, settings
):
    run = _build_crawled_run(monkeypatch, settings)
    data = compile_run_data(run, enrich=False)

    integrity = data["crawl_integrity"]
    assert integrity["status"] == "clean"
    assert integrity["fetched_pages"] == len(data["pages"])
    assert integrity["challenge_share"] == 0.0
    assert integrity["quarantined_urls"] == []
    statuses = {gate["id"]: gate["status"] for gate in data["qa"]["gates"]}
    assert statuses["QA-10"] == "PASS"


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
    assert len(ledger) == 3
    assert ledger[-1]["status"] == "available"
    assert ledger[-1]["returned_model"] == "fake-model"
    assert ledger[-1]["tokens"] == 30
    last_slide_points = data["deck"][-1]["points"]
    assert any(
        point["text"] == "Approve the evidence boundary at Gate 1."
        for point in last_slide_points
    )

    # The grounded proposal call replaced the deterministic values.
    assert data["onpage_proposals"]
    assert data["onpage_proposals"][0]["source"] == "llm_evidence_bound"
    assert data["onpage_proposals"][0]["proposed_title"].startswith("Model title")
    assert data["content_assets"][0]["outline_source"] == "llm_evidence_bound"
    assert data["content_assets"][0]["word_count"] >= BRIEF_MIN_WORDS


def test_llm_call_budget_is_capped_by_settings(db, settings):
    settings.PACKAGE_AI_MAX_CALLS = 1
    run = _build_legacy_run()
    data = compile_run_data(run, enrich=True, boundary_factory=FakeBoundary)

    available = [row for row in data["generation_ledger"] if row["status"] == "available"]
    assert len(available) == 1
    exhausted = [
        row
        for row in data["generation_ledger"]
        if row["unavailable_reason"] and "budget" in row["unavailable_reason"]
    ]
    assert len(exhausted) == 2
    assert data["onpage_proposals"][0]["source"] == "deterministic"


def test_compile_run_data_skips_enrichment_when_flag_disabled(db, settings):
    run = _build_legacy_run()
    settings.PACKAGE_AI_ENRICHMENT_ENABLED = False
    data = compile_run_data(run, enrich=None)

    ledger = data["generation_ledger"]
    assert len(ledger) == 3
    assert {row["status"] for row in ledger} == {"unavailable"}
    assert all("disabled" in row["unavailable_reason"] for row in ledger)
    assert GenerationPurpose.FINAL  # imported contract stays importable without openai
