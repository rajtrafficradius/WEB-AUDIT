from __future__ import annotations

import socket
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from audit_engine.crawler import BoundedCrawler, CrawlConfig, FetchResponse
from audit_engine.models import (
    Availability,
    BusinessProfile,
    ContractError,
    EvidenceRecord,
    PageSnapshot,
    Provenance,
    SourceKind,
)
from audit_engine.rules import AuditContext, enabled_modules, run_rules
from audit_engine.urls import SSRFGuard


def uid() -> str:
    return str(uuid4())


def test_derived_provenance_requires_rule_version() -> None:
    with pytest.raises(ContractError):
        Provenance(SourceKind.DERIVED, datetime.now(UTC))


def test_unavailable_provenance_requires_reason() -> None:
    with pytest.raises(ContractError):
        Provenance(
            SourceKind.GSC,
            datetime.now(UTC),
            availability=Availability.UNAVAILABLE,
        )


def test_evidence_rejects_non_finite_json() -> None:
    provenance = Provenance(SourceKind.CRAWL, datetime.now(UTC))
    with pytest.raises(ContractError):
        EvidenceRecord(uid(), uid(), "metric", float("nan"), provenance)


def make_page(
    project_id: str,
    url: str,
    *,
    status: int = 200,
    title: str | None = "Title",
    description: str | None = "Description",
    h1: tuple[str, ...] = ("Heading",),
    canonical: str | None = None,
    directives: tuple[str, ...] = (),
) -> PageSnapshot:
    return PageSnapshot(
        id=uid(),
        project_id=project_id,
        original_url=url,
        normalized_url=url,
        status_code=status,
        captured_at=datetime.now(UTC),
        evidence_id=uid(),
        title=title,
        meta_description=description,
        h1=h1,
        canonical_url=canonical,
        robots_directives=directives,
    )


def test_conditional_modules_match_business_profile() -> None:
    assert "ecommerce" in enabled_modules(BusinessProfile.ECOMMERCE)
    assert "local" not in enabled_modules(BusinessProfile.ECOMMERCE)
    assert {"local", "ecommerce"}.issubset(enabled_modules(BusinessProfile.HYBRID))


def test_rules_only_report_observed_conditions_with_evidence() -> None:
    project_id = uid()
    pages = (
        make_page(
            project_id,
            "https://example.com/",
            title=None,
            description=None,
            h1=(),
            canonical="https://attacker.test/",
            directives=("noindex",),
        ),
        make_page(project_id, "https://example.com/missing", status=404),
    )
    findings = run_rules(
        AuditContext(project_id, pages, ("example.com",), BusinessProfile.ECOMMERCE)
    )
    rule_ids = {finding.rule_id for finding in findings}
    assert {
        "technical.http_status",
        "technical.canonical_boundary",
        "technical.robots_directive",
        "on_page.title",
        "on_page.meta_description",
        "on_page.h1",
    }.issubset(rule_ids)
    assert all(finding.evidence_ids for finding in findings)


def public_resolver(host: str, port: int, family: int, kind: int):
    del host, family
    return [(socket.AF_INET, kind, 6, "", ("93.184.216.34", port))]


class FakeTransport:
    def __init__(self, routes: dict[str, FetchResponse]) -> None:
        self.routes = routes
        self.calls: list[str] = []

    def fetch(self, target, **kwargs):
        del kwargs
        assert target.approved_ips == ("93.184.216.34",)
        self.calls.append(target.normalized_url)
        return self.routes[target.normalized_url]


def test_bounded_crawler_obeys_robots_extracts_metadata_and_deduplicates() -> None:
    routes = {
        "https://example.com/robots.txt": FetchResponse(
            200,
            {"content-type": "text/plain"},
            b"User-agent: *\nAllow: /\n",
            "https://example.com/robots.txt",
        ),
        "https://example.com/": FetchResponse(
            200,
            {"content-type": "text/html; charset=utf-8"},
            (
                b"<html><head><title>Observed title</title>"
                b"<meta name='description' content='Observed description'>"
                b"<link rel='canonical' href='https://example.com/'>"
                b"</head><body><h1>Observed H1</h1>"
                b"<a href='/next?utm_source=test'>next</a>"
                b"<a href='/next'>duplicate</a></body></html>"
            ),
            "https://example.com/",
        ),
        "https://example.com/next": FetchResponse(
            200,
            {"content-type": "text/html"},
            b"<title>Next</title><h1>Next</h1>",
            "https://example.com/next",
        ),
    }
    transport = FakeTransport(routes)
    config = CrawlConfig(("example.com",), max_pages=10, max_depth=2, min_host_delay_seconds=0)
    crawler = BoundedCrawler(
        config,
        guard=SSRFGuard(("example.com",), resolver=public_resolver),
        transport=transport,
    )
    result = crawler.crawl(("https://example.com/",))
    assert len(result.pages) == 2
    assert result.discovered_count == 2
    assert result.pages[0].title == "Observed title"
    assert result.pages[0].h1 == ("Observed H1",)
    assert transport.calls.count("https://example.com/robots.txt") == 1


def test_bounded_crawler_fails_closed_when_robots_disallows() -> None:
    routes = {
        "https://example.com/robots.txt": FetchResponse(
            200,
            {"content-type": "text/plain"},
            b"User-agent: *\nDisallow: /\n",
            "https://example.com/robots.txt",
        )
    }
    transport = FakeTransport(routes)
    crawler = BoundedCrawler(
        CrawlConfig(("example.com",), min_host_delay_seconds=0),
        guard=SSRFGuard(("example.com",), resolver=public_resolver),
        transport=transport,
    )
    result = crawler.crawl(("https://example.com/",))
    assert not result.pages
    assert result.failures[0].code == "fetch_failed"
