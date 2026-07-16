from __future__ import annotations

import socket

import pytest

from audit_engine.crawler import (
    BoundedCrawler,
    CrawlConfig,
    FetchResponse,
    PinnedHTTPTransport,
)
from audit_engine.urls import SSRFGuard


def resolver(host: str, port: int, family: int, kind: int):
    del host, family
    return [(socket.AF_INET, kind, 6, "", ("93.184.216.34", port))]


class RedirectTransport:
    def fetch(self, target, **kwargs):
        del kwargs
        if target.normalized_url.endswith("/robots.txt"):
            return FetchResponse(
                200,
                {"content-type": "text/plain"},
                b"User-agent: *\nAllow: /",
                target.normalized_url,
            )
        return FetchResponse(
            302,
            {"location": "https://attacker.test/private"},
            b"",
            target.normalized_url,
        )


def test_crawler_revalidates_every_redirect_and_never_fetches_cross_domain() -> None:
    crawler = BoundedCrawler(
        CrawlConfig(("example.com",), min_host_delay_seconds=0),
        guard=SSRFGuard(("example.com",), resolver=resolver),
        transport=RedirectTransport(),
    )
    result = crawler.crawl(("https://example.com/",))
    assert not result.pages
    assert len(result.failures) == 1


def test_transport_rejects_header_injection_before_opening_socket() -> None:
    target = SSRFGuard(("example.com",), resolver=resolver).validate("https://example.com/")
    with pytest.raises(ValueError, match="header"):
        PinnedHTTPTransport().fetch(
            target,
            headers={"X-Trace": "safe\r\nX-Evil: injected"},
            timeout=1,
            max_bytes=100,
        )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_pages": 0},
        {"max_depth": -1},
        {"request_timeout_seconds": 0},
        {"max_body_bytes": 0},
        {"min_host_delay_seconds": -1},
    ],
)
def test_crawl_budgets_fail_closed(kwargs: dict) -> None:
    with pytest.raises(ValueError):
        CrawlConfig(("example.com",), **kwargs)
