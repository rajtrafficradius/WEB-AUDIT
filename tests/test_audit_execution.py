from app.domain.constants import RunState, StageStatus, UserRole
from app.domain.models import AuditRun, Client, Project, User
from audit_engine.crawler import CrawledPage, CrawlResult
from audit_engine.tasks import STAGE_SEQUENCE, run_website_audit


def _make_run(db_user_suffix: str):
    user = User.objects.create_user(
        username=f"auto-audit-{db_user_suffix}", password="A-secure-test-password-2026!",  # noqa: S106 - test credential
        role=UserRole.AGENCY_ADMIN, must_change_password=False,
    )
    client = Client.objects.create(name=f"Audit {db_user_suffix}", slug=f"audit-{db_user_suffix}")
    project = Project.objects.create(
        client=client, name=f"Audit {db_user_suffix}", slug=f"audit-{db_user_suffix}",
        primary_domain="example.com.au", approved_domains=["example.com.au"],
        business_type=Project.BusinessType.ECOMMERCE,
    )
    return AuditRun.objects.create(
        project=project, profile="quick", idempotency_key=f"automatic-{db_user_suffix}",
        rule_version="1.0.0", created_by=user,
    )


def _page(url: str, **overrides) -> CrawledPage:
    defaults = {
        "requested_url": url, "final_url": url, "status_code": 200,
        "content_type": "text/html", "body_sha256": "a" * 64,
        "title": "A perfectly good page title", "meta_description": "Summary",
        "h1": ("Heading",), "canonical_url": url, "robots_directives": (), "links": (),
        "redirect_chain": (url,), "word_count": 800,
    }
    defaults.update(overrides)
    return CrawledPage(**defaults)


def _install_crawler(monkeypatch, result):
    class FakeCrawler:
        def __init__(self, config):
            self.config = config

        def crawl(self, seeds):
            return result

    monkeypatch.setattr("audit_engine.tasks.BoundedCrawler", FakeCrawler)


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


def test_challenged_pages_are_quarantined_and_never_become_findings(db, monkeypatch):
    """The Shopify incident: 429 challenge pages must not become client defects."""

    run = _make_run("challenged")
    good = _page("https://example.com.au/")
    challenged = tuple(
        _page(
            f"https://example.com.au/blocked-{index}/",
            status_code=429,
            title=None,
            meta_description=None,
            h1=(),
            canonical_url=None,
            word_count=None,
            challenge=True,
            challenge_kind="rate_limited",
            retry_after=30,
        )
        for index in range(3)
    )
    _install_crawler(monkeypatch, CrawlResult(
        pages=(good, *challenged), failures=(), discovered_count=4,
        stopped_reason="queue_exhausted", challenged_count=3, rate_limited_count=3,
    ))

    run_website_audit.run(str(run.pk))
    run.refresh_from_db()

    quarantined = run.pages.filter(facts__challenge=True)
    assert quarantined.count() == 3
    # Quarantined rows carry no borrowed content and are flagged unavailable.
    for page in quarantined:
        assert page.title == ""
        assert page.h1 == ""
        assert page.facts["availability"] == "unavailable"
        assert page.facts["unavailable_reason"]

    codes = set(run.findings.values_list("code", flat=True))
    # No challenged URL may appear in ANY finding except the honest coverage caveat.
    blocked_urls = {page.normalized_url for page in quarantined}
    for finding in run.findings.exclude(code="technical.crawl_degraded"):
        assert finding.page is None or finding.page.normalized_url not in blocked_urls
    # Their HTTP 429 status and empty title/H1 produced no defect findings.
    assert "technical.http_status" not in codes
    assert "on_page.thin_content" not in codes
    assert "on_page.title" not in codes
    assert "on_page.h1" not in codes

    checkpoint = run.stages.get(name="auditing").checkpoint
    integrity = checkpoint["crawl_integrity"]
    assert integrity["status"] == "blocked"
    assert integrity["fetched_pages"] == 1
    assert integrity["challenged_pages"] == 3
    assert integrity["rate_limited_pages"] == 3
    assert integrity["challenge_share"] == 0.75
    assert sorted(integrity["quarantined_urls"]) == sorted(blocked_urls)

    # The degradation is reported honestly, as INFO, with the challenge share.
    assert "technical.crawl_degraded" in codes
    degraded = run.findings.get(code="technical.crawl_degraded")
    assert degraded.severity == "info"
    assert "75%" in degraded.description


def test_degraded_status_between_five_and_thirty_percent(db, monkeypatch):
    """5% challenged is still clean; above 5% is degraded; above 30% is blocked."""

    pages = tuple(_page(f"https://example.com.au/p{index}/") for index in range(18))
    blocked = tuple(
        _page(
            f"https://example.com.au/blocked-{index}/", status_code=403, title=None, h1=(),
            meta_description=None, word_count=None, challenge=True,
            challenge_kind="access_denied",
        )
        for index in range(2)
    )
    run = _make_run("degraded")
    _install_crawler(monkeypatch, CrawlResult(
        pages=(*pages, *blocked), failures=(), discovered_count=20,
        stopped_reason="queue_exhausted", challenged_count=2, rate_limited_count=0,
    ))
    run_website_audit.run(str(run.pk))
    integrity = run.stages.get(name="auditing").checkpoint["crawl_integrity"]
    assert integrity["challenge_share"] == 0.1
    assert integrity["status"] == "degraded"
    assert integrity["rate_limited_pages"] == 0
    assert run.findings.filter(code="technical.crawl_degraded").exists()


