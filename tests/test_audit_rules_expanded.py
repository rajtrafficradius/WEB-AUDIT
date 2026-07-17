"""Deterministic unit tests for the 1.1.0 expanded rule set and parser fields."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from audit_engine.crawler import _DocumentParser, detect_analytics_tags
from audit_engine.models import BusinessProfile, PageSnapshot, Severity
from audit_engine.rules import (
    AuditContext,
    BrokenInternalLinkRule,
    CanonicalMissingRule,
    DuplicateContentRule,
    GenericTitleRule,
    H2MissingRule,
    HeavyPageRule,
    ImageAltMissingRule,
    OrganizationSchemaMissingRule,
    OrphanPageRule,
    RedirectChainLengthRule,
    SlowResponseRule,
    StructuredDataMissingRule,
    ThinContentRule,
    TitleCannibalizationRule,
    TrackingCoverageRule,
    run_rules,
)
from audit_engine.scoring import CATEGORY_WEIGHTS

PROJECT_ID = str(uuid4())


def make_page(
    url: str,
    *,
    status: int = 200,
    title: str | None = "A distinct descriptive title",
    description: str | None = "A page description",
    h1: tuple[str, ...] = ("Heading",),
    canonical: str | None = "https://example.com/",
    content_type: str | None = "text/html",
    body_sha256: str | None = None,
    links: tuple[str, ...] = (),
    word_count: int | None = None,
    body_bytes: int = 1000,
    response_ms: int | None = None,
    images_total: int = 0,
    images_missing_alt: int = 0,
    schema_types: tuple[str, ...] = (),
    h2: tuple[str, ...] = (),
    analytics_tags: tuple[str, ...] = (),
    lang: str | None = "en",
    url_depth: int = 1,
    redirect_chain: tuple[str, ...] = (),
) -> PageSnapshot:
    return PageSnapshot(
        id=str(uuid4()),
        project_id=PROJECT_ID,
        original_url=url,
        normalized_url=url,
        status_code=status,
        captured_at=datetime.now(UTC),
        evidence_id=str(uuid4()),
        title=title,
        meta_description=description,
        h1=h1,
        canonical_url=canonical,
        content_type=content_type,
        body_sha256=body_sha256,
        links=links,
        word_count=word_count,
        body_bytes=body_bytes,
        response_ms=response_ms,
        images_total=images_total,
        images_missing_alt=images_missing_alt,
        schema_types=schema_types,
        h2=h2,
        analytics_tags=analytics_tags,
        lang=lang,
        url_depth=url_depth,
        redirect_chain=redirect_chain,
    )


def context(pages, profile: BusinessProfile = BusinessProfile.SERVICE_SAAS) -> AuditContext:
    return AuditContext(PROJECT_ID, tuple(pages), ("example.com",), profile)


PARSER_HTML = """<html lang="en-AU">
<head>
<title>Widgets — Acme</title>
<meta name="description" content="Buy widgets.">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta property="og:title" content="Widgets">
<meta property="og:description" content="Buy widgets online.">
<link rel="canonical" href="https://example.com/widgets">
<link rel="alternate" hreflang="en-au" href="https://example.com/widgets">
<link rel="alternate" hreflang="en-nz" href="https://example.com/nz/widgets">
<script type="application/ld+json">
{"@context": "https://schema.org", "@graph": [{"@type": "Organization", "name": "Acme"}, {"@type": "WebSite"}]}
</script>
<script type="application/ld+json">not valid json {</script>
<script src="https://www.googletagmanager.com/gtag/js?id=G-123"></script>
<script>gtag('config', 'G-123');</script>
<style>body { color: red }</style>
</head>
<body>
<h1>Widget range</h1>
<h2>Popular widgets</h2>
<h2>New widgets</h2>
<div itemscope itemtype="https://schema.org/Product"><span>Widget</span></div>
<img src="/a.png" alt="A widget">
<img src="/b.png">
<img src="/c.png" alt="  ">
<p>Order our widgets today with fast shipping.</p>
<noscript>You need JavaScript enabled to view analytics.</noscript>
<a href="/widgets/blue">Blue</a>
<a href="https://partner.example.net/catalogue">Partner</a>
<a href="https://cdn.other.org/file">Other</a>
</body></html>"""


def test_parser_extracts_expanded_fields() -> None:
    parser = _DocumentParser()
    parser.feed(PARSER_HTML)
    doc = parser.result("https://example.com/widgets", ("example.com",))

    assert doc.title == "Widgets — Acme"
    assert doc.lang == "en-AU"
    assert doc.viewport is True
    assert doc.og_title is True
    assert doc.og_description is True
    assert doc.hreflang_count == 2
    assert doc.images_total == 3
    assert doc.images_missing_alt == 2
    assert set(doc.schema_types) == {"Organization", "WebSite", "Product"}
    assert doc.h2 == ("Popular widgets", "New widgets")
    assert doc.links == ("https://example.com/widgets/blue",)
    assert doc.external_links == (
        "https://cdn.other.org/file",
        "https://partner.example.net/catalogue",
    )
    # Visible words exclude script/style/noscript content entirely.
    assert doc.word_count == 20
    assert detect_analytics_tags(PARSER_HTML) == ("ga4",)


def test_detect_analytics_tags_signatures() -> None:
    assert detect_analytics_tags("<script src='https://www.googletagmanager.com/gtm.js?id=GTM-XYZ'>") == ("gtm",)
    assert detect_analytics_tags("<script src='https://connect.facebook.net/en_US/fbevents.js'>") == ("meta_pixel",)
    assert detect_analytics_tags("<script src='https://static.hotjar.com/c/hotjar.js'>") == ("hotjar",)
    assert detect_analytics_tags("<script src='https://cdn.segment.com/analytics.js/v1/x.js'>") == ("segment",)
    assert detect_analytics_tags("<script src='https://www.google-analytics.com/analytics.js'>") == ("ua",)
    assert detect_analytics_tags("<html><body>plain page</body></html>") == ()


def test_redirect_chain_length_rule() -> None:
    long_chain = make_page(
        "https://example.com/final",
        redirect_chain=("https://example.com/a", "https://example.com/b", "https://example.com/final"),
    )
    short_chain = make_page("https://example.com/x", redirect_chain=("https://example.com/x",))
    findings = list(RedirectChainLengthRule().evaluate(context([long_chain, short_chain])))
    assert len(findings) == 1
    assert findings[0].severity is Severity.MEDIUM
    assert findings[0].affected_urls == ("https://example.com/final",)
    assert findings[0].evidence_ids == (long_chain.evidence_id,)


def test_canonical_missing_rule() -> None:
    missing = make_page("https://example.com/no-canonical", canonical=None)
    declared = make_page("https://example.com/canonical", canonical="https://example.com/canonical")
    findings = list(CanonicalMissingRule().evaluate(context([missing, declared])))
    assert [finding.affected_urls[0] for finding in findings] == ["https://example.com/no-canonical"]
    assert findings[0].severity is Severity.LOW


def test_duplicate_content_is_single_site_wide_finding() -> None:
    twin_a = make_page("https://example.com/a", body_sha256="a" * 64)
    twin_b = make_page("https://example.com/b", body_sha256="a" * 64)
    unique = make_page("https://example.com/c", body_sha256="b" * 64)
    findings = list(DuplicateContentRule().evaluate(context([twin_a, twin_b, unique])))
    assert len(findings) == 1
    finding = findings[0]
    assert finding.severity is Severity.MEDIUM
    assert set(finding.affected_urls) == {"https://example.com/a", "https://example.com/b"}
    assert set(finding.evidence_ids) == {twin_a.evidence_id, twin_b.evidence_id}
    assert finding.affected_share == 2 / 3
    # The category must be a scoring key for every business profile.
    assert all("technical" in weights for weights in CATEGORY_WEIGHTS.values())


def test_broken_internal_link_yields_one_site_wide_high_finding() -> None:
    target = make_page("https://example.com/missing", status=404)
    source_a = make_page("https://example.com/", links=("https://example.com/missing",))
    source_b = make_page("https://example.com/blog", links=("https://example.com/missing",))
    findings = list(BrokenInternalLinkRule().evaluate(context([source_a, source_b, target])))
    assert len(findings) == 1
    finding = findings[0]
    assert finding.severity is Severity.HIGH
    assert "https://example.com/missing" in finding.description
    assert "HTTP 404" in finding.description
    assert finding.evidence_ids  # evidence-backed, never asserted without observation


def test_orphan_page_rule_excludes_homepage_and_linked_pages() -> None:
    home = make_page("https://example.com/", links=("https://example.com/a",), url_depth=0)
    linked = make_page("https://example.com/a")
    orphan = make_page("https://example.com/lost")
    findings = list(OrphanPageRule().evaluate(context([home, linked, orphan])))
    assert len(findings) == 1
    assert findings[0].affected_urls == ("https://example.com/lost",)
    assert findings[0].severity is Severity.MEDIUM


def test_thin_content_rule_skips_utility_pages_and_unknown_word_counts() -> None:
    thin = make_page("https://example.com/services", word_count=120)
    utility = make_page("https://example.com/privacy", word_count=50)
    unknown = make_page("https://example.com/about", word_count=None)
    findings = list(ThinContentRule().evaluate(context([thin, utility, unknown])))
    assert [finding.affected_urls[0] for finding in findings] == ["https://example.com/services"]
    assert "120 words" in findings[0].description


def test_image_alt_and_h2_rules() -> None:
    with_images = make_page("https://example.com/gallery", images_total=5, images_missing_alt=3)
    long_no_h2 = make_page("https://example.com/guide", word_count=700, h2=())
    long_with_h2 = make_page("https://example.com/other", word_count=700, h2=("Section",))
    alt_findings = list(ImageAltMissingRule().evaluate(context([with_images, long_no_h2])))
    assert len(alt_findings) == 1
    assert "3 of 5" in alt_findings[0].description
    h2_findings = list(H2MissingRule().evaluate(context([long_no_h2, long_with_h2])))
    assert [finding.affected_urls[0] for finding in h2_findings] == ["https://example.com/guide"]


def test_slow_response_severity_bands_and_heavy_page() -> None:
    fast = make_page("https://example.com/fast", response_ms=800)
    slow = make_page("https://example.com/slow", response_ms=1300)
    very_slow = make_page("https://example.com/very-slow", response_ms=3000)
    findings = {
        finding.affected_urls[0]: finding
        for finding in SlowResponseRule().evaluate(context([fast, slow, very_slow]))
    }
    assert "https://example.com/fast" not in findings
    assert findings["https://example.com/slow"].severity is Severity.MEDIUM
    assert findings["https://example.com/very-slow"].severity is Severity.HIGH
    heavy = make_page("https://example.com/heavy", body_bytes=2_000_000)
    heavy_findings = list(HeavyPageRule().evaluate(context([heavy, fast])))
    assert [finding.affected_urls[0] for finding in heavy_findings] == ["https://example.com/heavy"]


def test_tracking_coverage_thresholds() -> None:
    untagged = [make_page(f"https://example.com/u{i}") for i in range(5)]
    all_missing = list(TrackingCoverageRule().evaluate(context(untagged)))
    assert len(all_missing) == 1
    assert all_missing[0].rule_id == "analytics.tracking_missing"
    assert all_missing[0].severity is Severity.HIGH

    tagged = [make_page(f"https://example.com/t{i}", analytics_tags=("ga4",)) for i in range(3)]
    partial = list(TrackingCoverageRule().evaluate(context(tagged + untagged[:2])))
    assert len(partial) == 1
    assert partial[0].rule_id == "analytics.tracking_partial"
    assert partial[0].severity is Severity.MEDIUM

    covered = list(TrackingCoverageRule().evaluate(context(tagged)))
    assert covered == []


def test_structured_data_and_organization_schema_rules() -> None:
    bare = [make_page(f"https://example.com/p{i}") for i in range(4)]
    missing = list(StructuredDataMissingRule().evaluate(context(bare)))
    assert len(missing) == 1
    assert missing[0].affected_share == 1.0

    org_findings = list(OrganizationSchemaMissingRule().evaluate(context(bare)))
    assert len(org_findings) == 1
    assert org_findings[0].evidence_ids

    with_org = bare + [make_page("https://example.com/", schema_types=("Organization",), url_depth=0)]
    assert list(OrganizationSchemaMissingRule().evaluate(context(with_org))) == []


def test_title_cannibalization_clusters_near_identical_titles() -> None:
    competing_a = make_page("https://example.com/one", title="Buy Blue Widgets Online")
    competing_b = make_page("https://example.com/two", title="Buy Blue Widgets Online Now")
    distinct = make_page("https://example.com/three", title="Contact our support team")
    findings = list(
        TitleCannibalizationRule().evaluate(context([competing_a, competing_b, distinct]))
    )
    assert len(findings) == 1
    assert set(findings[0].affected_urls) == {"https://example.com/one", "https://example.com/two"}
    assert "cluster" in findings[0].description


def test_generic_title_rule() -> None:
    generic = make_page("https://example.com/", title="Acme")
    descriptive = make_page("https://example.com/svc", title="Managed SEO services for retailers")
    findings = list(GenericTitleRule().evaluate(context([generic, descriptive])))
    assert [finding.affected_urls[0] for finding in findings] == ["https://example.com/"]
    assert findings[0].severity is Severity.LOW


def test_profile_gating_for_ecommerce_and_local_rules() -> None:
    product_pages = [
        make_page("https://example.com/product/widget-a"),
        make_page("https://example.com/product/widget-b"),
    ]
    ecommerce_ids = {
        finding.rule_id
        for finding in run_rules(context(product_pages, BusinessProfile.ECOMMERCE))
    }
    assert "ecommerce.product_schema_missing" in ecommerce_ids
    saas_ids = {
        finding.rule_id
        for finding in run_rules(context(product_pages, BusinessProfile.SERVICE_SAAS))
    }
    assert "ecommerce.product_schema_missing" not in saas_ids

    local_ids = {
        finding.rule_id for finding in run_rules(context(product_pages, BusinessProfile.LOCAL))
    }
    assert "local.localbusiness_schema_missing" in local_ids
    assert "local.localbusiness_schema_missing" not in saas_ids


def test_every_rule_category_is_a_scoring_key_and_findings_carry_evidence() -> None:
    from audit_engine.rules import DEFAULT_RULES

    scoring_keys = set()
    for weights in CATEGORY_WEIGHTS.values():
        scoring_keys.update(weights)
    assert {rule.category for rule in DEFAULT_RULES} <= scoring_keys

    messy_site = [
        make_page("https://example.com/", title=None, description=None, h1=(), canonical=None,
                  lang=None, url_depth=0, word_count=90, response_ms=2600,
                  links=("https://example.com/Broken_Path",)),
        make_page("https://example.com/Broken_Path", status=404, url_depth=1),
        make_page("https://example.com/dup-a", body_sha256="c" * 64, word_count=100),
        make_page("https://example.com/dup-b", body_sha256="c" * 64, word_count=100),
    ]
    findings = run_rules(context(messy_site, BusinessProfile.HYBRID))
    assert findings
    for finding in findings:
        assert finding.evidence_ids
        assert finding.rule_version == "1.1.0"
        assert 0 <= finding.affected_share <= 1
