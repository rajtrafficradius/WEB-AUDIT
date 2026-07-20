from __future__ import annotations

import socket

import pytest

from audit_engine.crawler import (
    BoundedCrawler,
    CrawlConfig,
    FetchResponse,
    PinnedHTTPTransport,
    classify_challenge,
    parse_retry_after,
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


ROBOTS_BODY = b"User-agent: *\nAllow: /"
CHALLENGE_HTML = (
    b"<html><head><title>Just a moment...</title></head>"
    b"<body>Verifying your connection is secure. cf-browser-verification</body></html>"
)


class ScriptedTransport:
    """Serves a scripted sequence of responses per URL, recording every request."""

    def __init__(self, responses: dict[str, list[tuple[int, dict, bytes]]]) -> None:
        self.responses = responses
        self.requested: list[str] = []

    def fetch(self, target, **kwargs):
        del kwargs
        url = target.normalized_url
        self.requested.append(url)
        if url.endswith("/robots.txt"):
            return FetchResponse(200, {"content-type": "text/plain"}, ROBOTS_BODY, url)
        script = self.responses.get(url) or [(200, {"content-type": "text/html"}, b"<html></html>")]
        status, headers, body = script[0] if len(script) == 1 else script.pop(0)
        return FetchResponse(status, headers, body, url)


def _crawler(transport, sleeps, **config):
    config.setdefault("min_host_delay_seconds", 0)
    return BoundedCrawler(
        CrawlConfig(("example.com",), **config),
        guard=SSRFGuard(("example.com",), resolver=resolver),
        transport=transport,
        sleep=sleeps.append,
    )


def test_rate_limited_url_is_retried_once_after_honouring_retry_after() -> None:
    transport = ScriptedTransport({
        "https://example.com/": [
            (429, {"retry-after": "3", "content-type": "text/html"}, b"slow down"),
            (200, {"content-type": "text/html"}, b"<html><title>Real page</title></html>"),
        ],
    })
    sleeps: list[float] = []
    result = _crawler(transport, sleeps).crawl(("https://example.com/",))

    assert 3 in sleeps  # the origin's Retry-After was honoured, not ignored
    assert result.challenged_count == 0
    assert len(result.pages) == 1
    assert result.pages[0].challenge is False
    assert result.pages[0].status_code == 200
    assert transport.requested.count("https://example.com/") == 2


def test_retry_after_is_capped_so_one_host_cannot_stall_the_run() -> None:
    transport = ScriptedTransport({
        "https://example.com/": [
            (429, {"retry-after": "86400"}, b"slow down"),
            (200, {"content-type": "text/html"}, b"<html></html>"),
        ],
    })
    sleeps: list[float] = []
    _crawler(transport, sleeps, max_retry_after_seconds=5).crawl(("https://example.com/",))
    assert max(sleeps) <= 5


def test_persistent_rate_limit_quarantines_the_url_instead_of_blaming_the_site() -> None:
    transport = ScriptedTransport({
        "https://example.com/": [(429, {"retry-after": "1"}, b"slow down")],
    })
    result = _crawler(transport, []).crawl(("https://example.com/",))

    assert result.challenged_count == 1
    assert result.rate_limited_count == 1
    page = result.pages[0]
    assert page.challenge is True
    assert page.challenge_kind == "rate_limited"
    assert page.retry_after == 1
    # None of the challenge body may be presented as the client's own content.
    assert page.title is None
    assert page.h1 == ()
    assert page.word_count is None
    assert result.failures[0].challenge is True
    assert result.failures[0].code == "challenge_response"


def test_rate_limit_widens_the_per_host_delay() -> None:
    transport = ScriptedTransport({
        "https://example.com/": [
            (429, {}, b"slow down"),
            (200, {"content-type": "text/html"}, b"<html></html>"),
        ],
    })
    crawler = _crawler(transport, [], min_host_delay_seconds=0.5, max_host_delay_seconds=8)
    assert crawler.host_delay("example.com") == 0.5
    crawler.crawl(("https://example.com/",))
    assert crawler.host_delay("example.com") == 2.0  # 0.5 x 4


def test_repeated_rate_limits_escalate_the_delay_up_to_the_cap() -> None:
    transport = ScriptedTransport({
        "https://example.com/": [(429, {}, b"slow down")],
    })
    crawler = _crawler(transport, [], min_host_delay_seconds=0.5, max_host_delay_seconds=8)
    crawler.crawl(("https://example.com/",))
    assert crawler.host_delay("example.com") == 8.0  # 0.5 -> 2 -> 8, capped


def test_challenge_body_on_a_200_response_is_detected_and_quarantined() -> None:
    transport = ScriptedTransport({
        "https://example.com/": [(200, {"content-type": "text/html"}, CHALLENGE_HTML)],
    })
    result = _crawler(transport, []).crawl(("https://example.com/",))

    assert result.challenged_count == 1
    assert result.rate_limited_count == 0
    assert result.pages[0].challenge_kind == "bot_challenge"
    # The interstitial's title must never survive into the audit.
    assert result.pages[0].title is None


@pytest.mark.parametrize(
    ("status", "headers", "body", "expected_kind"),
    [
        (429, {}, "", "rate_limited"),
        (503, {"retry-after": "10"}, "", "rate_limited"),
        (403, {}, "Attention Required! Cloudflare", "access_denied"),
        (200, {}, "Checking your browser before accessing", "bot_challenge"),
        (200, {}, "A normal page about our services", None),
        (503, {}, "Scheduled maintenance", None),
        (404, {}, "Page not found", None),
    ],
)
def test_classify_challenge_is_deterministic(status, headers, body, expected_kind) -> None:
    signal = classify_challenge(status, headers, body, len(body))
    assert signal.challenge is (expected_kind is not None)
    assert signal.kind == expected_kind


def test_large_2xx_page_quoting_a_marker_is_not_misread_as_a_challenge() -> None:
    body = "Just a moment " + ("real editorial copy " * 10_000)
    assert classify_challenge(200, {}, body, len(body)).challenge is False


@pytest.mark.parametrize(
    ("value", "expected"),
    [("30", 30), ("0", 0), (None, None), ("", None), ("Wed, 21 Oct 2026 07:28:00 GMT", None),
     ("-5", None), ("abc", None)],
)
def test_parse_retry_after_never_guesses(value, expected) -> None:
    assert parse_retry_after(value) == expected