def test_exactly_five_percent_challenged_is_still_clean(db, monkeypatch):
    pages = tuple(_page(f"https://example.com.au/p{index}/") for index in range(19))
    blocked = _page(
        "https://example.com.au/blocked/", status_code=403, title=None, h1=(),
        meta_description=None, word_count=None, challenge=True,
        challenge_kind="access_denied",
    )
    run = _make_run("threshold")
    _install_crawler(monkeypatch, CrawlResult(
        pages=(*pages, blocked), failures=(), discovered_count=20,
        stopped_reason="queue_exhausted", challenged_count=1, rate_limited_count=0,
    ))
    run_website_audit.run(str(run.pk))
    integrity = run.stages.get(name="auditing").checkpoint["crawl_integrity"]
    assert integrity["challenge_share"] == 0.05
    assert integrity["status"] == "clean"
    # Even when clean, the challenged URL is still quarantined out of the rules.
    assert run.pages.filter(facts__challenge=True).count() == 1
    assert not run.findings.filter(code="technical.crawl_degraded").exists()


def test_clean_crawl_reports_clean_integrity_and_no_degradation_finding(db, monkeypatch):
    run = _make_run("clean")
    _install_crawler(monkeypatch, CrawlResult(
        pages=(_page("https://example.com.au/"),), failures=(), discovered_count=1,
        stopped_reason="queue_exhausted",
    ))
    run_website_audit.run(str(run.pk))

    integrity = run.stages.get(name="auditing").checkpoint["crawl_integrity"]
    assert integrity["status"] == "clean"
    assert integrity["challenged_pages"] == 0
    assert integrity["quarantined_urls"] == []
    assert not run.findings.filter(code="technical.crawl_degraded").exists()
    assert run.pages.filter(facts__challenge=True).count() == 0


def test_crawl_failures_are_summarised_on_the_source_snapshot(db, monkeypatch):
    from audit_engine.crawler import CrawlFailure

    run = _make_run("failures")
    _install_crawler(monkeypatch, CrawlResult(
        pages=(_page("https://example.com.au/"),),
        failures=(
            CrawlFailure("https://example.com.au/gone/", "fetch_failed", "TimeoutError"),
            CrawlFailure(
                "https://example.com.au/blocked/", "challenge_response",
                "Origin returned a rate_limited response; the URL was quarantined.",
                challenge=True, challenge_kind="rate_limited", retry_after=10,
            ),
        ),
        discovered_count=3, stopped_reason="queue_exhausted",
    ))
    run_website_audit.run(str(run.pk))

    metadata = run.source_snapshots.get(source_type="crawl").metadata
    assert metadata["failure_count"] == 2
    assert metadata["failure_codes"] == {"fetch_failed": 1, "challenge_response": 1}
    assert {entry["url"] for entry in metadata["failures"]} == {
        "https://example.com.au/gone/", "https://example.com.au/blocked/",
    }
    assert metadata["crawl_integrity"]["status"] == "clean"


def test_stage_sequence_is_declared_before_new_stages_are_added():
    # Every stage must have a distinct, ordered sequence or "latest running stage"
    # lookups silently resolve to the wrong stage.
    assert STAGE_SEQUENCE == {
        "collecting": 10, "auditing": 20, "enriching": 25, "packaging": 30,
    }
    assert sorted(STAGE_SEQUENCE.values()) == list(STAGE_SEQUENCE.values())
    assert len(set(STAGE_SEQUENCE.values())) == len(STAGE_SEQUENCE)


def test_market_data_dispatch_is_optional_and_never_fails_the_audit(db, monkeypatch, settings):
    run = _make_run("market")
    _install_crawler(monkeypatch, CrawlResult(
        pages=(_page("https://example.com.au/"),), failures=(), discovered_count=1,
        stopped_reason="queue_exhausted",
    ))
    settings.MARKET_DATA_ENABLED = True
    # A key must resolve for the dispatch to fire; the env fallback is enough.
    settings.SEMRUSH_API_KEY = "dispatch-test-env-key"
    sent = []

    class ExplodingApp:
        def send_task(self, name, args=None, queue=None):
            sent.append((name, args, queue))
            raise RuntimeError("broker unreachable")

    monkeypatch.setattr("celery.current_app", ExplodingApp())
    output = run_website_audit.run(str(run.pk))
    run.refresh_from_db()

    assert output["state"] == RunState.GATE_1_REVIEW
    assert run.state == RunState.GATE_1_REVIEW
    assert sent == [("studio.analysis.collect_market_data", [str(run.pk)], "analysis")]
    enriching = run.stages.get(name="enriching")
    assert enriching.sequence == STAGE_SEQUENCE["enriching"]


def test_market_data_dispatch_is_skipped_when_disabled(db, monkeypatch, settings):
    run = _make_run("market-off")
    _install_crawler(monkeypatch, CrawlResult(
        pages=(_page("https://example.com.au/"),), failures=(), discovered_count=1,
        stopped_reason="queue_exhausted",
    ))
    settings.MARKET_DATA_ENABLED = False
    run_website_audit.run(str(run.pk))
    assert not run.stages.filter(name="enriching").exists()


def test_market_data_dispatch_is_skipped_when_no_key_is_configured(db, monkeypatch, settings):
    """Enabled with no key anywhere is a no-op, never a queued no-op task."""

    run = _make_run("market-unconfigured")
    _install_crawler(monkeypatch, CrawlResult(
        pages=(_page("https://example.com.au/"),), failures=(), discovered_count=1,
        stopped_reason="queue_exhausted",
    ))
    settings.MARKET_DATA_ENABLED = True
    settings.SEMRUSH_API_KEY = ""
    run_website_audit.run(str(run.pk))
    assert not run.stages.filter(name="enriching").exists()
